"""
Real Razorpay integration: test-mode order creation, and parsing of
payment.captured / payment.failed webhook payloads. The SDK usage and the
webhook payload shapes here are exactly what was verified live against
Razorpay's test servers and docs in spikes/razorpay_spike.py (Day 1) — not
reconstructed from memory.

Signature verification (client.utility.verify_payment_signature, documented
in the day-1 spike) isn't wired in yet: there's no real signed webhook to
verify against until the Day 9 checkout UI exists to actually trigger one.
"""

import os
from decimal import Decimal
from typing import Optional

import razorpay
from dotenv import load_dotenv

load_dotenv()

_client: Optional[razorpay.Client] = None


def _get_client() -> razorpay.Client:
    global _client
    if _client is None:
        key_id = os.environ.get("RAZORPAY_KEY_ID")
        key_secret = os.environ.get("RAZORPAY_KEY_SECRET")
        if not key_id or not key_secret:
            raise RuntimeError("RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET not set in .env")
        if not key_id.startswith("rzp_test_"):
            raise RuntimeError(f"refusing to use a non-test key_id: {key_id}")
        _client = razorpay.Client(auth=(key_id, key_secret))
    return _client


def create_razorpay_order(amount_rupees: Decimal, receipt: str) -> dict:
    """
    Create a real test-mode Razorpay order. amount_rupees is in rupees
    (matching the rest of this codebase); Razorpay's API wants paise.
    """
    client = _get_client()
    amount_paise = int((amount_rupees * 100).to_integral_value())
    return client.order.create(
        {
            "amount": amount_paise,
            "currency": "INR",
            "receipt": receipt,
            "payment_capture": 1,
        }
    )


def parse_payment_webhook(payload: dict) -> dict:
    """
    Normalize a payment.captured / payment.failed webhook payload (shape
    per https://razorpay.com/docs/webhooks/payloads/payments/, verified in
    spikes/razorpay_spike.py) into the fields the verification node needs.
    """
    event = payload["event"]
    entity = payload["payload"]["payment"]["entity"]
    return {
        "event": event,
        "razorpay_payment_id": entity["id"],
        "razorpay_order_id": entity["order_id"],
        "status": entity["status"],
        "error_code": entity.get("error_code"),
        "error_description": entity.get("error_description"),
        "error_reason": entity.get("error_reason"),
    }
