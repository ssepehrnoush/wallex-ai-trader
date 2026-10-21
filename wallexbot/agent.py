"""Tool-using analyst agent with TWO MCP servers:

1. the official Wallex MCP (https://api.wallex.ir/mcp, Streamable HTTP): raw Wallex market data,
   order books, latest trades, candles;
2. our engine MCP (mcp_server.py, stdio): global fair value, premium statistics, the bot's own
   trades and risk state, and replaying a candidate threshold on recorded live ticks.

The LLM decides which tools to call, then returns its policy proposal plus the full tool trace
for the decision log. Only an allowlist of read-only, public official tools is exposed and no API
key is sent to the official server, so the agent can never reach account or order tools.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from .config import ROOT

WALLEX_MCP_URL = "https://api.wallex.ir/mcp"
# read-only, public, small responses. Everything else on the official server (orders, transfers,
# margin, account data) is deliberately not exposed to the model.
WALLEX_ALLOWED = {"wallex_basic_get_market_depth", "wallex_basic_get_latest_trades", "wallex_basic_candle_history",
                  "wallex_otc_get_otc_price"}

AGENT_SYSTEM = """You are the quant analyst supervising a fair-value reversion trading engine on the Iranian exchange Wallex.
The engine buys when a Wallex ASK is below global fair value by more than `entry_dev` (after that market's normal premium)
and sells when the BID is back within `exit_slack` of normal, or at the stop / max holding time.
Round-trip fees: TMN markets ~0.55%, USDT markets ~0.23%. Thresholds below fee + margin lose money.

Two tool sets:
- wallex_* tools come from the OFFICIAL Wallex MCP server: live order books, latest trades and candles straight from Wallex.
- the other tools come from the engine: global fair value, premium statistics, the bot's own trades and risk state,
  the 7-day backtest, and simulate_threshold, which replays recorded live ticks with a candidate policy.
Investigate before deciding: check order books (wallex_basic_get_market_depth) and recent trades
(wallex_basic_get_latest_trades) of the markets that matter, global momentum, and use
simulate_threshold to test candidate thresholds before proposing them.
Budget: at most 8 tool calls, so pick the markets that matter. Prefer thresholds that historically fired a few times per hour with positive simulated results.
Disabling a market means missing its next dislocation. Disable ONLY for structural problems (spread above 0.6%,
top-of-book depth below the minimum order, stale or missing data). A quiet market with no recent discounts should
stay enabled at a fee-safe threshold: the threshold itself protects capital.
Then answer with JSON only, no prose:
{"risk_mode": "normal|defensive|halt",
 "markets": {"<SYMBOL>": {"enabled": true, "entry_dev": 0.005, "exit_slack": 0.0005, "stop_pct": 0.008,
              "max_hold_min": 20, "size_factor": 0.5, "reason": "<one sentence citing the evidence you used>"}},
 "summary": "<two sentences>"}
entry_dev, exit_slack and stop_pct are FRACTIONS (0.005 = 0.5%)."""


def _json(text: str) -> dict:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        m = re.search(r"\{.*\}", text or "", re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def _schema(t) -> dict:
    return {"type": "function", "function": {"name": t.name, "description": (t.description or "")[:900],
                                             "parameters": t.inputSchema or {"type": "object", "properties": {}}}}


async def _run(llm, model: str, brief: dict, max_steps: int, kw: dict) -> tuple[dict, list, list]:
    async with AsyncExitStack() as stack:
        sessions: dict[str, ClientSession] = {}
        tools, servers = [], []
        # our engine MCP (always)
        r, w = await stack.enter_async_context(stdio_client(
            StdioServerParameters(command=sys.executable, args=["-m", "wallexbot.mcp_server"], cwd=str(ROOT))))
        ours = await stack.enter_async_context(ClientSession(r, w))
        await ours.initialize()
        for t in (await ours.list_tools()).tools:
            sessions[t.name] = ours
            tools.append(_schema(t))
        servers.append("engine")
        # the official Wallex MCP (best effort: the agent still works if it is down)
        try:
            r2, w2, _ = await stack.enter_async_context(streamablehttp_client(WALLEX_MCP_URL, timeout=20))
            official = await stack.enter_async_context(ClientSession(r2, w2))
            await official.initialize()
            for t in (await official.list_tools()).tools:
                if t.name in WALLEX_ALLOWED:
                    sessions[t.name] = official
                    tools.append(_schema(t))
            servers.append("wallex-official")
            # raw order books come from Wallex's own server when it is up; drop our duplicate
            tools = [t for t in tools if t["function"]["name"] != "get_orderbook"]
            sessions.pop("get_orderbook", None)
        except Exception:
            pass

        messages = [{"role": "system", "content": AGENT_SYSTEM},
                    {"role": "user", "content": json.dumps(brief, ensure_ascii=False)}]
        trace = []
        for _ in range(max_steps):
            resp = await asyncio.to_thread(llm.chat.completions.create, model=model, messages=messages, tools=tools, **kw)
            msg = resp.choices[0].message
            if not msg.tool_calls:
                return _json(msg.content), trace, servers
            messages.append({"role": "assistant", "content": msg.content or "",
                             "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    if name not in sessions:      # the model may only call what we listed
                        raise ValueError(f"tool {name} is not allowed")
                    args = json.loads(tc.function.arguments or "{}")
                    res = await sessions[name].call_tool(name, args)
                    text = "".join(getattr(c, "text", "") for c in res.content)[:6000]
                except Exception as e:      # a failed tool is information for the model, not a crash
                    args, text = {}, json.dumps({"error": str(e)[:300]})
                trace.append({"server": "wallex-official" if name in WALLEX_ALLOWED else "engine",
                              "tool": name, "args": args, "result": text[:400]})
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": text})
        messages.append({"role": "user", "content": "Tool budget used. Reply now with the final JSON only."})
        resp = await asyncio.to_thread(llm.chat.completions.create, model=model, messages=messages,
                                       response_format={"type": "json_object"}, **kw)
        return _json(resp.choices[0].message.content), trace, servers


def analyst_with_tools(committee, brief: dict, max_steps: int = 12) -> tuple[dict, list, str]:
    """Run the agent on the committee's primary provider.
    gpt-5.x on /chat/completions accepts function tools only with reasoning_effort "none".
    Only the primary provider runs the agent: a tool loop costs ~20x a single call, and the BoxAPI
    fallback credit is shared with the other challenge apps. On failure the supervisor falls back to
    the single-call analyst instead."""
    kw = {"reasoning_effort": "none"} if "gpt-5" in committee.model else {}
    if committee.client is None:
        raise RuntimeError("no primary LLM client")
    prop, trace, servers = asyncio.run(_run(committee.client, committee.model, brief, max_steps, kw))
    return prop, trace, f"{committee.model} via MCP[{', '.join(servers)}]"
