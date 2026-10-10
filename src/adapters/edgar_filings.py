"""SEC EDGAR adapter — XBRL company facts (fundamentals) + recent filing metadata.

Free, no API key; a descriptive User-Agent header is required by SEC policy.
BMV-listed names are not SEC reporters (except some ADRs filing 20-F) — they are
skipped here and surfaced as WARN events by the calling job (documented limitation).

Bitemporal note: EDGAR company facts carry a filing *date* (day granularity).
That is sufficient for quarterly fundamentals, which matter at day scale.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import httpx
import structlog

from src.adapters.base import source_retry

log = structlog.get_logger()

_DATA = "https://data.sec.gov"
_WWW = "https://www.sec.gov"
_UA = os.getenv("EDGAR_USER_AGENT", "SET_EDGAR_USER_AGENT")

# us-gaap tags we extract, mapped to our metric names (first match wins)
_TAGS: dict[str, list[str]] = {
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
    ],
    "net_income": ["NetIncomeLoss"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
    "equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "total_debt": ["LongTermDebt", "LongTermDebtNoncurrent"],
    "gross_profit": ["GrossProfit"],
}

# Flow metrics cover a duration (filter to ~quarterly windows to avoid FY/Q duplication);
# instant metrics (equity, debt) have no period start.
_FLOW_METRICS = {"revenue", "net_income", "eps", "gross_profit"}


@dataclass
class RawFundamentalFact:
    ticker: str
    period_end: date
    metric: str
    value: float
    form: str
    filed_at: datetime
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class RawFiling:
    ticker: str
    accession_no: str
    form_type: str
    filed_at: datetime
    url: str
    payload: dict[str, Any] = field(default_factory=dict)


class EdgarAdapter:
    name = "sec_edgar"

    def __init__(self) -> None:
        self._client = httpx.Client(headers={"User-Agent": _UA}, timeout=30)
        self._cik_map: dict[str, str] | None = None

    @source_retry
    def _load_cik_map(self) -> dict[str, str]:
        resp = self._client.get(f"{_WWW}/files/company_tickers.json")
        resp.raise_for_status()
        data = resp.json()
        return {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in data.values()}

    def cik_for(self, ticker: str) -> str | None:
        if self._cik_map is None:
            self._cik_map = self._load_cik_map()
        return self._cik_map.get(ticker.upper())

    # ---------- Fundamentals (XBRL company facts) ----------

    @source_retry
    def _fetch_facts(self, cik: str) -> dict[str, Any]:
        resp = self._client.get(f"{_DATA}/api/xbrl/companyfacts/CIK{cik}.json")
        resp.raise_for_status()
        return resp.json()

    def fetch_fundamentals(self, tickers: list[str]) -> list[RawFundamentalFact]:
        facts: list[RawFundamentalFact] = []
        for ticker in tickers:
            cik = self.cik_for(ticker)
            if cik is None:
                log.info("edgar_no_cik", ticker=ticker)  # non-SEC reporter (BMV names)
                continue
            try:
                data = self._fetch_facts(cik)
            except Exception as exc:
                log.warning("edgar_facts_failed", ticker=ticker, error=str(exc))
                continue
            gaap = data.get("facts", {}).get("us-gaap", {})
            for metric, tags in _TAGS.items():
                for tag in tags:
                    node = gaap.get(tag)
                    if not node:
                        continue
                    for unit, entries in node.get("units", {}).items():
                        for e in entries:
                            if e.get("form") not in ("10-Q", "10-K"):
                                continue
                            if not e.get("end") or e.get("val") is None or not e.get("filed"):
                                continue
                            is_annual = False
                            if metric in _FLOW_METRICS and e.get("start"):
                                days = (
                                    date.fromisoformat(e["end"]) - date.fromisoformat(e["start"])
                                ).days
                                if 60 <= days <= 120:
                                    pass  # quarterly
                                elif 300 <= days <= 380:
                                    is_annual = True  # FY entry — used to derive Q4 residuals
                                else:
                                    continue
                            facts.append(
                                RawFundamentalFact(
                                    ticker=ticker,
                                    period_end=date.fromisoformat(e["end"]),
                                    metric=metric,
                                    value=float(e["val"]),
                                    form=e["form"],
                                    filed_at=datetime.fromisoformat(e["filed"]),
                                    payload={
                                        "tag": tag,
                                        "unit": unit,
                                        "accn": e.get("accn"),
                                        "annual": is_annual,
                                    },
                                )
                            )
        return facts

    # ---------- Recent filing metadata (10-K / 10-Q / 8-K) ----------

    @source_retry
    def _fetch_submissions(self, cik: str) -> dict[str, Any]:
        resp = self._client.get(f"{_DATA}/submissions/CIK{cik}.json")
        resp.raise_for_status()
        return resp.json()

    def fetch_recent_filings(
        self,
        tickers: list[str],
        forms: tuple[str, ...] = ("10-K", "10-Q", "8-K"),
        limit: int = 40,
    ) -> list[RawFiling]:
        filings: list[RawFiling] = []
        for ticker in tickers:
            cik = self.cik_for(ticker)
            if cik is None:
                continue
            try:
                data = self._fetch_submissions(cik)
            except Exception as exc:
                log.warning("edgar_submissions_failed", ticker=ticker, error=str(exc))
                continue
            recent = data.get("filings", {}).get("recent", {})
            form_list = recent.get("form", [])
            descriptions = recent.get("primaryDocDescription", [])
            for i, form in enumerate(form_list[:limit]):
                if form not in forms:
                    continue
                accn = recent["accessionNumber"][i]
                doc = recent["primaryDocument"][i]
                url = f"{_WWW}/Archives/edgar/data/{int(cik)}/{accn.replace('-', '')}/{doc}"
                filings.append(
                    RawFiling(
                        ticker=ticker,
                        accession_no=accn,
                        form_type=form,
                        filed_at=datetime.fromisoformat(recent["filingDate"][i]),
                        url=url,
                        payload={
                            "description": descriptions[i] if i < len(descriptions) else "",
                        },
                    )
                )
        return filings
