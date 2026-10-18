"""Smoke test of the MCP server against the real database (run from GitHub Actions).

1. Calls every tool for a few tickers and prints a short summary of each answer,
   live and as of a past date (env MCP_SMOKE_AS_OF, default a week ago).
2. Replay checks: a past read returns nothing dated after its as_of.
3. Validator check: every number in the answers is cited back through
   resolve_refs and must resolve to the same value.
4. Starts the server over stdio as a given role and checks it exposes exactly
   that role's tools, the way a committee agent would see them.
Exits non-zero if any tool raises or returns an error, or any check fails.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import date, timedelta

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from src.mcp_server import server, tools

_GLOBAL_TOOLS = ("get_macro", "get_cost_model", "get_data_status", "screen_universe", "get_portfolio")
_TEXT_TOOLS = ("get_news", "get_filing_text", "get_risk_factors", "get_short_interest", "get_price_series")
_TICKER_TOOLS = [n for n in tools.ALL_TOOLS if n not in _GLOBAL_TOOLS + ("resolve_refs",)]
# date-like fields that must not be later than a past read's as_of
_DATE_FIELDS = ("bar_date", "last_bar_date", "filed_at", "date", "grade_date", "snapshot_date", "as_of",
                "period", "obs_date", "last_8k_at", "last_dividend_ex_date", "started_at", "trade_date",
                "alert_day", "exit_day", "published_at", "filing_date")


def _summary(out: dict) -> str:
    if "error" in out:
        return f"ERROR {out['error']}"
    data = out.get("data", {})
    sizes = {k: (len(v) if isinstance(v, (list, dict)) else v) for k, v in data.items()}
    return f"sections={sizes} missing={out['data_status']['missing']} notes={out.get('notes', [])}"


def _cites(node, out: list, prefix: str = "", ref: str | None = None) -> list:
    """Every number in an answer as {"cite": "<_ref>#<field>", "value": v}."""
    if isinstance(node, dict):
        if "_ref" in node and not prefix:
            ref = node["_ref"]
        for k, v in node.items():
            if k.startswith("_"):
                continue
            if isinstance(v, dict) and "_ref" in v:
                _cites(v, out)
            elif isinstance(v, dict) and ref:
                _cites(v, out, f"{prefix}{k}.", ref)
            elif isinstance(v, (dict, list)):
                _cites(v, out)
            elif ref and isinstance(v, (int, float)) and not isinstance(v, bool):
                out.append({"cite": f"{ref}#{prefix}{k}", "value": v})
    elif isinstance(node, list):
        for v in node:
            _cites(v, out)
    return out


def _leaks(node, day: str, found: list) -> list:
    """Dated fields after the read date (next_* fields look ahead on purpose)."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k in _DATE_FIELDS and isinstance(v, str) and v[:10] > day:
                found.append(f"{k}={v}")
            elif isinstance(v, (dict, list)):
                _leaks(v, day, found)
    elif isinstance(node, list):
        for v in node:
            _leaks(v, day, found)
    return found


def _validate(answers: list[dict], as_of: str | None, label: str) -> int:
    cites = []
    for out in answers:
        _cites(out.get("data"), cites)
    started = time.monotonic()
    res = tools.resolve_refs(cites, as_of=as_of)
    summ = res["data"]["summary"]
    bad = [r for r in res["data"]["results"] if r["status"] != "found" or not r.get("match")]
    print(f"resolve_refs {label}: {summ} in {time.monotonic() - started:.1f}s")
    for r in bad[:15]:
        print("   ", r)
    for r in bad[:3]:
        print(f"::warning title=resolve_refs {label}::{json.dumps(r, default=str)[:400]}")
    return len(bad)


async def _role_check(role: str) -> list[str]:
    params = StdioServerParameters(command=sys.executable, args=["-m", "src.mcp_server.server", "--role", role],
                                    env=dict(os.environ))  # the SDK passes only a minimal env by default
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        listed = sorted(t.name for t in (await s.list_tools()).tools)
        probe = ("get_data_status", {}) if "get_data_status" in listed else ("resolve_refs", {"refs": []})
        res = await s.call_tool(*probe)
        assert not res.isError, res
    return listed


def _run(tickers: list[str], as_of: str | None) -> tuple[int, list[dict]]:
    failures, answers = 0, []
    for t in tickers:
        for name in _TICKER_TOOLS:
            started = time.monotonic()
            try:
                out = tools.ALL_TOOLS[name](t, as_of=as_of)
                line = _summary(out)
                failures += "error" in out
                answers.append(out)
            except Exception as exc:  # noqa: BLE001 — reported and counted
                line, failures = f"RAISED {type(exc).__name__}: {exc}", failures + 1
                print(f"::warning title={name} {t}::{line[:300]}")
            print(f"{t:6} {name:22} {time.monotonic() - started:5.2f}s {line}"[:600])
            if t == tickers[0] and name in _TEXT_TOOLS and as_of is None:
                print(f"::notice title={name} {t}::{line[:400]}")
    for name in _GLOBAL_TOOLS:
        out = tools.ALL_TOOLS[name](as_of=as_of)
        failures += "error" in out
        answers.append(out)
        print(f"{'-':6} {name:22} {_summary(out)}"[:600])
    return failures, answers


def main(tickers: list[str]) -> int:
    as_of = os.getenv("MCP_SMOKE_AS_OF") or (date.today() - timedelta(days=7)).isoformat()
    print("== live ==")
    failures, live = _run(tickers, None)
    print(f"\n== as of {as_of} ==")
    f_past, past = _run(tickers, as_of)
    failures += f_past
    leaks = [(o["tool"], o.get("ticker"), _leaks(o.get("data"), as_of, [])) for o in past]
    leaks = [x for x in leaks if x[2]]
    for x in leaks:
        print("LEAK", x)
    failures += len(leaks)
    print()
    failures += _validate(live, None, "live")
    failures += _validate(past, as_of, f"as of {as_of}")
    print("\nSample answer (get_price_technicals, first ticker):")
    print(json.dumps(tools.get_price_technicals(tickers[0]), indent=1, default=str)[:4000])
    print("\nSample answer (get_analyst_view, first ticker):")
    print(json.dumps(tools.get_analyst_view(tickers[0]), indent=1, default=str)[:4000])

    for role in ("optimista", "presidente", "validador"):
        listed = asyncio.run(_role_check(role))
        expected = sorted(f.__name__ for f in server.role_tools(role))
        ok = listed == expected
        failures += not ok
        print(f"role {role}: {'OK' if ok else 'MISMATCH'} {listed}")
    print(f"\nfailures: {failures}")
    # annotations are readable through the API even where the log download is not
    print(f"::notice title=mcp_smoke::failures={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["AAPL", "NVDA", "AON", "APA", "JPM"]))
