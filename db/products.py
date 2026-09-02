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
