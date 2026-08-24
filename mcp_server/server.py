"""
Day 10-11 Front Door 2: MCP tools for an external AI buyer agent, calling
INTO the exact same pipeline functions Front Door 1 (server/app.py's
WebSocket handler) already uses. This is the concrete proof of the
unified-backend claim in project-brief.md: every tool handler below is a
thin wrapper around either catalog/retrieval.py's search_products (pure
catalog reads need no pipeline involvement) or pipeline/graph.py's
run_pipeline/resume_pipeline (checkout must go through
intent -> retrieve -> recommend -> policy_check -> authorization ->
razorpay -> verification exactly like a human's chat message does — there
is no special-cased "AI path" that skips a step).

Scope note, same shortcut as HUMAN_AGENT_ID in server/app.py: there is no
AI-agent auth/identity system this session, so every MCP checkout is
hardcoded to AI_AGENT_ID below (the seeded ai_agent "Shopping Assistant
Agent" row from db/seed.py) — stated explicitly, not silently built.
AI_AGENT_ID's value is an assumption (db/seed.py inserts "Demo Shopper
(human)" — id 5, per HUMAN_AGENT_ID — immediately followed by "Shopping
Assistant Agent" in the same run, so its id is assumed to be 6); main()
validates this at startup against the real agent row and fails loudly with
a fix-it message if it's wrong rather than silently mis-attributing
purchases, since getting this wrong would mean every MCP order is charged
to/authorized as the wrong agent.

No real payment UI exists for a server-to-server AI buyer this session
(there's no browser, so no Checkout.js), so checkout() and
resolve_pending_approval() close the loop themselves: once a real
test-mode Razorpay order exists and the pipeline pauses at verification for
a payment webhook, _synthetic_captured_webhook() below builds a
payment.captured envelope (the same shape payments/razorpay_gateway.py's
parse_payment_webhook already expects from a real webhook) and resumes
immediately. This is the AI-buyer analog of server/app.py's
_build_webhook_from_checkout_outcome, which does the same thing from real
Checkout.js client-callback data — an MCP buyer has no client callback to
wrap, so this synthesizes the capture instead. Every other pipeline node
(intent/retrieve/recommend/policy_check/authorization/razorpay) still runs
for real, unmodified.

Process-boundary note for resolve_pending_approval: pipeline/graph.py's
GRAPH is compiled with an InMemorySaver checkpointer, valid only inside the
single process that ran the original run_pipeline() call for a given
thread_id (see that module's comment on GRAPH). Because checkout() runs
inside this MCP server process, resuming a paused thread must also happen
inside this same process. Day 10-11 solved that by exposing merchant
approval AS an MCP tool (merchant_resolve_pending_approval), reached by a
separate approve_order.py CLI script, and relied on convention (a name and
docstring that said MERCHANT-ONLY) to keep an AI buyer from ever calling
it. Day 12 removes that tool entirely: resolve_pending_approval below is a
plain function, never decorated with @mcp.tool(), so it never appears in
this server's MCP tools/list and no MCP client — AI buyer or otherwise —
can invoke it under any circumstance. The merchant dashboard (see
merchant_router below) still needs it to run in-process for the same
InMemorySaver reason above, so build_http_app() mounts the dashboard's
FastAPI routes into this exact process/ASGI app, alongside the MCP
transport, on the same port. The dashboard calls resolve_pending_approval()
as a normal Python function call from its own HTTP handler — never over
MCP — the same pattern server/app.py's WebSocket handler already uses to
call resume_pipeline() directly. This makes the self-approval exclusion
structural (no tool exists to call) instead of conventional (a tool exists
but you're asked not to call it).

Run:
    python -m mcp_server.server            # stdio transport (day-1 spike's
                                            # verified pattern; one server
                                            # subprocess per client)
    python -m mcp_server.server --http     # streamable-http + the merchant
                                            # dashboard API/WS, both served
                                            # from one process on
                                            # 127.0.0.1:8765 (MCP at /mcp,
                                            # dashboard at /merchant/*) —
                                            # required so both share this
                                            # process's in-memory pipeline
                                            # state.
"""

import argparse
import asyncio
import os
import time
import uuid
from decimal import Decimal
from typing import Any, Optional

import uvicorn
from fastapi import APIRouter, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

from catalog.retrieval import get_product_detail, search_products
from db.agents import get_agent, list_agents
from db.approvals import list_pending_approval_requests
from db.audit import get_full_audit_trail
from db.orders import get_order
from db.policy import get_merchant_policy
from pipeline.graph import resume_pipeline, run_pipeline

AI_AGENT_ID = 6  # seeded ai_agent "Shopping Assistant Agent"; hardcoded, no
# auth this session — see module docstring. Verify with:
#   SELECT id FROM agent WHERE name = 'Shopping Assistant Agent';

mcp = FastMCP("AgenticCommerceAIBuyer", port=8765)

# order_id -> thread_id for pipeline runs started by checkout() in THIS
# process, so resolve_pending_approval() can find which paused LangGraph
# thread to resume. Same in-memory-map pattern as server/app.py's
# _razorpay_order_to_thread — necessarily process-local plumbing for this
# front door, not pipeline logic.
_order_to_thread: dict[int, str] = {}

# thread_id -> the interrupt "type" currently pending for that thread. Same
# duplicate-resume guard as server/app.py's _pending_interrupt: a stale or
# repeated resume call must not be replayed against whatever the pipeline
# has since moved on to.
_pending_interrupt: dict[str, str] = {}

# Live WebSocket connections from the merchant dashboard (Day 12) — same
# "process-local set of sockets, best-effort push" shape as server/app.py's
# _connections, just many-to-one (every open dashboard tab) instead of
# one-per-thread_id.
_merchant_connections: set[WebSocket] = set()


async def _broadcast_merchant(event: dict) -> None:
    """
    Best-effort push to every open merchant-dashboard socket. Events carry
    no payload beyond what changed (e.g. {"type": "pending_approval_created",
    "order_id": ...}) — the dashboard reacts by re-fetching the relevant
    REST snapshot, so this function doesn't need to duplicate the
    list_pending_approval_requests() join shape.
    """
    for ws in list(_merchant_connections):
        try:
            await ws.send_json(event)
        except Exception:
            _merchant_connections.discard(ws)


def _synthetic_captured_webhook(razorpay_order_id: str, amount: Optional[float]) -> dict:
    now = int(time.time())
    amount_paise = int(round(float(amount or 0) * 100))
    entity = {
        "id": f"pay_mcp_{uuid.uuid4().hex[:14]}",
        "entity": "payment",
        "amount": amount_paise,
        "currency": "INR",
        "status": "captured",
        "order_id": razorpay_order_id,
        "method": "card",
        "captured": True,
        "email": None,
        "contact": None,
        "error_code": None,
        "error_description": None,
        "created_at": now,
    }
    return {
        "entity": "event",
        "event": "payment.captured",
        "contains": ["payment"],
        "payload": {"payment": {"entity": entity}},
        "created_at": now,
    }


async def _advance_and_summarize(thread_id: str, order_id: Optional[int], result: dict) -> dict:
    """
    Shared by checkout() and resolve_pending_approval(): turns
    whatever run_pipeline()/resume_pipeline() just returned into one of the
    three outcomes an external agent needs to tell apart — completed,
    pending_approval (with the approval_request id), or failed (with a
    reason) — auto-advancing through the webhook pause (see module
    docstring) rather than leaving that as a fourth, ambiguous state.
    """
    interrupts = result.get("__interrupt__")
    if interrupts:
        value = interrupts[0].value
        itype = value.get("type") if isinstance(value, dict) else None
        _pending_interrupt[thread_id] = itype
        if order_id is not None:
            _order_to_thread[order_id] = thread_id

        if itype == "merchant_approval_required":
            return {
                "outcome": "pending_approval",
                "order_id": order_id,
                "approval_request_id": value["approval_request_id"],
                "amount": value["amount"],
                "threshold": value["threshold"],
                "message": value["message"],
            }

        if itype == "webhook_required":
            payload = _synthetic_captured_webhook(value["razorpay_order_id"], result.get("amount"))
            next_result = await asyncio.to_thread(resume_pipeline, thread_id, payload)
            return await _advance_and_summarize(thread_id, order_id, next_result)

        # human_confirm_required should never occur for an ai_agent purchase
        # (see authorization_node) — surfaced as a failure rather than
        # silently mishandled.
        return {
            "outcome": "failed",
            "order_id": order_id,
            "reason": f"unexpected interrupt type {itype!r} for an ai_agent purchase",
        }

    _pending_interrupt.pop(thread_id, None)

    if result.get("final_status") == "completed":
        return {
            "outcome": "completed",
            "order_id": order_id,
            "amount": result.get("amount"),
            "razorpay_order_id": result.get("razorpay_order_id"),
        }
    if result.get("final_status") == "failed":
        return {
            "outcome": "failed",
            "order_id": order_id,
            "reason": "Razorpay payment failed (payment.failed webhook)",
        }
    if result.get("policy_passed") is False:
        reasons = "; ".join(c["reason"] for c in result.get("policy_checks", []) if not c["passed"])
        return {"outcome": "failed", "order_id": order_id, "reason": reasons or "policy check failed"}
    if result.get("authorized") is False:
        return {
            "outcome": "failed",
            "order_id": order_id,
            "reason": result.get("authorization_reason", "authorization declined"),
        }
    if order_id is None:
        return {
            "outcome": "failed",
            "order_id": None,
            "reason": "no matching product found, or the message wasn't recognized as a checkout intent",
        }
    return {"outcome": "failed", "order_id": order_id, "reason": "pipeline ended without a recognized terminal state"}


@mcp.tool()
async def search_catalog(
    query: str,
    category: Optional[str] = None,
    max_price: Optional[float] = None,
    in_stock_only: bool = True,
) -> list[dict]:
    """
    Search the product catalog. Thin wrapper around catalog/retrieval.py's
    search_products (hybrid keyword + semantic + structured-filter search) —
    no pipeline involvement, pure search needs none.
    """
    filters: dict[str, Any] = {"in_stock_only": in_stock_only}
    if category:
        filters["category"] = category
    if max_price is not None:
        filters["max_price"] = Decimal(str(max_price))

    products = await asyncio.to_thread(search_products, query, filters, 10)
    return [
        {
            "id": p.id,
            "name": p.name,
            "category": p.category,
            "price": float(p.price),
            "stock": p.stock,
            "semantic_description": p.semantic_description,
            "structured_attributes": p.structured_attributes,
            "score": round(p.score, 4),
        }
        for p in products
    ]


@mcp.tool()
async def get_product(product_id: int) -> dict:
    """Full product detail, including cross-sell/co-purchase and substitute info."""
    detail = await asyncio.to_thread(get_product_detail, product_id)
    if detail is None:
        raise ValueError(f"product_id {product_id} not found")
    return detail


@mcp.tool()
async def checkout(product_id: int, quantity: int = 1) -> dict:
    """
    Buy `quantity` of `product_id` as the seeded AI buyer agent. Runs the
    exact same checkout pipeline Front Door 1 uses (run_pipeline over
    intent -> retrieve -> recommend -> policy_check -> authorization ->
    razorpay -> verification). Returns one of three outcomes: "completed",
    "pending_approval" (with approval_request_id — poll check_order_status
    until a merchant resolves it), or "failed" (with a reason).
    """
    product = await asyncio.to_thread(get_product_detail, product_id)
    if product is None:
        return {"outcome": "failed", "order_id": None, "reason": f"product_id {product_id} not found in catalog"}

    # Deliberately close to the phrasing already proven (pipeline/demo_run.py)
    # to reliably rank #1 in retrieve_node's search — extra tokens like
    # "product id 20" would only dilute the keyword/semantic match, and
    # quantity is threaded through initial_state directly rather than
    # parsed from this message (see PipelineState.quantity in
    # pipeline/graph.py), so it doesn't need to appear here for anything
    # other than realism.
    user_message = (
        f"Buy the {product['name']}." if quantity == 1 else f"Buy {quantity} of the {product['name']}."
    )
    thread_id = str(uuid.uuid4())
    initial_state = {
        "user_message": user_message,
        "agent_id": AI_AGENT_ID,
        "payment_method": "card",
        "discount_pct": 0,
        "quantity": quantity,
    }
    result = await asyncio.to_thread(run_pipeline, initial_state, thread_id)
    order_id = result.get("order_id")
    summary = await _advance_and_summarize(thread_id, order_id, result)
    if summary.get("outcome") == "pending_approval":
        await _broadcast_merchant({"type": "pending_approval_created", "order_id": order_id})
    return summary


@mcp.tool()
async def check_order_status(order_id: int) -> dict:
    """Current status of a previously placed order — for polling after pending_approval."""
    order = await asyncio.to_thread(get_order, order_id)
    return {
        "order_id": order["id"],
        "status": order["status"],
        "amount": float(order["amount"]),
        "razorpay_order_id": order["razorpay_order_id"],
        "razorpay_payment_id": order["razorpay_payment_id"],
    }


async def resolve_pending_approval(
    order_id: int, approved: bool, resolved_by: str = "merchant_dashboard"
) -> dict:
    """
    MERCHANT-ONLY: resolves a pending merchant-approval request for
    order_id by resuming the paused pipeline with the given decision.

    Deliberately a plain function, not an @mcp.tool() — see the module
    docstring's "Process-boundary note". It's called in-process, only from
    merchant_router's /resolve-approval handler below (the merchant
    dashboard's backend), which runs in this same process/ASGI app so it
    can see _order_to_thread/_pending_interrupt and share GRAPH's
    InMemorySaver. No MCP tool wraps this, so no MCP client — including an
    AI buyer — can reach it.
    """
    thread_id = _order_to_thread.get(order_id)
    if thread_id is None:
        return {
            "outcome": "error",
            "reason": f"no in-progress MCP checkout found for order_id={order_id} in this server process",
        }
    if _pending_interrupt.get(thread_id) != "merchant_approval_required":
        return {
            "outcome": "error",
            "reason": (
                f"order_id={order_id} is not currently awaiting merchant approval "
                f"(pending={_pending_interrupt.get(thread_id)!r})"
            ),
        }

    resume_value = {"approved": approved, "resolved_by": resolved_by}
    result = await asyncio.to_thread(resume_pipeline, thread_id, resume_value)
    return await _advance_and_summarize(thread_id, order_id, result)


# ---------------------------------------------------------------------------
# Merchant dashboard HTTP/WS surface (Day 12) — mounted into this same
# process/ASGI app by build_http_app() below, alongside the MCP transport,
# so its handlers can call resolve_pending_approval() and the other
# in-process state above directly. Plain FastAPI routes, not MCP tools: an
# MCP client only ever sees whatever main.list_tools() exposes, and none of
# these are registered there.
# ---------------------------------------------------------------------------

merchant_router = APIRouter()


class ResolveApprovalBody(BaseModel):
    order_id: int
    approved: bool
    resolved_by: str = "merchant_dashboard"


@merchant_router.get("/pending-approvals")
async def get_pending_approvals() -> list[dict]:
    approvals = await asyncio.to_thread(list_pending_approval_requests)
    policy = await asyncio.to_thread(get_merchant_policy)
    threshold = float(policy["approval_required_above"])
    for a in approvals:
        a["threshold"] = threshold
    return approvals


@merchant_router.post("/resolve-approval")
async def post_resolve_approval(body: ResolveApprovalBody) -> dict:
    result = await resolve_pending_approval(body.order_id, body.approved, body.resolved_by)
    if result.get("outcome") != "error":
        await _broadcast_merchant({"type": "approval_resolved", "order_id": body.order_id})
    return result


@merchant_router.get("/audit-trail")
async def get_audit_trail_all() -> list[dict]:
    return await asyncio.to_thread(get_full_audit_trail)


@merchant_router.get("/agents")
async def get_agents() -> list[dict]:
    return await asyncio.to_thread(list_agents)


@merchant_router.websocket("/ws")
async def merchant_ws(websocket: WebSocket) -> None:
    """
    Live-update channel for the pending-approvals panel: pushes a bare
    {"type": ...} event whenever a new approval is created (checkout()
    above) or one is resolved (post_resolve_approval above); the dashboard
    reacts by re-fetching GET /merchant/pending-approvals. Same "socket
    just for push notifications" pattern as server/app.py's audit-streaming
    websocket, minus the request/response chat traffic that socket also
    carries — this one never receives anything meaningful from the client.
    """
    await websocket.accept()
    _merchant_connections.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _merchant_connections.discard(websocket)


def build_http_app() -> FastAPI:
    """
    Combines FastMCP's streamable-http ASGI app (serving /mcp) with
    merchant_router (serving /merchant/*) into one FastAPI app on one port,
    so both run in the same process — required for resolve_pending_approval
    to see state written by checkout() (see module docstring).
    """
    # Day 13: same reasoning as server/app.py's ALLOWED_ORIGINS — the
    # deployed frontend's origin isn't known at code-writing time, so it's
    # read from an env var (set in Render's dashboard) rather than
    # hardcoded, defaulting to the two local dev origins.
    allowed_origins = [
        o.strip()
        for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
        if o.strip()
    ]

    mcp_asgi_app = mcp.streamable_http_app()
    app = FastAPI(lifespan=mcp_asgi_app.router.lifespan_context)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(merchant_router, prefix="/merchant")
    app.mount("/", mcp_asgi_app)
    return app


def _validate_ai_agent_id() -> None:
    agent = get_agent(AI_AGENT_ID)
    if agent["type"] != "ai_agent":
        raise RuntimeError(
            f"AI_AGENT_ID={AI_AGENT_ID} resolved to an agent of type {agent['type']!r} "
            f"({agent['name']!r}), not 'ai_agent'. Update AI_AGENT_ID in "
            "mcp_server/server.py — find the right value with: "
            "SELECT id FROM agent WHERE name = 'Shopping Assistant Agent';"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Front Door 2 MCP server")
    parser.add_argument(
        "--http",
        action="store_true",
        help="serve over streamable-http instead of stdio, plus the merchant "
        "dashboard API/WS on the same port (needed so a separate MCP client "
        "process, and the merchant dashboard, can both reach this same "
        "running server)",
    )
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    _validate_ai_agent_id()

    if args.http:
        mcp.settings.port = args.port
        # 0.0.0.0, not FastMCP's 127.0.0.1 default: a container (Docker
        # locally, Render in prod) only forwards external traffic to a port
        # bound on all interfaces, not just loopback.
        print(f"MCP server ready on http://0.0.0.0:{args.port}/mcp", flush=True)
        print(f"Merchant dashboard API ready on http://0.0.0.0:{args.port}/merchant", flush=True)
        uvicorn.run(build_http_app(), host="0.0.0.0", port=args.port, log_level="info")  # noqa: S104
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
