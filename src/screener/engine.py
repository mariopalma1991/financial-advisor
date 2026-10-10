"""Screener engine — evaluates config/screens.yaml over both tiers daily.

Results (matched and unmatched, with the computed metric values) land in
screener_results — bitemporal, so screens themselves can be backtested later.
"""
from __future__ import annotations

import operator
from datetime import date
from typing import Any, Mapping

from src.config import load_screens
from src.screener.metrics import compute_metrics
from src.storage import curated

_OPS = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}


def evaluate_screen(metrics: Mapping[str, float | None], filters: list[Any]) -> bool:
    """All filters must pass; a None metric fails closed."""
    for f in filters:
        val = metrics.get(f.metric)
        if val is None:
            return False
        if not _OPS[f.op](val, f.value):
            return False
    return True


def run_screener(result_date: date, tickers: list[str] | None = None) -> list[dict[str, Any]]:
    """Bulk path: 4 queries total instead of 3 per ticker (plan: CI minutes budget)."""
    screens = load_screens().screens
    if tickers is None:
        tickers = [t["ticker"] for t in curated.enabled_tickers()]  # both tiers

    bars = curated.get_latest_bars_all()
    inds = curated.get_latest_indicators_all()
    highs = curated.get_52w_highs_all()
    funds = curated.get_fundamentals_all(quarters=5)

    results: list[dict[str, Any]] = []
    for ticker in tickers:
        if ticker not in bars or ticker not in inds:
            continue  # no data yet (e.g. screening names awaiting backfill)
        metrics = compute_metrics(bars[ticker], inds[ticker], highs.get(ticker), funds.get(ticker, []))
        for name, screen in screens.items():
            results.append(
                {
                    "result_date": result_date.isoformat(),
                    "ticker": ticker,
                    "screen_name": name,
                    "matched": evaluate_screen(metrics, screen.filters),
                    "metrics_json": {k: v for k, v in metrics.items() if v is not None},
                }
            )
    return results
