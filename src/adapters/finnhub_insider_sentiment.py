"""Finnhub insider sentiment adapter — monthly insider buying/selling pressure.

Finnhub's /stock/insider-sentiment endpoint aggregates insider transactions
(SEC Form 4, so US-listed companies only) into one row per month:

- mspr: Monthly Share Purchase Ratio, from -100 (insiders only selling) to
  100 (insiders only buying).
- change: net number of shares bought (+) or sold (-) by insiders that month.

One call per symbol returns every month in the requested window.

Rate limit: 30 calls/min on our Finnhub plan (shared across all Finnhub adapters).
"""

from __future__ import annotations

import os
from typing import Any

import finnhub

from src.adapters.base import RawInsiderSentiment, source_retry
from src.adapters.finnhub_client import _finnhub_client


def _num(x: Any) -> float | None:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


class FinnhubInsiderSentimentAdapter:
    """Fetches monthly insider sentiment (MSPR + net share change) from Finnhub.

    Usage:
        adapter = FinnhubInsiderSentimentAdapter()
        rows = adapter.fetch(["AAPL"], _from="2024-01-01", to="2025-12-31")
    """

    name = "finnhub"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.getenv("FINNHUB_API_KEY", "")

    @source_retry
    def _fetch_one(self, symbol: str, _from: str, to: str) -> dict[str, Any]:
        client = (
            _finnhub_client()
            if not self._api_key
            else finnhub.Client(api_key=self._api_key)
        )
        return client.stock_insider_sentiment(symbol, _from, to)

    def fetch(
        self,
        symbols: list[str],
        _from: str,
        to: str,
        strict: bool = False,
    ) -> list[RawInsiderSentiment]:
        """Fetch monthly insider sentiment for multiple symbols.

        Args:
            symbols: List of ticker symbols.
            _from: Start date YYYY-MM-DD.
            to: End date YYYY-MM-DD.
            strict: Raise fetch errors instead of skipping the symbol.
        """
        out: list[RawInsiderSentiment] = []

        for symbol in symbols:
            try:
                raw = self._fetch_one(symbol, _from=_from, to=to)
            except Exception:
                if strict:
                    raise
                continue

            for item in (raw or {}).get("data", []) or []:
                year, month = item.get("year"), item.get("month")
                if not year or not month:
                    continue
                out.append(
                    RawInsiderSentiment(
                        source=self.name,
                        ticker=symbol,
                        year=int(year),
                        month=int(month),
                        change=_num(item.get("change")),
                        mspr=_num(item.get("mspr")),
                        payload={"symbol": item.get("symbol")},
                    )
                )

        return out
