"""
FastAPI backend for Day 9 Front Door 1: a chat UI wired to the real
checkout pipeline (pipeline/graph.py), plus a real Razorpay webhook
receiver with signature verification.

Scope note: there is no login/auth system this session. Every chat
connection is hardcoded to HUMAN_AGENT_ID (the seeded human_session agent
from db/seed.py, "Demo Shopper (human)") — a deliberate shortcut, not
something a real multi-user app would do; stated explicitly rather than
silently building auth, per this session's scope.

Known limitation: _connections and _razorpay_order_to_thread below are
plain in-process dicts, not persisted anywhere. As of Day 13, this is no
longer the durability gap it used to be — pipeline/graph.py's GRAPH itself
is now PostgresSaver-backed (see that module's comment on GRAPH), so a
paused order's actual state survives a restart. What's still process-local
here is narrower: _connections (a live browser WebSocket obviously can't
survive a process restart regardless of checkpointer — the frontend just
reconnects and gets a fresh thread_id) and _razorpay_order_to_thread (if
the process restarts between start_checkout and Razorpay's real webhook
arriving, that webhook's POST would 404 with "no in-progress pipeline run
found" until a new checkout re-populates the map for that razorpay_order_id
— the order itself isn't lost, just that one webhook's routing). Not fixed
this session — Part 1's scope was the checkpointer itself.

Run: uvicorn server.app:app --reload --port 8000
"""

import functools
import os
import time
import uuid
from typing import Optional

import anyio
import razorpay
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from catalog.retrieval import get_product_detail, search_products
from pipeline.graph import resume_pipeline, run_pipeline

load_dotenv()

HUMAN_AGENT_ID = 5  # seeded human_session agent; hardcoded, no auth this session

MAX_QUANTITY = 100  # Day 18 security hardening — same rationale/limit as
# mcp_server/server.py's checkout() tool: quantity <=0 previously reached
# the orders_amount_check DB constraint unvalidated instead of a clean
# rejection, and no upper bound existed at all.


def _invalid_quantity(quantity) -> Optional[str]:
    """None if `quantity` is a valid positive int <= MAX_QUANTITY, else a
    ready-to-send rejection message. Shared by both checkout entry points
    below (single-product and cart) since both accept quantity straight
    from the client's raw WebSocket JSON, unlike a pydantic-validated
    HTTP body."""
    if not isinstance(quantity, int) or isinstance(quantity, bool) or not (0 < quantity <= MAX_QUANTITY):
        return f"quantity must be a positive integer, at most {MAX_QUANTITY} (got {quantity!r})"
    return None

RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET")
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET")

# Day 13: the deployed frontend's origin (a vercel.app URL) isn't known at
# code-writing time and differs per deploy, so it's read from an env var
# (set in Render's dashboard) rather than hardcoded — same two local dev
# origins remain the default so nothing changes for local development.
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    if o.strip()
]

app = FastAPI(title="Agentic Commerce Chat Backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# thread_id -> live WebSocket connection (one active chat per thread_id)
_connections: dict[str, WebSocket] = {}
# razorpay_order_id -> thread_id, so the webhook handler (a separate HTTP
# request with no idea what websocket it's related to) can find which
# pipeline run to resume.
_razorpay_order_to_thread: dict[str, str] = {}
# thread_id -> the interrupt "type" currently pending for that thread, e.g.
# "human_confirm_required" or "webhook_required". Command(resume=...) has no
# concept of "which interrupt call site was this meant for" — it just
# resumes whatever task is currently paused on that thread_id. A stale or
# duplicate message (e.g. a double-clicked Confirm button firing twice)
# would otherwise get resumed against whatever the pipeline has since moved
# on to, silently corrupting an unrelated step. Every _handle_* resumer
# below MUST check this before calling resume_pipeline.
_pending_interrupt: dict[str, str] = {}


async def _safe_send(websocket: WebSocket, payload: dict, thread_id: Optional[str] = None) -> bool:
    """
    Every websocket.send_json call in this module goes through here instead
    of calling it directly. The client can disappear at any point while a
    pipeline run is still in flight in its worker thread — a closed tab, or
    (the more likely real-world case, per a live Render incident) an
    idle-connection timeout somewhere in the network path firing during a
    slow/cold-started run that produces no traffic for a while. The first
    send attempted afterward raises RuntimeError("Cannot call \"send\" once
    a close message has been sent.") from Starlette, or WebSocketDisconnect
    — neither is a bug in the result being sent, it's just stale. Letting
    that propagate unhandled crashes the whole ASGI connection (and, worse,
    does it silently from the frontend's point of view: no message ever
    arrives, so `busy` never clears and the UI hangs forever). Logged, not
    swallowed silently, so a real pattern of this is visible in server logs.
    """
    try:
        await websocket.send_json(payload)
        return True
    except (RuntimeError, WebSocketDisconnect) as exc:
        print(
            f"[ws {thread_id}] send skipped, socket already closed "
            f"(type={payload.get('type')!r}): {exc!r}",
            flush=True,
        )
        return False


def _make_on_audit(thread_id: str):
    """
    Callback passed to run_pipeline/resume_pipeline as `on_audit`. Runs
    INSIDE the worker thread executing the pipeline (see pipeline/graph.py's
    _audit_sink) — anyio.from_thread.run schedules the actual send back on
    the event loop that owns this websocket and blocks the worker thread
    until it's delivered, so each audit_log_entry reaches the client the
    instant it's written, not after the pipeline call returns.
    """

    def _on_audit(entry: dict) -> None:
        ws = _connections.get(thread_id)
        if ws is None:
            return
        try:
            anyio.from_thread.run(_safe_send, ws, {"type": "audit_entry", **entry}, thread_id)
        except Exception as exc:
            # _safe_send itself never raises for a dead socket (see above) —
            # this is a backstop for anything else going wrong scheduling
            # onto the event loop, kept broad because a live pipeline run
            # must never be broken by a best-effort UI push failing.
            print(f"[ws {thread_id}] audit push failed unexpectedly: {exc!r}", flush=True)

    return _on_audit


@app.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket) -> None:
    await websocket.accept()
    thread_id = str(uuid.uuid4())
    _connections[thread_id] = websocket
    await _safe_send(websocket, {"type": "connected", "thread_id": thread_id}, thread_id)

    try:
        while True:
            try:
                data = await websocket.receive_json()
            except RuntimeError:
                # Starlette raises this (not WebSocketDisconnect) specifically
                # when the socket was already torn down earlier in this
                # connection's lifetime — e.g. a slow pipeline run's _safe_send
                # calls above already discovered the client was gone and
                # logged it there; this is just the next receive() on the same
                # dead connection surfacing the same fact. Narrowly scoped to
                # this one call (not the whole loop body) so an unrelated
                # RuntimeError from inside a handler — a real bug — still
                # propagates and gets logged loudly instead of being mistaken
                # for a closed socket.
                break
            msg_type = data.get("type")

            if msg_type == "message":
                await _handle_message(websocket, thread_id, data.get("text", ""))
            elif msg_type == "checkout_product":
                await _handle_checkout_product(
                    websocket, thread_id, data.get("product_id"), data.get("quantity", 1)
                )
            elif msg_type == "checkout_cart":
                await _handle_checkout_cart(websocket, thread_id, data.get("items") or [])
            elif msg_type == "browse":
                await _handle_browse(websocket, data.get("filters") or {})
            elif msg_type == "confirm":
                await _handle_confirm(websocket, thread_id, data.get("decision", "confirm"))
            elif msg_type == "checkout_outcome":
                await _handle_checkout_outcome(websocket, thread_id, data)
            else:
                await _safe_send(
                    websocket, {"type": "error", "message": f"unknown message type: {msg_type!r}"}, thread_id
                )
    except WebSocketDisconnect:
        pass
    finally:
        _connections.pop(thread_id, None)
        _pending_interrupt.pop(thread_id, None)


def _serialize_products(results) -> list[dict]:
    # Mirrors pipeline/graph.py's _retrieve_impl serialization exactly, so
    # the frontend's ProductCard/search_results handling works unchanged
    # regardless of which path produced the results.
    return [
        {
            "id": p.id,
            "name": p.name,
            "category": p.category,
            "price": float(p.price),
            "stock": p.stock,
            "score": p.score,
            "image_url": p.image_url,
            "merchant_name": p.merchant_name,
        }
        for p in results
    ]


@app.get("/api/products/{product_id}")
async def get_product(product_id: int) -> dict:
    # Backs the product detail modal. Both pieces of cross-catalog data it
    # needs are reused verbatim from existing lookups, no new retrieval
    # logic: "similar items" is the same search_products() the retrieve
    # node calls (filtered to this product's category, self excluded), and
    # "you might also like" is get_product_detail's cross_sell list, which
    # already reads co_purchase_stat the same way _recommend_impl does.
    detail = await anyio.to_thread.run_sync(get_product_detail, product_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="product not found")

    similar_raw = await anyio.to_thread.run_sync(
        functools.partial(
            search_products, detail["semantic_description"], {"category": detail["category"]}, 6
        )
    )
    similar_items = [p for p in _serialize_products(similar_raw) if p["id"] != product_id][:5]

    return {**detail, "similar_items": similar_items, "recommendations": detail["cross_sell"]}


async def _handle_browse(websocket: WebSocket, filters: dict) -> None:
    # A pure catalog browse driven by a real structured filter (e.g. the
    # gender toggle) rather than free text — no intent classification
    # needed since the filter is already explicit, so this bypasses
    # run_pipeline entirely (no order, no checkout, nothing to authorize).
    # Deliberately doesn't log to the audit trail: it's a read with no
    # order attached, same as how browsing-only pipeline runs already
    # don't appear in the merchant dashboard's per-order audit view.
    results = await anyio.to_thread.run_sync(
        functools.partial(search_products, "running gear and apparel", filters, 10)
    )
    await _safe_send(websocket, {"type": "search_results", "results": _serialize_products(results)})
    await _safe_send(websocket, {"type": "turn_complete"})


async def _handle_message(websocket: WebSocket, thread_id: str, text: str) -> None:
    initial_state = {
        "user_message": text,
        "agent_id": HUMAN_AGENT_ID,
        "payment_method": "card",
        "discount_pct": 0,
    }
    on_audit = _make_on_audit(thread_id)
    result = await anyio.to_thread.run_sync(
        functools.partial(run_pipeline, initial_state, thread_id, on_audit)
    )
    await _emit_result(websocket, thread_id, result)


async def _handle_checkout_product(
    websocket: WebSocket, thread_id: str, product_id: Optional[int], quantity: int
) -> None:
    # Cart checkout: the product_id is already known (picked from search
    # results earlier, or from the product modal), so this skips straight
    # to pipeline/graph.py's product_id-aware intent/retrieve branch
    # instead of re-running text search via _handle_message — same
    # downstream pipeline (recommend/policy_check/authorization/razorpay/
    # verification), same _emit_result handling either way.
    if product_id is None:
        await _safe_send(websocket, {"type": "error", "message": "checkout_product requires a product_id"}, thread_id)
        await _safe_send(websocket, {"type": "turn_complete"}, thread_id)
        return

    invalid = _invalid_quantity(quantity)
    if invalid is not None:
        await _safe_send(websocket, {"type": "error", "message": invalid}, thread_id)
        await _safe_send(websocket, {"type": "turn_complete"}, thread_id)
        return

    initial_state = {
        "product_id": product_id,
        "quantity": quantity,
        "agent_id": HUMAN_AGENT_ID,
        "payment_method": "card",
        "discount_pct": 0,
    }
    on_audit = _make_on_audit(thread_id)
    result = await anyio.to_thread.run_sync(
        functools.partial(run_pipeline, initial_state, thread_id, on_audit)
    )
    await _emit_result(websocket, thread_id, result)


async def _handle_checkout_cart(websocket: WebSocket, thread_id: str, items: list[dict]) -> None:
    # Combined cart checkout: every {product_id, quantity} pair bills as
    # ONE order — pipeline/graph.py's cart_items-aware intent/retrieve
    # branch creates a single order covering all of them, so
    # policy_check/authorization/razorpay/verification each run exactly
    # once for the whole cart (unmodified — same nodes _handle_message and
    # _handle_checkout_product already go through). Chosen deliberately
    # over per-item checkout: simpler for the shopper (one confirm, one
    # payment), at the cost of losing per-item failure isolation — if this
    # one payment fails, the whole cart fails together.
    if not items:
        await _safe_send(websocket, {"type": "error", "message": "checkout_cart requires at least one item"}, thread_id)
        await _safe_send(websocket, {"type": "turn_complete"}, thread_id)
        return

    for item in items:
        invalid = _invalid_quantity(item.get("quantity", 1))
        if invalid is not None:
            await _safe_send(
                websocket,
                {"type": "error", "message": f"{invalid} (product_id={item.get('product_id')!r})"},
                thread_id,
            )
            await _safe_send(websocket, {"type": "turn_complete"}, thread_id)
            return

    initial_state = {
        "cart_items": items,
        "agent_id": HUMAN_AGENT_ID,
        "payment_method": "card",
        "discount_pct": 0,
    }
    on_audit = _make_on_audit(thread_id)
    result = await anyio.to_thread.run_sync(
        functools.partial(run_pipeline, initial_state, thread_id, on_audit)
    )
    await _emit_result(websocket, thread_id, result)


async def _handle_confirm(websocket: WebSocket, thread_id: str, decision: str) -> None:
    pending = _pending_interrupt.get(thread_id)
    if pending != "human_confirm_required":
        await _safe_send(
            websocket,
            {
                "type": "error",
                "message": (
                    "no confirm is pending for this chat right now "
                    f"(pending={pending!r}) — ignoring, likely a duplicate click"
                ),
            },
            thread_id,
        )
        # Every other path ends with turn_complete via _emit_result — this
        # early return must too, or the frontend's `busy` flag (cleared
        # only on turn_complete) gets stuck forever after a rejected
        # duplicate.
        await _safe_send(websocket, {"type": "turn_complete"}, thread_id)
        return

    on_audit = _make_on_audit(thread_id)
    result = await anyio.to_thread.run_sync(
        functools.partial(resume_pipeline, thread_id, decision, on_audit)
    )
    await _emit_result(websocket, thread_id, result)


def _build_webhook_from_checkout_outcome(data: dict) -> dict:
    """
    No public tunnel exists yet, so Razorpay's real webhook POST can't
    reach us this session (the standalone /webhooks/razorpay endpoint and
    its signature verification are real code, exercised for real once a
    webhook is registered against a deployed URL — see README.md's
    Deployment section). This instead wraps GENUINE data from Checkout.js's
    own client-side `payment.failed` / success handler — real error
    code/description/source/step/reason from Razorpay's servers for an
    actual triggered decline, not synthetic — into the same envelope shape
    parse_payment_webhook() already expects, so it's processed identically
    to a real webhook once one can reach us.
    """
    now = int(time.time())
    status = data["status"]  # "captured" | "failed"
    entity = {
        "id": data["razorpay_payment_id"],
        "entity": "payment",
        "amount": data.get("amount_paise"),
        "currency": "INR",
        "status": status,
        "order_id": data["razorpay_order_id"],
        "method": "card",
        "captured": status == "captured",
        "email": None,
        "contact": None,
        "created_at": now,
    }
    if status == "failed":
        error = data.get("error") or {}
        entity.update(
            {
                "error_code": error.get("code"),
                "error_description": error.get("description"),
                "error_source": error.get("source"),
                "error_step": error.get("step"),
                "error_reason": error.get("reason"),
            }
        )
    else:
        entity.update({"error_code": None, "error_description": None, "fee": None, "tax": None})

    event = "payment.captured" if status == "captured" else "payment.failed"
    return {
        "entity": "event",
        "event": event,
        "contains": ["payment"],
        "payload": {"payment": {"entity": entity}},
        "created_at": now,
    }


async def _handle_checkout_outcome(websocket: WebSocket, thread_id: str, data: dict) -> None:
    pending = _pending_interrupt.get(thread_id)
    if pending != "webhook_required":
        await _safe_send(
            websocket,
            {
                "type": "error",
                "message": (
                    "no checkout is pending for this chat right now "
                    f"(pending={pending!r}) — ignoring, likely a duplicate Checkout.js callback"
                ),
            },
            thread_id,
        )
        await _safe_send(websocket, {"type": "turn_complete"}, thread_id)
        return

    payload = _build_webhook_from_checkout_outcome(data)
    on_audit = _make_on_audit(thread_id)
    result = await anyio.to_thread.run_sync(
        functools.partial(resume_pipeline, thread_id, payload, on_audit)
    )
    await _emit_result(websocket, thread_id, result)


async def _emit_result(websocket: WebSocket, thread_id: str, result: dict) -> None:
    if result.get("search_results") is not None:
        await _safe_send(websocket, {"type": "search_results", "results": result["search_results"]}, thread_id)

    if result.get("recommendation"):
        await _safe_send(websocket, {"type": "recommendation", **result["recommendation"]}, thread_id)

    interrupts = result.get("__interrupt__")
    if interrupts:
        value = interrupts[0].value
        itype = value.get("type") if isinstance(value, dict) else None
        _pending_interrupt[thread_id] = itype

        if itype == "human_confirm_required":
            await _safe_send(
                websocket,
                {
                    "type": "awaiting_confirm",
                    "order_id": value["order_id"],
                    "amount": value["amount"],
                },
                thread_id,
            )
        elif itype == "webhook_required":
            razorpay_order_id = value["razorpay_order_id"]
            _razorpay_order_to_thread[razorpay_order_id] = thread_id
            await _safe_send(
                websocket,
                {
                    "type": "start_checkout",
                    "order_id": value["order_id"],
                    "razorpay_order_id": razorpay_order_id,
                    "amount": result.get("amount"),
                    "key_id": RAZORPAY_KEY_ID,
                },
                thread_id,
            )
        else:
            await _safe_send(websocket, {"type": "paused", "detail": value}, thread_id)
    else:
        # graph reached a terminal state (or ended after retrieve/policy_check)
        # for this thread — nothing is pending anymore.
        _pending_interrupt.pop(thread_id, None)
        if result.get("final_status"):
            await _safe_send(websocket, {"type": "final_status", "status": result["final_status"]}, thread_id)
        elif result.get("authorized") is False:
            await _safe_send(
                websocket, {"type": "order_failed", "reason": result.get("authorization_reason")}, thread_id
            )
        elif result.get("policy_passed") is False:
            # route_after_policy_check sends a failed check straight to END —
            # the graph never reaches authorization, so `authorized` above is
            # never set (state key doesn't exist, not merely False). Without
            # this branch a policy rejection (bad discount, disallowed
            # payment method, or here: insufficient budget) produced no
            # client-visible signal at all — just a re-sent search_results
            # and turn_complete, indistinguishable from nothing having
            # happened, which is exactly what made a checkout intent look
            # like it silently "never reaches confirm."
            failed_reasons = "; ".join(
                c["reason"] for c in result.get("policy_checks", []) if not c.get("passed")
            )
            await _safe_send(
                websocket,
                {"type": "order_failed", "reason": failed_reasons or "Order did not pass policy checks."},
                thread_id,
            )

    await _safe_send(websocket, {"type": "turn_complete"}, thread_id)


@app.post("/webhooks/razorpay")
async def razorpay_webhook(request: Request) -> dict:
    body_bytes = await request.body()
    signature = request.headers.get("x-razorpay-signature")
    if not signature:
        raise HTTPException(status_code=400, detail="missing X-Razorpay-Signature header")
    if not RAZORPAY_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="RAZORPAY_WEBHOOK_SECRET not configured")

    verifier = razorpay.Client(auth=(RAZORPAY_KEY_ID or "", RAZORPAY_KEY_SECRET or ""))
    try:
        verifier.utility.verify_webhook_signature(
            body_bytes.decode("utf-8"), signature, RAZORPAY_WEBHOOK_SECRET
        )
    except razorpay.errors.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="invalid webhook signature")

    payload = await request.json()
    try:
        razorpay_order_id = payload["payload"]["payment"]["entity"]["order_id"]
    except (KeyError, TypeError):
        raise HTTPException(status_code=400, detail="unrecognized webhook payload shape")

    thread_id = _razorpay_order_to_thread.get(razorpay_order_id)
    if thread_id is None:
        raise HTTPException(
            status_code=404,
            detail=f"no in-progress pipeline run found for razorpay_order_id={razorpay_order_id}",
        )

    pending = _pending_interrupt.get(thread_id)
    if pending != "webhook_required":
        # Razorpay is documented to retry webhook delivery; a retry (or a
        # duplicate) arriving after this order already resolved must not be
        # resumed against whatever the thread has moved on to since. Same
        # class of bug as the confirm/checkout_outcome guards above.
        return {"status": "ignored", "reason": f"no webhook pending for this order (pending={pending!r})"}

    on_audit = _make_on_audit(thread_id)
    result = await anyio.to_thread.run_sync(
        functools.partial(resume_pipeline, thread_id, payload, on_audit)
    )

    ws = _connections.get(thread_id)
    if ws is not None:
        await _safe_send(ws, {"type": "final_status", "status": result.get("final_status")}, thread_id)
        await _safe_send(ws, {"type": "turn_complete"}, thread_id)

    return {"status": "ok", "final_status": result.get("final_status")}
