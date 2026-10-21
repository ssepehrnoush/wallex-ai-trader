"""Wallex MCP server (stdio): exposes the read-only tools in tools.py to any MCP client.

The challenge mentions a "Wallex MCP" but none was published, so we ship our own. The bot's
AI supervisor connects to it as an MCP client (agent.py); you can also attach it to Claude
Desktop / Cursor to inspect the bot:

    {"mcpServers": {"wallex": {"command": "<repo>/.venv/Scripts/python", "args": ["-m", "wallexbot.mcp_server"],
                               "cwd": "<repo>"}}}

No tool can trade, and none returns keys or balances.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import tools

mcp = FastMCP("wallex", instructions=(
    "Read-only market intelligence for a fair-value reversion bot on the Iranian exchange Wallex. "
    "buy_dev is how far the Wallex ask is below global fair value after the market's normal premium "
    "(negative = cheap). Fees: TMN markets ~0.55% round trip, USDT markets ~0.23%."))

for fn in tools.TOOLS:
    mcp.tool()(fn)


if __name__ == "__main__":
    mcp.run()
