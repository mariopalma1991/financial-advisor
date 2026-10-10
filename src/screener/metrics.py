"""Screener metrics — computed from the latest bars/indicators/fundamentals.

Every metric used in config/screens.yaml must be produced here. None values
mean "not computable" and cause the filter to fail closed (no match).
"""
from __future__ import annotations

from typing import Any


def compute_metrics(
    latest_bar: dict[str, Any],
    latest_ind: dict[str, Any],
    high_252: float | None,
    fund_rows: list[dict[str, Any]],
) -> dict[str, float | None]:
    close = latest_bar.get("close")
    volume = latest_bar.get("volume")
    atr = latest_ind.get("atr_14")
    vol_ma = latest_ind.get("volume_ma_20")
    sma_50 = latest_ind.get("sma_50")
    sma_200 = latest_ind.get("sma_200")

    # P/E (TTM): close / sum of last 4 quarterly EPS
    eps_values = [f["eps"] for f in fund_rows[-4:] if f.get("eps")]
    eps_ttm = sum(eps_values) if len(eps_values) == 4 else None
    pe_ttm = (close / eps_ttm) if close and eps_ttm else None

    latest_fund = fund_rows[-1] if fund_rows else {}

    return {
        "rsi_14": latest_ind.get("rsi_14"),
        "atr_pct": (atr / close) if atr and close else None,
        "dollar_volume_20d": (vol_ma * close) if vol_ma and close else None,
        "volume_ratio_20d": (volume / vol_ma) if volume and vol_ma else None,
        "close_vs_sma_50": (close - sma_50) if close and sma_50 else None,
        "close_vs_sma_200": (close - sma_200) if close and sma_200 else None,
        "dist_from_sma_50_pct": (close / sma_50 - 1) if close and sma_50 else None,
        "pct_from_52w_high": (close / high_252 - 1) if close and high_252 else None,
        "pe_ttm": pe_ttm,
        "revenue_growth_yoy": latest_fund.get("growth_yoy"),
    }
