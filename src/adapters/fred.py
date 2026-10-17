"""FRED adapter — public CSV download (fredgraph.csv), no API key needed.

https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10&cosd=2016-01-01 returns two
columns (date, value); the header names have changed over time, so columns are read
by position. Missing observations are published as "." and skipped.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date

import httpx

from src.adapters.base import source_retry

URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


@dataclass
class MacroObservation:
    series_id: str
    obs_date: date
    value: float


def parse_csv(series_id: str, text: str) -> list[MacroObservation]:
    rows = list(csv.reader(io.StringIO(text)))
    out: list[MacroObservation] = []
    for r in rows[1:]:
        if len(r) < 2 or not r[0] or r[1] in ("", "."):
            continue
        try:
            out.append(MacroObservation(series_id, date.fromisoformat(r[0]), float(r[1])))
        except ValueError:
            continue
    return out


class FredAdapter:
    name = "fred"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=30, follow_redirects=True)

    @source_retry
    def _get(self, series_id: str, start: date) -> str:
        resp = self._client.get(URL, params={"id": series_id, "cosd": start.isoformat()})
        resp.raise_for_status()
        return resp.text

    def fetch(self, series_id: str, start: date) -> list[MacroObservation]:
        obs = parse_csv(series_id, self._get(series_id, start))
        if not obs:
            raise ValueError(f"FRED {series_id}: no observations parsed")
        return obs
