"""Finnhub financials adapter — financials as reported (BS, CF, IC).

Finnhub's /stock/financials-reported endpoint returns GAAP financial statements
as reported to the SEC. Supports annual and quarterly frequency.

Rate limit: 60 calls/min (shared across all Finnhub adapters).
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any

import finnhub
from dotenv import load_dotenv

from src.adapters.base import RawFinancialReport, source_retry

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


def _parse_date(d: str | None) -> date | None:
    if not d:
        return None
    return date.fromisoformat(d[:10])


def _parse_dt(d: str | None) -> datetime | None:
    if not d:
        return None
    try:
        return datetime.strptime(d, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.strptime(d[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)


class FinnhubFinancialsAdapter:
    """Fetches financials as reported from Finnhub.

    Usage:
        adapter = FinnhubFinancialsAdapter()
        reports = adapter.fetch(["AAPL"], freq="quarterly")
    """

    name = "finnhub"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.getenv("FINNHUB_API_KEY", "")

    @source_retry
    def _fetch_one(self, symbol: str, freq: str) -> dict[str, Any]:
        client = (
            _finnhub_client()
            if not self._api_key
            else finnhub.Client(api_key=self._api_key)
        )
        return client.financials_reported(symbol=symbol, freq=freq)

    def fetch(
        self,
        symbols: list[str],
        freq: str = "quarterly",
    ) -> list[RawFinancialReport]:
        """Fetch financials as reported for multiple symbols.

        Args:
            symbols: List of ticker symbols.
            freq: "annual" or "quarterly". Default "quarterly".

        Returns:
            List of RawFinancialReport objects.
        """
        reports: list[RawFinancialReport] = []

        for symbol in symbols:
            try:
                raw = self._fetch_one(symbol, freq=freq)
            except Exception:
                continue

            cik = raw.get("cik", "")
            for entry in raw.get("data", []):
                report_data = entry.get("report", {})
                if not report_data:
                    continue

                reports.append(
                    RawFinancialReport(
                        source=self.name,
                        ticker=symbol,
                        cik=cik,
                        access_number=entry.get("accessNumber", ""),
                        form=entry.get("form", ""),
                        year=entry.get("year", 0),
                        quarter=entry.get("quarter", 0),
                        start_date=_parse_date(entry.get("startDate")),
                        end_date=_parse_date(entry.get("endDate")),
                        filed_date=_parse_dt(entry.get("filedDate")),
                        report=report_data,
                        payload={
                            "accepted_date": entry.get("acceptedDate"),
                        },
                    )
                )

        return reports