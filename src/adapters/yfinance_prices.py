"""yfinance adapter — primary source for daily bars and corporate actions.

Raw OHLC only (auto_adjust=False): adjustments are computed at feed-build time
as of the as_of date — storing pre-adjusted prices would inject look-ahead bias
(plan §6 critical rule).
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone

import pandas as pd
import yfinance as yf

from src.adapters.base import RawCorporateAction, RawPriceBar, source_retry

# Conservative: a daily bar is complete by end of its date (UTC).
_BAR_CLOSE_UTC = time(23, 59)


def _f(x) -> float | None:
    return None if x is None or pd.isna(x) else float(x)


def _v(x) -> int | None:
    return None if x is None or pd.isna(x) else int(x)


class YFinanceAdapter:
    name = "yfinance"

    @source_retry
    def fetch_bars(self, tickers: list[str], since: date) -> list[RawPriceBar]:
        if not tickers:
            return []
        df = yf.download(
            tickers=" ".join(tickers),
            start=since.isoformat(),
            auto_adjust=False,
            group_by="ticker",
            progress=False,
            threads=True,
        )
        if df is None or df.empty:
            return []

        bars: list[RawPriceBar] = []
        for ticker in tickers:
            try:
                sub = df[ticker] if isinstance(df.columns, pd.MultiIndex) else df
            except KeyError:
                continue
            sub = sub[sub["Close"].notna()]
            bar_dates = pd.to_datetime(sub.index).date
            for bar_date, (_, row) in zip(bar_dates, sub.iterrows()):
                bars.append(
                    RawPriceBar(
                        source=self.name,
                        ticker=ticker,
                        bar_date=bar_date,
                        open=_f(row.get("Open")),
                        high=_f(row.get("High")),
                        low=_f(row.get("Low")),
                        close=_f(row.get("Close")),
                        volume=_v(row.get("Volume")),
                        valid_from=datetime.combine(bar_date, _BAR_CLOSE_UTC, tzinfo=timezone.utc),
                    )
                )
        return bars

    @source_retry
    def fetch_corporate_actions(
        self, tickers: list[str], since: date
    ) -> list[RawCorporateAction]:
        actions: list[RawCorporateAction] = []
        for ticker in tickers:
            try:
                acts = yf.Ticker(ticker).actions
            except Exception:
                continue
            if acts is None or acts.empty:
                continue
            ex_dates = pd.to_datetime(acts.index).date
            for ex_date, (_, row) in zip(ex_dates, acts.iterrows()):
                if ex_date < since:
                    continue
                div = _f(row.get("Dividends")) or 0.0
                split = _f(row.get("Stock Splits")) or 0.0
                if div > 0:
                    actions.append(
                        RawCorporateAction(self.name, ticker, "dividend", ex_date, amount=div)
                    )
                if split > 0:
                    actions.append(
                        RawCorporateAction(self.name, ticker, "split", ex_date, ratio=split)
                    )
        return actions
