"""
merchant row helpers — Phase 2's real login reads credentials through
here; mcp_server/merchant_auth.py does the actual password/session work.
"""

from typing import Optional

import psycopg

from db.connection import get_database_url


def get_merchant_by_email(email: str) -> Optional[dict]:
    with psycopg.connect(get_database_url()) as conn:
        row = conn.execute(
            "SELECT id, name, slug, email, password_hash FROM merchant WHERE email = %s",
            (email,),
        ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "name": row[1], "slug": row[2], "email": row[3], "password_hash": row[4]}


def get_merchant(merchant_id: int) -> Optional[dict]:
    with psycopg.connect(get_database_url()) as conn:
        row = conn.execute(
            "SELECT id, name, slug, email FROM merchant WHERE id = %s",
            (merchant_id,),
        ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "name": row[1], "slug": row[2], "email": row[3]}
