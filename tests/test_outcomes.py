import pytest

from src.mcp_server.tools import positions_from_trades
from src.transforms.outcomes import Series, outcome


def test_series_last_value_on_or_before():
    s = Series([("2026-01-02", 1.0), ("2026-01-05", 2.0)])
    assert s.at("2026-01-01") is None and s.at("2026-01-04") == 1.0 and s.at("2026-01-09") == 2.0


def test_outcome_in_mxn_from_next_day_close():
    days = ["2026-01-01", "2026-01-02", "2026-01-05", "2026-01-06"]
    stock = Series([("2026-01-02", 10.0), ("2026-01-06", 12.0)])
    bench = Series([("2026-01-02", 100.0), ("2026-01-06", 105.0)])
    usd_mxn = Series([("2026-01-02", 20.0), ("2026-01-06", 19.0)])
    o = outcome(days, "2026-01-01", 2, stock, bench, usd_mxn)
    assert o["entry_day"] == "2026-01-02" and o["exit_day"] == "2026-01-06"   # not the alert day's close
    assert o["ret_mxn"] == pytest.approx(12 * 19 / (10 * 20) - 1)
    assert o["excess"] == pytest.approx(o["ret_mxn"] - (105 * 19 / (100 * 20) - 1))
    assert outcome(days, "2026-01-02", 2, stock, bench, usd_mxn) is None     # horizon not reached yet


def _tr(i, t, side, day, shares, price, fees=0.0):
    return {"id": i, "ticker": t, "side": side, "trade_date": day, "shares": shares, "price_mxn": price,
            "fees_mxn": fees}


def test_positions_average_cost_splits_and_realized_gain():
    trades = [_tr(1, "A", "buy", "2026-01-02", 10, 100.0, 10.0),
              _tr(2, "A", "sell", "2026-05-04", 10, 150.0, 5.0),
              _tr(3, "B", "buy", "2026-01-02", 1, 50.0),
              _tr(4, "B", "sell", "2026-01-03", 1, 60.0)]
    book = positions_from_trades(trades, {"A": [{"ex_date": "2026-03-02", "ratio": 2.0}]})
    a = book["positions"]["A"]
    # 10 bought before a 2-for-1 split are 20 today; selling 10 leaves 10 at half the cost
    assert a["shares"] == pytest.approx(10.0) and a["cost_mxn"] == pytest.approx(505.0)
    assert a["avg_cost_mxn"] == pytest.approx(50.5) and a["trade_ids"] == [1, 2]
    assert "B" not in book["positions"]
    # A: 1,495 - 505 = 990; B: 60 - 50 = 10
    assert book["realized_by_year"]["2026"] == pytest.approx(1000.0)
