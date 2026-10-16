"""Insider sentiment: adapter parsing and feed summary windows."""
from src.adapters.finnhub_insider_sentiment import FinnhubInsiderSentimentAdapter
from src.transforms.insider_sentiment import summarize


def test_adapter_parses_monthly_rows(monkeypatch):
    adapter = FinnhubInsiderSentimentAdapter(api_key="x")
    payload = {
        "symbol": "AAPL",
        "data": [
            {"symbol": "AAPL", "year": 2026, "month": 8, "change": -1500, "mspr": -42.5},
            {"symbol": "AAPL", "year": 2026, "month": 9, "change": 300, "mspr": 12},
            {"symbol": "AAPL", "year": None, "month": 9, "change": 1, "mspr": 1},
        ],
    }
    monkeypatch.setattr(adapter, "_fetch_one", lambda symbol, _from, to: payload)
    rows = adapter.fetch(["AAPL"], _from="2025-01-01", to="2026-10-16")
    assert [(r.year, r.month, r.change, r.mspr) for r in rows] == [
        (2026, 8, -1500.0, -42.5),
        (2026, 9, 300.0, 12.0),
    ]


def test_summarize_windows():
    rows = [
        {"period": f"2026-{m:02d}-01", "mspr": mspr, "change": change}
        for m, mspr, change in [
            (4, 10, 100), (5, -20, -200), (6, None, None),
            (7, 30, 300), (8, -40, -400), (9, 50, 500),
        ]
    ]
    out = summarize(rows)
    assert out["latest_month"] == "2026-09"
    assert out["latest_mspr"] == 50
    assert out["windows"]["3m"] == {
        "mspr_avg": 13.33, "net_change": 400, "months_net_buying": 2, "months": 3,
    }
    assert out["windows"]["6m"]["mspr_avg"] == 6.0
    assert out["windows"]["6m"]["months"] == 6
    assert len(out["monthly"]) == 6


def test_summarize_empty():
    assert summarize([]) == {}
