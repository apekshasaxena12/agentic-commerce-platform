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


def get_incident_summary(merchant_id: int) -> dict:
    """
    Day 15: the merchant dashboard's Incident Center tab — six real counts
    computed from orders/audit_log_entry/approval_request, scoped to this
    merchant's own orders (orders.merchant_id, same join pattern as
    get_full_audit_trail above). Every number below is derived from the
    exact node behavior in pipeline/graph.py, not guessed:

    - total_orders: COUNT(orders) for this merchant. Pure-browsing pipeline
      runs (audit_log_entry.order_id IS NULL — see migration 0003) are
      deliberately NOT added in: audit_log_entry has no merchant_id column,
      so a browsing run has no way to be attributed to a specific merchant
      at all, and counting it here would be a guess dressed up as a number.
    - auto_approved: ai_agent orders whose authorization step actually ran
      (an 'authorization' audit_log_entry exists) with no approval_request
      row — that combination only happens on the amount<=threshold branch
      of _authorization_impl, which returns immediately without ever
      calling create_approval_request (only the over-threshold branch does).
    - merchant_approvals: orders with an approval_request row that was
      resolved 'approved' — the literal "approval_request -> approved" the
      task asked for; does not include rejected approval_requests (a
      different, unrequested category).
    - policy_blocks: orders whose policy_check audit entry recorded at
      least one FAIL — _policy_check_impl's output_summary is always
      "check=PASS" or "check=FAIL" joined by "; ", so this is a direct read
      of the logged check result, not an inference from order status alone
      (status='failed' alone can't distinguish this from an authorization
      rejection or a payment failure — see verification_impl/
      authorization_impl, both of which also set status='failed').
    - payment_failures: status='failed' orders that reached a 'verification'
      audit entry — that entry is only ever logged (by the `audited` wrapper)
      once _verification_impl's payment.failed branch runs, so its presence
      plus status='failed' means the failure happened at/after Razorpay, not
      earlier in the pipeline.
    - unhandled_crashes: audit_log_entry rows whose output_summary starts
      with "FAILED:" — written by pipeline/graph.py's `audited` wrapper's
      except-Exception branch (a real unhandled node exception, re-raised
      after logging, not swallowed) — joined to this merchant's orders via
      order_id. NOT the WebSocket-disconnect handling added in the prior
      session (server/app.py's _safe_send/receive_json guards): those only
      ever `print()` to process stdout, they write nothing to any table, so
      they have no SQL-queryable signal and are correctly excluded here —
      counting them would mean fabricating a number with no data behind it.
      A crash before any order exists (order_id IS NULL, e.g. an intent-node
      failure) is excluded for the same reason pure-browsing runs are: no
      merchant to attribute it to.
    """
    query = """
        SELECT
            (SELECT COUNT(*) FROM orders WHERE merchant_id = %(mid)s) AS total_orders,
            (SELECT COUNT(*) FROM orders o
               JOIN agent ag ON ag.id = o.agent_id
               WHERE o.merchant_id = %(mid)s AND ag.type = 'ai_agent'
                 AND EXISTS (SELECT 1 FROM audit_log_entry a WHERE a.order_id = o.id AND a.step = 'authorization')
                 AND NOT EXISTS (SELECT 1 FROM approval_request ar WHERE ar.order_id = o.id)
            ) AS auto_approved,
            (SELECT COUNT(*) FROM orders o
               WHERE o.merchant_id = %(mid)s
                 AND EXISTS (SELECT 1 FROM approval_request ar WHERE ar.order_id = o.id AND ar.status = 'approved')
            ) AS merchant_approvals,
            (SELECT COUNT(*) FROM orders o
               WHERE o.merchant_id = %(mid)s AND o.status = 'failed'
                 AND EXISTS (
                     SELECT 1 FROM audit_log_entry a
                     WHERE a.order_id = o.id AND a.step = 'policy_check' AND a.output_summary LIKE %(fail_pat)s
                 )
            ) AS policy_blocks,
            (SELECT COUNT(*) FROM orders o
               WHERE o.merchant_id = %(mid)s AND o.status = 'failed'
                 AND EXISTS (SELECT 1 FROM audit_log_entry a WHERE a.order_id = o.id AND a.step = 'verification')
            ) AS payment_failures,
            (SELECT COUNT(*) FROM audit_log_entry a
               JOIN orders o ON o.id = a.order_id
               WHERE o.merchant_id = %(mid)s AND a.output_summary LIKE %(crash_pat)s
            ) AS unhandled_crashes
    """
    with psycopg.connect(get_database_url()) as conn:
        row = conn.execute(
            query,
            {"mid": merchant_id, "fail_pat": "%=FAIL%", "crash_pat": "FAILED:%"},
        ).fetchone()
    return {
        "total_orders": row[0],
        "auto_approved": row[1],
        "merchant_approvals": row[2],
        "policy_blocks": row[3],
        "payment_failures": row[4],
        "unhandled_crashes": row[5],
    }


def get_full_audit_trail(merchant_id: Optional[int] = None) -> list[dict]:
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

    Phase 2 multi-tenant: merchant_id, when given, scopes this to that
    merchant's own orders only (o.merchant_id = %s) — the merchant dashboard
    passes the logged-in merchant's id so one merchant never sees another's
    audit rows; omitted, this stays the old cross-merchant behavior for any
    other caller.
    """
    query = """
        SELECT a.id, a.order_id, a.step, a."timestamp", a.input_summary,
               a.output_summary, a.reasoning_text,
               o.status, o.amount, o.items, o.agent_id, ag.name, ag.type
        FROM audit_log_entry a
        JOIN orders o ON o.id = a.order_id
        JOIN agent ag ON ag.id = o.agent_id
    """
    params: tuple = ()
    if merchant_id is not None:
        query += " WHERE o.merchant_id = %s"
        params = (merchant_id,)
    query += " ORDER BY a.order_id, a.id"

    with psycopg.connect(get_database_url()) as conn:
        rows = conn.execute(query, params).fetchall()
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
