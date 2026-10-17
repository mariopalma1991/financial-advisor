"""Config loading and cost-model math."""
from src.config import (
    load_analysis_universe,
    load_cost_model,
    load_feeds,
    load_platform,
    load_screening_universe,
    load_screens,
    tier_of,
)


def test_platform_loads():
    cfg = load_platform()
    assert cfg.indicators["rsi_period"] == 14
    assert "5d" in cfg.horizons["returns_windows"]
    assert cfg.quality["price_divergence_pct"] > 0


def test_universes_load_and_tiers():
    analysis = load_analysis_universe()
    screening = load_screening_universe()
    assert len(analysis.enabled()) >= 5
    assert len(screening.enabled()) >= 100
    assert tier_of("AAPL") == "analysis"
    assert tier_of("XOM") == "screening"


def test_cost_model_math():
    cost = load_cost_model()
    rt_mxn = cost.round_trip_pct("MXN")
    rt_usd = cost.round_trip_pct("USD")
    assert 0.005 < rt_mxn < 0.008  # ≈ 0.68%
    assert rt_usd > rt_mxn  # FX leg adds cost for US stocks
    assert abs(cost.min_edge_pct("MXN") - rt_mxn * 3.0) < 1e-12


def test_screens_and_feeds_load():
    assert "oversold_swing_candidate" in load_screens().screens
    assert "full" in load_feeds().feeds


def test_benchmark_symbols_are_etfs_outside_the_universe():
    from src.config import all_symbols, benchmark_symbols

    bench = benchmark_symbols()
    assert "SPY" in bench
    assert not set(bench) & set(all_symbols())
