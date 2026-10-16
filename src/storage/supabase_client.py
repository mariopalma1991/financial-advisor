"""Supabase access: PostgREST client for table I/O + direct Postgres for migrations.

Two access paths:
- get_client()      -> supabase-py (PostgREST over HTTPS). All reads/writes.
- pg_connection()   -> psycopg2 direct connection. Migrations only.

All queries go through the REST API (port 443) — no direct Postgres connection
needed for reads. This avoids IPv6 connectivity issues from GitHub Actions.
"""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator
from urllib.parse import quote, unquote

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


def _quote_password(url: str) -> str:
    """Percent-encode the password of a postgres URL.

    Supabase passwords may contain '@' or ':'; pasted unencoded, libpq splits the
    host at the wrong '@'. The host part never contains '@', so split at the last one.
    """
    scheme, sep, rest = url.partition("://")
    if not sep or rest.count("@") < 2:
        return url
    creds, host = rest.rsplit("@", 1)
    user, colon, password = creds.partition(":")
    if not colon:
        return url
    return f"{scheme}://{user}:{quote(unquote(password), safe='')}@{host}"


@contextmanager
def pg_connection() -> Iterator[psycopg2.extensions.connection]:
    """Direct Postgres connection — migrations only. Not used for queries."""
    settings = Settings.from_env()
    if not settings.supabase_db_url:
        raise RuntimeError(
            "SUPABASE_DB_URL not set. Copy .env.example to .env and add the "
            "direct connection string (Project Settings -> Database)."
        )
    conn = psycopg2.connect(_quote_password(settings.supabase_db_url))
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------- REST query helpers ----------

def _exec(query) -> list[dict]:
    """Execute a supabase-py query, return rows as dicts."""
    result = query.execute()
    return [dict(r) for r in (result.data or [])]


def _paginate(query, page_size: int = 1000) -> list[dict]:
    """Fetch all rows from a paginated query."""
    rows: list[dict] = []
    start = 0
    while True:
        page = _exec(query.range(start, start + page_size - 1))
        if not page:
            break
        rows.extend(page)
        if len(page) < page_size:
            break
        start += page_size
    return rows


def fetch_all(sql: str, params: tuple | None = None) -> list[dict]:
    """DEPRECATED — use the typed helpers below or call get_client() directly.

    Kept for backward compatibility during migration. Raises if called with
    complex SQL that can't be trivially translated.
    """
    raise RuntimeError(
        "fetch_all(raw SQL) is deprecated. Use the supabase-py client directly "
        "via get_client().table(...).select(...).eq(...).execute(), or use the "
        "typed helpers: query_table, query_distinct, query_aggregate."
    )
