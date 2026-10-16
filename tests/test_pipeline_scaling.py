"""Regression tests for the 1,100+ ticker universe: chunked reads, rotating
time-budgeted Finnhub loop, and incremental sentiment scoring."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from src.adapters.base import RawNewsArticle, RawPriceBar
from src.jobs import _symbol_loop
from src.storage import curated
from src.transforms import news_sentiment


# ---------- _symbol_loop ----------

class _HTTPError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"HTTP {code}")
        self.status_code = code


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(_symbol_loop.time, "sleep", lambda s: None)


def test_rotation_covers_universe_across_runs(monkeypatch):
    symbols = [f"S{i}" for i in range(10)]
    seen: list[str] = []
    for run in range(3):
        monkeypatch.setenv("GITHUB_RUN_NUMBER", str(run))
        res = _symbol_loop.fetch_symbols(
            symbols, lambda s: [s], em=MagicMock(), label="t",
            delay_s=0, max_symbols=4, budget_s=60,
        )
        assert len(res.processed) == 4
        seen += res.processed
    assert set(seen) == set(symbols)


def test_errors_are_counted_not_swallowed(monkeypatch):
    monkeypatch.delenv("GITHUB_RUN_NUMBER", raising=False)

    def fetch(s):
        if s == "BAD":
            raise _HTTPError(403)
        return [s]

    em = MagicMock()
    res = _symbol_loop.fetch_symbols(
        ["A", "BAD", "C"], fetch, em=em, label="t", delay_s=0, max_symbols=10, budget_s=60
    )
    assert res.processed == ["A", "C"]
    assert res.errors == 1
    level, event, *_ = em.emit.call_args.args
    assert level == "WARN" and event == "symbol_loop"


def test_rate_limit_backs_off_and_retries_once(monkeypatch):
    monkeypatch.delenv("GITHUB_RUN_NUMBER", raising=False)
    calls = {"n": 0}

    def fetch(s):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _HTTPError(429)
        return [s]

    res = _symbol_loop.fetch_symbols(
        ["A"], fetch, em=MagicMock(), label="t", delay_s=0, max_symbols=10, budget_s=60
    )
    assert res.processed == ["A"] and res.rate_limited == 1 and res.errors == 0


def test_budget_stops_loop(monkeypatch):
    monkeypatch.delenv("GITHUB_RUN_NUMBER", raising=False)
    res = _symbol_loop.fetch_symbols(
        ["A", "B"], lambda s: [s], em=MagicMock(), label="t",
        delay_s=0, max_symbols=10, budget_s=0,
    )
    assert res.stopped_by_budget and res.processed == []


# ---------- curated reads ----------

def _fake_client(rows_by_call=None):
    client = MagicMock()
    query = MagicMock()
    client.table.return_value = query
    for m in ("select", "in_", "gte", "lte", "insert", "upsert"):
        getattr(query, m).return_value = query
    return client, query


def test_current_map_chunks_tickers_and_filters_dates(monkeypatch):
    client, query = _fake_client()
    monkeypatch.setattr(curated, "get_client", lambda: client)
    monkeypatch.setattr(curated, "_paginate", lambda q: [])

    tickers = [f"T{i}" for i in range(1141)]
    curated._current_map(
        "current_price_bars", ["ticker", "bar_date"], tickers,
        date_col="bar_date", date_range=("2026-10-12", "2026-10-15"),
    )
    chunks = [c.args[1] for c in query.in_.call_args_list]
    assert len(chunks) == 8  # ceil(1141 / 150)
    assert max(len(c) for c in chunks) <= curated._TICKER_CHUNK
    assert sorted(t for c in chunks for t in c) == sorted(tickers)
    query.gte.assert_called_with("bar_date", "2026-10-12")
    query.lte.assert_called_with("bar_date", "2026-10-15")


def test_upsert_price_bars_only_reads_window_of_new_bars(monkeypatch):
    seen = {}

    def fake_current_map(view, key_cols, tickers, *, date_col=None, date_range=None):
        seen.update(view=view, date_col=date_col, date_range=date_range)
        return {}

    monkeypatch.setattr(curated, "_current_map", fake_current_map)
    monkeypatch.setattr(curated, "_insert_chunked", lambda table, rows: None)
    bars = [
        RawPriceBar("yf", "AAPL", date(2026, 10, d), 1.0, 1.0, 1.0, 1.0, 100)
        for d in (13, 14, 15)
    ]
    assert curated.upsert_price_bars(bars) == 3
    assert seen["date_col"] == "bar_date"
    assert seen["date_range"] == ("2026-10-13", "2026-10-15")


# ---------- sentiment ----------

def _article(aid: str, headline: str) -> RawNewsArticle:
    ts = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    return RawNewsArticle(
        source="finnhub", article_id=aid, published_at=ts, url=None,
        headline=headline, body=None, payload={},
    )


def test_known_articles_are_not_rescored(monkeypatch):
    scored_texts: list[str] = []

    def fake_scores(texts):
        scored_texts.extend(texts)
        return [0.5] * len(texts)

    monkeypatch.setattr(news_sentiment, "_finbert_scores", fake_scores)
    arts = [_article("fh_1", "AAPL beats estimates"), _article("fh_2", "AAPL record sales")]
    arts.append(_article("fh_1", "AAPL beats estimates"))  # duplicate from another symbol

    rows, daily = news_sentiment.process(
        arts, ["AAPL", "MSFT"], known_scores={"fh_1": -0.2}, aggregate_tickers={"AAPL"}
    )
    assert scored_texts == ["AAPL record sales"]
    by_id = {r["article_id"]: r["sentiment_score"] for r in rows}
    assert by_id == {"fh_1": -0.2, "fh_2": 0.5}
    assert {r["ticker"] for r in daily} == {"AAPL"}


# ---------- backfill_gaps ----------

from src.jobs import backfill_gaps  # noqa: E402

_TODAY = date(2026, 10, 16)


def test_gap_start_uses_latest_minus_overlap(monkeypatch):
    monkeypatch.setattr(backfill_gaps, "latest_stored", lambda s: date(2026, 10, 10))
    start, _ = backfill_gaps.gap_start("filings", _TODAY, 30, None)
    assert start == date(2026, 10, 7)


def test_gap_start_caps_and_handles_empty(monkeypatch):
    monkeypatch.setattr(backfill_gaps, "latest_stored", lambda s: date(2025, 1, 1))
    assert backfill_gaps.gap_start("news", _TODAY, 30, None)[0] == date(2026, 9, 16)
    monkeypatch.setattr(backfill_gaps, "latest_stored", lambda s: None)
    assert backfill_gaps.gap_start("news", _TODAY, 30, None)[0] == date(2026, 9, 16)
    assert backfill_gaps.gap_start("news", _TODAY, 30, date(2026, 10, 11))[0] == date(2026, 10, 11)


def test_backfill_runs_whole_universe_and_continues_after_failure(monkeypatch):
    monkeypatch.setattr(backfill_gaps, "latest_stored", lambda s: date.today() - timedelta(days=5))
    calls = []
    monkeypatch.setattr(backfill_gaps.ingest_prices, "run", lambda em, since: calls.append(("prices", since)))

    def news(em, _from, *, max_symbols, budget_s):
        calls.append(("news", _from, max_symbols))
        raise RuntimeError("boom")

    monkeypatch.setattr(backfill_gaps.ingest_news, "run", news)
    monkeypatch.setattr(
        backfill_gaps.ingest_filings, "run",
        lambda em, _from, *, max_symbols, budget_s: calls.append(("filings", _from, max_symbols)),
    )

    class _JR:
        def __init__(self, name): ...
        def __enter__(self): return MagicMock()
        def __exit__(self, *exc): return False

    monkeypatch.setattr(backfill_gaps, "job_run", _JR)
    assert backfill_gaps.main([]) == 1
    assert [c[0] for c in calls] == ["prices", "news", "filings"]
    news_call = calls[1]
    assert news_call[1] == (date.today() - timedelta(days=6)).isoformat()
    assert news_call[2] is None and calls[2][2] is None  # whole universe


def test_backfill_dry_run_fetches_nothing(monkeypatch):
    monkeypatch.setattr(backfill_gaps, "latest_stored", lambda s: None)
    monkeypatch.setattr(backfill_gaps, "missing_tables", lambda s: [])
    monkeypatch.setattr(backfill_gaps, "_run_source", lambda *a: (_ for _ in ()).throw(AssertionError))
    assert backfill_gaps.main(["--dry-run", "--sources", "news"]) == 0


def test_backfill_detection_error_does_not_stop_other_sources(monkeypatch):
    def latest(source):
        if source == "news":
            raise RuntimeError("relation does not exist")
        return None

    monkeypatch.setattr(backfill_gaps, "latest_stored", latest)
    monkeypatch.setattr(backfill_gaps, "missing_tables", lambda s: ["news_articles (x)"] if s == "news" else [])
    ran = []
    monkeypatch.setattr(backfill_gaps, "_run_source", lambda src, *a: ran.append(src))
    assert backfill_gaps.main(["--dry-run"]) == 1
    assert ran == []
    assert backfill_gaps.main([]) == 1
    assert ran == ["prices", "filings"]
