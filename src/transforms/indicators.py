"""Swing indicator set on daily bars (plan §7). Pure pandas — no C extensions.

Input bars must be adjusted (see transforms/adjustments.py): indicators computed
across an unadjusted split are garbage.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def sma(s: pd.Series, w: int) -> pd.Series:
    return s.rolling(w).mean()


def ema(s: pd.Series, w: int) -> pd.Series:
    return s.ewm(span=w, adjust=False).mean()


def rsi_wilder(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss  # avg_loss == 0 -> inf -> RSI 100 (correct)
    rsi = 100 - 100 / (1 + rs)
    return rsi.mask((avg_gain == 0) & (avg_loss == 0), 50.0)  # flat series -> neutral


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False).mean()
    return line, sig, line - sig


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()  # Wilder


def bollinger(close: pd.Series, window: int = 20, num_std: float = 2.0):
    mid = sma(close, window)
    sd = close.rolling(window).std()
    return mid + num_std * sd, mid, mid - num_std * sd


def compute_indicators(
    bars: pd.DataFrame, benchmark_close: pd.Series | None, cfg: dict[str, Any]
) -> pd.DataFrame:
    """Compute the full indicator set.

    bars: ascending by date; columns open/high/low/close/volume (adjusted).
    benchmark_close: benchmark close series (for relative strength), any index;
                     reindexed to bars with forward-fill.
    cfg: the 'indicators' section of config/platform.yaml.
    """
    close, high, low, volume = bars["close"], bars["high"], bars["low"], bars["volume"]
    out = pd.DataFrame(index=bars.index)

    for w in cfg["sma_windows"]:
        out[f"sma_{w}"] = sma(close, w)
    for w in cfg["ema_windows"]:
        out[f"ema_{w}"] = ema(close, w)

    out["rsi_14"] = rsi_wilder(close, int(cfg["rsi_period"]))

    line, sig, hist = macd(close, **cfg["macd"])
    out["macd"], out["macd_signal"], out["macd_hist"] = line, sig, hist

    out["atr_14"] = atr(high, low, close, int(cfg["atr_period"]))

    bb_u, bb_m, bb_l = bollinger(close, **cfg["bollinger"])
    out["bb_upper"], out["bb_middle"], out["bb_lower"] = bb_u, bb_m, bb_l

    out["volume_ma_20"] = volume.rolling(int(cfg["volume_ma_window"])).mean()

    w = int(cfg["relative_strength_window_days"])
    if benchmark_close is not None and not benchmark_close.empty:
        bench = benchmark_close.reindex(bars.index).ffill()
        out["rel_strength_21d"] = close.pct_change(w) - bench.pct_change(w)
    else:
        out["rel_strength_21d"] = np.nan

    return out
