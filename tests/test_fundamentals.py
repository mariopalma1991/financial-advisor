"""Fundamentals shaping: pivot, margins, YoY growth."""
from datetime import date, datetime

from src.adapters.edgar_filings import RawFundamentalFact
from src.transforms.fundamentals import to_quarterly_rows


def _fact(ticker: str, period: str, metric: str, value: float) -> RawFundamentalFact:
    return RawFundamentalFact(
        ticker=ticker,
        period_end=date.fromisoformat(period),
        metric=metric,
        value=value,
        form="10-Q",
        filed_at=datetime(2024, 1, 1),
    )


def test_pivot_margins_and_growth():
    facts = [
        _fact("AAPL", "2023-03-31", "revenue", 80.0),
        _fact("AAPL", "2024-03-31", "revenue", 100.0),
        _fact("AAPL", "2024-03-31", "gross_profit", 40.0),
        _fact("AAPL", "2024-03-31", "net_income", 20.0),
    ]
    rows = to_quarterly_rows(facts)
    row = next(r for r in rows if r["period_end"] == "2024-03-31")
    assert row["gross_margin"] == 0.4
    assert row["net_margin"] == 0.2
    assert row["growth_yoy"] == 0.25


def test_first_filed_value_wins():
    facts = [
        _fact("AAPL", "2024-03-31", "revenue", 100.0),
        _fact("AAPL", "2024-03-31", "revenue", 105.0),  # later restatement ignored in v1
    ]
    rows = to_quarterly_rows(facts)
    assert rows[0]["revenue"] == 100.0
