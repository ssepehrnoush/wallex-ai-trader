"""Walk-forward test of the pre-declared trend / momentum grid. Writes results.json."""
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
rng = np.random.default_rng(7)


def seg(P, a, b):
    return hidx(P, a), hidx(P, b) + 1


def weekly(trades, start, end):
    rows = []
    t = start
    while t < end:
        u = min(t + pd.Timedelta(days=7), end)
        sel = [x for x in trades if t <= pd.Timestamp(x["exit"]) <= u]
        rows.append(dict(week=f"{t:%m-%d}..{u:%m-%d}", trades=len(sel),
                         sum_net_pct=round(100 * sum(x["net"] for x in sel), 2),
                         pnl_toman=round(sum(x["pnl"] for x in sel))))
        t = u
    return rows


def windows(P, run, a, b, hours=31):
    res = []
    s = a
    while s + pd.Timedelta(hours=hours) <= b:
        h0, h1 = seg(P, s, s + pd.Timedelta(hours=hours))
        tr, eq = run(h0, h1)
        res.append(dict(start=str(s), ret=float(eq[-1] / ACCOUNT - 1), n=len(tr), dd=max_dd(eq)))
        s += pd.Timedelta(hours=1)
    r = np.array([x["ret"] for x in res])
    return dict(n_windows=len(res), share_positive=round(float((r > 1e-9).mean()), 3),
                share_negative=round(float((r < -1e-9).mean()), 3),
                share_flat=round(float((np.abs(r) <= 1e-9).mean()), 3),
                mean_ret_pct=round(100 * float(r.mean()), 3), median_ret_pct=round(100 * float(np.median(r)), 3),
                worst_ret_pct=round(100 * float(r.min()), 3), best_ret_pct=round(100 * float(r.max()), 3),
                mean_trades=round(float(np.mean([x["n"] for x in res])), 2),
                worst_dd_pct=round(100 * float(max(x["dd"] for x in res)), 3))


def random_baseline(P, trades, h0, h1, reps=2000):
    if not trades:
        return None
    actual = np.mean([t["net"] for t in trades])
    means = []
    for _ in range(reps):
        nets = []
        for t in trades:
            d = P["sym"][t["sym"]]
            hold = max(t["hold_h"], 1)
            hi = h1 - 1 - hold
            if hi <= h0:
                continue
            h = rng.integers(h0, hi)
            nets.append((1 - FEE) ** 2 * d["bid"][h + hold] / d["ask"][h] - 1)
        means.append(np.mean(nets))
    means = np.array(means)
    return dict(actual_mean_pct=round(100 * actual, 3), random_mean_pct=round(100 * means.mean(), 3),
                random_p05_pct=round(100 * np.percentile(means, 5), 3),
                random_p95_pct=round(100 * np.percentile(means, 95), 3),
                share_random_ge_actual=round(float((means >= actual).mean()), 3))


def buy_hold(P, a, b):
    h0, h1 = seg(P, a, b)
    out = {}
    for s, d in P["sym"].items():
        out[s] = round(100 * ((1 - FEE) ** 2 * d["bid"][h1 - 1] / d["ask"][h0] - 1), 2)
    out["EQUAL_WEIGHT"] = round(float(np.mean(list(out.values()))), 2)
    out["USDTTMN_mid"] = round(100 * (P["usdt"][h1 - 1] / P["usdt"][h0] - 1), 2)
    return out


def main():
    res = {"universe": UNIVERSE, "half_spread": HALF, "excluded": ["POL", "SAND (no spread measurement)"],
           "periods": dict(IS=f"{IS_START}..{SPLIT}", OOS=f"{SPLIT}..{OOS_END}"),
           "execution": "decision at hourly close T; fill at Wallex 1m close of the bar starting at T "
                        "(~1 min later), buy close*(1+h), sell close*(1-h), taker 0.3% each side",
           "grid": [], "chosen": {}}
    P = load_30d(delay_min=1)
    is0, is1 = seg(P, IS_START, SPLIT)
    oo0, oo1 = seg(P, SPLIT, OOS_END)
    days_is = (SPLIT - IS_START).days
    days_oos = (OOS_END - SPLIT).days
    res["buy_hold"] = dict(IS=buy_hold(P, IS_START, SPLIT), OOS=buy_hold(P, SPLIT, OOS_END))
    runners = {}
    for fam, p in GRID:
        name = rule_name(fam, p)
        run = make_runner(P, fam, p)
        runners[name] = (fam, p, run)
        ti, ei = run(is0, is1)
        to, eo = run(oo0, oo1)
        res["grid"].append(dict(rule=name, family=fam, IS=stats(ti, ei, days_is), OOS=stats(to, eo, days_oos)))
        print(name, "IS", res["grid"][-1]["IS"]["trades"], res["grid"][-1]["IS"]["account_return_pct"],
              "OOS", res["grid"][-1]["OOS"]["trades"], res["grid"][-1]["OOS"]["account_return_pct"], flush=True)
    # selection on IS only: max IS account return with >= 15 IS trades
    for fam in "ABC":
        cand = [g for g in res["grid"] if g["family"] == fam and g["IS"]["trades"] >= 15]
        best = max(cand, key=lambda g: g["IS"]["account_return_pct"])
        name = best["rule"]
        fam_, p, run = runners[name]
        to, eo = run(oo0, oo1)
        ti, ei = run(is0, is1)
        fam_grid = [g for g in res["grid"] if g["family"] == fam]
        ch = dict(rule=name, IS=best["IS"], OOS=best["OOS"],
                  family_grid_OOS_positive_share=round(np.mean([g["OOS"]["account_return_pct"] > 0 for g in fam_grid]), 3),
                  family_grid_OOS_median_return_pct=round(float(np.median([g["OOS"]["account_return_pct"] for g in fam_grid])), 3),
                  weekly=weekly(ti, IS_START, SPLIT) + weekly(to, SPLIT, OOS_END),
                  windows31_OOS=windows(P, run, SPLIT, OOS_END),
                  windows31_IS=windows(P, run, IS_START, SPLIT),
                  random_baseline_OOS=random_baseline(P, to, oo0, oo1),
                  random_baseline_IS=random_baseline(P, ti, is0, is1),
                  exit_reasons_OOS=pd.Series([t["reason"] for t in to]).value_counts().to_dict() if to else {},
                  per_sym_OOS={s: dict(n=len(v), sum_net_pct=round(100 * sum(t["net"] for t in v), 2))
                               for s in UNIVERSE for v in [[t for t in to if t["sym"] == s]] if v},
                  trades_OOS=to)
        res["chosen"][fam] = ch
        print("CHOSEN", fam, name, ch["OOS"], ch["windows31_OOS"], ch["random_baseline_OOS"], flush=True)

    # execution-delay sensitivity for chosen rules
    for dl in (5, 15):
        Pd = load_30d(delay_min=dl)
        for fam, ch in res["chosen"].items():
            fam_, p, _ = runners[ch["rule"]]
            run = make_runner(Pd, fam_, p)
            ti, ei = run(is0, is1)
            to, eo = run(oo0, oo1)
            ch[f"delay{dl}m"] = dict(IS=stats(ti, ei, days_is), OOS=stats(to, eo, days_oos))

    # proxy-global (Wallex/USDT) on the same 30d -- consistency check for the 60d test
    Pp = load_30d(delay_min=1, global_src="proxy")
    for fam, ch in res["chosen"].items():
        fam_, p, _ = runners[ch["rule"]]
        run = make_runner(Pp, fam_, p)
        ti, ei = run(is0, is1)
        to, eo = run(oo0, oo1)
        ch["proxy_global_30d"] = dict(IS=stats(ti, ei, days_is), OOS=stats(to, eo, days_oos))

    # 60-day history (Wallex 60m, proxy global, next-bar-open fills, 7 coins, no ARB)
    P6 = load_60d()
    a6 = P6["hours"][0] + pd.Timedelta(hours=49)
    pre_end = IS_START
    e6 = P6["hours"][-1]  # last label -> decision e6+1h, fill falls back to its close
    res["h60"] = dict(window=f"{a6}..{e6}", buy_hold_pre=buy_hold(P6, a6, pre_end),
                      buy_hold_full=buy_hold(P6, a6, e6), rules={})
    for fam_, p in GRID:
        name = rule_name(fam_, p)
        run = make_runner(P6, fam_, p)
        h0, h1 = seg(P6, a6, pre_end)
        tp, ep = run(h0, h1)
        h0f, h1f = seg(P6, a6, e6)
        tf, ef = run(h0f, h1f)
        res["h60"]["rules"][name] = dict(pre_sample=stats(tp, ep, (pre_end - a6).days),
                                         full=stats(tf, ef, (e6 - a6).days))
    for fam, ch in res["chosen"].items():
        ch["h60"] = res["h60"]["rules"][ch["rule"]]
        r6 = make_runner(P6, *runners[ch["rule"]][:2])
        ch["h60"]["windows31_pre"] = windows(P6, r6, a6, pre_end)
        h0, h1 = seg(P6, a6, pre_end)
        tp, ep = r6(h0, h1)
        ch["h60"]["weekly_pre"] = weekly(tp, a6, pre_end)
    json.dump(res, open(os.path.join(OUT, "results.json"), "w"), indent=1, default=str)
    print("saved")


if __name__ == "__main__":
    main()
