"""Tool-using analyst agent. Connects to the Wallex MCP server (mcp_server.py) as an MCP client,
lets the LLM decide which tools to call (order books, premium statistics, global momentum,
replaying a candidate threshold on recorded live ticks...), then returns its policy proposal
plus the full tool trace for the decision log."""
from __future__ import annotations

import asyncio
import json
import re
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import ROOT

AGENT_SYSTEM = """You are the quant analyst supervising a fair-value reversion trading engine on the Iranian exchange Wallex.
The engine buys when a Wallex ASK is below global fair value by more than `entry_dev` (after that market's normal premium)
and sells when the BID is back within `exit_slack` of normal, or at the stop / max holding time.
Round-trip fees: TMN markets ~0.55%, USDT markets ~0.23%. Thresholds below fee + margin lose money.

You have read-only tools on the live market and on the engine's own recorded data. Investigate before deciding:
look at premium statistics and order books of the markets that matter, check global momentum, and use
simulate_threshold to test candidate thresholds on the recorded live ticks before proposing them.
Budget: at most 8 tool calls, so pick the markets that matter. Prefer thresholds that historically fired a few times per hour with positive simulated results.
Disabling a market means missing its next dislocation. Disable ONLY for structural problems (spread above 0.6%,
top-of-book depth below the minimum order, stale or missing data). A quiet market with no recent discounts should
stay enabled at a fee-safe threshold: the threshold itself protects capital. Then answer with JSON only, no prose:
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


async def _run(llm, model: str, brief: dict, max_steps: int, kw: dict) -> tuple[dict, list]:
    params = StdioServerParameters(command=sys.executable, args=["-m", "wallexbot.mcp_server"], cwd=str(ROOT))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            listed = await s.list_tools()
            tools = [{"type": "function", "function": {"name": t.name, "description": t.description or "",
                                                       "parameters": t.inputSchema}} for t in listed.tools]
            messages = [{"role": "system", "content": AGENT_SYSTEM},
                        {"role": "user", "content": json.dumps(brief, ensure_ascii=False)}]
            trace = []
            for _ in range(max_steps):
                resp = await asyncio.to_thread(llm.chat.completions.create, model=model, messages=messages,
                                               tools=tools, **kw)
                msg = resp.choices[0].message
                if not msg.tool_calls:
                    return _json(msg.content), trace
                messages.append({"role": "assistant", "content": msg.content or "",
                                 "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                        res = await s.call_tool(tc.function.name, args)
                        text = "".join(getattr(c, "text", "") for c in res.content)[:6000]
                    except Exception as e:      # a failed tool is information for the model, not a crash
                        args, text = {}, json.dumps({"error": str(e)[:300]})
                    trace.append({"tool": tc.function.name, "args": args, "result": text[:400]})
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": text})
            messages.append({"role": "user", "content": "Tool budget used. Reply now with the final JSON only."})
            resp = await asyncio.to_thread(llm.chat.completions.create, model=model, messages=messages,
                                           response_format={"type": "json_object"}, **kw)
            return _json(resp.choices[0].message.content), trace


def analyst_with_tools(committee, brief: dict, max_steps: int = 12) -> tuple[dict, list, str]:
    """Run the agent on the committee's primary provider, falling back to the other one."""
    # gpt-5.x on /chat/completions accepts function tools only with reasoning_effort "none".
    # Only the primary provider runs the agent: a tool loop costs ~20x a single call, and the BoxAPI
    # fallback credit is shared with the other challenge apps. On failure the supervisor falls back to
    # the single-call analyst instead.
    kw = {"reasoning_effort": "none"} if "gpt-5" in committee.model else {}
    last = None
    for llm, model in [(committee.client, committee.model)]:
        if llm is None:
            continue
        try:
            prop, trace = asyncio.run(_run(llm, model, brief, max_steps, kw))
            return prop, trace, model
        except Exception as e:
            last = e
    raise RuntimeError(f"agent failed: {last}")
