# Triggering a REAL decline through the Checkout UI

I don't have a local browser connected to this session (no Chrome
extension), so I couldn't click through Checkout.js myself — the only
browser tool I have runs on a remote cloud machine that can't reach your
`localhost`. I verified the decline-HANDLING code path is correct end to
end using the real `response.error` shape Razorpay's Checkout.js produces
(see `server/manual_test_decline_path.py` and its output), but the actual
human-driven decline needs you.

Both servers are already running:
- backend: `http://localhost:8000` (uvicorn, started in this session)
- frontend: `http://localhost:5173` (Vite dev server, started in this session)

## Steps

1. Open `http://localhost:5173` in your browser.
2. Type a checkout message, e.g. `Buy the Elastic No-Tie Laces` (cheap item,
   fast to test), and press Send. Watch the live audit panel on the right —
   you'll see `intent` → `retrieve` → `recommend` → `policy_check` →
   `authorization` (paused) stream in as they happen.
3. Click **Confirm purchase**. This creates a REAL Razorpay test-mode order
   (visible in the audit panel as `razorpay` step) and opens the real
   Checkout.js widget.
4. In the Checkout widget, choose **Card**, and enter this documented
   decline-triggering test card (from the Day-1 spike, verified against
   Razorpay's own test-card docs):

   ```
   Card number : 4100 2800 0006 0003
   Expiry      : any future date
   CVV         : any 3 digits
   Name        : anything
   ```

   This card deterministically triggers `error.reason = "card_declined"` —
   no need to click a "fail" button, it declines on its own.
5. Watch the chat log and audit panel: you should see a `[Checkout REAL
   decline: ...]` system message with the real `code`/`reason`/`description`
   Razorpay returned, then a `verification` audit entry showing the order
   marked `failed` and the budget released.

## What to compare

The synthetic webhook payload used throughout this build (`db-1 spike`,
`pipeline/demo_run.py`'s scenario e, `server/manual_test_decline_path.py`)
assumed this exact shape for a `card_declined` decline:

```
error_code:        BAD_REQUEST_ERROR
error_reason:       card_declined
error_source:       bank
error_step:         payment_authorization
error_description:  "Your card was declined by the bank. Try another card or bank account."
```

## Actual result (verified 2026-08-22, order #25)

The real decline, triggered live through Checkout.js with card
`4100 2800 0006 0003`, produced:

```
error_code:        BAD_REQUEST_ERROR   (matches assumption)
error_reason:       payment_failed      (assumption was card_declined — DIFFERENT)
error_description:  "Payment failed"    (assumption was the specific bank-decline
                                          message — DIFFERENT, this is generic)
```

`error_code` matched; `error_reason` and `error_description` did not — the
real Checkout.js `payment.failed` event reported a generic failure rather
than the specific `card_declined` reason Razorpay's test-card docs
describe for this card. The pipeline still handled it correctly end to
end (order marked `failed`, budget released, real `razorpay_payment_id`
recorded) since the code only branches on `event` type
(`payment.captured` vs `payment.failed`), not on the specific `reason`
value. Worth remembering for later: don't build merchant/human-facing
messaging that assumes `error_reason` will always be the specific
documented value — a generic fallback is needed.
