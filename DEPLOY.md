# Deploy notes

Target: backend on Render (persistent container, Dockerfile-based, not
serverless), frontend on Vercel.

## Day 13 status

- **Part 1 (durable checkpointer) — done.** `pipeline/graph.py`'s `GRAPH`
  now uses `PostgresSaver` (from `langgraph-checkpoint-postgres`) against
  `DATABASE_URL`, not `InMemorySaver`. Proven with
  `tests/test_checkpointer_restart.py`, which pauses a real pipeline run in
  one subprocess, kills it with `os._exit(0)`, and resumes the same
  `thread_id` from a second, unrelated subprocess.
- **Part 2 (Dockerfile) — done.** `Dockerfile` at the repo root builds a
  ~2.2GB image (CPU-only torch, not the ~3.2GB CUDA default), bakes the
  `all-MiniLM-L6-v2` model in at build time, and sets `HF_HUB_OFFLINE=1` so
  no request ever waits on a Hugging Face Hub round-trip — verified by
  running the built image with `--network none` (140s → 2s to load the
  model once offline mode was forced). A full chat purchase was run
  end-to-end against a live container of this image (real DB, real Groq,
  real Razorpay test-mode order).
- **Parts 3-5 (actual Render/Vercel deploy, Razorpay webhook
  registration, live smoke test) — need your action.** These create real
  external resources (a Render account's services, a Vercel project, a
  webhook registered against your live Razorpay dashboard). This
  environment has no Render or Vercel CLI/API token and no access to your
  Razorpay dashboard, so they can't be done from here without you either
  doing the dashboard steps yourself or handing over API credentials. `gh`
  IS already authenticated in this environment (account apekshasaxena12)
  if you want a GitHub repo created/pushed as the deploy source — ask and
  it'll be done before anything else below.

## Part 3 — Render (backend)

This one Dockerfile serves BOTH backend processes; create two Render **Web
Services** from the same repo/Dockerfile, differing only in the start
command:

| Service | Start command override | Purpose |
|---|---|---|
| `agentic-commerce-backend` | *(none — uses the Dockerfile's `CMD`)* | Front Door 1: chat WebSocket + `/webhooks/razorpay` |
| `agentic-commerce-mcp` | `python -m mcp_server.server --http --port $PORT` | Front Door 2: MCP tools + merchant dashboard API/WS |

Both services need the same environment variables, set via Render's
dashboard (Environment tab) — **not** committed anywhere:

- `DATABASE_URL` — the existing Supabase pooler URL from `.env`
- `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`
- `GROQ_API_KEY`
- `MERCHANT_SESSION_SECRET` — signs the merchant dashboard's session JWT
  (`mcp_server/merchant_auth.py`); required on the `agentic-commerce-mcp`
  service specifically (that's where `/merchant/login` lives) — without it,
  every merchant login fails with a 500
- `ALLOWED_ORIGINS` — the deployed Vercel URL(s), comma-separated (e.g.
  `https://your-app.vercel.app`); both `server/app.py` and
  `mcp_server/server.py` read this (Day 13 addition — defaults to the
  local dev origins if unset, so nothing breaks if you forget it, but the
  deployed frontend's fetch/WebSocket calls will be blocked by CORS until
  it's set correctly)

Render sets `$PORT` itself; both services already bind to it (see
`Dockerfile`'s `CMD` and `mcp_server/server.py::main`'s `--http` path).

After deploying, confirm both are reachable:
```
curl -i https://agentic-commerce-backend.onrender.com/            # any HTTP response, even 404, proves it's up
curl -i https://agentic-commerce-mcp.onrender.com/merchant/agents # should return the two seeded agents as JSON
```

## Part 4 — Vercel (frontend)

Project root: `frontend/`. Set these Vercel project env vars before the
build (Vite inlines `VITE_*` vars at build time, so they must be set
*before* the deploy, not after):

- `VITE_WS_URL` — `wss://agentic-commerce-backend.onrender.com/ws/chat`
- `VITE_API_URL` — `https://agentic-commerce-backend.onrender.com` (ProductModal.jsx's
  `GET /api/products/{id}` calls; defaults to `localhost:8000` if unset, which
  is why this is easy to miss — the product detail modal would silently try
  to reach the deployer's own machine instead of the deployed backend)
- `VITE_MERCHANT_API_BASE` — `https://agentic-commerce-mcp.onrender.com/merchant`

Confirm reachable: open the Vercel URL and `<vercel-url>/merchant` in a
browser; both should load without a CORS error in devtools (which would
mean `ALLOWED_ORIGINS` on the Render side doesn't include this exact
Vercel URL).

## Part 5 — real Razorpay webhook

In the Razorpay dashboard (test mode): Settings → Webhooks → Add New
Webhook.

- URL: `https://agentic-commerce-backend.onrender.com/webhooks/razorpay`
- Secret: the same value as `RAZORPAY_WEBHOOK_SECRET` (Render env var above)
  — `server/app.py`'s `/webhooks/razorpay` handler verifies the signature
  against this exact secret and 400s if it doesn't match.
- Active events: `payment.captured`, `payment.failed`

The frontend's `checkout_outcome` relay (Day 9, `App.jsx`'s Checkout.js
handlers) stays in place as a fallback — nothing was removed — but once
this webhook is registered, a real purchase against the deployed frontend
should show the order completing from the REAL inbound
`POST /webhooks/razorpay` (visible in Render's logs) rather than the relay
racing it. There's no reliable way to force which one "wins" without
disabling the relay; the ask here is to confirm the real webhook actually
arrives, not that it's the only path.

## Part 6 — smoke test against the deployed system

Same four scenarios as prior sessions' local testing, run against the
Vercel URL / Render URLs instead of localhost:

- (a) human purchase — chat UI at the Vercel URL, confirm, pay with a
  Razorpay test card, confirm `final_status: completed`.
- (b) AI agent auto-approve — an MCP client pointed at
  `https://agentic-commerce-mcp.onrender.com/mcp`, `checkout()` a
  under-threshold product (e.g. Compression Running Tights, ₹1599) →
  `"completed"`.
- (c) AI agent needs approval — `checkout()` an over-threshold product
  (e.g. Windproof Running Jacket, ₹3499) → `"pending_approval"`; approve it
  from `<vercel-url>/merchant`'s Pending Approvals tab; confirm the order
  updates to `"completed"` live.
- forced decline — the Razorpay test-mode declined card
  (4100 2800 0006 0003) through the deployed chat UI; confirm
  `final_status: failed` and the audit trail shows the real decline reason.
