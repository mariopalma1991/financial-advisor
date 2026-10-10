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

from tenacity import retry, stop_after_attempt, wait_exponential


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


class PriceAdapter(Protocol):
    """Contract for price sources (yfinance, Stooq, Tiingo later...)."""

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
source_retry = retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)
