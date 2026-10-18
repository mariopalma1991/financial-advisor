"""The response envelope every MCP tool returns.

Agents must cite a number as `<ref>#<field>`: the ref names the view and the key
of the row the number came from, so a validator (no LLM) can look it up again.
Numbers computed in code (returns, volatility, trends) get a `calc:` ref naming
the computation and the rows it read.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Any

SCHEMA_VERSION = "1.0"

# One cut-off per server process: every member of a committee talks to the same
# server, so they all report the same snapshot. The runner can pin it.
_SNAPSHOT = os.getenv("MCP_SNAPSHOT") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class At:
    """The moment a tool reads "as of": `ts` for timestamp clocks (filed_at,
    valid_from), `day` for date clocks (bar_date, period). past=False means now."""
    ts: datetime
    day: date
    past: bool

    @property
    def iso(self) -> str:
        return self.ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_as_of(as_of: str | None) -> At:
    """None -> now. 'YYYY-MM-DD' -> the end of that day (UTC), i.e. what was known
    after that session closed. A full ISO timestamp is taken as given (UTC if naive)."""
    now = datetime.now(timezone.utc)
    if not as_of:
        return At(now, now.date(), False)
    text = as_of.strip()
    try:
        if len(text) == 10:
            ts = datetime.combine(date.fromisoformat(text), time(23, 59, 59), timezone.utc)
        else:
            ts = datetime.fromisoformat(text)
            ts = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
    except ValueError:
        raise ValueError(f"as_of must be YYYY-MM-DD or an ISO timestamp, got {as_of!r}") from None
    if ts >= now:
        return At(now, now.date(), False)  # today or later reads the latest data
    return At(ts, ts.date(), True)


def snapshot_id(at: At | None = None) -> str:
    return f"snap_{at.iso}" if at and at.past else f"snap_{_SNAPSHOT}"


def row_ref(view: str, *key: Any) -> str:
    return f"{view}:" + "|".join(str(k) for k in key)


def with_ref(row: dict | None, view: str, *key_cols: str) -> dict | None:
    """Copy of a row with its `_ref`, built from the row's own key columns."""
    if row is None:
        return None
    return {"_ref": row_ref(view, *(row.get(c) for c in key_cols)), **row}


def rows_with_ref(rows: list[dict], view: str, *key_cols: str) -> list[dict]:
    return [with_ref(r, view, *key_cols) for r in rows]


class Envelope:
    def __init__(self, tool: str, ticker: str | None = None, at: At | None = None):
        self.tool = tool
        self.ticker = ticker.upper() if ticker else None
        self.at = at or parse_as_of(None)
        self.data: dict[str, Any] = {}
        self.missing: list[str] = []
        self.notes: list[str] = []

    def put(self, name: str, value: Any, *, required: bool = True) -> None:
        """Store a section; an empty required section is listed under missing."""
        self.data[name] = value
        if required and (value is None or value == [] or value == {}):
            self.missing.append(name)

    def note(self, text: str) -> None:
        self.notes.append(text)

    def out(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "tool": self.tool,
            "schema_version": SCHEMA_VERSION,
            "snapshot_id": snapshot_id(self.at),
        }
        if self.at.past:
            out["as_of"] = self.at.iso
        if self.ticker:
            out["ticker"] = self.ticker
        out["data"] = self.data
        out["data_status"] = {"complete": not self.missing, "missing": self.missing}
        if self.notes:
            out["notes"] = self.notes
        out["citation_rule"] = "Cite every number as <_ref>#<field>; say 'sin datos' for anything missing."
        return out


def error(tool: str, code: str, message: str, ticker: str | None = None,
          at: At | None = None) -> dict[str, Any]:
    return {"tool": tool, "ticker": ticker, "snapshot_id": snapshot_id(at),
            "error": {"code": code, "message": message}}
