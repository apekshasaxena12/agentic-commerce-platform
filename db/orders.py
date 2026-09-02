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
    merchant_id: int,
    items: list[dict],
    amount: Decimal,
    discount_applied: Decimal = Decimal("0"),
) -> int:
    with psycopg.connect(get_database_url()) as conn:
        row = conn.execute(
            """
            INSERT INTO orders (agent_id, merchant_id, items, amount, discount_applied, status)
            VALUES (%s, %s, %s, %s, %s, 'pending_approval')
            RETURNING id
            """,
            (agent_id, merchant_id, Jsonb(items), amount, discount_applied),
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


def set_thread_id(order_id: int, thread_id: str) -> None:
    """
    Persists the LangGraph thread_id this order's pipeline run is on
    (migration 0008), so a paused (merchant-approval-pending) order can be
    resumed by any process — e.g. the merchant dashboard's resolve-approval
    endpoint, which only knows the order_id, not the thread_id that created
    it. Called once from pipeline/graph.py's run_pipeline, right after the
    order_id first appears in a result.
    """
    with psycopg.connect(get_database_url()) as conn:
        conn.execute(
            "UPDATE orders SET thread_id = %s WHERE id = %s",
            (thread_id, order_id),
        )


def get_order(order_id: int) -> dict:
    with psycopg.connect(get_database_url()) as conn:
        row = conn.execute(
            """
            SELECT id, agent_id, merchant_id, items, amount, discount_applied, status,
                   razorpay_order_id, razorpay_payment_id, thread_id
            FROM orders WHERE id = %s
            """,
            (order_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"order {order_id} not found")
    return {
        "id": row[0],
        "agent_id": row[1],
        "merchant_id": row[2],
        "items": row[3],
        "amount": row[4],
        "discount_applied": row[5],
        "status": row[6],
        "razorpay_order_id": row[7],
        "razorpay_payment_id": row[8],
        "thread_id": row[9],
    }
