"""Collect everything into results.json (run after event_study.py, walkforward.py, ticks_check.py)."""
import os as _os
from pathlib import Path as _P
HERE = str(_P(__file__).resolve().parent)          # this study's folder (outputs)
REPO = str(_P(__file__).resolve().parents[2])      # repo root (research/candles_30d, research/premium)
MAIN = _os.environ.get("WALLEX_MAIN", str(_P(REPO).parent / "wallex-bot"))   # live bot folder (ticks.csv, team only)
import json
import os
import numpy as np
import pandas as pd
import common as c
import engine as E
import walkforward as W

OUT = c.OUT
es = pd.read_csv(os.path.join(OUT, "event_study_summary.csv"))
ev = pd.read_csv(os.path.join(OUT, "events_all.csv"))
wf = pd.read_csv(os.path.join(OUT, "walkforward_grid.csv"))
ticks = json.load(open(os.path.join(OUT, "ticks_summary.json")))
base = json.load(open(os.path.join(OUT, "baseline_unconditional.json")))


def detail(cfg, ex, lag):
    cands = W.candidates(cfg)
    out = {}
    for part, (t0, t1) in (("IS", (W.warm_i, W.split_i)), ("OOS", (W.split_i, W.N))):
        tr = W.run(cands, ex, lag, cfg[1], t0, t1)
        m, _ = W.metrics(tr, t0, t1, part)
        if len(tr):
            tr = tr.assign(t=[str(W.grid[i]) for i in tr.i])
            m["trades"] = tr[["market", "t", "net"]].assign(net=lambda d: (d.net * 100).round(3)).to_dict("records") if part == "OOS" else None
            if tr.net.sum() > 0:
                m["sum_without_best_trade_pct"] = round(float((tr.net.sum() - tr.net.max()) * 100), 2)
        out[part] = m
    return out


# Pooled event study (all variants, de-duplicated events) - continuation vs reversion decomposition
pooled = {}
for v in ("A", "B", "C"):
    g = ev[ev.variant == v]
    pooled[v] = {f"H{H}": {"gross_wallex_pct": round(g[f"gross{H}"].mean() * 100, 3), "global_pct": round(g[f"glob{H}"].mean() * 100, 3),
                           "premium_change_pct": round(g[f"dprem{H}"].mean() * 100, 3),
                           "net_instant_pct": round(g[f"net{H}_l0"].mean() * 100, 3), "net_next_min_pct": round(g[f"net{H}_l1"].mean() * 100, 3),
                           "share_gross_above_0.6pct": round(float((g[f"gross{H}"] > 0.006).mean() * 100), 1)}
                 for H in E.HOLDS}
    pooled[v]["n_event_rows"] = int(len(g))
    pooled[v]["mean_surge_size_pct"] = {"dp": round(g.dp.mean() * 100, 3), "r": round(g.r.mean() * 100, 3), "g": round(g.g.mean() * 100, 3)}

l1 = wf[(wf.lag == 1) & (wf.IS_n >= 20)].sort_values("IS_acct_ret_pct", ascending=False)
best = l1.iloc[0]
best_cfg = (best.variant, int(best.k), float(best.thr), float(best.Z))
best_ex = (best.exit, int(best.H))
canon_cfg, canon_ex = ("A", 5, 0.005, 3.0), ("fixed", 30)

res = {
    "family": "Wallex-local demand surges (premium momentum / pump continuation), long only",
    "verdict": "NOT VIABLE",
    "setup": {
        "markets": c.tradable(), "skipped_spread_gt_0.6pct": ["POLTMN", "SANDTMN"],
        "half_spread_h": {m: round(c.spreads()[m] / 2, 6) for m in c.tradable()},
        "fees": "taker 0.3% each side; net = 0.997^2 * sell/buy - 1; buy = close*(1+h), sell = close*(1-h)",
        "in_sample": "2026-09-22 13:00 .. 10-12 07:00 UTC (first 6 h = volume-baseline warm-up)",
        "out_of_sample": "2026-10-12 07:00 .. 10-22 07:00 UTC",
        "variants": {
            "A": "premium (close/fair-1) up >= Y over k min, Wallex k-min return > 0, premium above its trailing-24h median",
            "B": "Wallex k-min return >= X and (Wallex - global) k-min return >= X/2",
            "C": "B and global k-min return > 0",
            "all": "k-bar toman volume >= Z x trailing-24h median of k-bar volume (floor 0.25 x mean), bar i traded, >= 2 traded bars in window",
        },
        "grid": {"k": E.KS, "Y": E.A_Y, "X": E.B_X, "Z": E.ZS, "exits": E.EXITS, "stop_for_conditional_exits": E.STOP},
        "n_configs": int(len(wf)), "portfolio": "5M toman, 1M fixed per trade (no compounding), max 3 positions, 1 per market",
        "selection_rule": "lag=1 (next-minute entry), IS trades >= 20, highest IS account return",
        "note_on_returns": "account returns are fixed-1M-per-trade sums; values below -100% mean the account would have been wiped out",
    },
    "event_study": {
        "pooled_by_variant_dedup60": pooled,
        "unconditional_baseline_net_pct_by_market": {m: {h: round(v, 3) for h, v in b.items()} for m, b in base.items()},
        "grid_summary_csv": "event_study_summary.csv",
        "finding": "After a local surge the Wallex price falls back: premium change is negative at every horizon and swamps the small "
                   "positive global drift. No grid cell has positive IS net at any horizon; gross continuation is below the 0.6% fee "
                   "hurdle in every IS cell.",
    },
    "walk_forward": {
        "grid_csv": "walkforward_grid.csv",
        "configs_positive_IS": {"lag0": int((wf[wf.lag == 0].IS_acct_ret_pct > 0).sum()), "lag1": int((wf[wf.lag == 1].IS_acct_ret_pct > 0).sum()),
                                "of": int((wf.lag == 0).sum())},
        "configs_positive_OOS": {"lag0": int((wf[wf.lag == 0].OOS_acct_ret_pct > 0).sum()), "lag1": int((wf[wf.lag == 1].OOS_acct_ret_pct > 0).sum()),
                                 "of": int((wf.lag == 0).sum())},
        "median_mean_net_pct_per_trade": {"IS_lag1": round(float(wf[wf.lag == 1].IS_mean_net_pct.median()), 3),
                                          "OOS_lag1": round(float(wf[wf.lag == 1].OOS_mean_net_pct.median()), 3)},
        "selected": {"entry": dict(zip(["variant", "k", "thr", "Z"], best_cfg)), "exit": {"kind": best_ex[0], "H_min": best_ex[1]},
                     "next_minute_lag1": detail(best_cfg, best_ex, 1), "instant_lag0": detail(best_cfg, best_ex, 0)},
        "canonical_simple_rule": {"entry": dict(zip(["variant", "k", "thr", "Z"], canon_cfg)), "exit": {"kind": canon_ex[0], "H_min": canon_ex[1]},
                                  "next_minute_lag1": detail(canon_cfg, canon_ex, 1)},
        "top5_by_IS_lag1": l1.head(5)[["variant", "k", "thr", "Z", "exit", "H", "IS_n", "IS_acct_ret_pct", "OOS_n", "OOS_mean_net_pct",
                                       "OOS_acct_ret_pct", "OOS_max_dd_pct", "OOS_win31h_pos"]].to_dict("records"),
    },
    "live_ticks": ticks,
    "by_product": "Local surges are reliably followed by give-back (premium falls ~0.3-0.5% within 15-60 min on average). "
                  "Useful for other strategies only as a SELL-into-surge exit / do-not-buy filter, not as an entry.",
}
for key in ("next_minute_lag1", "instant_lag0"):
    res["walk_forward"]["selected"][key]["IS"].pop("trades", None)
json.dump(res, open(os.path.join(OUT, "results.json"), "w"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
print(json.dumps(res["walk_forward"]["selected"], indent=1, default=str)[:6000])
print(json.dumps(res["walk_forward"]["canonical_simple_rule"]["next_minute_lag1"]["OOS"], default=str)[:1500])
print(json.dumps(pooled["A"], indent=0)[:1500])
