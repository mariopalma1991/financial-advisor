"""Source adapter contract — one adapter file per source (plan §3).

Adding a new source = one new file implementing the protocol + one YAML entry.
Adapters return raw records; they never write to the database (that is the
job of src/storage/raw.py, keeping the insert-only bitemporal discipline in
exactly one place).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential


@dataclass
class RawPriceBar:
    source: str
    ticker: str
    bar_date: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None
    payload: dict[str, Any] = field(default_factory=dict)
    valid_from: datetime | None = None  # bar close time; set by the adapter


@dataclass
class RawCorporateAction:
    source: str
    ticker: str
    action_type: str  # "dividend" | "split"
    ex_date: date
    amount: float | None = None
    ratio: float | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class RawNewsArticle:
    source: str
    article_id: str  # stable hash of url
    published_at: datetime
    url: str | None = None
    headline: str | None = None
    body: str | None = None  # full text; stored in Supabase Storage for analysis tier
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class RawFiling:
    """SEC filing metadata — shared by edgar and finnhub adapters."""
    source: str
    ticker: str
    accession_no: str
    form_type: str
    filed_at: datetime
    url: str
    payload: dict[str, Any] = field(default_factory=dict)


    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class RawFinancialReport:
    """Finnhub financials-reported — balance sheet, cash flow, income statement."""
    source: str
    ticker: str
    cik: str
    access_number: str
    form: str
    year: int
    quarter: int
    start_date: date | None
    end_date: date | None
    filed_date: datetime | None
    report: dict[str, dict[str, float]]  # {"bs": {...}, "cf": {...}, "ic": {...}}
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class RawEarningsSurprise:
    """Finnhub company-earnings — historical quarterly earnings surprises."""
    source: str
    ticker: str
    period: date
    quarter: int
    year: int
    actual: float | None
    estimate: float | None
    surprise: float | None
    surprise_pct: float | None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class RawEarningsCalendar:
    """Finnhub earnings-calendar — upcoming and historical earnings releases."""
    source: str
    ticker: str
    date: date
    quarter: int
    year: int
    hour: str  # "bmo", "amc", "dmh"
    eps_actual: float | None
    eps_estimate: float | None
    revenue_actual: float | None
    revenue_estimate: float | None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class RawVisaApplication:
    """Finnhub visa-application — H1-B and Permanent visa applications from DOL."""
    source: str
    ticker: str
    case_number: str
    job_title: str
    visa_class: str
    case_status: str
    employer_name: str
    begin_date: date | None
    end_date: date | None
    received_date: date | None
    quarter: int
    year: int
    wage_range_from: float | None
    wage_range_to: float | None
    wage_unit: str
    worksite_city: str
    worksite_state: str
    full_time: bool
    h1b_dependent: bool
    payload: dict[str, Any] = field(default_factory=dict)


class PriceAdapter(Protocol):
    """Contract for price sources (yfinance)."""

    name: str

    def fetch_bars(self, tickers: list[str], since: date) -> list[RawPriceBar]:
        """Daily OHLCV bars for the given tickers, on/after `since`."""
        ...

    def fetch_corporate_actions(
        self, tickers: list[str], since: date
    ) -> list[RawCorporateAction]:
        """Dividends and splits with ex-dates on/after `since`."""
        ...


# Shared retry policy: exponential backoff, 4 attempts, give up loudly.
def _is_retryable(exc: BaseException) -> bool:
    """Don't retry errors that won't go away: auth/plan (401/403), not found (404).
    429s are left to the caller, which knows the rate limit window."""
    code = getattr(exc, "status_code", None)
    if code is None:
        code = getattr(getattr(exc, "response", None), "status_code", None)
    return code not in (401, 403, 404, 429)


source_retry = retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)
