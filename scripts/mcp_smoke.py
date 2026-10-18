"""Smoke test of the MCP server against the real database (run from GitHub Actions).

1. Calls every tool for a few tickers and prints a short summary of each answer.
2. Starts the server over stdio as a given role and checks it exposes exactly
   that role's tools, the way a committee agent would see them.
Exits non-zero if any tool raises or returns an error.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from src.mcp_server import server, tools

_TICKER_TOOLS = [n for n in tools.ALL_TOOLS if n not in ("get_macro", "get_cost_model", "get_data_status")]


def _summary(out: dict) -> str:
    if "error" in out:
        return f"ERROR {out['error']}"
    data = out.get("data", {})
    sizes = {k: (len(v) if isinstance(v, (list, dict)) else v) for k, v in data.items()}
    return f"sections={sizes} missing={out['data_status']['missing']} notes={out.get('notes', [])}"


async def _role_check(role: str) -> list[str]:
    params = StdioServerParameters(command=sys.executable, args=["-m", "src.mcp_server.server", "--role", role])
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        listed = sorted(t.name for t in (await s.list_tools()).tools)
        res = await s.call_tool("get_data_status", {})
        assert not res.isError, res
    return listed


def main(tickers: list[str]) -> int:
    failures = 0
    for t in tickers:
        for name in _TICKER_TOOLS:
            started = time.monotonic()
            try:
                out = tools.ALL_TOOLS[name](t)
                line = _summary(out)
                failures += "error" in out
            except Exception as exc:  # noqa: BLE001 — reported and counted
                line, failures = f"RAISED {type(exc).__name__}: {exc}", failures + 1
            print(f"{t:6} {name:22} {time.monotonic() - started:5.2f}s {line}"[:600])
    for name in ("get_macro", "get_cost_model", "get_data_status"):
        print(f"{'-':6} {name:22} {_summary(tools.ALL_TOOLS[name]())}"[:600])
    print("\nSample answer (get_price_technicals, first ticker):")
    print(json.dumps(tools.get_price_technicals(tickers[0]), indent=1, default=str)[:4000])
    print("\nSample answer (get_analyst_view, first ticker):")
    print(json.dumps(tools.get_analyst_view(tickers[0]), indent=1, default=str)[:4000])

    for role in ("optimista", "presidente"):
        listed = asyncio.run(_role_check(role))
        expected = sorted(f.__name__ for f in server.role_tools(role))
        ok = listed == expected
        failures += not ok
        print(f"role {role}: {'OK' if ok else 'MISMATCH'} {listed}")
    print(f"\nfailures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["AAPL", "NVDA", "AON", "APA", "JPM"]))
