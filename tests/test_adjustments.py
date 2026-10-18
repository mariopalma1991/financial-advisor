"""Point-in-time adjustment tests (plan §6 critical rule)."""
from datetime import date

import pandas as pd

from src.transforms.adjustments import adjust_bars


def _bars() -> pd.DataFrame:
    idx = [date(2024, 1, d) for d in (2, 3, 4, 5)]
    return pd.DataFrame(
        {
            "open": [100.0, 100.0, 50.0, 50.0],
            "high": [101.0, 101.0, 51.0, 51.0],
            "low": [99.0, 99.0, 49.0, 49.0],
            "close": [100.0, 100.0, 50.0, 50.0],
        },
        index=idx,
    )


def _split() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ex_date": [date(2024, 1, 4)],
            "action_type": ["split"],
            "amount": [None],
            "ratio": [2.0],
        }
    )


def test_split_adjustment_halves_prior_bars():
    adj = adjust_bars(_bars(), _split())
    assert adj["close"].iloc[0] == 50.0   # pre-split bar halved
    assert adj["close"].iloc[-1] == 50.0  # post-split bar unchanged


def test_as_of_excludes_future_actions():
    adj = adjust_bars(_bars(), _split(), as_of=date(2024, 1, 3))
    assert adj["close"].iloc[0] == 100.0  # split not yet known -> no adjustment


def test_dividend_adjustment_reduces_prior_bars():
    actions = pd.DataFrame(
        {
            "ex_date": [date(2024, 1, 4)],
            "action_type": ["dividend"],
            "amount": [1.0],
            "ratio": [None],
        }
    )
    adj = adjust_bars(_bars(), actions)
    assert adj["close"].iloc[0] < 100.0   # prior bars reduced
    assert adj["close"].iloc[-1] == 50.0  # ex-date and later unchanged


def test_split_not_applied_to_bars_fetched_after_it():
    # Yahoo serves split-adjusted closes: bars stored after the split are already adjusted
    bars = _bars().assign(close=[50.0, 50.0, 50.0, 50.0], ingested_at="2024-02-01T00:00:00+00:00")
    adj = adjust_bars(bars, _split())
    assert list(adj["close"]) == [50.0, 50.0, 50.0, 50.0]
    bars.loc[date(2024, 1, 2), ["close", "ingested_at"]] = [100.0, "2024-01-02T23:00:00+00:00"]
    assert adjust_bars(bars, _split()).loc[date(2024, 1, 2), "close"] == 50.0
