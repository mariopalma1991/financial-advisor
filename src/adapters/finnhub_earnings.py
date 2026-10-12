"""Finnhub earnings adapter — historical quarterly earnings surprises.

Finnhub's /stock/earnings endpoint returns actual vs estimate earnings data
going back to 2000. Free tier: last 4 quarters.

Rate limit: 60 calls/min (shared across all Finnhub adapters).
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any

import finnhub
from dotenv import load_dotenv

from src.adapters.base import RawEarningsSurprise, source_retry

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


class FinnhubEarningsAdapter:
    """Fetches historical quarterly earnings surprises from Finnhub.

    Usage:
        adapter = FinnhubEarningsAdapter()
        surprises = adapter.fetch(["AAPL", "MSFT"], limit=5)
    """

    name = "finnhub"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.getenv("FINNHUB_API_KEY", "")

    @source_retry
    def _fetch_one(self, symbol: str, limit: int) -> list[dict[str, Any]]:
        client = (
            _finnhub_client()
            if not self._api_key
            else finnhub.Client(api_key=self._api_key)
        )
        return client.company_earnings(symbol, limit=limit)

    def fetch(
        self,
        symbols: list[str],
        limit: int = 5,
    ) -> list[RawEarningsSurprise]:
        """Fetch earnings surprises for multiple symbols.

        Args:
            symbols: List of ticker symbols.
            limit: Max quarters per symbol. Default 5.

        Returns:
            List of RawEarningsSurprise objects.
        """
        surprises: list[RawEarningsSurprise] = []

        for symbol in symbols:
            try:
                raw = self._fetch_one(symbol, limit=limit)
            except Exception:
                continue

            for item in raw:
                period_str = item.get("period", "")
                period = (
                    date.fromisoformat(period_str)
                    if period_str
                    else date.today()
                )

                surprises.append(
                    RawEarningsSurprise(
                        source=self.name,
                        ticker=symbol,
                        period=period,
                        quarter=item.get("quarter", 0),
                        year=item.get("year", 0),
                        actual=item.get("actual"),
                        estimate=item.get("estimate"),
                        surprise=item.get("surprise"),
                        surprise_pct=item.get("surprisePercent"),
                        payload={},
                    )
                )

        return surprises