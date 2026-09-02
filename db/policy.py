import psycopg

from db.connection import get_database_url


def get_merchant_policy(merchant_id: int) -> dict:
    with psycopg.connect(get_database_url()) as conn:
        row = conn.execute(
            """
            SELECT max_discount_pct, max_autonomous_purchase_amount,
                   allowed_payment_methods, approval_required_above
            FROM merchant_policy
            WHERE merchant_id = %s
            """,
            (merchant_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"no merchant_policy row found for merchant_id={merchant_id}")
    return {
        "max_discount_pct": row[0],
        "max_autonomous_purchase_amount": row[1],
        "allowed_payment_methods": row[2],
        "approval_required_above": row[3],
    }


def get_merchant_policy_for_order(order_id: int) -> dict:
    """
    Resolves an order's merchant via orders.merchant_id (set once, at
    order-creation time in pipeline/graph.py's retrieve node, from the
    merchant_id of the product(s) being bought) and returns that
    merchant's policy row. This is what policy_check/authorization call
    instead of a bare get_merchant_policy() now that merchant_policy is
    one row per merchant rather than a singleton.
    """
    with psycopg.connect(get_database_url()) as conn:
        row = conn.execute(
            """
            SELECT mp.max_discount_pct, mp.max_autonomous_purchase_amount,
                   mp.allowed_payment_methods, mp.approval_required_above
            FROM orders o
            JOIN merchant_policy mp ON mp.merchant_id = o.merchant_id
            WHERE o.id = %s
            """,
            (order_id,),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"no merchant_policy row found for the merchant owning order_id={order_id}")
    return {
        "max_discount_pct": row[0],
        "max_autonomous_purchase_amount": row[1],
        "allowed_payment_methods": row[2],
        "approval_required_above": row[3],
    }
