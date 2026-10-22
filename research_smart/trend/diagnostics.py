"""Sanity checks + gross-edge diagnostics; appends 'diagnostics' to results.json."""
import os as _os
from pathlib import Path as _P
HERE = str(_P(__file__).resolve().parent)          # this study's folder (outputs)
REPO = str(_P(__file__).resolve().parents[2])      # repo root (research/candles_30d, research/premium)
MAIN = _os.environ.get("WALLEX_MAIN", str(_P(REPO).parent / "wallex-bot"))   # live bot folder (ticks.csv, team only)
import json, os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trend_lib import *  # noqa

OUT = os.path.dirname(os.path.abspath(__file__))
res = json.load(open(os.path.join(OUT, "results.json")))
P = load_30d(1)
is0, is1 = hidx(P, IS_START), hidx(P, SPLIT) + 1
oo0, oo1 = hidx(P, SPLIT), hidx(P, OOS_END) + 1
diag = {}

# 1) simulator sanity: buy at IS start, hold to SPLIT -> must equal buy&hold
n = len(P["hours"])
sig = {b: dict(enter=np.r_[np.ones(1, bool), np.zeros(n - 1, bool)] if b == "BTC" else np.zeros(n, bool),
               exit=np.zeros(n, bool), atr=np.ones(n), trail=1e30, maxhold=10 ** 9, rank=np.zeros(n))
       for b in P["sym"]}
for b in sig:
    if b == "BTC":
        e = np.zeros(n, bool); e[is0] = True; sig[b]["enter"] = e
tr, eq = sim_event(P, sig, is0, is1, 0)
diag["sanity_hold_BTC_IS_net_pct"] = round(100 * tr[0]["net"], 3)
diag["sanity_buyhold_BTC_IS_pct"] = res["buy_hold"]["IS"]["BTC"]
diag["sanity_equity_end_pct"] = round(100 * (eq[-1] / ACCOUNT - 1), 3)


# 2) gross edge (mid-to-mid, before fee and spread) per grid rule
def gross(t):
    h = HALF[t["sym"]]
    return (t["exit_bid"] / (1 - h)) / (t["entry_ask"] / (1 + h)) - 1


rows = []
for fam, p in GRID:
    run = make_runner(P, fam, p)
    ti, _ = run(is0, is1)
    to, _ = run(oo0, oo1)
    rows.append(dict(rule=rule_name(fam, p),
                     IS_gross_mean_pct=round(100 * np.mean([gross(t) for t in ti]), 3) if ti else None,
                     OOS_gross_mean_pct=round(100 * np.mean([gross(t) for t in to]), 3) if to else None,
                     IS_cost_mean_pct=round(100 * np.mean([gross(t) - t["net"] for t in ti]), 3) if ti else None))
diag["gross_edge"] = rows
g_is = [r["IS_gross_mean_pct"] for r in rows]
g_oos = [r["OOS_gross_mean_pct"] for r in rows]
diag["gross_summary"] = dict(IS_rules_with_positive_gross=int(sum(x > 0 for x in g_is)),
                             OOS_rules_with_positive_gross=int(sum(x > 0 for x in g_oos)),
                             n_rules=len(rows),
                             IS_gross_median_pct=float(np.median(g_is)), OOS_gross_median_pct=float(np.median(g_oos)),
                             mean_roundtrip_cost_pct=float(np.mean([r["IS_cost_mean_pct"] for r in rows])))

# 3) concentration of the only positive chosen OOS result (family B)
tb = res["chosen"]["B"]["trades_OOS"]
nets = sorted([t["net"] for t in tb], reverse=True)
diag["B_OOS_sum_pct"] = round(100 * sum(nets), 2)
diag["B_OOS_sum_without_best_trade_pct"] = round(100 * sum(nets[1:]), 2)
diag["B_OOS_best_trade"] = max(tb, key=lambda t: t["net"])

# 4) all 32 rules: OOS and pre-sample (60d) summary
diag["grid_OOS_positive"] = int(sum(g["OOS"]["account_return_pct"] > 0 for g in res["grid"]))
diag["grid_IS_positive"] = int(sum(g["IS"]["account_return_pct"] > 0 for g in res["grid"]))
diag["grid_60d_pre_positive"] = int(sum(v["pre_sample"]["account_return_pct"] > 0 for v in res["h60"]["rules"].values()))
diag["grid_60d_full_positive"] = int(sum(v["full"]["account_return_pct"] > 0 for v in res["h60"]["rules"].values()))
res["diagnostics"] = diag
json.dump(res, open(os.path.join(OUT, "results.json"), "w"), indent=1, default=str)
print(json.dumps({k: v for k, v in diag.items() if k != "gross_edge"}, indent=1, default=str))
for r in rows:
    print(r)
