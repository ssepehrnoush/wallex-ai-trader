"""Engine MCP server (stdio): exposes the read-only tools in tools.py to any MCP client.

Wallex's official MCP server (https://api.wallex.ir/mcp) serves raw Wallex market data, and the
bot's AI analyst uses both: four read-only public tools from the official server, plus this one
for what only the engine knows (global fair value, premium statistics, the bot's own trades and
risk state, the backtest, threshold replay). See agent.py. You can also attach this server to
Claude Desktop / Cursor to inspect the bot:

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
    "(negative = cheap). Fees (taker on both legs): TMN markets ~0.6% round trip, USDT markets ~0.26%."))

for fn in tools.TOOLS:
    mcp.tool()(fn)


if __name__ == "__main__":
    mcp.run()
