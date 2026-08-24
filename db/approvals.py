"""
approval_request row helpers for the ai_agent-over-threshold authorization
path in pipeline/graph.py.
"""

from typing import Optional

import psycopg

from db.connection import get_database_url


def get_pending_approval_request(order_id: int) -> Optional[dict]:
    with psycopg.connect(get_database_url()) as conn:
        row = conn.execute(
            "SELECT id, status FROM approval_request WHERE order_id = %s AND status = 'pending'",
            (order_id,),
        ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "status": row[1]}


def create_approval_request(order_id: int) -> int:
    with psycopg.connect(get_database_url()) as conn:
        row = conn.execute(
            """
            INSERT INTO approval_request (order_id, status)
            VALUES (%s, 'pending')
            RETURNING id
            """,
            (order_id,),
        ).fetchone()
        return row[0]


def resolve_approval_request(approval_id: int, status: str, resolved_by: str) -> None:
    if status not in ("approved", "rejected"):
        raise ValueError(f"invalid approval status: {status!r}")
    with psycopg.connect(get_database_url()) as conn:
        conn.execute(
            """
            UPDATE approval_request
            SET status = %s, resolved_at = now(), resolved_by = %s
            WHERE id = %s
            """,
            (status, resolved_by, approval_id),
        )


def list_pending_approval_requests() -> list[dict]:
    """
    All currently-pending approval_request rows, joined with the order's
    amount/items and the requesting agent's name/type — the merchant
    dashboard's pending-approvals panel (Day 12). This is a DB read, so it
    lists a pending request even if it originated in a since-restarted MCP
    server process; resolving it will fail with a clear reason in that case
    (see mcp_server/server.py's resolve_pending_approval), rather than being
    silently hidden here.
    """
    with psycopg.connect(get_database_url()) as conn:
        rows = conn.execute(
            """
            SELECT ar.id, ar.order_id, ar.requested_at, o.amount, o.items,
                   o.agent_id, ag.name, ag.type
            FROM approval_request ar
            JOIN orders o ON o.id = ar.order_id
            JOIN agent ag ON ag.id = o.agent_id
            WHERE ar.status = 'pending'
            ORDER BY ar.requested_at
            """
        ).fetchall()
    return [
        {
            "approval_request_id": r[0],
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


def get_approval_request(approval_id: int) -> dict:
    with psycopg.connect(get_database_url()) as conn:
        row = conn.execute(
            """
            SELECT id, order_id, status, requested_at, resolved_at, resolved_by
            FROM approval_request WHERE id = %s
            """,
            (approval_id,),
        ).fetchone()
    return {
        "id": row[0],
        "order_id": row[1],
        "status": row[2],
        "requested_at": row[3],
        "resolved_at": row[4],
        "resolved_by": row[5],
    }
