"""sync_dim_tickers: tickers dropped from the universe get a disabled version row."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.config import Ticker, TickerUniverse
from src.storage import curated


def _ticker_row(symbol: str, enabled: bool = True) -> dict:
    return {
        "ticker": symbol, "name": symbol, "exchange": "NASDAQ", "currency": "USD",
        "sector": "Equity", "benchmark": "SPY", "tier": "analysis", "enabled": enabled,
        "tags": [], "notes": "",
    }


def test_removed_tickers_are_disabled_and_kept_ones_untouched(monkeypatch):
    universe = TickerUniverse(tickers=[Ticker(
        symbol="AAPL", name="AAPL", exchange="NASDAQ", currency="USD",
        sector="Equity", benchmark="SPY",
    )])
    monkeypatch.setattr(curated, "load_curated_universe", lambda: universe)
    current = {("AAPL",): _ticker_row("AAPL")}
    monkeypatch.setattr(curated, "_current_map", lambda *a, **k: current)
    monkeypatch.setattr(curated, "get_client", MagicMock())
    monkeypatch.setattr(curated, "_paginate", lambda q: [_ticker_row("AAPL"), _ticker_row("GOOG")])
    written: list[dict] = []
    monkeypatch.setattr(curated, "_insert_chunked", lambda table, rows: written.extend(rows))

    assert curated.sync_dim_tickers() == 1
    (row,) = written
    assert row["ticker"] == "GOOG" and row["enabled"] is False
    assert row["notes"] == "removed from curated_universe.yaml"
