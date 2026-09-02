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
(there's no browser, so no Checkout.js), so checkout() closes the loop
itself: once a real test-mode Razorpay order exists and the pipeline
pauses at verification for a payment webhook, _synthetic_captured_webhook()
below builds a payment.captured envelope (the same shape
payments/razorpay_gateway.py's parse_payment_webhook already expects from a
real webhook) and resumes immediately. This is the AI-buyer analog of
server/app.py's _build_webhook_from_checkout_outcome, which does the same
thing from real Checkout.js client-callback data — an MCP buyer has no
client callback to wrap, so this synthesizes the capture instead. Every
other pipeline node (intent/retrieve/recommend/policy_check/authorization/
razorpay) still runs for real, unmodified. ai_agent purchases over the
merchant's approval_required_above pause instead of auto-authorizing (see
pipeline/graph.py's authorization_node) — checkout() surfaces that pause as
outcome "pending_approval" rather than treating it as a failure. Resolving
it is a plain HTTP call to merchant_router's /resolve-approval below, never
an MCP tool (an AI agent must never be able to approve its own purchase —
decided Day 12); once approved, that same handler auto-advances through the
webhook pause exactly like checkout() does, since there's still no
payment UI on this side to do it separately.

Run:
    python -m mcp_server.server            # stdio transport (day-1 spike's
                                            # verified pattern; one server
                                            # subprocess per client)
    python -m mcp_server.server --http     # streamable-http + the merchant
                                            # dashboard API, both served from
                                            # one process on 127.0.0.1:8765
                                            # (MCP at /mcp, dashboard at
                                            # /merchant/*) — one process, one
                                            # port, for convenience.
"""

import argparse
import asyncio
import os
import time
import uuid
from decimal import Decimal
from typing import Any, Optional

import uvicorn
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

from catalog.retrieval import get_product_detail, search_products
from db.agents import get_agent, list_agents
from db.approvals import get_approval_request, list_pending_approvals_for_merchant
from db.audit import get_full_audit_trail
from db.merchants import get_merchant_by_email
from db.orders import get_order
from db.products import adjust_product_stock, list_products_for_merchant
from mcp_server.merchant_auth import (
    COOKIE_NAME,
    TOKEN_TTL_SECONDS,
    create_session_token,
    get_current_merchant,
    verify_password,
)
from pipeline.graph import resume_pipeline, run_pipeline

AI_AGENT_ID = 6  # seeded ai_agent "Shopping Assistant Agent"; hardcoded, no
# auth this session — see module docstring. Verify with:
#   SELECT id FROM agent WHERE name = 'Shopping Assistant Agent';

mcp = FastMCP("AgenticCommerceAIBuyer", port=8765)


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
    Shared by checkout(): turns whatever run_pipeline()/resume_pipeline()
    just returned into one of the two outcomes an external agent needs to
    tell apart — completed, or failed (with a reason) — auto-advancing
    through the webhook pause (see module docstring) rather than leaving
    that as a third, ambiguous state.
    """
    interrupts = result.get("__interrupt__")
    if interrupts:
        value = interrupts[0].value
        itype = value.get("type") if isinstance(value, dict) else None

        if itype == "webhook_required":
            payload = _synthetic_captured_webhook(value["razorpay_order_id"], result.get("amount"))
            next_result = await asyncio.to_thread(resume_pipeline, thread_id, payload)
            return await _advance_and_summarize(thread_id, order_id, next_result)

        if itype == "merchant_approval_required":
            # ai_agent purchase over the merchant's approval_required_above —
            # not a failure, just not resolved yet. The AI agent polls
            # check_order_status (still "pending_approval") until a merchant
            # resolves approval_request_id via /merchant/resolve-approval.
            return {
                "outcome": "pending_approval",
                "order_id": order_id,
                "approval_request_id": value.get("approval_request_id"),
                "reason": value.get("message"),
            }

        # human_confirm_required should never occur for an ai_agent purchase
        # (see authorization_node) — surfaced as a failure rather than
        # silently mishandled.
        return {
            "outcome": "failed",
            "order_id": order_id,
            "reason": f"unexpected interrupt type {itype!r} for an ai_agent purchase",
        }

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
            "image_url": p.image_url,
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
    razorpay -> verification). Returns one of two outcomes: "completed", or
    "failed" (with a reason).
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
    return await _advance_and_summarize(thread_id, order_id, result)


@mcp.tool()
async def check_order_status(order_id: int) -> dict:
    """Current status of a previously placed order."""
    order = await asyncio.to_thread(get_order, order_id)
    return {
        "order_id": order["id"],
        "status": order["status"],
        "amount": float(order["amount"]),
        "razorpay_order_id": order["razorpay_order_id"],
        "razorpay_payment_id": order["razorpay_payment_id"],
    }


# ---------------------------------------------------------------------------
# Merchant dashboard HTTP surface (Day 12) — mounted into this same
# process/ASGI app by build_http_app() below, alongside the MCP transport.
# Plain FastAPI routes, not MCP tools: an MCP client only ever sees whatever
# main.list_tools() exposes, and none of these are registered there.
# ---------------------------------------------------------------------------

merchant_router = APIRouter()


class LoginBody(BaseModel):
    email: str
    password: str


class StockAdjustBody(BaseModel):
    delta: int


class ResolveApprovalBody(BaseModel):
    approved: bool


@merchant_router.post("/login")
async def post_login(body: LoginBody, response: Response) -> dict:
    merchant = await asyncio.to_thread(get_merchant_by_email, body.email)
    if merchant is None or not verify_password(body.password, merchant["password_hash"]):
        raise HTTPException(status_code=401, detail="invalid email or password")
    token = create_session_token(merchant["id"], merchant["email"])
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,  # local dev over http; flip to True behind https in prod
        max_age=TOKEN_TTL_SECONDS,
    )
    return {"id": merchant["id"], "name": merchant["name"], "email": merchant["email"]}


@merchant_router.post("/logout")
async def post_logout(response: Response) -> dict:
    response.delete_cookie(key=COOKIE_NAME)
    return {"ok": True}


@merchant_router.get("/me")
async def get_me(current: dict = Depends(get_current_merchant)) -> dict:
    return current


@merchant_router.get("/audit-trail")
async def get_audit_trail_all(current: dict = Depends(get_current_merchant)) -> list[dict]:
    return await asyncio.to_thread(get_full_audit_trail, current["id"])


@merchant_router.get("/agents")
async def get_agents(current: dict = Depends(get_current_merchant)) -> list[dict]:
    return await asyncio.to_thread(list_agents)


@merchant_router.get("/products")
async def get_products(current: dict = Depends(get_current_merchant)) -> list[dict]:
    return await asyncio.to_thread(list_products_for_merchant, current["id"])


@merchant_router.post("/products/{product_id}/stock")
async def post_adjust_stock(
    product_id: int, body: StockAdjustBody, current: dict = Depends(get_current_merchant)
) -> dict:
    updated = await asyncio.to_thread(adjust_product_stock, product_id, current["id"], body.delta)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"no product {product_id} for this merchant")
    return updated


@merchant_router.get("/pending-approvals")
async def get_pending_approvals(current: dict = Depends(get_current_merchant)) -> list[dict]:
    return await asyncio.to_thread(list_pending_approvals_for_merchant, current["id"])


@merchant_router.post("/resolve-approval/{approval_id}")
async def post_resolve_approval(
    approval_id: int, body: ResolveApprovalBody, current: dict = Depends(get_current_merchant)
) -> dict:
    """
    Approve/reject an over-threshold ai_agent purchase paused at
    authorization_node — the only way any paused order ever resolves (see
    pipeline/graph.py's authorization_node and this module's docstring on
    the self-approval boundary: this is a plain FastAPI route, not
    registered as an MCP tool, so no MCP client can ever call it).

    Merchant-scoped like every other /merchant/* route: 404s (not 403s, so
    as not to confirm the approval_request even exists) if the order behind
    approval_id doesn't belong to the logged-in merchant.
    """
    try:
        approval = await asyncio.to_thread(get_approval_request, approval_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"no pending approval #{approval_id} for this merchant")

    order = await asyncio.to_thread(get_order, approval["order_id"])
    if order["merchant_id"] != current["id"]:
        raise HTTPException(status_code=404, detail=f"no pending approval #{approval_id} for this merchant")
    if approval["status"] != "pending":
        raise HTTPException(
            status_code=409, detail=f"approval_request #{approval_id} already resolved (status={approval['status']})"
        )
    if order["thread_id"] is None:
        raise HTTPException(status_code=500, detail=f"order #{order['id']} has no recorded thread_id to resume")

    resume_value = {"approved": body.approved, "resolved_by": current["email"]}
    result = await asyncio.to_thread(resume_pipeline, order["thread_id"], resume_value)
    return await _advance_and_summarize(order["thread_id"], order["id"], result)


def build_http_app() -> FastAPI:
    """
    Combines FastMCP's streamable-http ASGI app (serving /mcp) with
    merchant_router (serving /merchant/*) into one FastAPI app on one port,
    so both run in the same process/port for convenience.
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
        allow_credentials=True,  # Phase 2: the merchant session cookie is
        # cross-origin (dashboard on :5173, this API on :8765), and browsers
        # drop credentialed requests/responses without this.
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
