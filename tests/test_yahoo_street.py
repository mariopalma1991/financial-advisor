from datetime import date

import pytest

from src.adapters import yahoo_street
from src.adapters.yahoo_street import YahooStreetAdapter, parse_summary

SNAP = date(2026, 10, 17)


def _payload():
    return {"quoteSummary": {"result": [{
        "earningsTrend": {"trend": [
            {"period": "0q", "endDate": "2026-12-31",
             "earningsEstimate": {"avg": {"raw": 2.5}, "low": {"raw": 2.3}, "high": {"raw": 2.7},
                                  "numberOfAnalysts": {"raw": 30}, "growth": {"raw": 0.1}},
             "revenueEstimate": {"avg": {"raw": 1.2e11}},
             "epsTrend": {"current": {"raw": 2.5}, "7daysAgo": {"raw": 2.45}, "30daysAgo": {"raw": 2.4},
                          "60daysAgo": {"raw": 2.35}, "90daysAgo": {"raw": 2.0}},
             "epsRevisions": {"upLast7days": {"raw": 3}, "upLast30days": {"raw": 8},
                              "downLast7Days": {"raw": 1}, "downLast30days": {"raw": 2}}},
            {"period": "-1q", "earningsEstimate": {"avg": {"raw": 9}}},
            {"period": "+1y", "earningsEstimate": {}, "epsTrend": {}, "revenueEstimate": {}},
        ]},
        "financialData": {"targetMeanPrice": 250.0, "targetLowPrice": 200, "targetHighPrice": 300,
                          "targetMedianPrice": 255, "numberOfAnalystOpinions": 40,
                          "recommendationMean": 1.9, "currentPrice": 230},
        "defaultKeyStatistics": {"sharesShort": 1.1e8, "sharesShortPriorMonth": 1.0e8,
                                 "dateShortInterest": 1790985600, "shortPercentOfFloat": 0.007,
                                 "shortRatio": 1.8, "floatShares": 1.5e10},
        "upgradeDowngradeHistory": {"history": [
            {"epochGradeDate": 1790899200, "firm": "Firm A", "toGrade": "Buy", "fromGrade": "Hold",
             "action": "up", "priceTargetAction": "Raises", "currentPriceTarget": 270, "priorPriceTarget": 240},
            {"epochGradeDate": 1600000000, "firm": "Old Firm", "toGrade": "Sell", "action": "down"},
            {"epochGradeDate": 1790899200, "firm": "", "toGrade": "Buy"},
        ]},
    }], "error": None}}


def test_parse_summary_routes_rows_by_kind():
    rows = parse_summary("AAPL", _payload(), SNAP)
    trends = [r for r in rows if r["kind"] == "trend"]
    assert [t["period"] for t in trends] == ["0q"]  # -1q is not tracked; +1y has no values
    t = trends[0]
    assert (t["eps_current"], t["eps_90d_ago"], t["up_30d"], t["down_7d"]) == (2.5, 2.0, 8, 1)
    assert t["snapshot_date"] == "2026-10-17" and t["n_analysts"] == 30

    street = next(r for r in rows if r["kind"] == "street")
    assert street["target_mean"] == 250.0 and street["shares_short"] == 110_000_000
    assert street["short_interest_date"] == "2026-10-03"

    ratings = [r for r in rows if r["kind"] == "rating"]
    assert len(ratings) == 1  # old and firm-less entries dropped
    assert ratings[0]["firm"] == "Firm A" and ratings[0]["current_price_target"] == 270.0


def test_parse_summary_tolerates_missing_modules():
    assert parse_summary("X", {"quoteSummary": {"result": [{}]}}, SNAP) == []
    assert parse_summary("X", {"quoteSummary": {"result": []}}, SNAP) == []
    assert parse_summary("X", None, SNAP) == []


class _FakeQuote:
    def __init__(self, payload):
        self.payload = payload

    def _fetch(self, modules):
        assert "earningsTrend" in modules and "defaultKeyStatistics" in modules
        return self.payload


@pytest.mark.parametrize("payload", [None, {"quoteSummary": {"result": None, "error": {"code": "Not Found"}}}])
def test_fetch_raises_on_missing_or_error_response(monkeypatch, payload):
    monkeypatch.setattr(yahoo_street.yf, "Ticker", lambda s: type("T", (), {"_quote": _FakeQuote(payload)})())
    with pytest.raises(RuntimeError):
        YahooStreetAdapter(SNAP).fetch("ZZZZ")


def test_fetch_returns_rows(monkeypatch):
    monkeypatch.setattr(yahoo_street.yf, "Ticker", lambda s: type("T", (), {"_quote": _FakeQuote(_payload())})())
    assert len(YahooStreetAdapter(SNAP).fetch("AAPL")) == 3
