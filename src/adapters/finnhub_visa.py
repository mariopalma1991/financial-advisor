"""Finnhub visa application adapter — H1-B and Permanent visa applications.

Finnhub's /stock/visa-application endpoint returns DOL visa application data
updated quarterly. Useful for tracking corporate hiring patterns.

Rate limit: 30 calls/min on our Finnhub plan (shared across all Finnhub adapters).
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any

import finnhub
from dotenv import load_dotenv

from src.adapters.base import RawVisaApplication, source_retry

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


class FinnhubVisaAdapter:
    """Fetches H1-B and Permanent visa applications from Finnhub.

    Usage:
        adapter = FinnhubVisaAdapter()
        apps = adapter.fetch(["AAPL"], _from="2025-01-01", to="2025-12-31")
    """

    name = "finnhub"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.getenv("FINNHUB_API_KEY", "")

    @source_retry
    def _fetch_one(
        self, symbol: str, _from: str, to: str
    ) -> dict[str, Any]:
        client = (
            _finnhub_client()
            if not self._api_key
            else finnhub.Client(api_key=self._api_key)
        )
        return client.stock_visa_application(symbol=symbol, _from=_from, to=to)

    def fetch(
        self,
        symbols: list[str],
        _from: str,
        to: str,
        strict: bool = False,
    ) -> list[RawVisaApplication]:
        """Fetch visa applications for multiple symbols.

        Args:
            symbols: List of ticker symbols.
            _from: Start date YYYY-MM-DD.
            to: End date YYYY-MM-DD.
            strict: Raise fetch errors instead of skipping the symbol.

        Returns:
            List of RawVisaApplication objects.
        """
        apps: list[RawVisaApplication] = []

        for symbol in symbols:
            try:
                raw = self._fetch_one(symbol, _from=_from, to=to)
            except Exception:
                if strict:
                    raise
                continue

            for item in raw.get("data", []):
                apps.append(
                    RawVisaApplication(
                        source=self.name,
                        ticker=symbol,
                        case_number=item.get("caseNumber", ""),
                        job_title=item.get("jobTitle", ""),
                        visa_class=item.get("visaClass", ""),
                        case_status=item.get("caseStatus", ""),
                        employer_name=item.get("employerName", ""),
                        begin_date=_parse_date(item.get("beginDate")),
                        end_date=_parse_date(item.get("endDate")),
                        received_date=_parse_date(item.get("receivedDate")),
                        quarter=item.get("quarter", 0),
                        year=item.get("year", 0),
                        wage_range_from=item.get("wageRangeFrom"),
                        wage_range_to=item.get("wageRangeTo"),
                        wage_unit=item.get("wageUnitOfPay", ""),
                        worksite_city=item.get("worksiteCity", ""),
                        worksite_state=item.get("worksiteState", ""),
                        full_time=item.get("fullTimePosition", False),
                        h1b_dependent=item.get("h1bDependent", False),
                        payload={
                            "soc_code": item.get("socCode"),
                            "wage_level": item.get("wageLevel"),
                            "worksite_address": item.get("worksiteAddress"),
                            "worksite_county": item.get("worksiteCounty"),
                            "worksite_postal_code": item.get("worksitePostalCode"),
                        },
                    )
                )

        return apps