"""Rate-limited, time-budgeted per-symbol fetch loop for Finnhub jobs.

Our Finnhub plan allows 30 calls/min and its per-company endpoints take one
symbol per call, so the full curated universe (1,100+ symbols) cannot be
fetched inside a single GitHub Actions run. This loop:

- processes at most `max_symbols` per run, starting at an offset that rotates
  with GITHUB_RUN_NUMBER, so consecutive runs cover the whole universe;
- stops cleanly when `budget_s` is spent, so the job finishes (and writes what
  it fetched) instead of being killed by the workflow timeout;
- backs off for a minute on HTTP 429 and retries that symbol once;
- counts and reports errors instead of silently swallowing them;
- aborts the job on HTTP 401 (bad API key) or when every symbol failed, so a
  broken credential turns the run red instead of logging "completed".
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import structlog

from src.quality.run_metadata import EventEmitter

log = structlog.get_logger()

_RATE_LIMIT_SLEEP_S = 61


class SourceAuthError(RuntimeError):
    """The source rejected our credentials; no symbol can succeed."""


class AllSymbolsFailedError(RuntimeError):
    """Every planned symbol failed, so the run fetched nothing."""


@dataclass
class LoopResult:
    items: list[Any] = field(default_factory=list)
    processed: list[str] = field(default_factory=list)
    errors: int = 0
    rate_limited: int = 0
    stopped_by_budget: bool = False
    start_offset: int = 0


def status_code_of(exc: BaseException) -> int | None:
    """Best-effort HTTP status from a Finnhub/requests exception."""
    code = getattr(exc, "status_code", None)
    if code is None:
        resp = getattr(exc, "response", None)
        code = getattr(resp, "status_code", None)
    if isinstance(code, int):
        return code
    text = str(exc)
    for c in (429, 403, 401, 404):
        if str(c) in text:
            return c
    return None


def rotation_offset(n_symbols: int, max_symbols: int) -> int:
    """Start offset that advances by `max_symbols` on every workflow run."""
    if n_symbols == 0 or max_symbols >= n_symbols:
        return 0
    run_number = int(os.getenv("GITHUB_RUN_NUMBER") or 0)
    return (run_number * max_symbols) % n_symbols


def fetch_symbols(
    symbols: list[str],
    fetch: Callable[[str], list[Any]],
    *,
    em: EventEmitter,
    label: str,
    delay_s: float,
    max_symbols: int,
    budget_s: float,
) -> LoopResult:
    n = len(symbols)
    start = rotation_offset(n, max_symbols)
    ordered = (symbols[start:] + symbols[:start])[:max_symbols]
    res = LoopResult(start_offset=start)
    error_samples: list[str] = []
    deadline = time.monotonic() + budget_s

    for i, symbol in enumerate(ordered):
        if time.monotonic() >= deadline:
            res.stopped_by_budget = True
            break
        for attempt in (1, 2):
            try:
                res.items.extend(fetch(symbol))
                res.processed.append(symbol)
                break
            except Exception as exc:  # noqa: BLE001 — reported below
                code = status_code_of(exc)
                if code == 429 and attempt == 1:
                    res.rate_limited += 1
                    log.warning("rate_limited", source=label, symbol=symbol)
                    time.sleep(_RATE_LIMIT_SLEEP_S)
                    continue
                if code == 401:
                    raise SourceAuthError(
                        f"{label}: the API key was rejected (HTTP 401) on {symbol}. "
                        f"Check the API key secret (e.g. FINNHUB_API_KEY). "
                        f"Aborted after {len(res.processed)}/{len(ordered)} symbols."
                    ) from exc
                res.errors += 1
                if len(error_samples) < 5:
                    error_samples.append(f"{symbol}: {type(exc).__name__} {code or ''} {exc}"[:200])
                log.warning("symbol_fetch_failed", source=label, symbol=symbol, error=str(exc))
                break
        if i < len(ordered) - 1:
            time.sleep(delay_s)

    level = "WARN" if res.errors or res.stopped_by_budget else "INFO"
    if os.getenv("GITHUB_ACTIONS"):
        kind = "warning" if level == "WARN" else "notice"
        detail = f"{label}: fetched {len(res.processed)}/{len(ordered)} symbols, {len(res.items)} items, " \
            f"{res.errors} errors, {res.rate_limited} rate-limited" \
            + (", stopped by time budget" if res.stopped_by_budget else "")
        if error_samples:
            detail += " | e.g. " + " ; ".join(error_samples[:3])
        print(f"::{kind} title={label}::{detail[:3000]}", flush=True)
    em.emit(
        level,
        "symbol_loop",
        f"{label}: {len(res.processed)}/{len(ordered)} symbols fetched",
        payload={
            "universe": n,
            "planned": len(ordered),
            "fetched": len(res.processed),
            "start_offset": start,
            "errors": res.errors,
            "rate_limited": res.rate_limited,
            "stopped_by_budget": res.stopped_by_budget,
            "error_samples": error_samples,
        },
    )
    if ordered and not res.processed and res.errors:
        raise AllSymbolsFailedError(
            f"{label}: all {res.errors} attempted symbols failed; "
            f"e.g. {error_samples[0] if error_samples else 'no sample'}"
        )
    return res
