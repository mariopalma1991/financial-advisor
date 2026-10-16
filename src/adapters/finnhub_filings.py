"""Finnhub SEC filings adapter — fetches filing metadata from Finnhub API.

Finnhub's /stock/filings endpoint returns SEC filing metadata (8-K, 10-K,
10-Q, etc.) for US-listed companies. Same 30 calls/min plan limit.

Returns RawFiling objects for the ingest_filings pipeline.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any

import finnhub
from dotenv import load_dotenv

from src.adapters.base import RawFiling, source_retry

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
        raise RuntimeError("FINNHUB_API_KEY not set. Add it to .env or export it.")
    return finnhub.Client(api_key=api_key)


def _parse_filing_date(date_str: str) -> datetime:
    """Parse Finnhub filing date string to UTC datetime."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)


class FinnhubFilingsAdapter:
    """Fetches SEC filing metadata from Finnhub.

    Usage:
        adapter = FinnhubFilingsAdapter()
        filings = adapter.fetch_filings(["AAPL", "MSFT"], _from="2026-01-01")
    """

    name = "finnhub"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.getenv("FINNHUB_API_KEY", "")

    @source_retry
    def _fetch_one(
        self, symbol: str, _from: str, to: str
    ) -> list[dict[str, Any]]:
        """Fetch filings for one symbol. Returns raw Finnhub dicts."""
        client = (
            _finnhub_client()
            if not self._api_key
            else finnhub.Client(api_key=self._api_key)
        )
        return client.filings(symbol=symbol, _from=_from, to=to)

    def fetch_filings(
        self,
        symbols: list[str],
        _from: str | None = None,
        to: str | None = None,
        forms: tuple[str, ...] = ("10-K", "10-Q", "8-K"),
        strict: bool = False,
    ) -> list[RawFiling]:
        """Fetch SEC filings for multiple symbols.

        Args:
            symbols: List of ticker symbols.
            _from: Start date YYYY-MM-DD. Defaults to 30 days ago.
            to: End date YYYY-MM-DD. Defaults to today.
            forms: Filter to these form types only.
            strict: Raise fetch errors instead of skipping the symbol.

        Returns:
            List of RawFiling objects.
        """
        if _from is None:
            from datetime import timedelta
            _from = (date.today() - timedelta(days=30)).isoformat()
        if to is None:
            to = date.today().isoformat()

        all_filings: list[RawFiling] = []

        for symbol in symbols:
            try:
                raw = self._fetch_one(symbol, _from=_from, to=to)
            except Exception:
                if strict:
                    raise
                continue

            for item in raw:
                form = item.get("form", "")
                if forms and form not in forms:
                    continue

                filed_str = item.get("filedDate", "")
                filed_at = (
                    _parse_filing_date(filed_str)
                    if filed_str
                    else datetime.now(timezone.utc)
                )

                all_filings.append(
                    RawFiling(
                        source=self.name,
                        ticker=symbol,
                        accession_no=item.get("accessNumber", ""),
                        form_type=form,
                        filed_at=filed_at,
                        url=item.get("filingUrl") or item.get("reportUrl") or "",
                        payload={
                            "cik": item.get("cik"),
                            "accepted_date": item.get("acceptedDate"),
                            "report_url": item.get("reportUrl"),
                        },
                    )
                )

        return all_filings