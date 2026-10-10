"""Stooq adapter — free CSV daily bars; cross-check / fallback source.

Symbol mapping: US tickers -> lowercase + ".us" (aapl.us); BMV -> base + ".mx".
Stooq's BMV coverage is spotty: missing tickers are tolerated here and surfaced
by the quality checks (a cross-check source that silently covers less is fine;
a primary that does is not).
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from io import StringIO

import httpx
import pandas as pd
import structlog

from src.adapters.base import RawCorporateAction, RawPriceBar, source_retry

log = structlog.get_logger()

_BASE = "https://stooq.com/q/d/l/"
_BAR_CLOSE_UTC = time(23, 59)


def _stooq_symbol(symbol: str) -> str:
    if symbol.endswith(".MX"):
        return symbol[:-3].lower() + ".mx"
    return symbol.lower() + ".us"


class StooqAdapter:
    name = "stooq"

    @source_retry
    def _fetch_one(self, symbol: str, since: date) -> list[RawPriceBar]:
        params = {"s": _stooq_symbol(symbol), "d1": since.strftime("%Y%m%d"), "i": "d"}
        resp = httpx.get(_BASE, params=params, timeout=30)
        resp.raise_for_status()
        text = resp.text.strip()
        if not text or "No data" in text or text.lower().startswith("exceeded"):
            return []
        df = pd.read_csv(StringIO(text))
        if df.empty or "Close" not in df.columns:
            return []
        bars: list[RawPriceBar] = []
        for _, row in df.iterrows():
            bar_date = pd.to_datetime(row["Date"]).date()
            bars.append(
                RawPriceBar(
                    source=self.name,
                    ticker=symbol,
                    bar_date=bar_date,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=None if pd.isna(row.get("Volume")) else int(row["Volume"]),
                    valid_from=datetime.combine(bar_date, _BAR_CLOSE_UTC, tzinfo=timezone.utc),
                )
            )
        return bars

    def fetch_bars(self, tickers: list[str], since: date) -> list[RawPriceBar]:
        bars: list[RawPriceBar] = []
        for ticker in tickers:
            try:
                bars.extend(self._fetch_one(ticker, since))
            except Exception as exc:
                log.warning("stooq_fetch_failed", ticker=ticker, error=str(exc))
        return bars

    def fetch_corporate_actions(
        self, tickers: list[str], since: date
    ) -> list[RawCorporateAction]:
        return []  # Stooq does not provide corporate actions
