"""Sanity rules for daily bars before they reach price_bars_daily.

yfinance occasionally returns impossible values (e.g. open = 0 on a normal day, or
a missing close). Indicators and agents would read them as real moves, so:
- a bar without a positive close is dropped (it carries no usable price);
- a non-positive open/high/low becomes null (the close is still good);
- high/low are widened to contain open and close when the source got them wrong.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import replace

from src.adapters.base import RawPriceBar


def _pos(x: float | None) -> float | None:
    return x if x is not None and not math.isnan(x) and x > 0 else None


def sanitize_bars(bars: list[RawPriceBar]) -> tuple[list[RawPriceBar], Counter]:
    """Return cleaned bars and a count of each fix applied."""
    fixes: Counter = Counter()
    out: list[RawPriceBar] = []
    for b in bars:
        close = _pos(b.close)
        if close is None:
            fixes["dropped_no_close"] += 1
            continue
        o, h, lo = _pos(b.open), _pos(b.high), _pos(b.low)
        if (o, h, lo) != (b.open, b.high, b.low):
            fixes["nulled_non_positive"] += 1
        prices = [p for p in (o, close) if p is not None]
        if h is not None and h < max(prices):
            h = max(prices)
            fixes["widened_high"] += 1
        if lo is not None and lo > min(prices):
            lo = min(prices)
            fixes["widened_low"] += 1
        volume = b.volume if b.volume is not None and b.volume >= 0 else None
        out.append(replace(b, open=o, high=h, low=lo, close=close, volume=volume))
    return out, fixes
