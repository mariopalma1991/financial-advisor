import math

import pandas as pd
import pytest

from src.transforms import factors as fx


def _bars(closes, start="2025-01-01"):
    d0 = pd.Timestamp(start)
    return [{"bar_date": (d0 + pd.Timedelta(days=i)).date().isoformat(), "close": c, "volume": 1000}
            for i, c in enumerate(closes)]


def test_split_adjust_keeps_returns_continuous():
    bars = _bars([400, 400, 100, 100])
    closes = fx.adjust_for_splits(bars, [{"ex_date": bars[2]["bar_date"], "ratio": 4.0}])
    assert closes == [100, 100, 100, 100]


def test_momentum_skips_last_month():
    closes = [100.0] * 231 + [150.0] * 21 + [999.0] * 21  # last month jump is ignored
    m = fx.momentum_12_1(closes)
    assert m == pytest.approx(150 / 100 - 1)
    assert fx.momentum_12_1(closes[:252]) is None


def test_window_return_around_report():
    bars = _bars([10, 10, 12, 13])
    assert fx.window_return(bars, [b["close"] for b in bars], bars[2]["bar_date"]) == pytest.approx(0.3)
    assert fx.window_return(bars, [b["close"] for b in bars], bars[3]["bar_date"]) is None


def test_revision_signals_uses_year_periods_only():
    rows = [
        {"period": "0y", "eps_current": 1.1, "eps_30d_ago": 1.0, "eps_90d_ago": 1.0,
         "n_analysts": 10, "up_30d": 3, "down_30d": 1},
        {"period": "+1y", "eps_current": 2.0, "eps_30d_ago": 2.0, "eps_90d_ago": 2.0,
         "n_analysts": 10, "up_30d": 0, "down_30d": 0},
        {"period": "0q", "eps_current": 9, "eps_30d_ago": 1, "n_analysts": 1, "up_30d": 1},
    ]
    chg, breadth = fx.revision_signals(rows)
    assert chg == pytest.approx(0.05) and breadth == pytest.approx(0.1)
    assert fx.revision_signals([]) == (None, None)


def test_quality_inputs_margin_stability():
    q = [{"roe_ttm": 0.2, "gross_margin": 0.5, "debt_to_equity": 1.0, "operating_margin": m}
         for m in (0.2, 0.2, 0.2, 0.2)]
    out = fx.quality_inputs(q)
    assert out["op_margin_std"] == 0 and out["roe_ttm"] == 0.2
    assert fx.quality_inputs(q[:3])["op_margin_std"] is None


def test_group_z_within_industry_and_pooling():
    vals = pd.Series([1.0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 100.0, 200.0])
    groups = pd.Series(["a"] * 10 + ["tiny"] * 2)
    z = fx.group_z(vals, groups)
    assert z.abs().max() <= fx.Z_CAP
    # the two-member industry is pooled with the rest, not scored on its own
    assert z.iloc[-1] > z.iloc[0]
    assert math.isnan(fx.group_z(pd.Series([None, 1.0]), pd.Series(["a", "a"])).iloc[0])


def test_score_frame_ranks_eligible_and_missing_is_neutral():
    n = 12
    df = pd.DataFrame({
        "ticker": [f"T{i}" for i in range(n)], "industry": ["x"] * n, "eligible": [True] * (n - 1) + [False],
        "mom_12_1": [i / 10 for i in range(n)], "rev_chg": [None] * n, "rev_breadth": [None] * n,
        "tgt_chg": [None] * n, "rating_breadth": [None] * n,
        "sue": [None] * n, "ear": [None] * n, "insider_raw": [0.0] * n,
        "roe_ttm": [None] * n, "gross_margin": [None] * n, "debt_to_equity": [None] * n, "op_margin_std": [None] * n,
    })
    out = fx.score_frame(df)
    el = out[out["eligible"]]
    assert el.sort_values("rank").iloc[0]["ticker"] == "T10"  # best momentum among eligible
    assert pd.isna(out.iloc[-1]["score"])                       # ineligible is not ranked
    assert set(el["n_factors"]) == {2}                          # momentum + insider (all zero)
    assert el["z_insider"].eq(0).all()


def test_insider_is_a_fixed_scale_not_a_z_score():
    n = 12
    df = pd.DataFrame({
        "ticker": [f"T{i}" for i in range(n)], "industry": ["x"] * n, "eligible": [True] * n,
        "mom_12_1": [None] * n, "rev_chg": [None] * n, "rev_breadth": [None] * n, "sue": [None] * n,
        "tgt_chg": [None] * n, "rating_breadth": [None] * n,
        "ear": [None] * n, "insider_raw": [fx.insider_signal(1, 0)] + [0.0] * (n - 1),
        "roe_ttm": [None] * n, "gross_margin": [None] * n, "debt_to_equity": [None] * n, "op_margin_std": [None] * n,
    })
    out = fx.score_frame(df)
    assert out.iloc[0]["z_insider"] == 0.5 and out.iloc[1]["z_insider"] == 0.0


def test_split_skips_bars_fetched_after_it():
    bars = _bars([100, 100, 100])
    for b in bars:
        b["ingested_at"] = "2026-10-17T00:00:00+00:00"  # fetched after the split: already adjusted
    split = [{"ex_date": bars[1]["bar_date"], "ratio": 4.0}]
    assert fx.adjust_for_splits(bars, split) == [100, 100, 100]
    bars[0]["ingested_at"] = "2024-12-31T00:00:00+00:00"  # stored before the split: raw
    bars[0]["close"] = 400
    assert fx.adjust_for_splits(bars, split) == [100, 100, 100]


def test_rating_signals_need_three_actions():
    few = [{"action": "up", "current_price_target": 120.0, "prior_price_target": 100.0}] * 2
    assert fx.rating_signals(few) == {"tgt_chg": None, "rating_breadth": None, "n_rating_actions": 2}
    rows = few + [{"action": "down", "current_price_target": 50.0, "prior_price_target": 200.0},
                  {"action": "main", "current_price_target": None, "prior_price_target": None}]
    r = fx.rating_signals(rows)
    assert r["n_rating_actions"] == 4 and r["rating_breadth"] == 0.25      # (2 up - 1 down) / 4
    assert r["tgt_chg"] == pytest.approx((0.2 + 0.2 - 0.5) / 3)            # -75% capped at -50%


def test_revisions_use_ratings_when_estimates_are_missing():
    n = 12
    df = pd.DataFrame({
        "ticker": [f"T{i}" for i in range(n)], "industry": ["x"] * n, "eligible": [True] * n,
        "mom_12_1": [None] * n, "rev_chg": [None] * n, "rev_breadth": [None] * n,
        "tgt_chg": [i / 100 for i in range(n)], "rating_breadth": [None] * n, "sue": [None] * n,
        "ear": [None] * n, "insider_raw": [None] * n,
        "roe_ttm": [None] * n, "gross_margin": [None] * n, "debt_to_equity": [None] * n, "op_margin_std": [None] * n,
    })
    out = fx.score_frame(df)
    assert out["z_revisions"].notna().all() and out.iloc[-1]["z_revisions"] > out.iloc[0]["z_revisions"]
