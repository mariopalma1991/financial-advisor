"""Indicator computation for both tiers, from adjusted bars.

Daily mode writes the last few days (covers missed runs). Backfill mode
(--full or via compute_and_store(full=True)) rewrites the last KEEP_DAYS.
Indicators are rebuildable derived data: a recompute overwrites (ticker, bar_date),
and rows older than KEEP_DAYS are dropped to fit the free database.
Runs after every ingest_prices run (.github/workflows/compute_indicators.yml).
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta

import pandas as pd

from src.config import load_platform
from src.quality.run_metadata import job_run
from src.storage import curated
from src.transforms.adjustments import adjust_bars
from src.transforms.indicators import compute_indicators

KEEP_DAYS = 365
# ~2 trading years: sma_200 and ema_200 need 200 bars of warm-up before the kept year.
HISTORY_BARS = 520

_INDICATOR_COLS = [
    "sma_10", "sma_20", "sma_50", "sma_200",
    "ema_10", "ema_20", "ema_50", "ema_200",
    "rsi_14", "macd", "macd_signal", "macd_hist",
    "atr_14", "bb_upper", "bb_middle", "bb_lower",
    "volume_ma_20", "rel_strength_21d",
]


def _benchmark_series(symbol: str) -> pd.Series:
    rows = curated.get_price_history(symbol, window_days=HISTORY_BARS)
    if not rows:
        return pd.Series(dtype=float)
    return pd.Series(
        {pd.to_datetime(r["bar_date"]).date(): r["close"] for r in rows}, dtype=float
    ).sort_index()


def compute_and_store(
    symbol: str, benchmark: str, cfg: dict, bench_cache: dict[str, pd.Series], full: bool = False
) -> int:
    bars_rows = curated.get_price_history(symbol, window_days=HISTORY_BARS)
    if len(bars_rows) < 30:
        return 0
    bars = pd.DataFrame(bars_rows)
    bars["bar_date"] = pd.to_datetime(bars["bar_date"]).dt.date
    bars = bars.set_index("bar_date").sort_index()

    actions_rows = curated.get_corporate_actions(symbol)
    actions = (
        pd.DataFrame(actions_rows)
        if actions_rows
        else pd.DataFrame(columns=["ex_date", "action_type", "amount", "ratio"])
    )
    if not actions.empty:
        actions["ex_date"] = pd.to_datetime(actions["ex_date"]).dt.date

    adj = adjust_bars(bars, actions)
    if benchmark not in bench_cache:
        bench_cache[benchmark] = _benchmark_series(benchmark)
    inds = compute_indicators(adj, bench_cache[benchmark], cfg)

    cutoff = date.today() - timedelta(days=KEEP_DAYS)
    tail = inds[inds.index >= cutoff] if full else inds.tail(5)
    rows = [
        {
            "ticker": symbol,
            "bar_date": str(idx),
            **{c: (None if pd.isna(row[c]) else float(row[c])) for c in _INDICATOR_COLS},
        }
        for idx, row in tail.iterrows()
    ]
    return curated.upsert_indicators(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="rewrite full history")
    args = parser.parse_args()

    with job_run("compute_indicators") as em:
        cfg = load_platform().indicators
        bench_cache: dict[str, pd.Series] = {}
        total = 0
        for t in curated.enabled_tickers():
            total += compute_and_store(t["ticker"], t["benchmark"], cfg, bench_cache, full=args.full)
        curated.prune_indicators(KEEP_DAYS)
        em.emit("INFO", "rows_written", "indicators_daily", payload={"rows": total, "full": args.full})


if __name__ == "__main__":
    main()
