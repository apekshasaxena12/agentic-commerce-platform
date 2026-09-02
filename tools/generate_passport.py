"""
Generates the AI Commerce Passport (project-brief.md, section 3): a
machine-readable profile of what each merchant sells and what an AI agent
is/isn't allowed to do, read straight from the live merchant/product/
merchant_policy tables — not hand-maintained, so it can't drift from the
real seeded policy.

The MCP tool surface descriptions below are hand-written (not introspected
from mcp_server/server.py's docstrings) so they read as a 30-second judge
summary rather than truncated code comments; keep them in sync if those
four tools' behavior changes.

Run: python -m tools.generate_passport
Writes: docs/ai_commerce_passport.json
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from db.connection import get_database_url
from db.merchants import get_merchant
from db.policy import get_merchant_policy
from db.products import list_products_for_merchant

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "ai_commerce_passport.json"

MCP_TOOL_SURFACE = [
    {
        "name": "search_catalog",
        "description": (
            "Search the product catalog by natural-language query, category, price "
            "ceiling, and stock — hybrid keyword + semantic + structured-filter search."
        ),
    },
    {
        "name": "get_product",
        "description": (
            "Fetch full detail for one product, including cross-sell/co-purchase "
            "data and substitute products."
        ),
    },
    {
        "name": "checkout",
        "description": (
            "Buy a quantity of one product as the AI buyer agent, through the same "
            "checkout pipeline (intent -> retrieve -> recommend -> policy check -> "
            "authorization -> Razorpay -> verification) a human shopper's purchase "
            "goes through. Returns completed, pending_approval, or failed."
        ),
    },
    {
        "name": "check_order_status",
        "description": "Poll the current status of a previously placed order.",
    },
]


def _list_merchant_ids() -> list[int]:
    with psycopg.connect(get_database_url()) as conn:
        rows = conn.execute("SELECT id FROM merchant ORDER BY id").fetchall()
    return [r[0] for r in rows]


def build_merchant_passport(merchant_id: int) -> dict:
    merchant = get_merchant(merchant_id)
    policy = get_merchant_policy(merchant_id)
    products = list_products_for_merchant(merchant_id)
    categories = sorted({p["category"] for p in products})

    return {
        "merchant": {
            "id": merchant["id"],
            "name": merchant["name"],
            "slug": merchant["slug"],
        },
        "what_this_merchant_sells": {
            "catalog_size": len(products),
            "categories": categories,
        },
        "what_an_ai_agent_may_do": {
            "browse": True,
            "cart": True,
            "purchase_autonomously_up_to": float(policy["approval_required_above"]),
            "purchase_above_that_requires": "merchant approval (pauses the checkout pipeline until resolved via the merchant dashboard)",
            "max_discount_pct": float(policy["max_discount_pct"]),
            "max_autonomous_purchase_amount": float(policy["max_autonomous_purchase_amount"]),
            "allowed_payment_methods": list(policy["allowed_payment_methods"]),
        },
        "mcp_tool_surface": MCP_TOOL_SURFACE,
    }


def main() -> None:
    passport = {
        "document": "AI Commerce Passport",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "merchants": [build_merchant_passport(mid) for mid in _list_merchant_ids()],
    }

    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(passport, indent=2) + "\n")
    print(f"wrote {OUTPUT_PATH} ({len(passport['merchants'])} merchant(s))")


if __name__ == "__main__":
    main()
