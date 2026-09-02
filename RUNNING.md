# Running this locally (as of Day 13)

Everything below was actually run and verified while writing this doc
(processes started, ports checked, DB round-tripped, CORS checked) — not
just copied from source comments. All commands assume your shell's cwd is
this directory (`Code/`).

## 1. Prerequisites: `.env`

A `.env` file at `Code/.env` (already present, already git-ignored — see
`.gitignore`). Required vars, and where each is used:

| Var | Required for | Notes |
|---|---|---|
| `DATABASE_URL` | Everything — both backends import `pipeline/graph.py`, which connects at **import time** (see §2) | Postgres connection string (currently the Supabase pooler URL) |
| `RAZORPAY_KEY_ID` | Any real checkout (both front doors call `payments/razorpay_gateway.py`) | Test-mode key |
| `RAZORPAY_KEY_SECRET` | Same as above | Test-mode secret |
| `GROQ_API_KEY` | Any pipeline run — `intent`/`recommend` nodes call Groq | Servers boot fine without it; a chat message or `checkout()` call fails at the `intent` node without it |
| `RAZORPAY_WEBHOOK_SECRET` | Only `server/app.py`'s real `/webhooks/razorpay` endpoint | Not needed for the normal chat-UI flow, which uses the Day 9 `checkout_outcome` same-origin relay instead — only needed if you're testing the real webhook path locally (e.g. via a tunnel) |
| `ALLOWED_ORIGINS` | CORS on both backends | **Optional for local dev.** Added Day 13 so the deployed frontend's origin isn't hardcoded; both `server/app.py` and `mcp_server/server.py` default to `http://localhost:5173,http://127.0.0.1:5173` when unset — which is exactly Vite's default dev port, so you don't need to set this locally at all. Only set it if you're running the frontend on a different port/host. |
| `MERCHANT_SESSION_SECRET` | The merchant dashboard's login (`mcp_server/merchant_auth.py`) | Required, no fallback — signs/verifies the merchant session JWT. `/merchant/login` raises `RuntimeError` if unset. |

## 2. One-time DB setup — mostly already done, and mostly automatic

Checked against the live `DATABASE_URL` right now: **50 products across 2
merchants (all embedded), 2 agents, and all 4 checkpointer tables already
exist.** You do not need to run any of the commands in this section to get
started.

For reference / if you ever point this at a fresh empty database:

```bash
python -m db.migrate          # applies db/migrations/*.sql, tracked in schema_migrations — safe to re-run
python -m db.seed             # (re)inserts the product catalog, merchant_policy, and the 2 demo agents
python -m db.embed_products   # embeds every product's semantic_description — safe to re-run
```

**The LangGraph checkpointer needs none of the above.** As of Day 13,
`pipeline/graph.py` uses `PostgresSaver`, and its module-level code calls
`_checkpointer.setup()` **automatically, every time `pipeline.graph` is
imported** — which happens the instant either backend process starts. It's
idempotent (no-ops if the `checkpoints`/`checkpoint_blobs`/
`checkpoint_writes`/`checkpoint_migrations` tables already exist), so
there's genuinely no manual step here — just start the servers.

One consequence worth knowing: because this happens at import time, both
backend processes make a real blocking connection to `DATABASE_URL` before
they can even start serving — if `DATABASE_URL` is wrong or unreachable,
the process fails immediately on startup (see §5 for what that looks like).

## 3. Start everything, in order

Three processes, three terminals (or run them backgrounded and tail logs —
your call). Order doesn't strictly matter between the two backends, but
start the frontend last since it immediately tries to connect to both.

**Terminal 1 — Front Door 1: chat + webhook receiver + `/ws/chat`**
```bash
cd Code
source venv/bin/activate        # skip if already installed: python -m venv venv && pip install -r requirements.txt
uvicorn server.app:app --reload --port 8000
```
Runs on **http://localhost:8000**.

**Terminal 2 — Front Door 2: MCP tools + merchant dashboard API/WS**
```bash
cd Code
source venv/bin/activate
python -m mcp_server.server --http --port 8765
```
Runs on **http://localhost:8765** (MCP endpoint at `/mcp`, merchant
dashboard endpoints under `/merchant`). This one validates the seeded
`ai_agent` row at startup (`_validate_ai_agent_id`) and will refuse to
start with a clear error if the DB isn't seeded the way `db/seed.py`
expects — another reason §2 is confirmed already done.

**Terminal 3 — frontend (both pages)**
```bash
cd Code/frontend
npm install                     # skip if node_modules/ already present
npm run dev -- --port 5173
```
Runs on **http://localhost:5173**.

(Versions this was verified against: Python 3.11.15, Node v22.22.3, npm
10.9.8 — shouldn't matter much, but noted in case something behaves
differently on a very different version.)

## 4. URLs to open

| Page | URL |
|---|---|
| Shopper chat (Front Door 1) | http://localhost:5173/ |
| Merchant dashboard (pending approvals, full audit trail, agent overview) | http://localhost:5173/merchant |

## 5. Smoke-check each piece

**DB connection is live** — either backend booting at all proves this
(§2: a bad `DATABASE_URL` crashes the process on import, before it ever
gets to "Uvicorn running on..."). To check explicitly:
```bash
curl -s http://localhost:8765/merchant/agents
```
Should return a JSON array with the 2 seeded agents (`Demo Shopper (human)`,
`Shopping Assistant Agent`) — this hits the DB directly, so JSON back means
the DB round-trip works. (Verified just now — returns both agents with
current `spent_so_far` values.)

**Chat backend (server/app.py) is live:**
```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/
```
`404` is the expected answer (there's no route at `/` — only `/ws/chat` and
`/webhooks/razorpay` exist) — a `404` means Uvicorn is up and routing, a
connection error means it isn't running. For a real check that also
touches the DB, open the DevTools console at http://localhost:5173/ and
confirm you see `{type: "connected", thread_id: "..."}` on load (verified
directly via a raw WebSocket client just now — works).

**MCP server responds:**
```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8765/mcp
```
A bare `GET` isn't a valid MCP request, so `400`/`406` is expected and
still proves the endpoint is mounted and answering — a connection error
means the process isn't running. To confirm the actual tool surface,
connect a real MCP client (streamable-http, `http://localhost:8765/mcp`)
and call `list_tools()` — should return exactly `search_catalog`,
`get_product`, `checkout`, `check_order_status` (`merchant_resolve_pending_approval`
was removed Day 12 — see that session's notes).

**Frontend can reach the backend (CORS, not just "the page loads"):**
```bash
curl -s -i http://localhost:8765/merchant/agents -H "Origin: http://localhost:5173" | grep -i access-control-allow-origin
```
Should echo back `access-control-allow-origin: http://localhost:5173`
(verified just now) — if this header is missing, the browser will block
the merchant dashboard's `fetch`/WebSocket calls even though `curl` itself
succeeds. Simplest real check: open http://localhost:5173/merchant and
confirm the Pending Approvals panel shows "live" (not "disconnected") and
the Agents tab populates — both require a successful cross-origin call to
port 8765.

## Nothing broken

Every command and check above was run against the current code + current
`.env` + current `DATABASE_URL` while writing this file, in order, from a
clean process start. All three processes came up clean, the DB round-trip
worked, CORS was correctly configured for the default dev origin, and the
chat WebSocket handshake succeeded.
