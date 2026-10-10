"""MCP server — stdio transport (plan §10).

Run locally:  python -m src.mcp_server.server
Register in Claude Desktop / agent frameworks as a stdio MCP server.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from src.mcp_server import tools

mcp = FastMCP("financial-advisor-data")

for fn in (
    tools.get_research_package,
    tools.get_feed,
    tools.get_price_history,
    tools.get_fundamentals,
    tools.run_screener,
    tools.get_screener_results,
    tools.list_tickers,
    tools.get_pipeline_runs,
    tools.get_pipeline_events,
):
    mcp.tool()(fn)


def main() -> None:
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
