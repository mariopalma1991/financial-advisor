"""Supabase access: PostgREST client for table I/O + direct Postgres for SQL.

Two access paths on purpose:
- get_client()      -> supabase-py (PostgREST). Simple inserts/selects on tables.
- pg_connection()   -> psycopg2 direct connection. Migrations, views, and complex
                       as_of queries that PostgREST expresses awkwardly.
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

import psycopg2
import psycopg2.extras
from supabase import Client, create_client

from src.config import Settings


@lru_cache(maxsize=1)
def get_client() -> Client:
    """Process-wide PostgREST client (service role — server-side jobs only)."""
    settings = Settings.from_env()
    settings.require_supabase()
    return create_client(settings.supabase_url, settings.supabase_service_key)


@contextmanager
def pg_connection() -> Iterator[psycopg2.extensions.connection]:
    """Direct Postgres connection with commit/rollback handling."""
    settings = Settings.from_env()
    if not settings.supabase_db_url:
        raise RuntimeError(
            "SUPABASE_DB_URL not set. Copy .env.example to .env and add the "
            "direct connection string (Project Settings -> Database)."
        )
    conn = psycopg2.connect(settings.supabase_db_url)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fetch_all(sql: str, params: tuple | None = None) -> list[dict]:
    """Run a read query and return rows as dicts."""
    with pg_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or ())
            return [dict(r) for r in cur.fetchall()]
