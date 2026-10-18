"""Long texts the MCP tools read from the Parquet archive (src/storage/archive.py):
news summaries and filing press releases live there, not in Postgres. Reads are
cached per partition for the life of the server process, and any storage error
returns nothing, so a tool still answers (with a note) without archive access.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from src.storage import archive


@lru_cache(maxsize=1)
def _s3():
    return archive.s3_client()


@lru_cache(maxsize=64)
def _partition(table: str, part: str) -> tuple[dict[str, Any], ...] | None:
    try:
        return tuple(archive.read_partition(table, part, s3=_s3()).to_pylist())
    except Exception:  # noqa: BLE001 — missing partition, no credentials, network
        return None


def rows(table: str, part: str, key: str, values: set[str]) -> dict[str, dict] | None:
    """Rows of one partition whose `key` is in values, by key; None when the
    archive can't be read (so the caller can say the text is unavailable)."""
    part_rows = _partition(table, part)
    if part_rows is None:
        return None
    return {str(r.get(key)): r for r in part_rows if str(r.get(key)) in values}


def put(table: str, part: str, new_rows: list[dict[str, Any]], key: tuple[str, ...]) -> bool:
    """Cache rows the server fetched itself (e.g. 10-K risk factors); False on any error."""
    try:
        archive.upsert_partition(table, part, new_rows, key=key, s3=_s3())
        _partition.cache_clear()
        return True
    except Exception:  # noqa: BLE001
        return False
