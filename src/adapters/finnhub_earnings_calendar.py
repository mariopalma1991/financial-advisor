"""Finnhub earnings calendar adapter — upcoming and historical earnings releases.

Finnhub's /calendar/earnings endpoint returns EPS and revenue actuals/estimates
with bmo/amc timing. Free tier: 1 month of historical + new updates.

Rate limit: 30 calls/min on our Finnhub plan (shared across all Finnhub adapters).
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any

import finnhub
from dotenv import load_dotenv

from src.adapters.base import RawEarningsCalendar, source_retry

_load_dotenv_done = False


def _ensure_dotenv() -> None:
    global _load_dotenv_done
    if not _load_dotenv_done:
        from pathlib import Path
        repo_root = Path(__file__).resolve().parent.parent.parent
        load_dotenv(repo_root / ".env")
        _load_dotenv_done = True


def _finnhub_client() -> finnhub.Client:
    _ensure_dotenv()
    api_key = os.getenv("FINNHUB_API_KEY", "")
    if not api_key:
        raise RuntimeError("FINNHUB_API_KEY not set.")
    return finnhub.Client(api_key=api_key)


class FinnhubEarningsCalendarAdapter:
    """Fetches earnings calendar from Finnhub.

    Usage:
        adapter = FinnhubEarningsCalendarAdapter()
        events = adapter.fetch(_from="2026-10-01", to="2026-10-31")
    """

    name = "finnhub"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.getenv("FINNHUB_API_KEY", "")

    @source_retry
    def _fetch(
        self, _from: str, to: str, symbol: str | None = None
    ) -> dict[str, Any]:
        client = (
            _finnhub_client()
            if not self._api_key
            else finnhub.Client(api_key=self._api_key)
        )
        # finnhub-python requires `symbol`; an empty string means the whole market.
        return client.earnings_calendar(_from=_from, to=to, symbol=symbol or "")

    def fetch(
        self,
        _from: str,
        to: str,
        symbols: list[str] | None = None,
        strict: bool = False,
    ) -> list[RawEarningsCalendar]:
        """Fetch earnings calendar events.

        Args:
            _from: Start date YYYY-MM-DD.
            to: End date YYYY-MM-DD.
            symbols: Optional list of tickers to filter. If None, fetches all.
            strict: Raise fetch errors instead of skipping the symbol.

        Returns:
            List of RawEarningsCalendar objects.
        """
        events: list[RawEarningsCalendar] = []

        if symbols:
            for symbol in symbols:
                try:
                    raw = self._fetch(_from=_from, to=to, symbol=symbol)
                except Exception:
                    if strict:
                        raise
                    continue
                events.extend(self._parse(raw))
        else:
            try:
                raw = self._fetch(_from=_from, to=to)
            except Exception:
                if strict:
                    raise
                return events
            events.extend(self._parse(raw))

        return events

    def _parse(self, raw: dict[str, Any]) -> list[RawEarningsCalendar]:
        events: list[RawEarningsCalendar] = []
        for item in raw.get("earningsCalendar", []):
            date_str = item.get("date", "")
            event_date = (
                date.fromisoformat(date_str) if date_str else date.today()
            )

            events.append(
                RawEarningsCalendar(
                    source=self.name,
                    ticker=item.get("symbol", ""),
                    date=event_date,
                    quarter=item.get("quarter", 0),
                    year=item.get("year", 0),
                    hour=item.get("hour", ""),
                    eps_actual=item.get("epsActual"),
                    eps_estimate=item.get("epsEstimate"),
                    revenue_actual=item.get("revenueActual"),
                    revenue_estimate=item.get("revenueEstimate"),
                    payload={},
                )
            )
        return events