"""Read-only Postgres access for the MCP tools.

One connection per server process, opened lazily. Every query runs in its own
READ ONLY transaction with a short statement timeout (set per transaction, so
it also holds behind Supabase's transaction pooler), then rolls back: a tool can
never write, and a slow query fails fast instead of stalling an agent.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg2
import psycopg2.extras

from src.config import Settings
from src.storage.supabase_client import _quote_password

_STATEMENT_TIMEOUT = "10s"
_conn = None


def _connect():
    settings = Settings.from_env()
    if not settings.supabase_db_url:
        raise RuntimeError("SUPABASE_DB_URL not set")
    return psycopg2.connect(_quote_password(settings.supabase_db_url))


def _plain(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return v


def query(sql: str, params: tuple | dict = ()) -> list[dict]:
    """Rows as plain dicts (Decimal -> float, dates -> ISO strings)."""
    global _conn
    if _conn is None or _conn.closed:
        _conn = _connect()
    try:
        with _conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(f"SET LOCAL statement_timeout = '{_STATEMENT_TIMEOUT}'")
            cur.execute(sql, params)
            return [{k: _plain(v) for k, v in row.items()} for row in cur.fetchall()]
    finally:
        _conn.rollback()


def one(sql: str, params: tuple | dict = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None
