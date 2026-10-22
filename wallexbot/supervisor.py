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

# Watch markets: their 7-day backtest profit depended on one-minute prints, so they start switched off and trade
# only while their OWN recorded live order-book ticks replay at a profit with the policy just set.
WATCH_EVIDENCE_MIN = 480
WATCH_RULE = ("Markets in watch_markets start switched off. The code switches one on only while simulate_threshold on "
              "its own recorded live ticks (last 8 hours) shows at least one trade and a positive total at the policy "
              "you set; otherwise it stays off whatever you propose. Run simulate_threshold for each watch market and "
              "propose enabled=true when its replay is profitable.")

# Contest mode (config "contest"): the contest pays its profit score only to a positive final return, and a market
# the bot never trades in can only end flat. The gain guard (risk.py) freezes risk once the account is ahead and a
# loss budget stops new entries after a small loss, so the AI may switch a live market off only for a structural
# reason the code can see, and its caution is bounded. Told to the model too, so it reasons with it.
CONTEST_RULE = ("Contest mode is on: the contest scores only a positive final return. The code freezes new risk as soon as "
                "the account is ahead (gain guard) and stops new entries after a small loss (loss budget), so an idle bot "
                "can only end flat. Keep every market without a structural problem (spread above the cap, top of book "
                "below the minimum order, stale data) enabled; set entry_dev between the fee floor and the contest cap "
                "and size_factor within the contest range; prefer lower thresholds where spreads are tight. Use risk_mode "
                "halt only for an account-level emergency (a crash, broken data): it switches every market off.")
CONTEST_DEFAULTS = {"keep_markets_on": True, "max_entry_dev": 0.01, "size_factor_range": [0.2, 0.3]}

ANALYST = """You are the quant analyst supervising a fair-value reversion trading engine on the Iranian exchange Wallex.
The engine buys a coin on Wallex when its ASK is below the global fair price (Binance price, times Wallex USDT/TMN
for toman markets) by more than `entry_dev` beyond that market's normal premium, and sells when the BID is back
within `exit_slack` of normal, or at the stop / max holding time. Fees per round trip (taker on both legs): toman (TMN)
markets about 0.6%, USDT markets about 0.26%; the entry threshold must clear the fee with margin, otherwise the trade loses.
For each market you get the last two hours: how often and how deep cheap moments occurred (buy_dev percentiles,
minutes below thresholds), spread and depth, and the engine's recent trades there. Global context: BTC and USDT/TMN moves.
Propose a policy per market. Prefer thresholds that historically occurred a few times per hour, not never and not
constantly (constant 'discounts' usually mean the market is stale or illiquid, not cheap). Disable markets whose
spread or depth makes exits unreliable, and lower size or raise thresholds when global volatility is high.
Reply JSON only:
{"risk_mode": "normal|defensive|halt",
 "markets": {"<SYMBOL>": {"enabled": true, "entry_dev": 0.007, "exit_slack": 0.0005, "stop_pct": 0.008,
              "max_hold_min": 20, "size_factor": 0.5, "reason": "<one sentence>"}},
 "summary": "<two sentences on the market state>"}"""

OFFICER = """You are the risk officer. You receive the analyst's proposed policies and the same data. The competition
ranks NET return but a losing book scores zero and drawdown is penalised, so capital preservation comes first.
You may ONLY tighten: raise entry_dev, lower size_factor, shorten max_hold_min, tighten stop_pct, or disable a market.
Never loosen anything. Do not disable a market just because no discount appeared recently: a fee-safe threshold
already protects capital, and a disabled market cannot catch the next dislocation. Disable only for structural
problems (wide spread, thin book, stale data) or when the account is near its loss limits. Reply JSON only, same shape:
{"risk_mode": "normal|defensive|halt",
 "markets": {"<SYMBOL>": {"enabled": true, "entry_dev": 0.007, "exit_slack": 0.0005, "stop_pct": 0.008,
              "max_hold_min": 20, "size_factor": 0.5, "reason_fa": "<one short Persian sentence>"}},
 "summary_fa": "<two short Persian sentences for the decision log>"}"""


def _clamp(k, v):
    lo, hi = BOUNDS[k]
    return min(max(float(v), lo), hi)


def min_entry(symbol: str, fees_by_quote: dict) -> float:
    """Smallest entry threshold that can pay a taker round trip plus a minimum edge."""
    # both legs are taker (buy at the ask, sell at or through the bid), plus at least 0.1% of edge
    f = fees_by_quote[quote_of(symbol)]
    return 2 * f["taker"] + 0.001


class Supervisor:
    def __init__(self, committee, fees: dict, watch=(), contest: dict | None = None, max_spread: float = 0.006,
                 min_depth: float = 60_000):
        self.ai = committee            # AICommittee instance: provides the cached _ask()
        self.fees = fees
        self.watch = set(watch)
        self.contest = {**CONTEST_DEFAULTS, **contest} if contest and contest.get("enabled") else {}
        self.max_spread, self.min_depth = max_spread, min_depth
        self.last: dict = {}

    def _structural(self, st: dict) -> str:
        """A reason the code can verify for keeping a market off, from the review's own market data ('' = none)."""
        if st.get("samples", 0) < 10 or "spread_pct" not in st:
            return "no fresh order book or fair value"
        if st["spread_pct"] > self.max_spread * 100:
            return f"spread {st['spread_pct']}% above the {self.max_spread:.1%} cap"
        if st.get("quote") == "TMN" and st.get("top_depth_quote", 0) < self.min_depth:
            return "top of book below the minimum order"
        return ""

    def _bound(self, s: str, p: Policy, problem: str | None) -> tuple[bool, float, float]:
        """Contest bounds for one market. problem=None: do not force it on (start-up, watch markets). Watch markets
        keep the AI's entry_dev, so their live replay tests what the AI proposed, but their size is clamped too."""
        c = self.contest
        lo, hi = c["size_factor_range"]
        watch = s in self.watch
        enabled = p.enabled or (not watch and problem == "" and c["keep_markets_on"])
        entry = p.entry_dev if watch else min(p.entry_dev, max(c["max_entry_dev"], min_entry(s, self.fees)))
        return enabled, entry, min(max(p.size_factor, lo), hi)

    def bound_start(self, policies: dict[str, Policy]) -> dict[str, Policy]:
        """Contest bounds on the start-up policies, which trade until the first review returns."""
        if not self.contest:
            return policies
        out = {}
        for s, p in policies.items():
            _, entry, size = self._bound(s, p, None)
            out[s] = Policy(**{**p.to_dict(), "entry_dev": entry, "size_factor": size})
        return out

    def _contest_bounds(self, new: dict[str, Policy], data: dict) -> dict:
        """Contest mode: a live market stays on unless the code sees a structural problem; entry_dev is capped and
        size_factor kept in range. Watch markets keep their own gate. Returns what was changed, for the log."""
        notes = {}
        for s, p in new.items():
            problem = None if s in self.watch else self._structural((data.get("markets") or {}).get(s, {}))
            enabled, entry, size = self._bound(s, p, problem)
            if (enabled, entry, size) == (p.enabled, p.entry_dev, p.size_factor):
                continue
            notes[s] = {"ai": {"enabled": p.enabled, "entry_dev": p.entry_dev, "size_factor": p.size_factor},
                        "contest": {"enabled": enabled, "entry_dev": entry, "size_factor": size},
                        "structural_problem": problem or None}
            why = "حالت مسابقه: " + ("روشن ماند، چون مشکل ساختاری نبود" if enabled and not p.enabled else
                                     "آستانه و اندازه در بازهٔ مسابقه")
            new[s] = Policy(**{**p.to_dict(), "enabled": enabled, "entry_dev": entry, "size_factor": size,
                               "reason": f"{why} | {p.reason}"[:200]})
        return notes

    def _live_evidence(self, s: str, p: Policy) -> dict:
        from .tools import simulate_threshold      # imported here: tools imports this module
        try:
            return simulate_threshold(s, p.entry_dev, p.exit_slack, p.stop_pct, p.max_hold_min, WATCH_EVIDENCE_MIN)
        except Exception as e:
            return {"error": str(e)[:200]}

    def review(self, data: dict, current: dict[str, Policy]) -> tuple[dict[str, Policy], dict]:
        """data: {"markets": {sym: stats}, "context": {...}, "recent_trades": [...]}.
        Returns new policies and a log record. On any AI failure keeps the current policies
        but switches to defensive sizing (fail safe)."""
        trace, analyst_model = [], None
        watch_note = {"watch_markets": sorted(self.watch), "watch_rule": WATCH_RULE} if self.watch else {}
        if self.contest:
            watch_note["contest_rule"] = CONTEST_RULE
            watch_note["contest_bounds"] = {"entry_dev_max": self.contest["max_entry_dev"],
                                            "size_factor_range": self.contest["size_factor_range"]}
        data = {**data, **watch_note}
        try:
            if self.ai.cfg.get("agentic", True):
                try:
                    # tool-using analyst over the Wallex MCP server (agent.py); brief = current policies only,
                    # the agent fetches what it needs itself
                    from .agent import analyst_with_tools
                    brief = {"task": "Review and set the trading policy of every market below.",
                             "current_policies": {s: p.to_dict() for s, p in current.items()},
                             "context": data.get("context", {}), **watch_note}
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
        contest_notes = {}
        if self.contest:
            contest_notes = (self._contest_bounds(new, data) if mode != "halt" else
                             {"halt": "AI risk_mode halt: every market off for this review (contest bounds not applied)"})
        # the watch gate is code, not prompt: no model output can switch a watch market on without live proof
        evidence = {}
        for s in sorted(self.watch & new.keys()):
            p = new[s]
            if not p.enabled:
                continue
            ev = evidence[s] = self._live_evidence(s, p)
            if not (ev.get("trades", 0) >= 1 and (ev.get("sum_ret_pct") or 0) > 0):
                new[s] = Policy(**{**p.to_dict(), "enabled": False,
                                   "reason": f"watch: live replay not profitable yet ({ev.get('trades', 0)} trades)"})
        rec = {"risk_mode": mode, "summary_fa": final.get("summary_fa", ""), "analyst_summary": prop.get("summary", ""),
               "analyst_model": analyst_model, "analyst_tool_calls": trace, "watch_evidence": evidence,
               "contest_adjustments": contest_notes, "policies": {s: p.to_dict() for s, p in new.items()}}
        self.last = rec
        return new, rec
