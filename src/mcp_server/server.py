"""MCP server over the Capa 1 catalog — stdio transport.

Run:  python -m src.mcp_server.server --role optimista
The role (config/mcp_roles.yaml) decides which tools are registered, so each
committee member only discovers the tools of its role. Default role: all.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import yaml
from mcp.server.fastmcp import FastMCP

from src.mcp_server import tools

_ROLES = Path(__file__).resolve().parents[2] / "config" / "mcp_roles.yaml"


def role_tools(role: str) -> list:
    roles = yaml.safe_load(_ROLES.read_text())["roles"]
    if role not in roles:
        raise SystemExit(f"unknown role {role!r}; choose one of {sorted(roles)}")
    names = roles[role]["tools"]
    if names == "*":
        return list(tools.ALL_TOOLS.values())
    unknown = [n for n in names if n not in tools.ALL_TOOLS]
    if unknown:
        raise SystemExit(f"role {role!r} lists unknown tools: {unknown}")
    return [tools.ALL_TOOLS[n] for n in names]


def build(role: str) -> FastMCP:
    mcp = FastMCP(f"financial-advisor-{role}")
    for fn in role_tools(role):
        mcp.tool()(fn)
    return mcp


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--role", default=os.getenv("MCP_ROLE", "all"))
    build(p.parse_args().role).run()  # stdio


if __name__ == "__main__":
    main()
