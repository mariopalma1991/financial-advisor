"""Indicator math sanity checks on synthetic series."""
import numpy as np
import pandas as pd

from src.transforms.indicators import atr, bollinger, compute_indicators, macd, rsi_wilder, sma

_CFG = {
    "sma_windows": [10, 20, 50, 200],
    "ema_windows": [10, 20, 50, 200],
    "rsi_period": 14,
    "macd": {"fast": 12, "slow": 26, "signal": 9},
    "atr_period": 14,
    "bollinger": {"window": 20, "num_std": 2.0},
    "volume_ma_window": 20,
    "relative_strength_window_days": 21,
}


def _trending_close(n: int = 300) -> pd.Series:
    return pd.Series(np.linspace(100, 200, n) + np.sin(np.arange(n)) * 2)


def test_sma():
    s = pd.Series([1.0, 2.0, 3.0, 4.0])
    assert sma(s, 2).iloc[-1] == 3.5


def test_rsi_bounds_and_uptrend():
    rsi = rsi_wilder(_trending_close(), 14)
    assert rsi.dropna().between(0, 100).all()
    assert rsi.iloc[-1] > 50  # uptrend


def test_rsi_all_gains_is_100():
    rsi = rsi_wilder(pd.Series(range(1, 100), dtype=float), 14)
    assert rsi.iloc[-1] == 100.0


def test_rsi_flat_is_50():
    rsi = rsi_wilder(pd.Series([100.0] * 50), 14)
    assert rsi.iloc[-1] == 50.0


def test_macd_hist_is_line_minus_signal():
    close = _trending_close()
    line, sig, hist = macd(close)
    assert abs(hist.iloc[-1] - (line.iloc[-1] - sig.iloc[-1])) < 1e-9


def test_atr_positive():
    n = 100
    high = pd.Series(np.linspace(101, 150, n))
    low = high - 2
    close = high - 1
    assert (atr(high, low, close, 14).dropna() > 0).all()


def test_bollinger_ordering():
    upper, mid, lower = bollinger(_trending_close())
    assert (upper.dropna() >= mid.dropna()).all()
    assert (mid.dropna() >= lower.dropna()).all()


def test_compute_indicators_columns():
    n = 300
    bars = pd.DataFrame(
        {
            "open": np.linspace(100, 150, n),
            "high": np.linspace(101, 151, n),
            "low": np.linspace(99, 149, n),
            "close": np.linspace(100, 150, n),
            "volume": np.full(n, 1_000_000.0),
        },
        index=pd.date_range("2024-01-01", periods=n).date,
    )
    out = compute_indicators(bars, None, _CFG)
    for col in ("sma_50", "sma_200", "rsi_14", "macd", "atr_14", "bb_upper", "volume_ma_20"):
        assert col in out.columns
    assert out["rel_strength_21d"].isna().all()  # no benchmark provided
