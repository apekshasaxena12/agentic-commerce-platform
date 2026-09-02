"""
Seeds real login credentials for the 2 existing merchants (Phase 2 of
multi-tenant architecture — no signup flow, exactly 2 seeded accounts).
Hashes passwords with the same `bcrypt` library the login endpoint verifies
against (mcp_server/merchant_auth.py) — not pgcrypto's SQL-side bcrypt —
so there's no cross-implementation format doubt.

Run once, after db/migrate.py has applied 0006_merchant_credentials.sql
and before 0007_merchant_credentials_not_null.sql (which requires these
columns to already be populated). Safe to re-run: UPDATEs by slug, no
inserts, no duplicate risk.
"""

import bcrypt
import psycopg

from db.connection import get_database_url

# Demo credentials — reasonable, memorable, not meant to be
# production-grade secrets. Printed at the end so they're never silently
# hidden after a run.
CREDENTIALS = [
    {"slug": "shopfront-running-co", "email": "owner@shopfrontrunning.com", "password": "RunningCo#2026"},
    {"slug": "roast-and-ritual", "email": "owner@roastandritual.com", "password": "RoastRitual#2026"},
]


def main() -> None:
    with psycopg.connect(get_database_url()) as conn:
        for cred in CREDENTIALS:
            password_hash = bcrypt.hashpw(cred["password"].encode("utf-8"), bcrypt.gensalt()).decode("ascii")
            result = conn.execute(
                "UPDATE merchant SET email = %s, password_hash = %s WHERE slug = %s RETURNING id, name",
                (cred["email"], password_hash, cred["slug"]),
            ).fetchone()
            if result is None:
                raise RuntimeError(f"no merchant with slug={cred['slug']!r} — run db/migrate.py's Phase 1 migration first")
            print(f"set credentials for merchant #{result[0]} {result[1]!r}: email={cred['email']!r}")

    print("\nLogin credentials (also see the task summary):")
    for cred in CREDENTIALS:
        print(f"  {cred['email']}  /  {cred['password']}")


if __name__ == "__main__":
    main()
