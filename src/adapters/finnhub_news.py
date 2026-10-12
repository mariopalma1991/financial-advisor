"""Finnhub news adapter — fetches company news by symbol from Finnhub API.

Finnhub's /company-news endpoint provides headline, summary, source, URL,
and related tickers for North American companies. Free tier: 1 year of
historical news, 60 API calls/minute.

Each article gets a stable article_id (Finnhub's own `id` field, prefixed
with "fh_" for uniqueness across sources). The adapter returns
RawNewsArticle objects; it never writes to the database.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any

import finnhub
from dotenv import load_dotenv

from src.adapters.base import RawNewsArticle, source_retry

# Ensure .env is loaded even when called standalone
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
        raise RuntimeError(
            "FINNHUB_API_KEY not set. Add it to .env or export it."
        )
    return finnhub.Client(api_key=api_key)


def _parse_finnhub_ts(unix_ts: int) -> datetime:
    """Convert Finnhub UNIX timestamp to UTC datetime."""
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc)


def _make_article_id(finnhub_id: int) -> str:
    """Stable article ID: 'fh_' prefix + Finnhub's numeric ID."""
    return f"fh_{finnhub_id}"


class FinnhubNewsAdapter:
    """Fetches company-specific news from Finnhub.

    Usage:
        adapter = FinnhubNewsAdapter()
        articles = adapter.fetch("AAPL", _from="2026-10-04", to="2026-10-11")
    """

    name = "finnhub"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.getenv("FINNHUB_API_KEY", "")

    @source_retry
    def _fetch_one(
        self, symbol: str, _from: str, to: str
    ) -> list[dict[str, Any]]:
        """Fetch company news for one symbol. Returns raw Finnhub dicts."""
        client = _finnhub_client() if not self._api_key else finnhub.Client(api_key=self._api_key)
        return client.company_news(symbol, _from=_from, to=to)

    def fetch(
        self,
        symbol: str,
        _from: str | None = None,
        to: str | None = None,
    ) -> list[RawNewsArticle]:
        """Fetch news articles for a single symbol.

        Args:
            symbol: Company ticker (e.g. 'AAPL').
            _from: Start date YYYY-MM-DD. Defaults to 7 days ago.
            to: End date YYYY-MM-DD. Defaults to today.

        Returns:
            List of RawNewsArticle objects.
        """
        if _from is None:
            _from = date.today().isoformat()
        if to is None:
            from datetime import timedelta
            to = (date.today() - timedelta(days=7)).isoformat()
            # swap: _from should be earlier
            _from, to = to, _from

        raw = self._fetch_one(symbol, _from=_from, to=to)

        articles: list[RawNewsArticle] = []
        for item in raw:
            article_id = _make_article_id(item.get("id", 0))
            published_at = _parse_finnhub_ts(item.get("datetime", 0))

            articles.append(
                RawNewsArticle(
                    source=self.name,
                    article_id=article_id,
                    published_at=published_at,
                    url=item.get("url"),
                    headline=item.get("headline"),
                    body=item.get("summary"),  # Finnhub provides summary as body
                    payload={
                        "finnhub_id": item.get("id"),
                        "category": item.get("category"),
                        "image": item.get("image"),
                        "source": item.get("source"),
                        "related": item.get("related"),
                    },
                )
            )

        return articles