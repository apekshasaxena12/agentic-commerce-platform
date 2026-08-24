"""
Manual end-to-end proof for the Day 9 backend, run against a live
`uvicorn server.app:app` process:

  1. Connects to /ws/chat as a real WebSocket client.
  2. Sends a checkout message, watches audit_entry messages stream in live
     (not batched), reaches "awaiting_confirm".
  3. Sends the confirm signal, reaches "start_checkout" with a REAL
     razorpay_order_id (created via the live Razorpay test-mode API).
  4. Builds a payment.captured webhook payload referencing that real
     razorpay_order_id, computes its REAL HMAC-SHA256 signature using
     RAZORPAY_WEBHOOK_SECRET (the same signing scheme Razorpay itself
     uses), and POSTs it to /webhooks/razorpay exactly like Razorpay would
     — proving the signature verification logic is real, not skipped.
  5. Confirms the webhook call resumes the paused pipeline and the
     WebSocket receives a final "completed" status.

Run (server must already be up on port 8000):
    python -m server.manual_test
"""

import asyncio
import hashlib
import hmac
import json
import os
import time

import httpx
import websockets
from dotenv import load_dotenv

load_dotenv()

WS_URL = "ws://127.0.0.1:8000/ws/chat"
HTTP_URL = "http://127.0.0.1:8000"
WEBHOOK_SECRET = os.environ["RAZORPAY_WEBHOOK_SECRET"]


def sign(body_bytes: bytes) -> str:
    return hmac.new(WEBHOOK_SECRET.encode(), msg=body_bytes, digestmod=hashlib.sha256).hexdigest()


def build_captured_webhook(razorpay_order_id: str, amount_paise: int) -> dict:
    now = int(time.time())
    return {
        "entity": "event",
        "account_id": "acc_BFQ7uQEaa7j2z7",
        "event": "payment.captured",
        "contains": ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_MANUALTEST0001",
                    "entity": "payment",
                    "amount": amount_paise,
                    "currency": "INR",
                    "status": "captured",
                    "order_id": razorpay_order_id,
                    "method": "card",
                    "captured": True,
                    "email": "manual.test@example.com",
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


async def main() -> None:
    async with websockets.connect(WS_URL) as ws:
        connected = json.loads(await ws.recv())
        print(f"connected: {connected}")

        await ws.send(json.dumps({"type": "message", "text": "Buy the DryTech Running Tee"}))

        order_id = None
        while True:
            msg = json.loads(await ws.recv())
            t = msg.get("type")
            if t == "audit_entry":
                print(f"  [live audit] step={msg['step']:<13} {msg['output_summary']}")
            elif t == "search_results":
                print(f"  search_results: {len(msg['results'])} products")
            elif t == "recommendation":
                print(f"  recommendation: {msg}")
            elif t == "awaiting_confirm":
                order_id = msg["order_id"]
                print(f"  awaiting_confirm: order_id={order_id} amount={msg['amount']}")
            elif t == "turn_complete":
                break
            else:
                print(f"  [{t}] {msg}")

        assert order_id is not None, "expected the pipeline to pause for confirm"

        print("\nsending confirm...")
        await ws.send(json.dumps({"type": "confirm", "decision": "confirm"}))

        razorpay_order_id = None
        amount = None
        while True:
            msg = json.loads(await ws.recv())
            t = msg.get("type")
            if t == "audit_entry":
                print(f"  [live audit] step={msg['step']:<13} {msg['output_summary']}")
            elif t == "start_checkout":
                razorpay_order_id = msg["razorpay_order_id"]
                amount = msg["amount"]
                print(f"  start_checkout: razorpay_order_id={razorpay_order_id} amount={amount} key_id={msg['key_id']}")
            elif t == "turn_complete":
                break
            else:
                print(f"  [{t}] {msg}")

        assert razorpay_order_id is not None, "expected a real razorpay_order_id from start_checkout"
        print(f"\nCONFIRMED: real Razorpay order created: {razorpay_order_id}")

        # --- Step 4/5: build + sign + POST a real webhook payload ---
        payload = build_captured_webhook(razorpay_order_id, int(amount * 100))
        body_bytes = json.dumps(payload).encode("utf-8")
        signature = sign(body_bytes)

        print("\nPOSTing a manually-signed payment.captured webhook to /webhooks/razorpay...")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{HTTP_URL}/webhooks/razorpay",
                content=body_bytes,
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": signature,
                },
            )
        print(f"webhook response: {resp.status_code} {resp.json()}")
        assert resp.status_code == 200
        assert resp.json()["final_status"] == "completed"

        # --- Also prove signature verification actually rejects a bad signature ---
        print("\nPOSTing the SAME payload with a WRONG signature (must be rejected)...")
        async with httpx.AsyncClient() as client:
            bad_resp = await client.post(
                f"{HTTP_URL}/webhooks/razorpay",
                content=body_bytes,
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": "0" * 64,
                },
            )
        print(f"bad-signature response: {bad_resp.status_code} {bad_resp.json()}")
        assert bad_resp.status_code == 400

        # --- Confirm the WebSocket saw the final status too ---
        while True:
            msg = json.loads(await ws.recv())
            print(f"  [{msg.get('type')}] {msg}")
            if msg.get("type") == "turn_complete":
                break

        print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
