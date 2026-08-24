"""
The checkout pipeline as a LangGraph StateGraph.

Graph shape:

    intent -> retrieve -> [recommend ->] policy_check -> authorization -> razorpay -> verification

  - retrieve routes straight to END for browsing intents, or when no
    product matched a checkout intent (no order gets created either way).
  - recommend only runs for checkout intents that resolved to a product.
  - policy_check routes to END (order marked 'failed') if any check fails.
  - authorization routes to END (order marked 'failed') if the human
    rejects, or the merchant rejects an over-threshold approval request —
    both cases release the budget reserved at policy_check.
  - razorpay creates a REAL test-mode Razorpay order (spikes/razorpay_spike.py's
    verified SDK usage).
  - verification pauses (interrupt()) waiting for a payment.captured /
    payment.failed webhook payload as its resume value — a real webhook
    HTTP endpoint doesn't exist yet (no UI to trigger one until Day 9), so
    this is fed synthetically by callers for now. captured -> completed;
    failed -> order marked failed and the reserved budget is released.

Schema note: audit_log_entry.order_id is nullable (migration 0003) because
intent/retrieve must log on every run even for pure browsing queries, which
never create an order.

Audit logging: every node is wrapped by the `audited` decorator below,
which is the single place that calls db.audit.log_audit_entry — no node
duplicates that logic. On an unexpected exception, the decorator logs the
failure and re-raises (it does NOT swallow it) — except for LangGraph's own
GraphInterrupt/GraphBubbleUp control-flow exceptions, which must pass
through untouched (see the long comment on `audited` below for why this
matters).
"""

import contextvars
import functools
import json
import os
from decimal import Decimal
from typing import Any, Callable, Optional, TypedDict

import psycopg
from groq import Groq
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from catalog.retrieval import search_products
from db.agents import get_agent
from db.approvals import (
    create_approval_request,
    get_pending_approval_request,
    resolve_approval_request,
)
from db.audit import log_audit_entry
from db.budget import check_and_reserve_budget, release_budget
from db.connection import get_database_url
from db.orders import create_order, set_razorpay_ids, update_order_status
from db.policy import get_merchant_policy
from payments.razorpay_gateway import create_razorpay_order, parse_payment_webhook

GROQ_MODEL = "openai/gpt-oss-120b"

_groq_client: Optional[Groq] = None


def _get_groq_client() -> Groq:
    global _groq_client
    if _groq_client is None:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY not set in .env")
        _groq_client = Groq(api_key=api_key)
    return _groq_client


class PipelineState(TypedDict, total=False):
    # --- input ---
    user_message: str
    agent_id: int
    payment_method: str
    discount_pct: float
    quantity: int  # optional input, defaults to 1 (see _intent_impl); added for
    # Front Door 2's checkout(product_id, quantity) MCP tool — Front Door 1
    # never sets this, so it's always 1 there, unaffected.

    # --- resolved in intent ---
    agent_type: str  # "human_session" | "ai_agent"
    intent_type: str  # "browsing" | "checkout"
    search_query: str
    filters: dict

    # --- resolved in retrieve ---
    search_results: list[dict]
    order_id: Optional[int]
    target_product: Optional[dict]
    amount: Optional[float]
    discount_applied: Optional[float]

    # --- resolved in recommend ---
    recommendation: Optional[dict]

    # --- resolved in policy_check ---
    policy_checks: list[dict]
    policy_passed: bool

    # --- resolved in authorization ---
    authorized: bool
    authorization_reason: str

    # --- resolved in razorpay ---
    razorpay_order_id: Optional[str]

    # --- input to verification (fed as the interrupt's resume value; see
    #     verification_node) ---
    webhook_payload: Optional[dict]

    # --- resolved in verification ---
    final_status: str


# ---------------------------------------------------------------------------
# Live audit streaming
# ---------------------------------------------------------------------------
#
# run_pipeline()/resume_pipeline() accept an optional `on_audit` callback.
# When set, it's stashed in this contextvar for the duration of that single
# GRAPH.invoke() call, and _emit_audit() (used everywhere log_audit_entry
# would otherwise be called directly) invokes it with each row right after
# the DB write — so a caller (the WebSocket handler) gets every
# audit_log_entry the instant it's written, not polled/batched afterward.
# Set via contextvars.ContextVar.set()/.reset() around the SAME synchronous
# call that runs the graph (not crossing a thread boundary), so there's no
# question of context propagation — it's plain call-stack scoping.
_audit_sink: contextvars.ContextVar[Optional[Callable[[dict], None]]] = contextvars.ContextVar(
    "_audit_sink", default=None
)


def _emit_audit(
    order_id: Optional[int],
    step: str,
    input_summary: str,
    output_summary: str,
    reasoning_text: str,
) -> int:
    entry_id = log_audit_entry(order_id, step, input_summary, output_summary, reasoning_text)
    sink = _audit_sink.get()
    if sink is not None:
        sink(
            {
                "id": entry_id,
                "order_id": order_id,
                "step": step,
                "input_summary": input_summary,
                "output_summary": output_summary,
                "reasoning_text": reasoning_text,
            }
        )
    return entry_id


# ---------------------------------------------------------------------------
# Shared audit-logging wrapper
# ---------------------------------------------------------------------------
#
# Every node function below has the signature
#   impl(state: PipelineState) -> tuple[dict, str, str, str]
# returning (state_update, input_summary, output_summary, reasoning_text).
# `audited(step)` wraps it: calls log_audit_entry with those four values on
# a normal return, or logs a failure summary and re-raises on an unexpected
# exception. This is the one place that touches log_audit_entry from node
# code, so every node logs identically.
#
# GraphInterrupt (raised by `interrupt()`) is a GraphBubbleUp, not a node
# failure — LangGraph uses it as normal pause control flow, so it MUST
# re-raise silently, not get logged as an error. See authorization_node's
# docstring for how the "paused" audit entry gets written instead (from the
# driver, not from here) — the interrupted node's own code never returns
# during the call that pauses it, so there's no tuple for this wrapper to
# log at that point.
def audited(step: str):
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(state: PipelineState) -> dict:
            try:
                update, input_summary, output_summary, reasoning_text = fn(state)
            except GraphBubbleUp:
                raise
            except Exception as exc:
                _emit_audit(
                    order_id=state.get("order_id"),
                    step=step,
                    input_summary=f"state keys present: {sorted(state.keys())}",
                    output_summary=f"FAILED: {type(exc).__name__}: {exc}",
                    reasoning_text=f"Unhandled error in {step} node: {exc}",
                )
                raise

            order_id_for_log = update.get("order_id", state.get("order_id"))
            _emit_audit(
                order_id=order_id_for_log,
                step=step,
                input_summary=input_summary,
                output_summary=output_summary,
                reasoning_text=reasoning_text,
            )
            return update

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# 1. intent
# ---------------------------------------------------------------------------

_INTENT_SYSTEM_PROMPT = """You classify a shopper's message for a running-shoes \
and sportswear store, and extract structured search filters. Respond with JSON \
only, matching exactly this schema:

{"intent_type": "browsing" | "checkout",
 "search_query": "<short product description to search for>",
 "filters": {"category": "<category or null>",
             "min_price": <number or null>,
             "max_price": <number or null>,
             "in_stock_only": <true|false>}}

intent_type is "checkout" only if the user clearly wants to buy/purchase a \
specific item now, not just browse or ask about products.

Valid categories: running_shoes, socks, insoles, apparel_top, apparel_bottom, \
outerwear, accessories, hydration, wearable_tech. Use null if no category is \
implied."""


def _intent_impl(state: PipelineState):
    user_message = state["user_message"]
    agent_id = state["agent_id"]

    agent = get_agent(agent_id)

    client = _get_groq_client()
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": _INTENT_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    raw = response.choices[0].message.content
    parsed = json.loads(raw)

    intent_type = parsed.get("intent_type")
    if intent_type not in ("browsing", "checkout"):
        intent_type = "browsing"
    search_query = parsed.get("search_query") or user_message
    filters = parsed.get("filters") or {}

    update = {
        "agent_type": agent["type"],
        "intent_type": intent_type,
        "search_query": search_query,
        "filters": filters,
        "payment_method": state.get("payment_method", "card"),
        "discount_pct": state.get("discount_pct", 0.0),
        "quantity": state.get("quantity", 1),
    }
    input_summary = f"user_message={user_message!r} agent_id={agent_id}"
    output_summary = f"intent_type={intent_type} search_query={search_query!r} filters={filters}"
    reasoning_text = (
        f"Groq ({GROQ_MODEL}) classified intent from raw text; "
        f"agent #{agent_id} resolved to type={agent['type']}."
    )
    return update, input_summary, output_summary, reasoning_text


intent_node = audited("intent")(_intent_impl)


# ---------------------------------------------------------------------------
# 2. retrieve
# ---------------------------------------------------------------------------


def _retrieve_impl(state: PipelineState):
    query = state["search_query"]
    raw_filters = state.get("filters") or {}

    clean_filters: dict[str, Any] = {}
    if raw_filters.get("category"):
        clean_filters["category"] = raw_filters["category"]
    if raw_filters.get("min_price") is not None:
        clean_filters["min_price"] = Decimal(str(raw_filters["min_price"]))
    if raw_filters.get("max_price") is not None:
        clean_filters["max_price"] = Decimal(str(raw_filters["max_price"]))
    if raw_filters.get("in_stock_only"):
        clean_filters["in_stock_only"] = True

    results = search_products(query, filters=clean_filters, limit=10)
    results_serialized = [
        {
            "id": p.id,
            "name": p.name,
            "category": p.category,
            "price": float(p.price),
            "stock": p.stock,
            "score": p.score,
        }
        for p in results
    ]
    update: dict[str, Any] = {"search_results": results_serialized}

    if state["intent_type"] == "checkout" and results:
        top = results[0]
        original_price = top.price
        quantity = int(state.get("quantity") or 1)
        subtotal = original_price * quantity
        discount_pct = Decimal(str(state.get("discount_pct", 0) or 0))
        discount_applied = (subtotal * discount_pct / Decimal("100")).quantize(Decimal("0.01"))
        final_amount = subtotal - discount_applied

        items = [
            {
                "product_id": top.id,
                "name": top.name,
                "quantity": quantity,
                "unit_price": float(original_price),
            }
        ]
        order_id = create_order(
            agent_id=state["agent_id"],
            items=items,
            amount=final_amount,
            discount_applied=discount_applied,
        )

        update.update(
            {
                "order_id": order_id,
                "target_product": {"id": top.id, "name": top.name, "price": float(original_price)},
                "amount": float(final_amount),
                "discount_applied": float(discount_applied),
            }
        )
        input_summary = f"query={query!r} filters={clean_filters} intent_type=checkout"
        output_summary = (
            f"{len(results)} results; created order_id={order_id} for "
            f"product #{top.id} {top.name!r} qty={quantity} amount={final_amount}"
        )
        reasoning_text = (
            f"Checkout intent resolved to top search result (blended score={top.score:.3f}); "
            "order row created here so subsequent steps have an order_id to log against."
        )
    else:
        input_summary = f"query={query!r} filters={clean_filters} intent_type={state['intent_type']}"
        output_summary = f"{len(results)} results returned; no order created"
        reasoning_text = (
            "Browsing intent, or a checkout intent with no matching product — "
            "no order to create; pipeline ends after this node."
        )

    return update, input_summary, output_summary, reasoning_text


retrieve_node = audited("retrieve")(_retrieve_impl)


def route_after_retrieve(state: PipelineState) -> str:
    if state.get("intent_type") == "checkout" and state.get("order_id") is not None:
        return "recommend"
    return END


# ---------------------------------------------------------------------------
# 3. recommend (checkout only)
# ---------------------------------------------------------------------------


def _recommend_impl(state: PipelineState):
    order_id = state["order_id"]
    product = state["target_product"]

    with psycopg.connect(get_database_url()) as conn:
        row = conn.execute(
            """
            SELECT product_b_id, co_purchase_rate FROM co_purchase_stat
            WHERE product_a_id = %s
            ORDER BY co_purchase_rate DESC
            LIMIT 1
            """,
            (product["id"],),
        ).fetchone()

    if row is None:
        update = {"recommendation": None}
        input_summary = f"order_id={order_id} product_id={product['id']}"
        output_summary = "no co_purchase_stat row found for this product"
        reasoning_text = "No cross-sell data available for this product; skipping recommendation."
        return update, input_summary, output_summary, reasoning_text

    cross_sell_id, rate = row
    with psycopg.connect(get_database_url()) as conn:
        cross_row = conn.execute(
            "SELECT name FROM product WHERE id = %s", (cross_sell_id,)
        ).fetchone()
    cross_sell_name = cross_row[0]

    client = _get_groq_client()
    prompt = (
        f"A shopper is buying '{product['name']}'. {rate * 100:.0f}% of buyers of "
        f"that product also bought '{cross_sell_name}'. Write ONE short, natural "
        f"sentence (max 25 words) stating this as a purchase suggestion, in the "
        f"style of '68% of buyers of X also bought Y.' Plain text only, no quotes, no JSON."
    )
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    reason = response.choices[0].message.content.strip()

    update = {
        "recommendation": {
            "product_id": cross_sell_id,
            "name": cross_sell_name,
            "co_purchase_rate": rate,
            "reason": reason,
        }
    }
    input_summary = f"order_id={order_id} product_id={product['id']} ({product['name']})"
    output_summary = f"cross-sell #{cross_sell_id} {cross_sell_name!r} (rate={rate:.2f}): {reason!r}"
    reasoning_text = (
        "Selected the highest co_purchase_rate row for this product; "
        f"Groq ({GROQ_MODEL}) drafted the stated reason from that rate."
    )
    return update, input_summary, output_summary, reasoning_text


recommend_node = audited("recommend")(_recommend_impl)


# ---------------------------------------------------------------------------
# 4. policy_check
# ---------------------------------------------------------------------------


def _policy_check_impl(state: PipelineState):
    order_id = state["order_id"]
    agent_id = state["agent_id"]
    amount = Decimal(str(state["amount"]))
    discount_applied = Decimal(str(state.get("discount_applied", 0) or 0))
    payment_method = state.get("payment_method") or "card"
    original_price = Decimal(str(state["target_product"]["price"]))

    policy = get_merchant_policy()
    checks = []

    discount_pct_actual = (
        (discount_applied / original_price * 100) if original_price > 0 else Decimal("0")
    )
    discount_ok = discount_pct_actual <= policy["max_discount_pct"]
    checks.append(
        {
            "check": "discount_within_limit",
            "passed": discount_ok,
            "reason": (
                f"discount {discount_pct_actual:.1f}% "
                f"{'<=' if discount_ok else '>'} max allowed {policy['max_discount_pct']}%"
            ),
        }
    )

    payment_ok = payment_method in policy["allowed_payment_methods"]
    checks.append(
        {
            "check": "payment_method_allowed",
            "passed": payment_ok,
            "reason": (
                f"payment method {payment_method!r} "
                f"{'is' if payment_ok else 'is NOT'} in allowed methods {policy['allowed_payment_methods']}"
            ),
        }
    )

    # Budget reservation has a side effect (increments agent.spent_so_far),
    # so it's only evaluated if the checks above already pass — we don't
    # want to reserve budget for an order that's already going to fail for
    # an unrelated reason. check_and_reserve_budget has no "release" path,
    # so this ordering is how we avoid ever needing one this session.
    if discount_ok and payment_ok:
        try:
            budget_ok = check_and_reserve_budget(agent_id, amount)
            budget_reason = (
                f"budget reserved: amount {amount} fits within agent #{agent_id}'s remaining budget"
                if budget_ok
                else f"budget check failed: amount {amount} would exceed agent #{agent_id}'s remaining budget"
            )
        except ValueError as exc:
            budget_ok = False
            budget_reason = f"budget check errored: {exc}"
    else:
        budget_ok = False
        budget_reason = "not evaluated — an earlier check (discount or payment method) already failed"
    checks.append({"check": "budget_available", "passed": budget_ok, "reason": budget_reason})

    all_passed = all(c["passed"] for c in checks)
    update = {"policy_checks": checks, "policy_passed": all_passed}

    if not all_passed:
        update_order_status(order_id, "failed")

    input_summary = (
        f"order_id={order_id} amount={amount} discount_applied={discount_applied} "
        f"payment_method={payment_method!r}"
    )
    output_summary = "; ".join(
        f"{c['check']}={'PASS' if c['passed'] else 'FAIL'}" for c in checks
    )
    reasoning_text = " | ".join(c["reason"] for c in checks)
    return update, input_summary, output_summary, reasoning_text


policy_check_node = audited("policy_check")(_policy_check_impl)


def route_after_policy_check(state: PipelineState) -> str:
    return "authorization" if state.get("policy_passed") else END


# ---------------------------------------------------------------------------
# 5. authorization
# ---------------------------------------------------------------------------
#
# IMPORTANT LangGraph behavior: when a node calls interrupt() for the first
# time, execution stops at that line (GraphInterrupt propagates out) — the
# function does NOT return, so `audited()` never gets a tuple to log for
# that call. On resume, LangGraph re-executes the node FROM THE TOP (not
# from the interrupt() call) — everything before interrupt() runs again.
# Two consequences handled below:
#   1. Any DB write before interrupt() must be idempotent across re-entry
#      (see the get_pending_approval_request guard).
#   2. There's no way to log a "paused" audit entry from inside the node on
#      the pausing call itself (there's no tuple to log). That entry is
#      written by run_pipeline()/resume_pipeline() below instead, right
#      after they observe '__interrupt__' in the invoke() result.


def _authorization_impl(state: PipelineState):
    order_id = state["order_id"]
    agent_type = state["agent_type"]
    amount = Decimal(str(state["amount"]))
    policy = get_merchant_policy()

    if agent_type == "human_session":
        decision = interrupt(
            {
                "type": "human_confirm_required",
                "order_id": order_id,
                "amount": float(amount),
                "message": f"Confirm purchase of order #{order_id} for INR {amount}?",
            }
        )
        authorized = decision == "confirm"
        if authorized:
            update_order_status(order_id, "approved")
            budget_note = ""
        else:
            release_budget(state["agent_id"], amount)
            update_order_status(order_id, "failed")
            budget_note = f" Budget of {amount} released back to agent #{state['agent_id']}."
        update = {"authorized": authorized, "authorization_reason": f"human confirm signal: {decision!r}"}
        input_summary = f"order_id={order_id} agent_type=human_session amount={amount}"
        output_summary = f"authorized={authorized} (resumed with decision={decision!r})"
        reasoning_text = (
            "human_session always requires an explicit confirm; graph paused via "
            "interrupt() until resumed with the confirm/reject signal." + budget_note
        )
        return update, input_summary, output_summary, reasoning_text

    # ai_agent
    threshold = policy["approval_required_above"]
    if amount <= threshold:
        update_order_status(order_id, "approved")
        update = {
            "authorized": True,
            "authorization_reason": f"ai_agent amount {amount} <= approval_required_above {threshold}: auto-authorized",
        }
        input_summary = f"order_id={order_id} agent_type=ai_agent amount={amount} threshold={threshold}"
        output_summary = "authorized=True (auto, no pause)"
        reasoning_text = (
            f"ai_agent purchase amount {amount} is within approval_required_above "
            f"({threshold}); auto-authorized per merchant policy, no human intervention needed."
        )
        return update, input_summary, output_summary, reasoning_text

    # ai_agent, over threshold: create (idempotently) a pending approval
    # request, then pause.
    existing = get_pending_approval_request(order_id)
    approval_id = existing["id"] if existing else create_approval_request(order_id)

    decision = interrupt(
        {
            "type": "merchant_approval_required",
            "order_id": order_id,
            "approval_request_id": approval_id,
            "amount": float(amount),
            "threshold": float(threshold),
            "message": (
                f"Order #{order_id} amount INR {amount} exceeds autonomous threshold "
                f"INR {threshold}; merchant approval required."
            ),
        }
    )

    approved = decision.get("approved") if isinstance(decision, dict) else bool(decision == "approved")
    resolved_by = decision.get("resolved_by", "unknown") if isinstance(decision, dict) else "unknown"
    resolve_approval_request(approval_id, "approved" if approved else "rejected", resolved_by)

    if approved:
        update_order_status(order_id, "approved")
        budget_note = ""
    else:
        release_budget(state["agent_id"], amount)
        update_order_status(order_id, "failed")
        budget_note = f" Budget of {amount} released back to agent #{state['agent_id']}."

    update = {
        "authorized": approved,
        "authorization_reason": (
            f"merchant {'approved' if approved else 'rejected'} approval_request "
            f"#{approval_id} (resolved_by={resolved_by})"
        ),
    }
    input_summary = (
        f"order_id={order_id} agent_type=ai_agent amount={amount} threshold={threshold} "
        f"approval_request_id={approval_id}"
    )
    output_summary = f"authorized={approved}"
    reasoning_text = (
        f"ai_agent amount {amount} exceeds approval_required_above ({threshold}); "
        f"created/reused approval_request #{approval_id}, paused via interrupt() "
        "until a merchant resolved it." + budget_note
    )
    return update, input_summary, output_summary, reasoning_text


authorization_node = audited("authorization")(_authorization_impl)


def route_after_authorization(state: PipelineState) -> str:
    return "razorpay" if state.get("authorized") else END


# ---------------------------------------------------------------------------
# 6. razorpay — real test-mode order creation (spikes/razorpay_spike.py's
#    verified SDK usage, via payments/razorpay_gateway.py). No capture/
#    payment yet — that needs the Day 9 checkout UI.
# ---------------------------------------------------------------------------


def _razorpay_impl(state: PipelineState):
    order_id = state["order_id"]
    amount = Decimal(str(state["amount"]))

    razorpay_order = create_razorpay_order(amount, receipt=f"order_{order_id}")
    set_razorpay_ids(order_id, razorpay_order_id=razorpay_order["id"])

    update = {"razorpay_order_id": razorpay_order["id"]}
    input_summary = f"order_id={order_id} amount={amount}"
    output_summary = (
        f"real Razorpay order created: id={razorpay_order['id']} "
        f"status={razorpay_order['status']} amount={razorpay_order['amount']}paise"
    )
    reasoning_text = (
        "Real test-mode Razorpay order created via client.order.create() "
        "(razorpay==2.0.1, same SDK call verified live in spikes/razorpay_spike.py). "
        "No payment has happened yet — that requires the Day 9 checkout UI."
    )
    return update, input_summary, output_summary, reasoning_text


razorpay_node = audited("razorpay")(_razorpay_impl)


# ---------------------------------------------------------------------------
# 7. verification — pauses for a real payment.captured / payment.failed
#    webhook payload (shape verified in spikes/razorpay_spike.py against
#    Razorpay's docs), fed as the interrupt's resume value. There's no live
#    webhook HTTP endpoint yet since nothing can trigger a real one until
#    the Day 9 checkout UI exists; payments/razorpay_gateway.parse_payment_webhook
#    is what a real endpoint would call too, so wiring one up later is just
#    an HTTP handler around the same function.
# ---------------------------------------------------------------------------


def _verification_impl(state: PipelineState):
    order_id = state["order_id"]

    webhook_payload = interrupt(
        {
            "type": "webhook_required",
            "order_id": order_id,
            "razorpay_order_id": state.get("razorpay_order_id"),
            "message": (
                f"Waiting for Razorpay payment.captured/payment.failed webhook "
                f"for order #{order_id} (razorpay_order_id={state.get('razorpay_order_id')})."
            ),
        }
    )

    parsed = parse_payment_webhook(webhook_payload)

    if parsed["event"] == "payment.captured":
        set_razorpay_ids(order_id, razorpay_payment_id=parsed["razorpay_payment_id"])
        update_order_status(order_id, "completed")
        update = {"final_status": "completed"}
        input_summary = f"order_id={order_id} webhook_event={parsed['event']}"
        output_summary = (
            f"order status set to completed; razorpay_payment_id={parsed['razorpay_payment_id']}"
        )
        reasoning_text = (
            f"Received payment.captured webhook for razorpay_payment_id="
            f"{parsed['razorpay_payment_id']} (razorpay_order_id={parsed['razorpay_order_id']}); "
            "payment confirmed, order marked completed."
        )
        return update, input_summary, output_summary, reasoning_text

    if parsed["event"] == "payment.failed":
        amount = Decimal(str(state["amount"]))
        release_budget(state["agent_id"], amount)
        set_razorpay_ids(order_id, razorpay_payment_id=parsed["razorpay_payment_id"])
        update_order_status(order_id, "failed")
        update = {"final_status": "failed"}
        input_summary = f"order_id={order_id} webhook_event={parsed['event']}"
        output_summary = (
            f"order status set to failed; budget released ({amount}); "
            f"decline reason={parsed['error_reason']}"
        )
        reasoning_text = (
            f"Received payment.failed webhook: error_code={parsed['error_code']} "
            f"error_reason={parsed['error_reason']} description={parsed['error_description']!r}. "
            f"Released the reserved {amount} back to agent #{state['agent_id']}'s budget "
            "since the order will not complete."
        )
        return update, input_summary, output_summary, reasoning_text

    raise ValueError(f"unrecognized webhook event: {parsed['event']!r}")


verification_node = audited("verification")(_verification_impl)


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

_builder = StateGraph(PipelineState)
_builder.add_node("intent", intent_node)
_builder.add_node("retrieve", retrieve_node)
_builder.add_node("recommend", recommend_node)
_builder.add_node("policy_check", policy_check_node)
_builder.add_node("authorization", authorization_node)
_builder.add_node("razorpay", razorpay_node)
_builder.add_node("verification", verification_node)

_builder.add_edge(START, "intent")
_builder.add_edge("intent", "retrieve")
_builder.add_conditional_edges("retrieve", route_after_retrieve, ["recommend", END])
_builder.add_edge("recommend", "policy_check")
_builder.add_conditional_edges("policy_check", route_after_policy_check, ["authorization", END])
_builder.add_conditional_edges("authorization", route_after_authorization, ["razorpay", END])
_builder.add_edge("razorpay", "verification")
_builder.add_edge("verification", END)

# Durable Postgres-backed checkpointer (Day 13). Fixes the Day 10-12
# finding: InMemorySaver's state lives only in this process's RAM, so every
# paused order (awaiting merchant approval, awaiting a payment webhook) was
# silently lost on a restart/redeploy. PostgresSaver persists the same
# checkpoint to the same DATABASE_URL every other table here already uses —
# it manages its own checkpoints/checkpoint_blobs/checkpoint_writes/
# checkpoint_migrations tables via .setup() below, which is idempotent and
# safe to call on every process start (it no-ops once already migrated).
#
# A ConnectionPool, not a single Connection: GRAPH is a module-level
# singleton reused for every pipeline invocation, and each invocation runs
# on its own worker thread (server/app.py's anyio.to_thread.run_sync,
# mcp_server/server.py's asyncio.to_thread) — concurrent threads need
# concurrent connections, not one shared connection object serializing them.
_checkpointer_pool = ConnectionPool(
    conninfo=get_database_url(),
    min_size=1,
    max_size=10,
    kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    open=False,
)
_checkpointer_pool.open()
_checkpointer = PostgresSaver(_checkpointer_pool)
_checkpointer.setup()

GRAPH = _builder.compile(checkpointer=_checkpointer)


# ---------------------------------------------------------------------------
# Driver helpers
# ---------------------------------------------------------------------------


def _log_interrupt_if_any(result: dict, config: dict) -> None:
    """
    Two nodes can interrupt now: authorization and verification. Both are
    real graph node names, which also happen to be valid audit_log_entry
    step names — so the paused node's name IS the step to log against.
    GRAPH.get_state(config).next tells us which node is paused.
    """
    interrupts = result.get("__interrupt__")
    if not interrupts:
        return
    state = GRAPH.get_state(config)
    step = state.next[0] if state.next else "authorization"
    for i in interrupts:
        value = i.value
        order_id = value.get("order_id") if isinstance(value, dict) else None
        message = value.get("message") if isinstance(value, dict) else str(value)
        _emit_audit(
            order_id=order_id,
            step=step,
            input_summary=f"interrupt raised: {value}",
            output_summary="PAUSED — awaiting external resume signal",
            reasoning_text=message,
        )


def run_pipeline(
    initial_state: dict, thread_id: str, on_audit: Optional[Callable[[dict], None]] = None
) -> dict:
    token = _audit_sink.set(on_audit) if on_audit is not None else None
    try:
        config = {"configurable": {"thread_id": thread_id}}
        result = GRAPH.invoke(initial_state, config=config)
        _log_interrupt_if_any(result, config)
        return result
    finally:
        if token is not None:
            _audit_sink.reset(token)


def resume_pipeline(
    thread_id: str, resume_value: Any, on_audit: Optional[Callable[[dict], None]] = None
) -> dict:
    token = _audit_sink.set(on_audit) if on_audit is not None else None
    try:
        config = {"configurable": {"thread_id": thread_id}}
        result = GRAPH.invoke(Command(resume=resume_value), config=config)
        _log_interrupt_if_any(result, config)
        return result
    finally:
        if token is not None:
            _audit_sink.reset(token)
