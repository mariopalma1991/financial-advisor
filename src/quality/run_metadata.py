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


def send_alert(message: str) -> None:
    """Push an alert to the configured webhook (Telegram/Discord/Slack). Optional."""
    settings = Settings.from_env()
    if not settings.alert_webhook_url:
        return
    try:
        httpx.post(
            settings.alert_webhook_url,
            json={"content": message, "text": message},
            timeout=10,
        )
    except Exception as exc:  # alerting must never break the pipeline
        log.warning("alert_delivery_failed", error=str(exc))


class EventEmitter:
    """Emits structured events for one job run."""

    def __init__(self, job_name: str, client: Any | None = None) -> None:
        self.job_name = job_name
        self.run_id = current_run_id()
        self._client = client

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
            send_alert(f"[{self.job_name}] {event_type}: {message}")


@contextmanager
def job_run(job_name: str) -> Iterator[EventEmitter]:
    """Context manager wrapping a job with started/completed/failed events."""
    emitter = EventEmitter(job_name)
    emitter.emit("INFO", "job_started", f"{job_name} started")
    try:
        yield emitter
    except Exception as exc:
        emitter.emit("ERROR", "job_failed", str(exc))
        raise
    else:
        emitter.emit("INFO", "job_completed", f"{job_name} completed")
