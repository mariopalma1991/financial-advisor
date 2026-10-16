"""Structured pipeline events — the observability backbone.

Two layers (see plan, observability section):
- Verbose layer: structlog -> stdout (captured by GitHub Actions logs).
- Structured layer: significant events persisted to the pipeline_events table,
  correlated by run_id (GitHub run id in CI, a local uuid otherwise).

Rule for what becomes a persisted event: job lifecycle, per-source fetch results,
rows written per table, every quality-check outcome, every screener match.
Observability must never break the pipeline — persistence is best-effort.
"""

from __future__ import annotations

import os
import time
import traceback
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Literal

import httpx
import structlog

from src.config import Settings

log = structlog.get_logger()

_LOCAL_RUN_ID = f"local-{uuid.uuid4().hex[:12]}"

Level = Literal["INFO", "WARN", "ERROR"]


def current_run_id() -> str:
    return os.getenv("GITHUB_RUN_ID") or _LOCAL_RUN_ID


def _slack_blocks(title: str, fields: dict[str, str], color: str = "#36a64f") -> dict:
    """Build a Slack message attachment with fields."""
    return {
        "attachments": [
            {
                "color": color,
                "title": title,
                "fields": [{"title": k, "value": v, "short": True} for k, v in fields.items()],
                "footer": f"financial-advisor · {current_run_id()}",
                "ts": int(__import__("time").time()),
            }
        ]
    }


def send_alert(message: str, title: str = "🚨 Pipeline Alert") -> None:
    """Push an alert to Slack via incoming webhook."""
    settings = Settings.from_env()
    if not settings.alert_webhook_url:
        return
    try:
        payload = _slack_blocks(title, {"Message": message}, color="#ff0000")
        httpx.post(settings.alert_webhook_url, json=payload, timeout=10)
    except Exception as exc:
        log.warning("alert_delivery_failed", error=str(exc))


def send_summary(job_name: str, fields: dict[str, str], success: bool = True) -> None:
    """Send a job completion summary to Slack."""
    settings = Settings.from_env()
    if not settings.alert_webhook_url:
        return
    try:
        emoji = "✅" if success else "❌"
        color = "#36a64f" if success else "#ff0000"
        payload = _slack_blocks(
            f"{emoji} {job_name} completed",
            fields,
            color=color,
        )
        httpx.post(settings.alert_webhook_url, json=payload, timeout=10)
    except Exception as exc:
        log.warning("summary_delivery_failed", error=str(exc))


class EventEmitter:
    """Emits structured events for one job run. Tracks summary stats."""

    def __init__(self, job_name: str, client: Any | None = None) -> None:
        self.job_name = job_name
        self.run_id = current_run_id()
        self._client = client
        self._summary: dict[str, str] = {}
        self._error_count = 0
        self._warn_count = 0
        # WARN/ERROR events of this run, for the end-of-job summary.
        self._issues: list[tuple[str, str, str]] = []

    def emit(
        self,
        level: Level,
        event_type: str,
        message: str = "",
        ticker: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        row = {
            "run_id": self.run_id,
            "job_name": self.job_name,
            "level": level,
            "event_type": event_type,
            "ticker": ticker,
            "message": message,
            "payload_json": payload or {},
        }
        getattr(log, level.lower(), log.info)(
            event_type, job=self.job_name, ticker=ticker, message=message, **(payload or {})
        )
        try:
            client = self._client
            if client is None:
                from src.storage.supabase_client import get_client
                client = get_client()
            client.table("pipeline_events").insert(row).execute()
        except Exception as exc:
            log.warning("event_persist_failed", error=str(exc))

        if level == "ERROR":
            self._error_count += 1
            send_alert(message, title=f"🚨 [{self.job_name}] {event_type}")
        elif level == "WARN":
            self._warn_count += 1
        if level != "INFO":
            self._issues.append((level, event_type, message))

        # Track summary stats from rows_written events, keyed by table so two
        # tables written in one run don't overwrite each other's counts.
        if event_type == "rows_written" and payload:
            for k, v in payload.items():
                self._summary[f"{message}.{k}" if message else k] = str(v)
        if event_type == "source_fetched" and payload:
            self._summary[message] = f"{payload.get('rows', '?')} rows"


def _gha_error_annotation(job_name: str, exc: BaseException) -> None:
    """Surface the failure as a GitHub Actions annotation (visible in the run
    summary and via the checks API without downloading logs)."""
    if not os.getenv("GITHUB_ACTIONS"):
        return
    import traceback

    frames = traceback.format_exception(type(exc), exc, exc.__traceback__)
    text = "".join(frames)[-3000:]
    text = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::error title={job_name} failed::{text}", flush=True)


def _failure_site(exc: BaseException) -> str:
    """Innermost project frame of the exception, e.g. `src/storage/raw.py:50 in insert_price_bars`."""
    site = ""
    for frame in traceback.extract_tb(exc.__traceback__):
        path = frame.filename.replace("\\", "/")
        if "/src/" in path:
            site = f"src/{path.split('/src/', 1)[1]}:{frame.lineno} in {frame.name}"
    return site


def _write_step_summary(
    emitter: EventEmitter, status: str, duration_s: float, error: str | None = None
) -> None:
    """Markdown summary on the GitHub Actions run page (no log download needed)."""
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [
        f"### {emitter.job_name}: {status}",
        "",
        (
            f"Run `{emitter.run_id}` · {duration_s:.0f} s · "
            f"{emitter._warn_count} warning(s) · {emitter._error_count} error(s)"
        ),
        "",
    ]
    if error:
        lines += [f"**Error:** {error}", ""]
    if emitter._summary:
        lines += ["| Metric | Value |", "| --- | --- |"]
        lines += [f"| {k} | {v} |" for k, v in emitter._summary.items()]
        lines.append("")
    if emitter._issues:
        lines += ["| Level | Event | Message |", "| --- | --- | --- |"]
        for level, event_type, message in emitter._issues[:20]:
            lines.append(f"| {level} | {event_type} | {message.replace('|', '/')[:300]} |")
        lines.append("")
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError as exc:
        log.warning("step_summary_failed", error=str(exc))


@contextmanager
def job_run(job_name: str) -> Iterator[EventEmitter]:
    """Context manager wrapping a job with started/completed/failed events.

    `job_completed` is WARN (not INFO) when the run logged warnings, and both
    end events carry the duration and the per-table row counts.
    """
    emitter = EventEmitter(job_name)
    started = time.monotonic()
    emitter.emit("INFO", "job_started", f"{job_name} started")
    try:
        yield emitter
    except Exception as exc:
        duration_s = round(time.monotonic() - started, 1)
        error = f"{type(exc).__name__}: {exc}"[:1000]
        emitter.emit(
            "ERROR",
            "job_failed",
            error,
            payload={
                "exception": type(exc).__name__,
                "where": _failure_site(exc),
                "duration_s": duration_s,
                "summary": emitter._summary,
            },
        )
        _gha_error_annotation(job_name, exc)
        _write_step_summary(emitter, "failed", duration_s, error)
        send_summary(job_name, emitter._summary, success=False)
        raise
    else:
        duration_s = round(time.monotonic() - started, 1)
        warnings = emitter._warn_count
        emitter.emit(
            "WARN" if warnings else "INFO",
            "job_completed",
            f"{job_name} completed with {warnings} warning(s)" if warnings else f"{job_name} completed",
            payload={"duration_s": duration_s, "warnings": warnings, "summary": emitter._summary},
        )
        _write_step_summary(
            emitter, "completed with warnings" if warnings else "completed", duration_s
        )
        send_summary(job_name, emitter._summary, success=True)
