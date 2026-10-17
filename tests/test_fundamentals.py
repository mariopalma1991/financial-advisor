"""Finnhub basic financials: parsing into snapshot + quarterly rows."""
from src.adapters.finnhub_basic_financials import QUARTERS_KEPT, parse
from src.jobs import ingest_fundamentals


def _series(values: dict[str, float]) -> list[dict]:
    return [{"period": p, "v": v} for p, v in sorted(values.items(), reverse=True)]


RAW = {
    "metric": {
        "peTTM": 31.2,
        "pbQuarterly": 45.0,
        "grossMarginTTM": 46.5,          # percent -> 0.465
        "revenueGrowthTTMYoy": 6.0,      # percent -> 0.06
        "totalDebt/totalEquityQuarterly": 1.5,
        "marketCapitalization": 3_500_000,
        "52WeekHigh": 260.1,
    },
    "series": {
        "quarterly": {
            "eps": _series({f"{y}-{m:02d}-30": 1.0 + y - 2023 for y in (2023, 2024, 2025, 2026) for m in (3, 6, 9)}),
            "salesPerShare": _series({"2025-06-30": 5.0, "2025-09-30": 5.5, "2026-06-30": 6.0}),
            "grossMargin": _series({"2026-06-30": 0.47}),
        }
    },
}


def test_snapshot_converts_percent_ratios_to_fractions():
    out = parse("AAPL", RAW)
    s = out.snapshot
    assert s["pe_ttm"] == 31.2 and s["pb"] == 45.0
    assert abs(s["gross_margin_ttm"] - 0.465) < 1e-9 and abs(s["revenue_growth_ttm_yoy"] - 0.06) < 1e-9
    assert s["debt_to_equity"] == 1.5  # a ratio, not a percent
    assert s["roe_ttm"] is None        # missing key -> null, not an error


def test_quarters_keep_latest_and_compute_growth_vs_year_earlier():
    out = parse("AAPL", RAW)
    assert len(out.quarters) == QUARTERS_KEPT
    assert out.quarters == sorted(out.quarters, key=lambda q: q["period_end"])
    by = {q["period_end"]: q for q in out.quarters}
    assert by["2026-06-30"]["gross_margin"] == 0.47
    assert by["2026-06-30"]["eps"] == 4.0
    assert abs(by["2026-06-30"]["growth_yoy"] - 0.2) < 1e-9   # 6.0 vs 5.0 a year earlier
    assert by["2025-09-30"]["growth_yoy"] is None             # no sales a year earlier


def test_empty_payload_is_skipped():
    assert parse("XYZ", {"metric": {}, "series": {}}) is None


def test_write_sends_snapshot_and_quarters(monkeypatch):
    calls = {}
    monkeypatch.setattr(ingest_fundamentals.curated, "upsert_fundamentals", lambda rows: calls.setdefault("q", rows) and len(rows))
    monkeypatch.setattr(ingest_fundamentals.curated, "upsert_fundamentals_snapshot", lambda rows: calls.setdefault("s", rows) and len(rows))
    item = parse("AAPL", RAW)
    assert ingest_fundamentals.write([item]) == 1
    assert calls["s"][0]["ticker"] == "AAPL" and calls["s"][0]["pe_ttm"] == 31.2
    assert len(calls["q"]) == QUARTERS_KEPT and all(q["ticker"] == "AAPL" for q in calls["q"])
    assert ingest_fundamentals.coverage([item])["pe_ttm"] == 1.0
