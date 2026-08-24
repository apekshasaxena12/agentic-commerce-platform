"""
Regression test for a real production bug (order #16, confirmed via the
live server's crash traceback and its audit_log_entry trail):

A duplicate/stale "confirm" WebSocket message — sent because the frontend's
Confirm button had no click-guard, so a fast double-click fired "confirm"
twice — arrived AFTER the pipeline had already moved past the
authorization pause to a LATER interrupt (verification's webhook_required).
Command(resume=...) has no concept of which interrupt call site a resume
value was meant for; it just resumes whatever task is currently pending for
that thread_id. So the second "confirm" (a plain string) got resumed
against verification's interrupt instead, landed in
parse_payment_webhook() as if it were a webhook payload, and crashed with:

    TypeError: string indices must be integers, not 'str'

(payload["event"] on a string). This was NOT a Checkout.js-vs-webhook
payload shape mismatch — the actual root cause was a protocol gap: nothing
validated that an incoming message matched what was actually pending
before resuming. The fix (server/app.py's _pending_interrupt tracking)
rejects a mismatched resume attempt instead of forwarding it to
resume_pipeline at all.
"""

import psycopg
from fastapi.testclient import TestClient

from db.connection import get_database_url
from server.app import HUMAN_AGENT_ID, app


def _drain_turn(ws, target_types):
    """
    Reads every message through the end of one logical turn (up to and
    including "turn_complete") and returns the first one seen whose type is
    in target_types, or None. Must fully drain to "turn_complete" — not
    return as soon as the target is seen — otherwise a message from the
    tail of this turn (like its own turn_complete) is left unread and gets
    misread as belonging to the NEXT turn's response.
    """
    found = None
    while True:
        msg = ws.receive_json()
        if found is None and msg["type"] in target_types:
            found = msg
        if msg["type"] == "turn_complete":
            return found


def test_duplicate_confirm_after_authorization_is_rejected_not_misapplied():
    with psycopg.connect(get_database_url()) as conn:
        conn.execute("UPDATE agent SET spent_so_far = 0 WHERE id = %s", (HUMAN_AGENT_ID,))

    with TestClient(app) as client:
        with client.websocket_connect("/ws/chat") as ws:
            connected = ws.receive_json()
            assert connected["type"] == "connected"

            ws.send_json({"type": "message", "text": "Buy the Elastic No-Tie Laces"})
            awaiting = _drain_turn(ws, {"awaiting_confirm"})
            assert awaiting is not None and awaiting["type"] == "awaiting_confirm"

            # First confirm: real resume of the authorization pause. This
            # legitimately proceeds through razorpay (real order creation)
            # to verification's NEW pause (webhook_required).
            ws.send_json({"type": "confirm", "decision": "confirm"})
            started = _drain_turn(ws, {"start_checkout"})
            assert started is not None and started["type"] == "start_checkout"

            # Second confirm — simulates the double-click. The pipeline has
            # already moved on to verification's pause; this must be
            # rejected, not resumed against it (and must not crash: this is
            # exactly the sequence that produced the TypeError on order #16).
            ws.send_json({"type": "confirm", "decision": "confirm"})
            rejected = _drain_turn(ws, {"error"})
            assert rejected is not None and rejected["type"] == "error"
            assert "pending" in rejected["message"]

            # The thread must still be alive and correctly paused at
            # verification afterward — not corrupted by the rejected resume.
            # A real checkout_outcome should still resolve it normally.
            ws.send_json(
                {
                    "type": "checkout_outcome",
                    "status": "captured",
                    "razorpay_payment_id": "pay_REGRESSIONTEST01",
                    "razorpay_order_id": started["razorpay_order_id"],
                    "amount_paise": int(started["amount"] * 100),
                }
            )
            final = _drain_turn(ws, {"final_status"})
            assert final is not None and final["status"] == "completed"
