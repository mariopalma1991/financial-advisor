"""compute_indicators keeps only the last KEEP_DAYS and reads raw corporate actions."""
from datetime import date, timedelta

import numpy as np

from src.jobs import compute_indicators as job
from tests.test_indicators import _CFG


def _bars(n=520):
    start = date.today() - timedelta(days=n - 1)
    close = np.linspace(50, 80, n)
    return [{"bar_date": (start + timedelta(days=i)).isoformat(), "open": c, "high": c + 1,
             "low": c - 1, "close": c, "volume": 1000} for i, c in enumerate(close)]


def test_full_writes_only_the_kept_year(monkeypatch):
    written = []
    monkeypatch.setattr(job.curated, "get_price_history", lambda s, window_days: _bars())
    monkeypatch.setattr(job.curated, "get_corporate_actions", lambda s: [])
    monkeypatch.setattr(job.curated, "upsert_indicators", lambda rows: written.extend(rows) or len(rows))

    n = job.compute_and_store("AAA", "SPY", _CFG, {}, full=True)

    cutoff = (date.today() - timedelta(days=job.KEEP_DAYS)).isoformat()
    assert n == len(written) == job.KEEP_DAYS + 1
    assert min(r["bar_date"] for r in written) >= cutoff
    last = written[-1]
    assert last["ticker"] == "AAA" and last["sma_200"] is not None
    assert all(v is None or isinstance(v, float) for k, v in last.items() if k not in ("ticker", "bar_date"))


def test_daily_writes_last_five_days(monkeypatch):
    monkeypatch.setattr(job.curated, "get_price_history", lambda s, window_days: _bars())
    monkeypatch.setattr(job.curated, "get_corporate_actions", lambda s: [])
    monkeypatch.setattr(job.curated, "upsert_indicators", len)
    assert job.compute_and_store("AAA", "SPY", _CFG, {}) == 5


def test_corporate_actions_read_from_raw_table(monkeypatch):
    seen = []
    monkeypatch.setattr(job.curated, "_pit_rows", lambda table, *a: seen.append(table) or [])
    job.curated.get_corporate_actions("AAA")
    assert seen == ["raw_corporate_actions"]


def test_financial_reports_are_written_in_small_chunks(monkeypatch):
    """One request with ~3,000 reports (100 symbols) hit the statement timeout."""
    from unittest.mock import MagicMock

    from src.adapters.base import RawFinancialReport
    from src.storage import raw

    client = MagicMock()
    monkeypatch.setattr(raw, "get_client", lambda: client)
    reports = [RawFinancialReport("finnhub", "AAA", "1", f"acc{i}", "10-Q", 2026, 1,
                                  None, None, None, {"bs": {}}) for i in range(250)]
    assert raw.insert_financial_reports(reports) == 250
    sizes = [len(c.args[0]) for c in client.table.return_value.upsert.call_args_list]
    assert sizes == [100, 100, 50]
