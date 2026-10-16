"""job_run end events: duration, per-table summary, warning level, failure site."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.quality import run_metadata
from src.storage import supabase_client


@pytest.fixture
def events(monkeypatch, tmp_path):
    """Captured pipeline_events rows; also points the step summary at a temp file."""
    rows: list[dict] = []
    client = MagicMock()
    client.table.return_value.insert.side_effect = lambda row: rows.append(row) or MagicMock()
    monkeypatch.setattr(supabase_client, "get_client", lambda: client)
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    return rows


def test_completed_carries_duration_and_per_table_rows(events, tmp_path):
    with run_metadata.job_run("ingest_x") as em:
        em.emit("INFO", "rows_written", "raw_a", payload={"rows": 3})
        em.emit("INFO", "rows_written", "raw_b", payload={"rows": 5})

    end = events[-1]
    assert end["event_type"] == "job_completed" and end["level"] == "INFO"
    assert end["payload_json"]["summary"] == {"raw_a.rows": "3", "raw_b.rows": "5"}
    assert "duration_s" in end["payload_json"]
    summary = (tmp_path / "summary.md").read_text()
    assert "ingest_x: completed" in summary and "| raw_b.rows | 5 |" in summary


def test_completed_is_warn_when_the_run_logged_warnings(events):
    with run_metadata.job_run("ingest_x") as em:
        em.emit("WARN", "symbol_loop", "t: 0/3 symbols fetched")

    end = events[-1]
    assert end["level"] == "WARN"
    assert end["message"] == "ingest_x completed with 1 warning(s)"


def test_failed_names_exception_and_where_it_happened(events, tmp_path):
    with pytest.raises(ValueError), run_metadata.job_run("ingest_x"):
        raise ValueError("boom")

    end = events[-1]
    assert end["event_type"] == "job_failed" and end["message"] == "ValueError: boom"
    assert end["payload_json"]["exception"] == "ValueError"
    assert end["payload_json"]["where"].startswith("src/quality/run_metadata.py:")
    assert "**Error:** ValueError: boom" in (tmp_path / "summary.md").read_text()
