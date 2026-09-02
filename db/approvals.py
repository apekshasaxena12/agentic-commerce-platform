"""
approval_request row helpers, backing the merchant-approval gate in
pipeline/graph.py's authorization_node (ai_agent, over-threshold branch).
"""

from typing import Optional

import psycopg

from db.connection import get_database_url


def create_approval_request(order_id: int) -> int:
    """
    Get-or-create: returns the existing approval_request for this order if
    one was already made, otherwise inserts a new one. Idempotent because
    LangGraph re-executes a node from the top on every resume of a pending
    interrupt() — the code path that calls this runs once on the pausing
    call and again on the resuming call, and must not create two rows for
    the same pause.
    """
    with psycopg.connect(get_database_url()) as conn:
        row = conn.execute(
            "SELECT id FROM approval_request WHERE order_id = %s ORDER BY id DESC LIMIT 1",
            (order_id,),
        ).fetchone()
        if row is not None:
            return row[0]
        row = conn.execute(
            "INSERT INTO approval_request (order_id) VALUES (%s) RETURNING id",
            (order_id,),
        ).fetchone()
        return row[0]


def resolve_approval_request(approval_id: int, approved: bool, resolved_by: Optional[str]) -> None:
    status = "approved" if approved else "rejected"
    with psycopg.connect(get_database_url()) as conn:
        conn.execute(
            """
            UPDATE approval_request
            SET status = %s, resolved_at = now(), resolved_by = %s
            WHERE id = %s
            """,
            (status, resolved_by, approval_id),
        )


def get_approval_request(approval_id: int) -> dict:
    with psycopg.connect(get_database_url()) as conn:
        row = conn.execute(
            """
            SELECT id, order_id, status, requested_at, resolved_at, resolved_by
            FROM approval_request WHERE id = %s
            """,
            (approval_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"approval_request {approval_id} not found")
    return {
        "id": row[0],
        "order_id": row[1],
        "status": row[2],
        "requested_at": row[3],
        "resolved_at": row[4],
        "resolved_by": row[5],
    }


def list_pending_approvals_for_merchant(merchant_id: int) -> list[dict]:
    """
    Pending approval_request rows for orders owned by this merchant — the
    merchant dashboard's Pending Approvals tab. Scoped via orders.merchant_id,
    same join pattern as db.audit.get_full_audit_trail, so one merchant only
    ever sees approval requests for their own orders.
    """
    with psycopg.connect(get_database_url()) as conn:
        rows = conn.execute(
            """
            SELECT ar.id, ar.order_id, ar.requested_at, o.amount, o.items,
                   o.agent_id, ag.name, ag.type
            FROM approval_request ar
            JOIN orders o ON o.id = ar.order_id
            JOIN agent ag ON ag.id = o.agent_id
            WHERE o.merchant_id = %s AND ar.status = 'pending'
            ORDER BY ar.requested_at
            """,
            (merchant_id,),
        ).fetchall()
    return [
        {
            "id": r[0],
            "order_id": r[1],
            "requested_at": r[2].isoformat(),
            "amount": float(r[3]),
            "items": r[4],
            "agent_id": r[5],
            "agent_name": r[6],
            "agent_type": r[7],
        }
        for r in rows
    ]
