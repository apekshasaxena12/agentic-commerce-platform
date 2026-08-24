"""
Shared audit-logging helper. Every pipeline node logs through this one
function so the insert shape/behavior is identical everywhere — no node
reimplements its own logging.
"""

from typing import Optional

import psycopg

from db.connection import get_database_url

VALID_STEPS = {
    "intent",
    "retrieve",
    "recommend",
    "policy_check",
    "authorization",
    "razorpay",
    "verification",
}


def log_audit_entry(
    order_id: Optional[int],
    step: str,
    input_summary: str,
    output_summary: str,
    reasoning_text: str,
) -> int:
    if step not in VALID_STEPS:
        raise ValueError(f"invalid audit step: {step!r}")

    with psycopg.connect(get_database_url()) as conn:
        row = conn.execute(
            """
            INSERT INTO audit_log_entry
                (order_id, step, input_summary, output_summary, reasoning_text)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (order_id, step, input_summary, output_summary, reasoning_text),
        ).fetchone()
        return row[0]


def get_audit_trail(order_id: int) -> list[dict]:
    with psycopg.connect(get_database_url()) as conn:
        rows = conn.execute(
            """
            SELECT id, step, "timestamp", input_summary, output_summary, reasoning_text
            FROM audit_log_entry
            WHERE order_id = %s
            ORDER BY id
            """,
            (order_id,),
        ).fetchall()

    return [
        {
            "id": r[0],
            "step": r[1],
            "timestamp": r[2],
            "input_summary": r[3],
            "output_summary": r[4],
            "reasoning_text": r[5],
        }
        for r in rows
    ]


def get_full_audit_trail() -> list[dict]:
    """
    audit_log_entry rows across ALL orders, joined with each order's status/
    amount/items and the requesting agent's name/type — the merchant
    dashboard's full audit-trail view (Day 12), so a judge can pick any past
    order (including a failed/declined one) and see its complete
    Intent->Retrieve->Recommend->PolicyCheck->Authorization->Razorpay->
    Verification trail. Filtering/sorting by order/agent/step is done
    client-side over this one result set — the dataset is demo-scale, and a
    single unfiltered query is simpler than a parameterized one. Inner-joins
    orders, so pure-browsing audit rows (order_id IS NULL, see migration
    0003) are correctly excluded — they belong to no order's trail.
    """
    with psycopg.connect(get_database_url()) as conn:
        rows = conn.execute(
            """
            SELECT a.id, a.order_id, a.step, a."timestamp", a.input_summary,
                   a.output_summary, a.reasoning_text,
                   o.status, o.amount, o.items, o.agent_id, ag.name, ag.type
            FROM audit_log_entry a
            JOIN orders o ON o.id = a.order_id
            JOIN agent ag ON ag.id = o.agent_id
            ORDER BY a.order_id, a.id
            """
        ).fetchall()
    return [
        {
            "id": r[0],
            "order_id": r[1],
            "step": r[2],
            "timestamp": r[3].isoformat(),
            "input_summary": r[4],
            "output_summary": r[5],
            "reasoning_text": r[6],
            "order_status": r[7],
            "order_amount": float(r[8]),
            "order_items": r[9],
            "agent_id": r[10],
            "agent_name": r[11],
            "agent_type": r[12],
        }
        for r in rows
    ]
