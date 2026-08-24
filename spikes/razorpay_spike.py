"""
Razorpay integration spike (throwaway script, not part of the real app).

Goal: verify we can create a test-mode order with the official Razorpay
Python SDK, and document (since it can't be scripted headlessly) what the
capture flow / webhook / decline-handling would look like.

Requires RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET (test mode keys, i.e.
key id starting with "rzp_test_") in .env at the project root.
"""

import os
import sys

import razorpay
from dotenv import load_dotenv

load_dotenv()

KEY_ID = os.environ.get("RAZORPAY_KEY_ID")
KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET")

AMOUNT_PAISE = 500 * 100  # Razorpay amounts are always in the smallest currency unit (paise for INR)
CURRENCY = "INR"


# ---------------------------------------------------------------------------
# Step 1: create a test-mode order
# ---------------------------------------------------------------------------

def create_order(client: razorpay.Client) -> dict:
    return client.order.create(
        {
            "amount": AMOUNT_PAISE,
            "currency": CURRENCY,
            "receipt": "spike_receipt_001",
            "payment_capture": 1,  # auto-capture once payment is authorized
        }
    )


# ---------------------------------------------------------------------------
# Step 2: what the capture flow requires (documented, not executable here)
# ---------------------------------------------------------------------------
#
# An Order is just a container for a payment amount — it does not move money.
# Turning it into an actual payment requires a checkout UI (Razorpay
# Checkout.js, or a hosted page) where a real or test card/UPI/etc. is
# entered by a human, because card data can't legally/safely be POSTed
# straight from a backend script (PCI-DSS). That's why this spike can only
# create the order, not complete a payment.
#
# Full flow, for reference:
#
# 1. Backend creates an Order (what create_order() above does) and sends
#    order_id + key_id to the frontend.
#
# 2. Frontend opens Razorpay Checkout with those values:
#      const options = {
#        key: "rzp_test_...",
#        amount: 50000,
#        currency: "INR",
#        order_id: "order_xxxxx",
#        handler: function (response) { ... }
#      };
#      new Razorpay(options).open();
#    The user enters card/UPI details inside Razorpay's hosted widget —
#    the merchant backend never sees raw card data.
#
# 3. On success, Checkout calls the `handler` with:
#      {
#        razorpay_payment_id: "pay_xxxxx",
#        razorpay_order_id: "order_xxxxx",
#        razorpay_signature: "..."
#      }
#    The backend MUST verify razorpay_signature (HMAC-SHA256 of
#    order_id + "|" + payment_id, keyed with key_secret) before trusting
#    the payment:
#      client.utility.verify_payment_signature({
#          "razorpay_order_id": order_id,
#          "razorpay_payment_id": payment_id,
#          "razorpay_signature": signature,
#      })
#
# 4. If payment_capture was set to 1 at order creation (as above), Razorpay
#    auto-captures the payment on successful authorization and no further
#    API call is needed. If auto-capture is off (payment_capture: 0), the
#    backend must explicitly call:
#      client.payment.capture(payment_id, amount, {"currency": "INR"})
#    within the auto-refund window (currently 5 days) or the authorized
#    amount is refunded automatically.
#
# 5. Razorpay also sends a `payment.captured` webhook (POST to a URL
#    configured in the dashboard) as the source of truth — webhooks should
#    be verified (HMAC of the raw body with the webhook secret) and treated
#    as more reliable than the frontend handler, since the frontend callback
#    can be skipped (tab closed, network drop, etc.) while the webhook still
#    fires. A `payment.captured` webhook payload looks like this:
#
# {
#   "entity": "event",
#   "account_id": "acc_BFQ7uQEaa7j2z7",
#   "event": "payment.captured",
#   "contains": ["payment"],
#   "payload": {
#     "payment": {
#       "entity": {
#         "id": "pay_DESlfW9H8K9uqM",
#         "entity": "payment",
#         "amount": 50000,
#         "currency": "INR",
#         "status": "captured",
#         "order_id": "order_DESlLckIVRkHWj",
#         "method": "card",
#         "captured": true,
#         "email": "gaurav.kumar@example.com",
#         "contact": "+919876543210",
#         "fee": 2,
#         "tax": 0,
#         "error_code": null,
#         "error_description": null,
#         "created_at": 1567674599
#       }
#     }
#   },
#   "created_at": 1567674606
# }
#
# Source: https://razorpay.com/docs/webhooks/payloads/payments/


# ---------------------------------------------------------------------------
# Step 3: simulating a DECLINED payment in test mode
# ---------------------------------------------------------------------------
#
# Razorpay test mode replaces the real bank page with a mock page that has
# Success/Failure buttons, but specific test card numbers deterministically
# trigger specific decline reasons without needing to click anything, e.g.
# (any future expiry date + any random CVV):
#
#   card_declined          Visa 4100 2800 0006 0003  -> "declined by the bank"
#   insufficient_fund      Visa 4100 2800 0008 0001  -> "insufficient account balance"
#   card_number_invalid    Visa 4100 2800 0001 0008  -> "incorrect card number"
#   payment_timed_out      Visa 4100 2800 0009 0000  -> temporary issue / timeout
#   authentication_failed  Visa 4100 2800 0000 0009  -> incorrect OTP / 3DS failure
#
# Mastercard equivalents also exist (e.g. 5305 6200 0003 0003 for
# card_declined). Full list:
# https://razorpay.com/docs/payments/payments/test-card-details/
#
# For UPI specifically, no card number is needed: entering the VPA
# "failure@razorpay" instantly simulates a declined UPI payment, and
# "success@razorpay" simulates success.
#
# Since a card decline can only actually be triggered through the Checkout
# UI (see note in Step 2 — we can't legally POST card data from a script),
# this spike instead documents + implements the *handling* side: what the
# backend does when it receives a failed payment, either via the
# `payment.failed` webhook or by fetching a payment and checking its status.

def handle_declined_payment(payment: dict) -> None:
    """
    Given a payment dict (e.g. from client.payment.fetch(payment_id), or
    from a payment.failed webhook payload["payload"]["payment"]["entity"]),
    extract and report the decline reason.
    """
    if payment.get("status") != "failed":
        return

    error_code = payment.get("error_code")
    error_description = payment.get("error_description")
    error_reason = payment.get("error_reason")
    error_source = payment.get("error_source")
    error_step = payment.get("error_step")

    print("Payment declined:")
    print(f"  payment_id : {payment.get('id')}")
    print(f"  error_code : {error_code}")
    print(f"  reason     : {error_reason}")
    print(f"  source     : {error_source} (step: {error_step})")
    print(f"  description: {error_description}")


# A `payment.failed` webhook payload looks like this (from Razorpay docs):
EXAMPLE_FAILED_PAYMENT_WEBHOOK = {
    "entity": "event",
    "event": "payment.failed",
    "contains": ["payment"],
    "payload": {
        "payment": {
            "entity": {
                "id": "pay_DEAU825sJlCbGa",
                "entity": "payment",
                "amount": 50000,
                "currency": "INR",
                "status": "failed",
                "order_id": "order_DEATVTRRctwEGb",
                "method": "card",
                "captured": False,
                "email": "gaurav.kumar@example.com",
                "contact": "+919876543210",
                "error_code": "BAD_REQUEST_ERROR",
                "error_description": "Your card was declined by the bank. "
                "Try another card or bank account.",
                "error_source": "bank",
                "error_step": "payment_authorization",
                "error_reason": "card_declined",
                "created_at": 1567610214,
            }
        }
    },
    "created_at": 1567610215,
}


# Also demonstrate what catching an SDK-level error looks like: if you try
# to capture a payment that doesn't exist / isn't in the right state, the
# SDK raises razorpay.errors.BadRequestError.
def demo_capture_error_handling(client: razorpay.Client) -> None:
    try:
        client.payment.capture("pay_doesnotexist000", AMOUNT_PAISE, {"currency": CURRENCY})
    except razorpay.errors.BadRequestError as exc:
        print(f"Caught BadRequestError as expected: {exc}")
    except Exception as exc:  # pragma: no cover - just for the spike demo
        print(f"Caught unexpected error type ({type(exc).__name__}): {exc}")


# ---------------------------------------------------------------------------
# Step 4: run it and print a summary
# ---------------------------------------------------------------------------

def main() -> None:
    print("=== Razorpay Spike ===\n")

    if not KEY_ID or not KEY_SECRET:
        print("RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET not set in .env — aborting.")
        print("Paste your test-mode keys into .env (not committed) and re-run.")
        sys.exit(1)

    if not KEY_ID.startswith("rzp_test_"):
        print(f"WARNING: key_id '{KEY_ID}' does not look like a test-mode key "
              "(expected it to start with 'rzp_test_'). Refusing to run "
              "against what might be a live key.")
        sys.exit(1)

    client = razorpay.Client(auth=(KEY_ID, KEY_SECRET))

    order_created = False
    order = None
    error = None
    try:
        order = create_order(client)
        order_created = True
    except razorpay.errors.BadRequestError as exc:
        error = f"BadRequestError: {exc}"
    except Exception as exc:  # noqa: BLE001 - spike script, want to see any failure
        error = f"{type(exc).__name__}: {exc}"

    print("--- Decline handling demo (using a synthetic webhook payload) ---")
    handle_declined_payment(EXAMPLE_FAILED_PAYMENT_WEBHOOK["payload"]["payment"]["entity"])

    print("\n--- Capture-error handling demo (calling capture on a fake payment id) ---")
    demo_capture_error_handling(client)

    print("\n=== Summary ===")
    print(f"Order creation: {'WORKS' if order_created else 'FAILS'}")
    if order_created:
        print("Success response looks like:")
        print(f"  id       : {order['id']}")
        print(f"  amount   : {order['amount']} paise (₹{order['amount'] / 100})")
        print(f"  currency : {order['currency']}")
        print(f"  status   : {order['status']}")
        print(f"  receipt  : {order['receipt']}")
    else:
        print(f"Failure reason: {error}")
        print("Example of what a failure response looks like (BadRequestError):")
        print('  {"error": {"code": "BAD_REQUEST_ERROR", '
              '"description": "<field>: <what was wrong>"}}')


if __name__ == "__main__":
    main()
