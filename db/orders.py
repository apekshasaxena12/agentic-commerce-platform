"""
orders row helpers, called from the checkout pipeline (pipeline/graph.py).
"""

from decimal import Decimal
from typing import Optional

import psycopg
from psycopg.types.json import Jsonb

from db.connection import get_database_url


def create_order(
    agent_id: int,
    items: list[dict],
    amount: Decimal,
    discount_applied: Decimal = Decimal("0"),
) -> int:
    with psycopg.connect(get_database_url()) as conn:
        row = conn.execute(
            """
            INSERT INTO orders (agent_id, items, amount, discount_applied, status)
            VALUES (%s, %s, %s, %s, 'pending_approval')
            RETURNING id
            """,
            (agent_id, Jsonb(items), amount, discount_applied),
        ).fetchone()
        return row[0]


def update_order_status(order_id: int, status: str) -> None:
    with psycopg.connect(get_database_url()) as conn:
        conn.execute(
            "UPDATE orders SET status = %s, updated_at = now() WHERE id = %s",
            (status, order_id),
        )


def set_razorpay_ids(
    order_id: int,
    razorpay_order_id: Optional[str] = None,
    razorpay_payment_id: Optional[str] = None,
) -> None:
    with psycopg.connect(get_database_url()) as conn:
        conn.execute(
            """
            UPDATE orders
            SET razorpay_order_id = COALESCE(%s, razorpay_order_id),
                razorpay_payment_id = COALESCE(%s, razorpay_payment_id),
                updated_at = now()
            WHERE id = %s
            """,
            (razorpay_order_id, razorpay_payment_id, order_id),
        )


def get_order(order_id: int) -> dict:
    with psycopg.connect(get_database_url()) as conn:
        row = conn.execute(
            """
            SELECT id, agent_id, items, amount, discount_applied, status,
                   razorpay_order_id, razorpay_payment_id
            FROM orders WHERE id = %s
            """,
            (order_id,),
        ).fetchone()
    return {
        "id": row[0],
        "agent_id": row[1],
        "items": row[2],
        "amount": row[3],
        "discount_applied": row[4],
        "status": row[5],
        "razorpay_order_id": row[6],
        "razorpay_payment_id": row[7],
    }
