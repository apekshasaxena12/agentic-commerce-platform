"""
product row helpers for the merchant dashboard's stock-management tab.
"""

from typing import Optional

import psycopg

from db.connection import get_database_url


def list_products_for_merchant(merchant_id: int) -> list[dict]:
    with psycopg.connect(get_database_url()) as conn:
        rows = conn.execute(
            """
            SELECT id, name, category, price, stock, image_url
            FROM product
            WHERE merchant_id = %s
            ORDER BY name
            """,
            (merchant_id,),
        ).fetchall()
    return [
        {"id": r[0], "name": r[1], "category": r[2], "price": float(r[3]), "stock": r[4], "image_url": r[5]}
        for r in rows
    ]


def get_ai_commerce_score(merchant_id: int) -> dict:
    """
    Day 16: the merchant dashboard's AI Commerce Score tab — five real,
    independently-queryable percentages (no fabricated numbers, no
    invented dimensions) averaged into one overall score. Each one:

    - catalog_completeness: % of this merchant's products where
      embedding IS NOT NULL (genuinely nullable — VECTOR(1536), no NOT
      NULL constraint), structured_attributes is a non-empty object (the
      column is NOT NULL DEFAULT '{}', so IS NOT NULL alone would be
      trivially always true — '{}' is the real "not populated" state),
      and semantic_description is non-blank (same reasoning: NOT NULL
      doesn't rule out an empty string). On the current seed data this is
      100% for both merchants — that's real data completeness, not a
      trivial check: an incompletely-seeded or newly-added product with a
      NULL embedding would show up here.
    - policy_clarity: whether a merchant_policy row exists at all (UNIQUE
      per merchant_id since migration 0005 — at most one), and how many
      of its 4 fields are actually meaningfully set: max_discount_pct,
      max_autonomous_purchase_amount and approval_required_above are all
      NOT NULL by schema so they're really checking "row exists";
      allowed_payment_methods is the one field that could be NOT NULL yet
      practically empty ('{}'), which is checked for real.
    - agent_accessibility: NOT queried — deliberately hardcoded to 100.
      search_products (catalog/retrieval.py) takes no merchant scoping by
      default and returns candidates across every merchant unconditionally,
      so there is no per-merchant "is this catalog reachable via MCP"
      state to query; it structurally cannot be anything but 100 for any
      merchant in this system. Shown as its own dimension for
      transparency, not disguised as a computed number.
    - cross_sell_readiness: % of this merchant's products that appear as
      either side (product_a_id or product_b_id) of at least one
      co_purchase_stat row.
    - track_record: same success-rate logic as db.agents.list_agents' new
      stats (successful/total), just scoped to orders.merchant_id instead
      of agent_id. None (not 0) when this merchant has no orders yet, so
      a merchant with zero history doesn't get a misleading 0% dragging
      the average down — the overall score is the mean of whichever
      dimensions have a real value.
    """
    query = """
        SELECT
            (SELECT COUNT(*) FROM product WHERE merchant_id = %(mid)s) AS total_products,
            (SELECT COUNT(*) FROM product
               WHERE merchant_id = %(mid)s
                 AND embedding IS NOT NULL
                 AND structured_attributes <> '{}'::jsonb
                 AND semantic_description IS NOT NULL
                 AND trim(semantic_description) <> ''
            ) AS catalog_complete_count,
            (SELECT COUNT(*) FROM product p
               WHERE p.merchant_id = %(mid)s
                 AND EXISTS (
                     SELECT 1 FROM co_purchase_stat cs
                     WHERE cs.product_a_id = p.id OR cs.product_b_id = p.id
                 )
            ) AS cross_sell_count,
            (SELECT COUNT(*) FROM merchant_policy WHERE merchant_id = %(mid)s) AS policy_row_exists,
            (SELECT
                (CASE WHEN max_discount_pct IS NOT NULL THEN 1 ELSE 0 END)
                + (CASE WHEN max_autonomous_purchase_amount IS NOT NULL THEN 1 ELSE 0 END)
                + (CASE WHEN array_length(allowed_payment_methods, 1) > 0 THEN 1 ELSE 0 END)
                + (CASE WHEN approval_required_above IS NOT NULL THEN 1 ELSE 0 END)
             FROM merchant_policy WHERE merchant_id = %(mid)s
            ) AS policy_fields_set,
            (SELECT COUNT(*) FROM orders WHERE merchant_id = %(mid)s) AS total_orders,
            (SELECT COUNT(*) FROM orders WHERE merchant_id = %(mid)s AND status = 'completed') AS successful_orders
    """
    with psycopg.connect(get_database_url()) as conn:
        row = conn.execute(query, {"mid": merchant_id}).fetchone()
    (
        total_products,
        catalog_complete_count,
        cross_sell_count,
        policy_row_exists,
        policy_fields_set,
        total_orders,
        successful_orders,
    ) = row
    policy_fields_set = policy_fields_set or 0

    catalog_completeness_pct = round(catalog_complete_count / total_products * 100, 1) if total_products else 0.0
    cross_sell_pct = round(cross_sell_count / total_products * 100, 1) if total_products else 0.0
    policy_clarity_pct = round(policy_fields_set / 4 * 100, 1) if policy_row_exists else 0.0
    agent_accessibility_pct = 100.0
    track_record_pct = round(successful_orders / total_orders * 100, 1) if total_orders else None

    dimensions = [
        {
            "key": "catalog_completeness",
            "label": "Catalog completeness",
            "score": catalog_completeness_pct,
            "detail": f"{catalog_complete_count}/{total_products} products fully described "
            "(semantic description, embedding, and structured attributes all populated)",
        },
        {
            "key": "policy_clarity",
            "label": "Policy clarity",
            "score": policy_clarity_pct,
            "detail": f"{policy_fields_set}/4 policy fields set" if policy_row_exists else "no merchant_policy row configured",
        },
        {
            "key": "agent_accessibility",
            "label": "Agent accessibility",
            "score": agent_accessibility_pct,
            "detail": "always 100% by design — search_products has no per-merchant reachability gate; "
            "every merchant's catalog is included in every MCP search by default",
        },
        {
            "key": "cross_sell_readiness",
            "label": "Cross-sell readiness",
            "score": cross_sell_pct,
            "detail": f"{cross_sell_count}/{total_products} products have at least one co_purchase_stat row",
        },
        {
            "key": "track_record",
            "label": "Track record (order success rate)",
            "score": track_record_pct,
            "detail": f"{successful_orders}/{total_orders} orders completed" if total_orders else "no orders yet",
        },
    ]

    scored = [d["score"] for d in dimensions if d["score"] is not None]
    overall_score = round(sum(scored) / len(scored), 1) if scored else 0.0

    return {"overall_score": overall_score, "dimensions": dimensions}


def adjust_product_stock(product_id: int, merchant_id: int, delta: int) -> Optional[dict]:
    """
    Adds delta (positive or negative) to a product's stock, clamped at 0 so
    a rapid double-click on "-" can never violate product's
    `stock >= 0` CHECK constraint. Scoped to merchant_id so a merchant can
    only adjust their own products. Returns the updated row, or None if no
    such product belongs to this merchant.
    """
    with psycopg.connect(get_database_url()) as conn:
        row = conn.execute(
            """
            UPDATE product
            SET stock = GREATEST(0, stock + %s)
            WHERE id = %s AND merchant_id = %s
            RETURNING id, name, category, price, stock, image_url
            """,
            (delta, product_id, merchant_id),
        ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "name": row[1], "category": row[2], "price": float(row[3]), "stock": row[4], "image_url": row[5]}
