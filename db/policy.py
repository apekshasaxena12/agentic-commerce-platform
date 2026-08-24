import psycopg

from db.connection import get_database_url


def get_merchant_policy() -> dict:
    with psycopg.connect(get_database_url()) as conn:
        row = conn.execute(
            """
            SELECT max_discount_pct, max_autonomous_purchase_amount,
                   allowed_payment_methods, approval_required_above
            FROM merchant_policy
            ORDER BY id
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        raise RuntimeError("no merchant_policy row found — run db/seed.py")
    return {
        "max_discount_pct": row[0],
        "max_autonomous_purchase_amount": row[1],
        "allowed_payment_methods": row[2],
        "approval_required_above": row[3],
    }
