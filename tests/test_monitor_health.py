"""Health checks: thresholds turn into INFO / WARN / ERROR results."""
from datetime import datetime, timedelta, timezone

from src.jobs import monitor_health as mh

NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)


class FakeCursor:
    def __init__(self, results):
        self.results = list(results)  # one fetch result per execute, in order
        self._next = None

    def execute(self, sql, params=None):
        self._next = self.results.pop(0)

    def fetchone(self):
        return self._next

    def fetchall(self):
        return self._next


def test_job_check_flags_stale_and_missing_jobs():
    cur = FakeCursor([[
        ("ingest_news", NOW - timedelta(hours=3), "completed", "https://x/1"),
        ("ingest_prices", NOW - timedelta(hours=80), "failed", "https://x/2"),
    ]])
    res = {r["name"]: r for r in mh.check_jobs(cur, {
        "ingest_news": {"max_hours": 12},
        "ingest_prices": {"max_hours": 54},
        "ingest_visa": {"max_hours": 768},
    }, NOW)}
    assert res["ingest_news"]["level"] == "INFO"
    assert res["ingest_prices"]["level"] == "ERROR" and "last run failed" in res["ingest_prices"]["message"]
    assert res["ingest_visa"]["level"] == "ERROR" and "no successful run" in res["ingest_visa"]["message"]


def test_table_check_uses_newest_row_age():
    cur = FakeCursor([(NOW - timedelta(hours=30),), (None,)])
    res = mh.check_tables(cur, {
        "raw_news_articles": {"column": "ingested_at", "max_hours": 24},
        "raw_filings": {"column": "ingested_at", "max_hours": 78},
    }, NOW)
    assert [r["level"] for r in res] == ["ERROR", "ERROR"]


def test_budget_levels():
    cfg = {"limit": 500, "warn_pct": 80, "error_pct": 95}
    assert mh._budget("database_mb", 369, cfg)["level"] == "INFO"
    assert mh._budget("database_mb", 420, cfg)["level"] == "WARN"
    assert mh._budget("database_mb", 490, cfg)["level"] == "ERROR"


def test_quality_check_errors_only_above_max_tickers():
    cur = FakeCursor([[("stale_prices", 1, ["WBD last bar 2026-10-05"]), ("bad_bar", 25, ["X"])]])
    res = {r["name"]: r for r in mh.check_quality(cur, {
        "stale_prices": {"max_tickers": 5},
        "bad_bar": {"max_tickers": 20},
        "indicators_behind": {"max_tickers": 20},
    })}
    assert res["stale_prices"]["level"] == "INFO" and res["stale_prices"]["payload"]["examples"]
    assert res["bad_bar"]["level"] == "ERROR"
    assert res["indicators_behind"]["level"] == "INFO" and res["indicators_behind"]["payload"]["tickers"] == 0
