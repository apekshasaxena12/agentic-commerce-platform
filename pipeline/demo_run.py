"""
Integration demo for the checkout pipeline (pipeline/graph.py). Runs five
real scenarios against the real seeded DB, real Groq, and (for d/e) real
Razorpay test-mode order creation, and prints the actual audit trail for
each.

Note: since Day 6-7 part 2, `verification` pauses via interrupt() waiting
for a payment.captured/payment.failed webhook payload — there's no live
webhook endpoint yet (nothing can trigger a real one until the Day 9
checkout UI exists), so every checkout scenario below now needs a second
resume feeding a synthetic-but-real-shaped webhook payload to reach a
terminal status. Scenarios (a)/(b)/(c) were written before that pause
existed; they've been extended with that resume step so they still reach
"completed". (d) and (e) are new, and are specifically about proving the
webhook path itself, including the failure/release side.

Run: python -m pipeline.demo_run
"""

import time
from decimal import Decimal

import psycopg

from db.approvals import get_approval_request
from db.audit import get_audit_trail
from db.connection import get_database_url
from db.orders import get_order
from pipeline.graph import resume_pipeline, run_pipeline

HUMAN_AGENT_ID = 5  # "Demo Shopper (human)", human_session, budget_limit=50000
AI_AGENT_ID = 6  # "Shopping Assistant Agent", ai_agent, budget_limit=5000
# merchant_policy.approval_required_above = 2000 (seeded in db/seed.py)


def print_audit_trail(order_id: int) -> None:
    trail = get_audit_trail(order_id)
    print(f"\n--- audit_log_entry rows for order #{order_id} ---")
    for row in trail:
        print(
            f"  [{row['id']}] step={row['step']:<13} at={row['timestamp']}\n"
            f"      input : {row['input_summary']}\n"
            f"      output: {row['output_summary']}\n"
            f"      reason: {row['reasoning_text']}"
        )


def print_order(order_id: int) -> None:
    order = get_order(order_id)
    print(
        f"order #{order_id}: status={order['status']} amount={order['amount']} "
        f"razorpay_order_id={order['razorpay_order_id']} razorpay_payment_id={order['razorpay_payment_id']}"
    )


def get_agent_spent(agent_id: int) -> Decimal:
    with psycopg.connect(get_database_url()) as conn:
        return conn.execute(
            "SELECT spent_so_far FROM agent WHERE id = %s", (agent_id,)
        ).fetchone()[0]


def reset_agent_spend() -> None:
    """Demo hygiene: zero out spent_so_far on the two seeded demo agents so
    repeated runs of this script don't accumulate false budget pressure."""
    with psycopg.connect(get_database_url()) as conn:
        conn.execute(
            "UPDATE agent SET spent_so_far = 0 WHERE id IN (%s, %s)",
            (HUMAN_AGENT_ID, AI_AGENT_ID),
        )


# ---------------------------------------------------------------------------
# Synthetic webhook payloads, shaped exactly like what spikes/razorpay_spike.py
# documented from Razorpay's own docs (https://razorpay.com/docs/webhooks/payloads/payments/)
# — field-for-field, not reconstructed from memory.
# ---------------------------------------------------------------------------


def build_captured_webhook(razorpay_order_id: str, amount_rupees: Decimal, payment_id: str) -> dict:
    now = int(time.time())
    return {
        "entity": "event",
        "account_id": "acc_BFQ7uQEaa7j2z7",
        "event": "payment.captured",
        "contains": ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "entity": "payment",
                    "amount": int(amount_rupees * 100),
                    "currency": "INR",
                    "status": "captured",
                    "order_id": razorpay_order_id,
                    "method": "card",
                    "captured": True,
                    "email": "demo.shopper@example.com",
                    "contact": "+919876543210",
                    "fee": 2,
                    "tax": 0,
                    "error_code": None,
                    "error_description": None,
                    "created_at": now,
                }
            }
        },
        "created_at": now,
    }


def build_failed_webhook(razorpay_order_id: str, amount_rupees: Decimal, payment_id: str) -> dict:
    now = int(time.time())
    return {
        "entity": "event",
        "account_id": "acc_BFQ7uQEaa7j2z7",
        "event": "payment.failed",
        "contains": ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "entity": "payment",
                    "amount": int(amount_rupees * 100),
                    "currency": "INR",
                    "status": "failed",
                    "order_id": razorpay_order_id,
                    "method": "card",
                    "captured": False,
                    "email": "demo.shopper@example.com",
                    "contact": "+919876543210",
                    "error_code": "BAD_REQUEST_ERROR",
                    "error_description": "Your card was declined by the bank. "
                    "Try another card or bank account.",
                    "error_source": "bank",
                    "error_step": "payment_authorization",
                    "error_reason": "card_declined",
                    "created_at": now,
                }
            }
        },
        "created_at": now,
    }


def _wait_for_step(result: dict, expected_type: str) -> dict:
    assert "__interrupt__" in result, f"expected a pause (waiting on {expected_type!r})"
    value = result["__interrupt__"][0].value
    assert value["type"] == expected_type, f"expected interrupt type {expected_type!r}, got {value['type']!r}"
    return value


def scenario_a_human_in_budget() -> None:
    print("\n" + "=" * 70)
    print("SCENARIO (a): human_session buys something in-budget")
    print("=" * 70)

    result = run_pipeline(
        {
            "user_message": "I want to buy the Convertible Running Pants",
            "agent_id": HUMAN_AGENT_ID,
            "payment_method": "card",
            "discount_pct": 0,
        },
        thread_id="demo-a",
    )
    order_id = result.get("order_id")
    _wait_for_step(result, "human_confirm_required")
    print_order(order_id)

    print("\n(simulating the human sending a 'confirm' signal)")
    result2 = resume_pipeline("demo-a", "confirm")
    interrupt_value = _wait_for_step(result2, "webhook_required")
    razorpay_order_id = interrupt_value["razorpay_order_id"]
    print(f"real razorpay_order_id created: {razorpay_order_id}")

    print("(simulating a payment.captured webhook)")
    payload = build_captured_webhook(razorpay_order_id, Decimal(str(result2["amount"])), "pay_DEMOA0001CAPTUR")
    result3 = resume_pipeline("demo-a", payload)
    print(f"final_status={result3.get('final_status')}")
    print_order(order_id)
    print_audit_trail(order_id)


def scenario_b_ai_agent_under_threshold() -> None:
    print("\n" + "=" * 70)
    print("SCENARIO (b): ai_agent buys something UNDER approval_required_above (2000)")
    print("=" * 70)

    result = run_pipeline(
        {
            "user_message": "Buy the Compression Running Tights now",
            "agent_id": AI_AGENT_ID,
            "payment_method": "card",
            "discount_pct": 0,
        },
        thread_id="demo-b",
    )
    order_id = result.get("order_id")
    print(f"authorized without pausing: authorized={result.get('authorized')}")
    interrupt_value = _wait_for_step(result, "webhook_required")
    razorpay_order_id = interrupt_value["razorpay_order_id"]
    print(f"real razorpay_order_id created: {razorpay_order_id}")

    print("(simulating a payment.captured webhook)")
    payload = build_captured_webhook(razorpay_order_id, Decimal(str(result["amount"])), "pay_DEMOB0001CAPTUR")
    result2 = resume_pipeline("demo-b", payload)
    print(f"final_status={result2.get('final_status')}")
    print_order(order_id)
    print_audit_trail(order_id)


def scenario_c_ai_agent_over_threshold() -> None:
    print("\n" + "=" * 70)
    print("SCENARIO (c): ai_agent buys something OVER approval_required_above (2000)")
    print("=" * 70)

    # Isolate the approval-required GATE: without this, scenario (b)'s
    # purchase (same agent) would still count against the budget here.
    reset_agent_spend()

    result = run_pipeline(
        {
            "user_message": "Buy the Windproof Running Jacket right now",
            "agent_id": AI_AGENT_ID,
            "payment_method": "card",
            "discount_pct": 0,
        },
        thread_id="demo-c",
    )
    order_id = result.get("order_id")
    interrupt_value = _wait_for_step(result, "merchant_approval_required")
    approval_id = interrupt_value["approval_request_id"]
    approval_before = get_approval_request(approval_id)
    print(f"approval_request #{approval_id} status BEFORE resolution: {approval_before['status']}")
    print_order(order_id)
    assert get_order(order_id)["status"] != "completed"
    print("    confirmed: order status is not 'completed' while approval is pending")

    print("\n(simulating a merchant approving the request)")
    result2 = resume_pipeline("demo-c", {"approved": True, "resolved_by": "demo_merchant"})
    approval_after = get_approval_request(approval_id)
    print(f"approval_request #{approval_id} status AFTER resolution: {approval_after['status']}")
    interrupt_value2 = _wait_for_step(result2, "webhook_required")
    razorpay_order_id = interrupt_value2["razorpay_order_id"]
    print(f"real razorpay_order_id created: {razorpay_order_id}")

    print("(simulating a payment.captured webhook)")
    payload = build_captured_webhook(razorpay_order_id, Decimal(str(result2["amount"])), "pay_DEMOC0001CAPTUR")
    result3 = resume_pipeline("demo-c", payload)
    print(f"final_status={result3.get('final_status')}")
    print_order(order_id)
    print_audit_trail(order_id)


def scenario_d_captured_webhook() -> None:
    print("\n" + "=" * 70)
    print("SCENARIO (d): ai_agent in-budget purchase, real order + simulated payment.captured")
    print("=" * 70)

    reset_agent_spend()

    result = run_pipeline(
        {
            "user_message": "Buy the Compression Base Layer Top",
            "agent_id": AI_AGENT_ID,
            "payment_method": "card",
            "discount_pct": 0,
        },
        thread_id="demo-d",
    )
    order_id = result.get("order_id")
    print(f"authorized={result.get('authorized')} (auto, under threshold)")
    interrupt_value = _wait_for_step(result, "webhook_required")
    razorpay_order_id = interrupt_value["razorpay_order_id"]
    assert razorpay_order_id is not None and razorpay_order_id.startswith("order_")
    print(f"REAL razorpay_order_id created via live Razorpay test-mode API: {razorpay_order_id}")

    print("\n(feeding a synthetic payment.captured webhook, shaped per spikes/razorpay_spike.py)")
    payload = build_captured_webhook(razorpay_order_id, Decimal(str(result["amount"])), "pay_DEMOD0001CAPTUR")
    result2 = resume_pipeline("demo-d", payload)
    print(f"final_status={result2.get('final_status')}")
    print_order(order_id)
    print_audit_trail(order_id)

    order = get_order(order_id)
    assert order["status"] == "completed"
    assert order["razorpay_order_id"] == razorpay_order_id
    print("\nconfirmed: order completed, razorpay_order_id is the real one from the live API call")


def scenario_e_failed_webhook_releases_budget() -> None:
    print("\n" + "=" * 70)
    print("SCENARIO (e): ai_agent in-budget purchase, real order + simulated payment.failed (card_declined)")
    print("=" * 70)

    reset_agent_spend()
    spent_before = get_agent_spent(AI_AGENT_ID)
    print(f"agent #{AI_AGENT_ID} spent_so_far BEFORE purchase attempt: {spent_before}")

    result = run_pipeline(
        {
            "user_message": "Buy the DryTech Running Tee",
            "agent_id": AI_AGENT_ID,
            "payment_method": "card",
            "discount_pct": 0,
        },
        thread_id="demo-e",
    )
    order_id = result.get("order_id")
    amount = Decimal(str(result["amount"]))
    spent_after_reserve = get_agent_spent(AI_AGENT_ID)
    print(f"agent #{AI_AGENT_ID} spent_so_far AFTER policy_check reserved the order amount ({amount}): {spent_after_reserve}")
    assert spent_after_reserve == spent_before + amount

    interrupt_value = _wait_for_step(result, "webhook_required")
    razorpay_order_id = interrupt_value["razorpay_order_id"]
    print(f"REAL razorpay_order_id created via live Razorpay test-mode API: {razorpay_order_id}")

    print("\n(feeding a synthetic payment.failed webhook, error_reason=card_declined, "
          "shaped per spikes/razorpay_spike.py's EXAMPLE_FAILED_PAYMENT_WEBHOOK)")
    payload = build_failed_webhook(razorpay_order_id, amount, "pay_DEMOE0001FAILED")
    result2 = resume_pipeline("demo-e", payload)
    print(f"final_status={result2.get('final_status')} (no exception raised)")
    print_order(order_id)

    spent_after_release = get_agent_spent(AI_AGENT_ID)
    print(f"agent #{AI_AGENT_ID} spent_so_far AFTER release_budget: {spent_after_release}")
    assert spent_after_release == spent_before

    order = get_order(order_id)
    assert order["status"] == "failed"
    print("\nconfirmed: order status='failed', budget released back to its pre-purchase value, no exception bubbled up")

    print_audit_trail(order_id)


def main() -> None:
    reset_agent_spend()
    scenario_a_human_in_budget()
    scenario_b_ai_agent_under_threshold()
    scenario_c_ai_agent_over_threshold()
    scenario_d_captured_webhook()
    scenario_e_failed_webhook_releases_budget()


if __name__ == "__main__":
    main()
