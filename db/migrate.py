"""
Tiny migration runner: applies db/migrations/*.sql in filename order,
tracking what's already applied in a schema_migrations table. Re-running
is a no-op for files already applied.

Run: python -m db.migrate
"""

from pathlib import Path

import psycopg

from db.connection import get_database_url

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def main() -> None:
    with psycopg.connect(get_database_url(), autocommit=True) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        applied = {
            row[0]
            for row in conn.execute("SELECT filename FROM schema_migrations").fetchall()
        }

        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                print(f"skip    {path.name} (already applied)")
                continue

            print(f"apply   {path.name}")
            with conn.transaction():
                conn.execute(path.read_text())
                conn.execute(
                    "INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,)
                )
            print(f"done    {path.name}")


if __name__ == "__main__":
    main()
