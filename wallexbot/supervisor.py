"""AI supervisor: the agentic layer that decides WHERE and HOW aggressively the fast engine trades.

Arbitrage-style entries must be taken within seconds, faster than an LLM round trip, so the
LLM does not click the buy button. Every few minutes it reads the live statistics of every
market and sets that market's policy: on/off, the entry threshold, stop, holding time and size.
Two roles, two calls:

  quant analyst -> proposes policies from the data
  risk officer  -> may only tighten them (higher threshold, smaller size, disable), with reasons

The code then clamps everything to hard bounds. Each review is written to the decision log, so
every trade can be traced to the policy (and the AI reasoning) that allowed it.
"""
from __future__ import annotations

import json

from .fairvalue import Policy, quote_of

BOUNDS = {"entry_dev": (0.0025, 0.02), "exit_slack": (0.0, 0.002), "stop_pct": (0.004, 0.015),
          "max_hold_min": (5.0, 45.0), "size_factor": (0.0, 1.0)}

ANALYST = """You are the quant analyst supervising a fair-value reversion trading engine on the Iranian exchange Wallex.
The engine buys a coin on Wallex when its ASK is below the global fair price (Binance price, times Wallex USDT/TMN
for toman markets) by more than `entry_dev` beyond that market's normal premium, and sells when the BID is back
within `exit_slack` of normal, or at the stop / max holding time. Fees per round trip: toman (TMN) markets about
0.55%, USDT markets about 0.23%; the entry threshold must clear the fee with margin, otherwise the trade loses.
For each market you get the last two hours: how often and how deep cheap moments occurred (buy_dev percentiles,
minutes below thresholds), spread and depth, and the engine's recent trades there. Global context: BTC and USDT/TMN moves.
Propose a policy per market. Prefer thresholds that historically occurred a few times per hour, not never and not
constantly (constant 'discounts' usually mean the market is stale or illiquid, not cheap). Disable markets whose
spread or depth makes exits unreliable, and lower size or raise thresholds when global volatility is high.
Reply JSON only:
{"risk_mode": "normal|defensive|halt",
 "markets": {"<SYMBOL>": {"enabled": true, "entry_dev": 0.005, "exit_slack": 0.0005, "stop_pct": 0.008,
              "max_hold_min": 20, "size_factor": 0.5, "reason": "<one sentence>"}},
 "summary": "<two sentences on the market state>"}"""

OFFICER = """You are the risk officer. You receive the analyst's proposed policies and the same data. The competition
ranks NET return but a losing book scores zero and drawdown is penalised, so capital preservation comes first.
You may ONLY tighten: raise entry_dev, lower size_factor, shorten max_hold_min, tighten stop_pct, or disable a market.
Never loosen anything. Do not disable a market just because no discount appeared recently: a fee-safe threshold
already protects capital, and a disabled market cannot catch the next dislocation. Disable only for structural
problems (wide spread, thin book, stale data) or when the account is near its loss limits. Reply JSON only, same shape:
{"risk_mode": "normal|defensive|halt",
 "markets": {"<SYMBOL>": {"enabled": true, "entry_dev": 0.005, "exit_slack": 0.0005, "stop_pct": 0.008,
              "max_hold_min": 20, "size_factor": 0.5, "reason_fa": "<one short Persian sentence>"}},
 "summary_fa": "<two short Persian sentences for the decision log>"}"""


def _clamp(k, v):
    lo, hi = BOUNDS[k]
    return min(max(float(v), lo), hi)


def min_entry(symbol: str, fees_by_quote: dict) -> float:
    """Smallest entry threshold that can pay a taker round trip plus a minimum edge."""
    f = fees_by_quote[quote_of(symbol)]
    return f["maker"] + f["taker"] + 0.0005


class Supervisor:
    def __init__(self, committee, fees: dict):
        self.ai = committee            # AICommittee instance: provides the cached _ask()
        self.fees = fees
        self.last: dict = {}

    def review(self, data: dict, current: dict[str, Policy]) -> tuple[dict[str, Policy], dict]:
        """data: {"markets": {sym: stats}, "context": {...}, "recent_trades": [...]}.
        Returns new policies and a log record. On any AI failure keeps the current policies
        but switches to defensive sizing (fail safe)."""
        trace, analyst_model = [], None
        try:
            if self.ai.cfg.get("agentic", True):
                try:
                    # tool-using analyst over the Wallex MCP server (agent.py); brief = current policies only,
                    # the agent fetches what it needs itself
                    from .agent import analyst_with_tools
                    brief = {"task": "Review and set the trading policy of every market below.",
                             "current_policies": {s: p.to_dict() for s, p in current.items()},
                             "context": data.get("context", {})}
                    prop, trace, analyst_model = analyst_with_tools(self.ai, brief)
                except Exception as e:
                    trace = [{"agent_error": str(e)[:300]}]
                    prop = self.ai._ask("supervisor_analyst", ANALYST, data)
            else:
                prop = self.ai._ask("supervisor_analyst", ANALYST, data)
            final = self.ai._ask("supervisor_officer", OFFICER, {"data": data, "analyst_proposal": prop})
        except Exception as e:
            out = {s: Policy(**{**p.to_dict(), "size_factor": min(p.size_factor, 0.3), "reason": f"AI unavailable: {e}"[:120]})
                   for s, p in current.items()}
            return out, {"error": str(e)[:300], "risk_mode": "defensive"}

        mode = final.get("risk_mode", prop.get("risk_mode", "normal"))
        new: dict[str, Policy] = {}
        for s, cur in current.items():
            a = (prop.get("markets") or {}).get(s, {})
            o = (final.get("markets") or {}).get(s, {})
            if not a and not o:
                new[s] = cur
                continue
            # officer may only tighten the analyst: enforce it in code, not just in the prompt
            enabled = bool(a.get("enabled", cur.enabled)) and bool(o.get("enabled", True))
            entry = max(float(a.get("entry_dev", cur.entry_dev)), float(o.get("entry_dev", 0)), min_entry(s, self.fees))
            size = min(float(a.get("size_factor", cur.size_factor)), float(o.get("size_factor", 1.0)))
            hold = min(float(a.get("max_hold_min", cur.max_hold_min)), float(o.get("max_hold_min", 999)))
            stop = min(float(a.get("stop_pct", cur.stop_pct)), float(o.get("stop_pct", 1.0)))
            slack = max(float(a.get("exit_slack", cur.exit_slack)), float(o.get("exit_slack", 0)))
            if mode == "defensive":
                size = min(size, 0.4)
            if mode == "halt":
                enabled = False
            new[s] = Policy(enabled=enabled, entry_dev=_clamp("entry_dev", entry), exit_slack=_clamp("exit_slack", slack),
                            stop_pct=_clamp("stop_pct", stop), max_hold_min=_clamp("max_hold_min", hold),
                            size_factor=_clamp("size_factor", size),
                            reason=str(o.get("reason_fa") or a.get("reason") or "")[:200])
        rec = {"risk_mode": mode, "summary_fa": final.get("summary_fa", ""), "analyst_summary": prop.get("summary", ""),
               "analyst_model": analyst_model, "analyst_tool_calls": trace,
               "policies": {s: p.to_dict() for s, p in new.items()}}
        self.last = rec
        return new, rec
