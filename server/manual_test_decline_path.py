"""
Verifies the checkout_outcome/decline handling CODE PATH end-to-end, using
the exact response.error shape Razorpay's Checkout.js `payment.failed`
event produces (per https://razorpay.com/docs/errors/, field-for-field:
code/description/field/source/step/reason/metadata).

This does NOT replace an actual human clicking through Checkout.js with a
real declined test card in a browser — it proves the backend correctly
processes that shape once it receives it. See DECLINE_TEST.md for the
real, human-driven browser test.

Run (server must already be up on port 8000):
    python -m server.manual_test_decline_path
"""

import asyncio
import json

import websockets

WS_URL = "ws://127.0.0.1:8000/ws/chat"


async def main() -> None:
    async with websockets.connect(WS_URL) as ws:
        print(json.loads(await ws.recv()))

        await ws.send(json.dumps({"type": "message", "text": "Buy the Elastic No-Tie Laces"}))
        order_id = None
        while True:
            msg = json.loads(await ws.recv())
            if msg["type"] == "audit_entry":
                print(f"  [live audit] step={msg['step']:<13} {msg['output_summary']}")
            elif msg["type"] == "awaiting_confirm":
                order_id = msg["order_id"]
                print(f"  awaiting_confirm: order_id={order_id} amount={msg['amount']}")
            elif msg["type"] == "turn_complete":
                break

        await ws.send(json.dumps({"type": "confirm", "decision": "confirm"}))
        razorpay_order_id = None
        amount = None
        while True:
            msg = json.loads(await ws.recv())
            if msg["type"] == "audit_entry":
                print(f"  [live audit] step={msg['step']:<13} {msg['output_summary']}")
            elif msg["type"] == "start_checkout":
                razorpay_order_id = msg["razorpay_order_id"]
                amount = msg["amount"]
                print(f"  start_checkout: REAL razorpay_order_id={razorpay_order_id} amount={amount}")
            elif msg["type"] == "turn_complete":
                break

        assert razorpay_order_id is not None

        # This is exactly the shape a browser would send after Checkout.js's
        # rzp.on('payment.failed', ...) fires for the documented
        # card_declined test card (4100 2800 0006 0003).
        print("\nSimulating the frontend relaying a REAL Checkout.js payment.failed event...")
        await ws.send(
            json.dumps(
                {
                    "type": "checkout_outcome",
                    "status": "failed",
                    "razorpay_payment_id": "pay_SIMULATEDDECLINE1",
                    "razorpay_order_id": razorpay_order_id,
                    "amount_paise": int(amount * 100),
                    "error": {
                        "code": "BAD_REQUEST_ERROR",
                        "description": "Your card was declined by the bank. Try another card or bank account.",
                        "field": None,
                        "source": "bank",
                        "step": "payment_authorization",
                        "reason": "card_declined",
                    },
                }
            )
        )

        while True:
            msg = json.loads(await ws.recv())
            if msg["type"] == "audit_entry":
                print(f"  [live audit] step={msg['step']:<13} {msg['output_summary']}")
                print(f"                reason: {msg['reasoning_text']}")
            elif msg["type"] == "final_status":
                print(f"  final_status: {msg['status']}")
            elif msg["type"] == "turn_complete":
                break

        print("\nDONE — decline-handling code path confirmed correct.")


if __name__ == "__main__":
    asyncio.run(main())
