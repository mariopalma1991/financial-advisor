from datetime import date

from src.adapters.finnhub_company import parse_profile, parse_recommendations
from src.adapters.fred import parse_csv


def test_fred_csv_skips_missing_and_reads_by_position():
    text = "observation_date,DGS10\n2026-10-13,4.05\n2026-10-14,.\n2026-10-15,4.10\nbad,1\n"
    obs = parse_csv("DGS10", text)
    assert [(o.obs_date, o.value) for o in obs] == [(date(2026, 10, 13), 4.05), (date(2026, 10, 15), 4.10)]
    assert all(o.series_id == "DGS10" for o in obs)


def test_recommendations_one_row_per_month():
    raw = [
        {"period": "2026-10-01", "strongBuy": 10, "buy": 20, "hold": 5, "sell": 1, "strongSell": 0},
        {"period": "2026-09-01", "strongBuy": 9, "buy": 21, "hold": 6, "sell": None, "strongSell": 0},
        {"period": None, "buy": 3},
    ]
    rows = parse_recommendations("AAPL", raw)
    assert len(rows) == 2
    oct_ = next(r for r in rows if r["period"] == "2026-10-01")
    assert oct_ == {"ticker": "AAPL", "period": "2026-10-01", "strong_buy": 10, "buy": 20,
                    "hold": 5, "sell": 1, "strong_sell": 0}
    assert parse_recommendations("X", {}) == []


def test_profile_drops_self_from_peers():
    prof = {"name": "Apple Inc", "country": "US", "finnhubIndustry": "Technology",
            "ipo": "1980-12-12", "shareOutstanding": 15000.5, "exchange": "NASDAQ"}
    row = parse_profile("AAPL", prof, ["AAPL", "MSFT", "DELL", "MSFT", ""])
    assert row["industry"] == "Technology"
    assert row["ipo"] == "1980-12-12"
    assert row["peers"] == ["DELL", "MSFT"]
    assert parse_profile("ETF", {}, []) is None
