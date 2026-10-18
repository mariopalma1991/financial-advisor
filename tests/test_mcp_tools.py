from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from src.mcp_server import server, tools


def _bars(n, start=100.0, step=1.0):
    d0 = date(2025, 1, 1)
    return [{"bar_date": (d0 + timedelta(days=i)).isoformat(), "open": start + i * step,
             "high": start + i * step + 1, "low": start + i * step - 1,
             "close": start + i * step, "volume": 1000} for i in range(n)]


def test_split_adjust_divides_prices_before_ex_date():
    bars = [{"bar_date": "2026-01-01", "open": 400, "high": 400, "low": 400, "close": 400, "volume": 10},
            {"bar_date": "2026-01-02", "open": 100, "high": 100, "low": 100, "close": 100, "volume": 40}]
    adj = tools.split_adjust(bars, [{"ex_date": "2026-01-02", "ratio": 4.0}])
    assert adj[0]["close"] == 100 and adj[0]["volume"] == 40
    assert adj[1]["close"] == 100
    assert bars[0]["close"] == 400  # input untouched


def test_price_metrics_windows_and_levels():
    m = tools.price_metrics(_bars(300))
    assert m["last_close"] == 399.0
    assert m["returns"]["5d"] == round(399 / 394 - 1, 4)
    assert m["returns"]["12m"] == round(399 / 147 - 1, 4)
    assert m["max_drawdown"]["3m"] == 0.0  # steady uptrend
    assert m["range_52w"]["high"] == 400.0 and m["range_52w"]["position"] == pytest.approx(0.996, abs=1e-3)
    assert m["levels"]["support_20d"] == 379.0
    assert m["avg_dollar_volume_20d"] == round(sum(range(380, 400)) * 1000 / 20)
    assert m["realized_vol_annual"]["20d"] > 0


def test_price_metrics_drawdown_and_short_history():
    closes = [100, 120, 90, 95]
    bars = [{"bar_date": f"2026-01-0{i + 1}", "close": c, "high": c, "low": c, "volume": 1}
            for i, c in enumerate(closes)]
    m = tools.price_metrics(bars)
    assert m["max_drawdown"]["3m"] == round(90 / 120 - 1, 4)
    assert m["returns"]["1m"] is None
    assert tools.price_metrics(bars[:1]) == {}


def test_trend_of_counts_quarter_steps():
    t = tools.trend_of([0.30, 0.28, 0.29, 0.25, 0.20])  # newest first
    assert t["direction"] == "sube" and t["quarters_up"] == 3 and t["quarters_down"] == 1
    assert t["chg_vs_4q_ago"] == 0.1
    assert tools.trend_of([None, 0.1])["direction"] is None


def test_weighted_sentiment_uses_article_counts():
    daily = [{"date": "2026-10-17", "avg_sentiment": 0.5, "n_scored": 3},
             {"date": "2026-10-16", "avg_sentiment": -0.5, "n_scored": 1},
             {"date": "2026-09-01", "avg_sentiment": 1.0, "n_scored": 10}]
    w = tools.weighted_sentiment(daily, 7, date(2026, 10, 17))
    assert w == {"mean": 0.25, "n_scored": 4}
    assert tools.weighted_sentiment(daily, 1, date(2026, 10, 18)) == {"mean": None, "n_scored": 0}


def test_round_trip_cost_matches_cost_model():
    cfg = yaml.safe_load(Path("config/cost_model.yaml").read_text())
    c = tools.round_trip_cost(cfg)
    per_side = 0.0025 * 1.16 + 0.005 + 0.0005
    assert c["round_trip"] == round(2 * per_side, 5)
    assert c["min_expected_move_3x"] == round(6 * per_side, 4)
    assert tools.round_trip_cost(cfg, sic=False)["fx_spread_per_side"] == 0.0
    assert tools.round_trip_cost(cfg, amount_mxn=4_000_000)["commission_with_iva_per_side"] == round(0.0015 * 1.16, 5)


def test_every_role_lists_real_tools_and_chair_alone_gets_digest():
    roles = yaml.safe_load(Path("config/mcp_roles.yaml").read_text())["roles"]
    for role in roles:
        assert server.role_tools(role)
    for role, cfg in roles.items():
        if role not in ("presidente", "all"):
            assert "get_research_digest" not in cfg["tools"]
    with pytest.raises(SystemExit):
        server.role_tools("nadie")


def test_tool_returns_envelope_with_refs(monkeypatch):
    def one(sql, params=()):
        if "current_tickers" in sql:
            return {"ok": 1}
        if "catalysts" in sql:
            return {"ticker": "AAPL", "days_to_earnings": 12, "n_8k_30d": 2}
        return None

    monkeypatch.setattr(tools.db, "one", one)
    out = tools.get_catalysts("aapl")
    assert out["ticker"] == "AAPL" and out["snapshot_id"].startswith("snap_")
    assert out["data"]["catalysts"]["_ref"] == "catalysts:AAPL"
    assert out["data_status"] == {"complete": True, "missing": []}


def test_unknown_ticker_is_an_error(monkeypatch):
    monkeypatch.setattr(tools.db, "one", lambda sql, params=(): None)
    out = tools.get_fundamentals("NOPE")
    assert out["error"]["code"] == "unknown_ticker"


def test_missing_sections_are_reported(monkeypatch):
    monkeypatch.setattr(tools.db, "one", lambda sql, params=(): {"ok": 1} if "current_tickers" in sql else None)
    monkeypatch.setattr(tools.db, "query", lambda sql, params=(): [])
    out = tools.get_earnings("AAPL")
    assert out["data_status"]["missing"] == ["surprises"]


def test_parse_as_of_day_means_end_of_that_day():
    from src.mcp_server.envelope import parse_as_of
    at = parse_as_of("2026-10-09")
    assert at.past and at.iso == "2026-10-09T23:59:59Z" and at.day == date(2026, 10, 9)
    assert parse_as_of("2026-10-09T14:30:00-06:00").iso == "2026-10-09T20:30:00Z"
    assert not parse_as_of(None).past
    assert not parse_as_of("2999-01-01").past  # the future reads the latest data
    with pytest.raises(ValueError):
        parse_as_of("ayer")


def test_past_read_is_marked_in_envelope(monkeypatch):
    monkeypatch.setattr(tools.db, "one", lambda sql, params=(): {"ok": 1} if "current_tickers" in sql
                        else {"ticker": "AAPL", "n_8k_30d": 1})
    out = tools.get_catalysts("AAPL", as_of="2026-10-09")
    assert out["as_of"] == "2026-10-09T23:59:59Z"
    assert out["snapshot_id"] == "snap_2026-10-09T23:59:59Z"
    assert tools.get_catalysts("AAPL", as_of="nope")["error"]["code"] == "bad_as_of"


def test_parse_ref_splits_view_key_and_field():
    assert tools.parse_ref("street_latest:AAPL|2026-10-18#target_upside") == \
        ("street_latest", "AAPL|2026-10-18", "target_upside")
    assert tools.parse_ref("calc:price_metrics:AAPL|2026-10-16#returns.5d") == \
        ("calc:price_metrics", "AAPL|2026-10-16", "returns.5d")
    with pytest.raises(ValueError):
        tools.parse_ref("sin ref")


def test_matches_accepts_rounding_and_percent_form():
    assert tools._matches(3.77, 3.7712, 0.005)
    assert tools._matches(-4.1, -0.041, 0.005)
    assert not tools._matches(4.5, 3.77, 0.005)
    assert tools._matches("amc", "AMC", 0.005)


def test_resolve_refs_reruns_the_owning_tool(monkeypatch):
    calls = []

    def fake_peers(ticker, as_of=None):
        calls.append((ticker, as_of))
        return {"data": {"comparison": {"_ref": "peer_comparison:JPM", "pe_ttm": 13.45,
                                        "ratio_vs_peers": {"pe_ttm": 1.2534}}}}

    monkeypatch.setitem(tools.ALL_TOOLS, "get_peer_valuation", fake_peers)
    out = tools.resolve_refs([
        {"cite": "peer_comparison:JPM#pe_ttm", "value": 13.0},
        {"cite": "peer_comparison:JPM#ratio_vs_peers.pe_ttm", "value": 1.25},
        "peer_comparison:JPM#nope",
        "peer_comparison:MSFT#pe_ttm",
        "made_up:JPM#x",
    ], as_of="2026-10-09")
    res = out["data"]["results"]
    assert [r["status"] for r in res] == ["found", "found", "field_not_found", "ref_not_found", "unknown_view"]
    assert res[0]["match"] is False and res[1]["match"] is True  # 13.0 is 3% off; 1.25 rounds 1.2534
    assert calls == [("JPM", "2026-10-09"), ("MSFT", "2026-10-09")]  # one call per ticker
    assert out["data"]["summary"]["mismatches"] == 1


def test_resolve_refs_merges_fields_across_tools(monkeypatch):
    ref = "street_latest:AAPL|2026-10-18"
    monkeypatch.setitem(tools.ALL_TOOLS, "get_analyst_view",
                        lambda t, as_of=None: {"data": {"price_targets": {"_ref": ref, "target_mean": 329.4}}})
    monkeypatch.setitem(tools.ALL_TOOLS, "get_short_interest",
                        lambda t, as_of=None: {"data": {"short_interest": {"_ref": ref, "short_ratio": 2.82}}})
    res = tools.resolve_refs([f"{ref}#target_mean", f"{ref}#short_ratio"])["data"]["results"]
    assert [r["value"] for r in res] == [329.4, 2.82]


def test_split_adjust_leaves_already_adjusted_bars():
    bars = [{"bar_date": "2026-01-01", "close": 100, "volume": 40, "ingested_at": "2026-10-17T00:00:00"},
            {"bar_date": "2026-01-02", "close": 100, "volume": 40, "ingested_at": "2026-10-17T00:00:00"}]
    adj = tools.split_adjust(bars, [{"ex_date": "2026-01-02", "ratio": 4.0}])
    assert adj[0]["close"] == 100 and adj[0]["volume"] == 40
