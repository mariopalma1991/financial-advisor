"""Apply db/migrations/*.sql to Supabase, in filename order.

Usage: python scripts/apply_migration.py
Requires SUPABASE_DB_URL in .env (Project Settings -> Database -> Connection string).
"""
from __future__ import annotations

from pathlib import Path

from src.storage.supabase_client import pg_connection

MIGRATIONS = Path(__file__).resolve().parent.parent / "db" / "migrations"


def main() -> None:
    for path in sorted(MIGRATIONS.glob("*.sql")):
        print(f"applying {path.name} ...", flush=True)
        with pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(path.read_text(encoding="utf-8"))
        print("  ok")


if __name__ == "__main__":
    main()
