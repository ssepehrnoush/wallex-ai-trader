"""Part 1: top-of-book depth vs planned position size, from the recorded live ticks (TMN markets).

Entry side: the engine buys qty = min(planned / ask, ask_qty) ONCE per signal (a held symbol is
never added to), so the fill is capped by the top-ask notional (ask x ask_qty) at the entry tick.
Exit side: a target exit is a LIMIT at the bid for the whole position, so only the top-bid
notional fills at once (the rest is re-tried on later ticks); stop/time exits cross 0.3% below
the bid (depth beyond the top level is not recorded).

Writes out/depth_ticks.json and out/depth_entry_episodes.csv
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from common import ENTRY_MIN_FILL, OUT, TMN8, dump, load_ticks, planned_notional

SIZES = {"0.25": 0.25, "0.4": 0.4, "0.7": 0.7, "1.0": 1.0}
PLAN = {k: planned_notional(v) for k, v in SIZES.items()}
THRS = [0.002, 0.003, 0.004, 0.005, 0.007]
GAP_S = 10.0          # ticks further apart than this break an episode (restarts)
MAX_SPREAD = 0.006

t = load_ticks()
res: dict = {"planned_notional_tmn": {k: round(v) for k, v in PLAN.items()},
             "tick_sample": {"rows": int(len(t)),
                             "start_utc": str(pd.to_datetime(t.t.min(), unit="s")),
                             "end_utc": str(pd.to_datetime(t.t.max(), unit="s")),
                             "per_market": t.groupby("symbol").size().to_dict(),
                             "hours_per_market": (t.groupby("symbol").size() * 2.5 / 3600).round(2).to_dict()}}


def qtiles(x: pd.Series) -> dict:
    if len(x) == 0:
        return {}
    q = x.quantile([0.05, 0.1, 0.25, 0.5]).to_dict()
    return {"p5": round(q[0.05]), "p10": round(q[0.1]), "p25": round(q[0.25]), "p50": round(q[0.5]), "min": round(x.min())}


def fill_stats(depth: pd.Series) -> dict:
    out = {}
    for k, p in PLAN.items():
        f = np.minimum(depth.to_numpy() / p, 1.0)
        out[k] = {"binds_pct": round(float((depth < p).mean() * 100), 2),
                  "mean_fill_pct": round(float(f.mean() * 100), 2),
                  "p10_fill_pct": round(float(np.quantile(f, 0.1) * 100), 1) if len(f) else None}
    return out


# ---- (a) unconditional and conditional depth per market --------------------------------------
uncond, cond = {}, {}
for s, g in t.groupby("symbol"):
    uncond[s] = {"ticks": int(len(g)), "spread_median_pct": round(g.spread.median() * 100, 3),
                 "ask_notional": qtiles(g.ask_n), "bid_notional": qtiles(g.bid_n),
                 "entry_fill_vs_plan": fill_stats(g.ask_n), "exit_fill_vs_plan": fill_stats(g.bid_n)}
    cond[s] = {}
    for thr in THRS:
        m = g[(g.buy_dev <= -thr)]
        cond[s][f"buy_dev<=-{thr * 100:.1f}%"] = {
            "ticks": int(len(m)), "minutes": round(len(m) * 2.5 / 60, 1),
            "ask_notional": qtiles(m.ask_n), "entry_fill_vs_plan": fill_stats(m.ask_n) if len(m) else {}}
res["unconditional"] = uncond
res["conditional_on_buy_dev"] = cond

# pooled over markets, conditional buckets
pooled = {}
for thr in THRS:
    m = t[t.buy_dev <= -thr]
    pooled[f"buy_dev<=-{thr * 100:.1f}%"] = {"ticks": int(len(m)), "by_market": m.groupby("symbol").size().to_dict(),
                                            "ask_notional": qtiles(m.ask_n),
                                            "entry_fill_vs_plan": fill_stats(m.ask_n) if len(m) else {}}
pooled["all_ticks"] = {"ticks": int(len(t)), "ask_notional": qtiles(t.ask_n), "entry_fill_vs_plan": fill_stats(t.ask_n),
                       "bid_notional": qtiles(t.bid_n), "exit_fill_vs_plan": fill_stats(t.bid_n)}
res["pooled"] = pooled

# ---- (b) engine-like entry episodes + simulated exits on ticks ---------------------------------
rows = []
for s, g in t.groupby("symbol"):
    g = g.reset_index(drop=True)
    T, bd, sd = g.t.to_numpy(), g.buy_dev.to_numpy(), g.sell_dev.to_numpy()
    ask, bid, an, bn, spr = g.ask.to_numpy(), g.bid.to_numpy(), g.ask_n.to_numpy(), g.bid_n.to_numpy(), g.spread.to_numpy()
    hold_s = (15 if s == "ARBTMN" else 25) * 60
    for thr in [0.003, 0.004, 0.005, 0.007]:
        i, n = 0, len(g)
        while i < n:
            if not (bd[i] <= -thr and spr[i] <= MAX_SPREAD):
                i += 1
                continue
            # episode start: first qualifying tick; engine places an order only if top-ask notional >= min
            j = i
            ep_max = an[i]
            entry_k = None
            while j < n and bd[j] <= -thr and (j == i or T[j] - T[j - 1] <= GAP_S):
                ep_max = max(ep_max, an[j])
                if entry_k is None and an[j] >= ENTRY_MIN_FILL and spr[j] <= MAX_SPREAD:
                    entry_k = j
                j += 1
            ep_end = j
            rec = {"symbol": s, "thr_pct": thr * 100, "t": T[i], "utc": str(pd.to_datetime(T[i], unit="s")),
                   "episode_ticks": ep_end - i, "buy_dev_first_pct": round(bd[i] * 100, 3),
                   "ask_n_first": an[i], "ask_n_episode_max": ep_max, "entered": entry_k is not None}
            if entry_k is not None:
                k = entry_k
                rec["ask_n_entry"] = an[k]
                e = ask[k]
                # follow the engine's exit rules on later ticks
                x, why = None, "end_of_data"
                for m in range(k + 1, n):
                    if T[m] - T[m - 1] > 600:     # data gap (restart): stop following
                        x, why = m - 1, "data_gap"
                        break
                    if bid[m] <= e * (1 - 0.009):
                        x, why = m, "stop"
                        break
                    if sd[m] >= -0.0005 and bid[m] >= e * (1 + 2 * 0.003 + 0.0005):
                        x, why = m, "target"
                        break
                    if T[m] - T[k] >= hold_s:
                        x, why = m, "time"
                        break
                if x is None:
                    x = n - 1
                rec.update({"exit_reason": why, "bid_n_exit": bn[x], "held_min": round((T[x] - T[k]) / 60, 1),
                            "gross_ret_pct": round((bid[x] / e - 1) * 100, 3)})
                # target: sell min(remaining, bid_n) per tick while the target condition still holds
                for kk, p in PLAN.items():
                    pos = min(p, an[k])
                    rec[f"fill_frac_{kk}"] = round(pos / p, 4)
                    rec[f"exit_cover_{kk}"] = round(min(1.0, bn[x] / pos), 4)
            rows.append(rec)
            i = ep_end
ep = pd.DataFrame(rows)
ep.to_csv(OUT / "depth_entry_episodes.csv", index=False)

epi = {}
for thr, g in ep.groupby("thr_pct"):
    d = {"episodes": int(len(g)), "by_market": g.groupby("symbol").size().to_dict(),
         "never_fillable_pct": round(float((~g.entered).mean() * 100), 1)}
    e = g[g.entered]
    if len(e):
        d["entry_fill_pct_mean"] = {k: round(float(e[f"fill_frac_{k}"].mean() * 100), 1) for k in PLAN}
        d["entry_depth_binds_pct"] = {k: round(float((e[f"fill_frac_{k}"] < 1).mean() * 100), 1) for k in PLAN}
        d["ask_n_entry"] = qtiles(e.ask_n_entry)
        d["exit_reasons"] = e.exit_reason.value_counts().to_dict()
        d["bid_n_exit"] = qtiles(e.bid_n_exit)
        d["exit_cover_pct_mean"] = {k: round(float(e[f"exit_cover_{k}"].mean() * 100), 1) for k in PLAN}
        d["exit_top_bid_short_pct"] = {k: round(float((e[f"exit_cover_{k}"] < 1).mean() * 100), 1) for k in PLAN}
    epi[f"{thr:.1f}%"] = d
res["entry_episodes"] = epi

# ---- (c) exit-moment proxies ---------------------------------------------------------------------
exitp = {}
for s, g in t.groupby("symbol"):
    g = g.reset_index(drop=True)
    tz = g[g.sell_dev >= -0.0005]
    # stop proxy: bid down >= 0.5% vs the max bid of the previous 5 minutes (falling market)
    ser = pd.Series(g.bid.to_numpy(), index=pd.to_datetime(g.t, unit="s"))
    prior_max = ser.rolling("300s").max().shift(1).to_numpy()
    fall = g[(g.bid.to_numpy() <= prior_max * (1 - 0.005))]
    exitp[s] = {"target_zone_ticks": int(len(tz)), "target_zone_bid_notional": qtiles(tz.bid_n),
                "target_zone_fill_vs_plan": fill_stats(tz.bid_n) if len(tz) else {},
                "falling_ticks": int(len(fall)), "falling_bid_notional": qtiles(fall.bid_n),
                "falling_fill_vs_plan": fill_stats(fall.bid_n) if len(fall) else {}}
res["exit_moments"] = exitp

dump(res, "depth_ticks.json")

# ---- console summary -------------------------------------------------------------------------------
pd.set_option("display.width", 250)
print("planned notional:", {k: round(v) for k, v in PLAN.items()})
print("tick sample:", res["tick_sample"])
tab = pd.DataFrame({s: {"ticks": u["ticks"], "spr%": u["spread_median_pct"],
                        "askN_p5": u["ask_notional"]["p5"], "askN_p50": u["ask_notional"]["p50"],
                        "bidN_p5": u["bid_notional"]["p5"], "bidN_p50": u["bid_notional"]["p50"],
                        "askBind0.4%": u["entry_fill_vs_plan"]["0.4"]["binds_pct"],
                        "askBind1.0%": u["entry_fill_vs_plan"]["1.0"]["binds_pct"],
                        "askFill0.4%": u["entry_fill_vs_plan"]["0.4"]["mean_fill_pct"],
                        "askFill1.0%": u["entry_fill_vs_plan"]["1.0"]["mean_fill_pct"],
                        "bidBind0.4%": u["exit_fill_vs_plan"]["0.4"]["binds_pct"],
                        "bidBind1.0%": u["exit_fill_vs_plan"]["1.0"]["binds_pct"]} for s, u in uncond.items()}).T
print(tab.to_string())
for k, v in pooled.items():
    print(k, v["ticks"], v.get("by_market", ""), v["ask_notional"], v.get("entry_fill_vs_plan"))
for k, v in epi.items():
    print("episodes", k, v)
for s, v in exitp.items():
    print("exit", s, v["target_zone_ticks"], v["target_zone_bid_notional"].get("p5"), v["target_zone_fill_vs_plan"].get("1.0"),
          "| falling", v["falling_ticks"], v["falling_bid_notional"].get("p5") if v["falling_ticks"] else None,
          v["falling_fill_vs_plan"].get("1.0") if v["falling_ticks"] else None)
