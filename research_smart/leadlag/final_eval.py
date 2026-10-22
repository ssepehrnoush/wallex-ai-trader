"""Step 2b + 3: evaluate the selected rules with a plain loop simulator (independent of the vectorised grid code),
with the 3-position portfolio cap, entry variants, per-market breakdown; then replay them on real ticks."""
import os as _os
from pathlib import Path as _P
HERE = str(_P(__file__).resolve().parent)          # this study's folder (outputs)
REPO = str(_P(__file__).resolve().parents[2])      # repo root (research/candles_30d, research/premium)
MAIN = _os.environ.get("WALLEX_MAIN", str(_P(REPO).parent / "wallex-bot"))   # live bot folder (ticks.csv, team only)
import json

import numpy as np
import pandas as pd

from common import *
from search import build, FEE2, FEE_TM
import tick_replay as TR

RULES = {
    # pre-registered selection: best in-sample mean at next-minute entry among configs with >= 30 in-sample trades
    "A_best_IS_n30": dict(k=2, X=0.005, lag="zero", trd=0, pers=1, mode="cu0", stop=9.0, N=30),
    # exploratory: only positive in-sample pocket, needs relaxing the trade floor to n >= 10
    "B_best_IS_n10": dict(k=5, X=0.010, lag="zero", trd=0, pers=0, mode="cu10", stop=0.015, N=10),
}


def signals(a, r):
    W, B, dev, tr = a["W"], a["B"], a["dev"], a["tr"]
    k, X = r["k"], r["X"]
    rb = np.full(len(B), np.nan); rb[k:] = B[k:] / B[:-k] - 1
    rw = np.full(len(W), np.nan); rw[k:] = W[k:] / W[:-k] - 1
    c = rb >= X
    lag = r["lag"]
    if lag == "half":
        c &= rw <= 0.5 * rb
    elif lag == "zero":
        c &= rw <= 0
    elif lag == "dev04":
        c &= dev <= -0.004
    elif lag == "dev07":
        c &= dev <= -0.007
    if r["trd"]:
        c &= tr
    if r["pers"]:
        c &= np.r_[False, rb[:-1] >= X]
    c &= ~np.isnan(dev)
    c[:200] = False
    c[-40:] = False
    return np.where(c)[0], rb, rw


def sim_trade(a, h, r, t, off, entry_mode="close", exit_lag=1):
    """One trade from signal minute t. Returns dict or None."""
    W, Hi, dev, tr = a["W"], a["Hi"], a["dev"], a["tr"]
    e = t + off
    if entry_mode == "traded":          # entry only at a minute that actually traded (first within 5 minutes)
        while e <= t + off + 5 and not tr[e]:
            e += 1
        if not tr[e]:
            return None
    ent = W[e] * (1 + h)
    N, S, mode = r["N"], r["stop"], r["mode"]
    p_lim = None
    if mode.startswith("tp"):
        tp = 0.003 if mode == "tp03" else 0.006
        p_lim = (1 + tp) * ent / FEE_TM
    for j in range(1, N + 1):
        m = e + j
        if p_lim is not None and tr[m] and Hi[m] > p_lim:
            return dict(t=t, e=e, x=m, net=tp, why="tp")
        mark = FEE2 * W[m] * (1 - h) / ent - 1
        if j == N:
            return dict(t=t, e=e, x=m, net=mark, why="time")
        dec = None
        if mark <= -S:
            dec = "stop"
        elif mode.startswith("cu"):
            c = 0.0 if mode == "cu0" else 0.001
            if dev[m] >= -c and mark >= 0:
                dec = "catchup"
        if dec:
            x = min(m + exit_lag, e + N)
            return dict(t=t, e=e, x=x, net=FEE2 * W[x] * (1 - h) / ent - 1, why=dec)
    return None


def run(rule, A, off=1, entry_mode="close", exit_lag=1, cap=3):
    cands = []
    for sym, (a, h) in A.items():
        idx, rb, rw = signals(a, rule)
        busy_until = -1
        for t in idx:
            if t < busy_until:
                continue
            tr_ = sim_trade(a, h, rule, t, off, entry_mode, exit_lag)
            if tr_ is None:
                continue
            tr_.update(sym=sym, rb=rb[t], rw=rw[t], dev=a["dev"][t], ts=str(a["ts"][t]), is_=bool(a["is"][t]))
            cands.append(tr_)
            busy_until = tr_["x"]
    # per-market non-overlap done above; now the global cap of `cap` open positions (by entry time)
    cands.sort(key=lambda d: (d["e"], d["sym"]))
    open_, taken = [], []
    for c in cands:
        open_ = [x for x in open_ if x > c["e"]]
        if len(open_) >= cap:
            continue
        taken.append(c)
        open_.append(c["x"])
    return pd.DataFrame(taken)


def summ(d, days):
    if len(d) == 0:
        return {"n": 0}
    return {"n": int(len(d)), "per_day": round(len(d) / days, 2), "mean%": round(d.net.mean() * 100, 3),
            "sum%": round(d.net.sum() * 100, 2), "win": round((d.net > 0).mean(), 3),
            "worst%": round(d.net.min() * 100, 3), "best%": round(d.net.max() * 100, 3),
            "t_stat": round(d.net.mean() / (d.net.std(ddof=1) / np.sqrt(len(d))), 2) if len(d) > 2 else None,
            "exit_reasons": d.why.value_counts().to_dict()}


def main():
    u = usdt()
    A = {s: (build(s, u), SPREADS[s] / 2) for s in MAIN_MKTS}
    out = {}
    for name, rule in RULES.items():
        res = {"rule": rule}
        for label, kw in {"entry_signal_minute_off0": dict(off=0), "entry_next_minute_off1": dict(off=1),
                          "entry_one_minute_later_off2": dict(off=2), "entry_next_traded_minute": dict(off=1, entry_mode="traded"),
                          "off1_exit_same_minute_optimistic": dict(off=1, exit_lag=0)}.items():
            d = run(rule, A, **kw)
            is_, oos = d[d.is_], d[~d.is_]
            res[label] = {"IS_20d": summ(is_, 20), "OOS_10d": summ(oos, 10), "all_30d": summ(d, 30)}
            if label == "entry_next_minute_off1":
                res["per_market_off1"] = {s: {"IS": summ(g[g.is_], 20), "OOS": summ(g[~g.is_], 10)} for s, g in d.groupby("sym")}
                d.to_csv(OUT / f"trades_{name}_off1.csv", index=False)
            print(name, label, "IS", res[label]["IS_20d"], "\n   OOS", res[label]["OOS_10d"], flush=True)
        out[name] = res
    # tick replay
    t = load_ticks()
    G = TR.prep(t)
    G = {s: g for s, g in G.items() if s in MAIN_MKTS}
    span_h = (t.t.max() - t.t.min()) / 3600
    tick = {"span_hours": round(span_h, 2), "first_utc": str(pd.to_datetime(t.t.min(), unit="s", utc=True)),
            "last_utc": str(pd.to_datetime(t.t.max(), unit="s", utc=True))}
    for name, rule in RULES.items():
        for lab, dl in {"next_tick": 0.0, "plus_60s": 60.0}.items():
            d = TR.replay(G, rule, entry_delay=dl)
            tick[f"{name}|{lab}"] = TR.summ(d)
            if len(d):
                tick[f"{name}|{lab}"]["trades"] = d[["sym", "utc", "rb", "rw", "dev", "spread_in", "net", "why", "depth_ok"]].round(5).to_dict("records")
            print("TICKS", name, lab, TR.summ(d), flush=True)
        # looser versions of the same rule so the tick check has more than a handful of events
        for X in [0.002, 0.003]:
            r2 = dict(rule, X=X)
            d = TR.replay(G, r2, entry_delay=0.0)
            tick[f"{name}|X={X}|next_tick"] = TR.summ(d)
            print("TICKS loosened", name, X, TR.summ(d), flush=True)
    out["tick_replay"] = tick
    json.dump(out, open(OUT / "final_eval.json", "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
