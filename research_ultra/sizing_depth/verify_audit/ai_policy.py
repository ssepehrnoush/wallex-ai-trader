"""Part 3: what the AI supervisor actually did in the live run, and what it costs in expected return.

Reads the live decision log (snapshot, partial last line tolerated) and the ticks. Expected returns use a
linear per-market model: trades/hour x mean net % x filled notional, from out/bt_sizing.json (part 2),
cross-checked against the chronological account replay there.

Writes out/ai_policy.json
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from common import EQUITY, MAIN, OUT, TICK_CUTOFF, TMN8, dump, load_ticks, planned_notional, read_jsonl

ev = read_jsonl(MAIN / "runs" / "live_main" / "decisions.jsonl")
bt = json.loads((OUT / "bt_sizing.json").read_text(encoding="utf-8"))
dt = json.loads((OUT / "depth_ticks.json").read_text(encoding="utf-8"))
FILLPTS = {float(k): v / 100 for k, v in dt["entry_episodes"]["0.3%"]["entry_fill_pct_mean"].items()}
ticks = load_ticks()
ts = lambda s: pd.Timestamp(s).timestamp()

# per-market fill at the deepest cheap-moment bucket with >= 8 ticks (part 1), else the market's unconditional fill
PM_FILL, PM_SRC = {}, {}
for s_ in TMN8:
    pick = None
    for b in ["buy_dev<=-0.4%", "buy_dev<=-0.3%", "buy_dev<=-0.2%"]:
        c = dt["conditional_on_buy_dev"][s_].get(b, {})
        if c.get("ticks", 0) >= 8:
            pick = (b, c["entry_fill_vs_plan"])
            break
    if pick is None:
        pick = ("unconditional", dt["unconditional"][s_]["entry_fill_vs_plan"])
    PM_SRC[s_] = pick[0]
    PM_FILL[s_] = {float(k): v["mean_fill_pct"] / 100 for k, v in pick[1].items()}

# ------------------------------------------------------------------ timeline of effective policies
DEFAULT = {s: {"enabled": True, "size_factor": 0.5, "entry_dev": 0.007} for s in TMN8}
timeline = []       # (t, kind, {sym: {...}}, extra)
reviews = []
for e in ev:
    if e.get("event") == "start":
        timeline.append((ts(e["t"]), "restart_default", DEFAULT, {}))
    elif e.get("event") == "policy_review":
        pol = {s: {"enabled": p["enabled"], "size_factor": p["size_factor"], "entry_dev": p["entry_dev"],
                   "max_hold_min": p["max_hold_min"], "stop_pct": p["stop_pct"], "reason": p.get("reason", "")}
               for s, p in e.get("policies", {}).items()}
        timeline.append((ts(e["t"]), "review", pol, {"risk_mode": e.get("risk_mode")}))
        an = (e.get("analyst_summary") or "")
        reviews.append({"t": e["t"], "risk_mode_final": e.get("risk_mode"),
                        "analyst_said": "normal" if "risk is normal" in an.lower() else ("defensive" if "defensive" in an.lower() else "?"),
                        "disabled": [s for s, p in pol.items() if not p["enabled"]],
                        "sizes": {s: p["size_factor"] for s, p in pol.items()},
                        "entry_devs": {s: p["entry_dev"] for s, p in pol.items()},
                        "reasons_disabled": {s: p["reason"] for s, p in pol.items() if not p["enabled"]}})
equity_ev = [e for e in ev if e.get("event") == "equity"]
t_end = min(TICK_CUTOFF, max(ts(e["t"]) for e in ev))
t0 = timeline[0][0]
tw = {s: {"enabled_time": 0.0, "size_x_enabled_time": 0.0} for s in TMN8}
mode_time = {}
for k, (t, kind, pol, extra) in enumerate(timeline):
    t_next = timeline[k + 1][0] if k + 1 < len(timeline) else t_end
    dur = max(0.0, min(t_next, t_end) - t)
    mode = extra.get("risk_mode", "default(before first review)")
    mode_time[mode] = mode_time.get(mode, 0.0) + dur
    for s in TMN8:
        p = pol.get(s, DEFAULT[s])
        if p["enabled"]:
            tw[s]["enabled_time"] += dur
            tw[s]["size_x_enabled_time"] += dur * p["size_factor"]
total = t_end - t0
eff = {s: {"enabled_share": round(v["enabled_time"] / total, 3),
           "avg_size_when_enabled": round(v["size_x_enabled_time"] / v["enabled_time"], 3) if v["enabled_time"] else 0.0}
       for s, v in tw.items()}
latest = [x for x in timeline if x[1] == "review"][-1][2]

# ------------------------------------------------------------------ spreads at the review moments vs the AI's reasons
spr_check = []
for r in reviews:
    t = ts(r["t"])
    for s in r["disabled"]:
        g = ticks[(ticks.symbol == s) & (ticks.t <= t)]
        if not len(g):
            continue
        last = g.iloc[-1]
        prior = g[g.t >= t - 7200]
        spr_check.append({"review": r["t"], "symbol": s, "reason_fa": r["reasons_disabled"][s],
                          "spread_at_review_pct": round(last.spread * 100, 3),
                          "spread_median_prev_2h_pct": round(prior.spread.median() * 100, 3) if len(prior) else None,
                          "spread_p90_prev_2h_pct": round(prior.spread.quantile(0.9) * 100, 3) if len(prior) else None,
                          "engine_spread_gate_pct": 0.6,
                          "top_ask_notional_at_review": round(last.ask_n), "top_bid_notional_at_review": round(last.bid_n)})
overall_spread = (ticks.groupby("symbol").spread.median() * 100).round(3).to_dict()


# ------------------------------------------------------------------ expected-return model
def fill(f, sym=None):
    pts = PM_FILL[sym] if sym else FILLPTS
    xs = sorted(pts)
    return float(np.interp(f, xs, [pts[x] for x in xs]))


def expected_45h(variant: str, policy: dict, rate_mult: float = 1.0, pooled_mean: bool = False, hours: float = 45.0,
                 per_market_fill: bool = False) -> dict:
    b = bt["backtests"][variant]
    rates = b["per_market_trades_per_day"]
    allm = b["all"]["mean_ret_pct"]
    tot, per = 0.0, {}
    for s in TMN8:
        p = policy.get(s, {"enabled": True, "size_factor": 0.4})
        n_trades = rates.get(s, 0.0) / 24 * hours * rate_mult * (p.get("enabled_share", 1.0) if p["enabled"] else 0.0)
        mean = allm if pooled_mean else (b["markets"][s].get("mean_ret_pct") or 0.0)
        f = p["size_factor"]
        notional = planned_notional(f) * fill(f, s if per_market_fill else None)
        pnl = n_trades * mean / 100 * notional
        per[s] = round(pnl)
        tot += pnl
    return {"ret_pct": round(tot / EQUITY * 100, 4), "tmn": round(tot), "per_market_tmn": per}


def uniform(f):
    return {s: {"enabled": True, "size_factor": f} for s in TMN8}


POL = {"ai_latest_review": {s: {"enabled": latest[s]["enabled"], "size_factor": latest[s]["size_factor"]} for s in TMN8},
       "ai_time_weighted_live_so_far": {s: {"enabled": True, "size_factor": eff[s]["avg_size_when_enabled"],
                                            "enabled_share": eff[s]["enabled_share"]} for s in TMN8},
       "all_on_0.4 (defensive cap)": uniform(0.4), "all_on_0.7": uniform(0.7), "all_on_1.0 (normal)": uniform(1.0)}
SCEN = {"base: live rule, tick spreads": ("live_tick_ff", 1.0, False),
        "pessimistic entry: full spread on entry": ("live_tick_ff_fullentry", 1.0, False),
        "tick-calibrated rate (x0.28)": ("live_tick_ff", None, False),
        "base, pooled mean per trade": ("live_tick_ff", 1.0, True),
        "base, per-market depth fill": ("live_tick_ff", 1.0, False, True)}
cal = json.loads((OUT / "calib_freq.json").read_text(encoding="utf-8"))["_total"]["0.3%"]
RATE_CAL = cal["observed_tick_minutes"] / cal["expected_candle_minutes_in_tick_period"]
table = {}
for sc, (var, rm, pooled, *pmf) in SCEN.items():
    table[sc] = {pn: expected_45h(var, pol, RATE_CAL if rm is None else rm, pooled, per_market_fill=bool(pmf and pmf[0]))
                 for pn, pol in POL.items()}
rates = bt["backtests"]["live_tick_ff"]["per_market_trades_per_day"]
wfill = {str(f): round(sum(rates.get(s_, 0) * fill(f, s_) for s_ in TMN8) / sum(rates.values()) * 100, 1) for f in (0.25, 0.4, 0.7, 1.0)}

# cross-check: linear model vs the chronological replay (fixed sizes, base depth) from part 2
xcheck = {}
for f, key in [(0.4, "fixed_0.4"), (0.7, "fixed_0.7"), (1.0, "fixed_1.0")]:
    xcheck[str(f)] = {"linear_45h_pct": expected_45h("live_tick_ff", uniform(f))["ret_pct"],
                      "replay_45h_pct": bt["account_replay"]["live_tick_ff"]["base"][key]["expected_45h_ret_pct_linear"],
                      "windows_45h_mean_pct": bt["windows_45h_live_tick_ff_base_depth"].get(key, {}).get("mean_pct"),
                      "monte_carlo_45h_mean_pct": bt["monte_carlo_45h_live_tick_ff_base_depth"].get(key, {}).get("mean_pct")}

res = {"live_window": {"from": str(pd.to_datetime(t0, unit="s")), "to": str(pd.to_datetime(t_end, unit="s")),
                       "hours": round(total / 3600, 2)},
       "reviews": reviews, "mode_share_of_time": {k: round(v / total, 3) for k, v in mode_time.items()},
       "effective_policy_live_so_far": eff, "latest_policy": {s: {k: latest[s][k] for k in ("enabled", "size_factor", "entry_dev", "max_hold_min")} for s in TMN8},
       "ai_disable_reason_vs_measured_spread": spr_check, "tick_median_spread_pct": overall_spread,
       "rate_calibration_factor": round(RATE_CAL, 3),
       "per_market_fill_pct": {s_: {str(k): round(v * 100, 1) for k, v in PM_FILL[s_].items()} | {"source": PM_SRC[s_]} for s_ in TMN8},
       "trade_weighted_fill_pct": wfill,
       "expected_45h": table, "linear_vs_replay_check": xcheck,
       "account_state_last": equity_ev[-1] if equity_ev else None}
dump(res, "ai_policy.json")

pd.set_option("display.width", 250)
print("live window", res["live_window"], "mode share", res["mode_share_of_time"])
for r in reviews:
    print(r["t"], "final:", r["risk_mode_final"], "analyst:", r["analyst_said"], "disabled:", r["disabled"], "sizes:", r["sizes"])
print("effective:", eff)
print("spread check:")
print(pd.DataFrame(spr_check).drop(columns=["reason_fa"]).to_string())
print("tick median spreads:", overall_spread)
print("rate calibration factor", RATE_CAL)
print("per-market fill", {s_: (PM_SRC[s_], PM_FILL[s_]) for s_ in TMN8})
print("trade-weighted fill", wfill)
for sc, d in table.items():
    print(sc, {k: (v["ret_pct"], v["tmn"]) for k, v in d.items()})
print("xcheck", xcheck)
