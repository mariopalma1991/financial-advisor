"""Finnhub company context: analyst recommendation trends, profile and peers.

/stock/recommendation returns one entry per month with the number of analysts per
rating; /stock/profile2 the company's industry, country, IPO date and share count;
/stock/peers same-industry tickers (including the ticker itself).
"""
from __future__ import annotations

import os
from datetime import date
from typing import Any

import finnhub

from src.adapters.base import source_retry
from src.adapters.finnhub_visa import _finnhub_client

_RATINGS = {"strongBuy": "strong_buy", "buy": "buy", "hold": "hold", "sell": "sell",
            "strongSell": "strong_sell"}


def _int(x: Any) -> int | None:
    try:
        return int(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _date(x: Any) -> date | None:
    try:
        return date.fromisoformat(str(x)[:10]) if x else None
    except ValueError:
        return None


def parse_recommendations(symbol: str, raw: Any) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in raw if isinstance(raw, list) else []:
        period = _date(r.get("period"))
        if period is None:
            continue
        out[period.isoformat()] = {"ticker": symbol, "period": period.isoformat(),
                                   **{col: _int(r.get(k)) for k, col in _RATINGS.items()}}
    return list(out.values())


def parse_profile(symbol: str, profile: Any, peers: Any) -> dict[str, Any] | None:
    if not isinstance(profile, dict) or not profile:
        return None
    ipo = _date(profile.get("ipo"))
    shares = profile.get("shareOutstanding")
    return {
        "ticker": symbol,
        "name": profile.get("name") or None,
        "country": profile.get("country") or None,
        "currency": profile.get("currency") or None,
        "exchange": profile.get("exchange") or None,
        "industry": profile.get("finnhubIndustry") or None,
        "ipo": ipo.isoformat() if ipo else None,
        "shares_outstanding": float(shares) if isinstance(shares, (int, float)) else None,
        "weburl": profile.get("weburl") or None,
        "peers": sorted({p for p in (peers if isinstance(peers, list) else [])
                         if isinstance(p, str) and p and p != symbol}),
    }


class FinnhubCompanyAdapter:
    name = "finnhub"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.getenv("FINNHUB_API_KEY", "")

    def _client(self) -> finnhub.Client:
        return finnhub.Client(api_key=self._api_key) if self._api_key else _finnhub_client()

    @source_retry
    def _get(self, method: str, symbol: str) -> Any:
        return getattr(self._client(), method)(symbol=symbol)

    def recommendations(self, symbol: str) -> list[dict[str, Any]]:
        return parse_recommendations(symbol, self._get("recommendation_trends", symbol))

    def profile(self, symbol: str) -> list[dict[str, Any]]:
        """Two calls (profile2 + peers); [] when Finnhub has no profile for the symbol."""
        row = parse_profile(symbol, self._get("company_profile2", symbol),
                            self._get("company_peers", symbol))
        return [row] if row else []
