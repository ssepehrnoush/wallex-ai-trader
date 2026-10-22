"""Step 3: replay lead-lag rules on the real order-book ticks (bid/ask every 2.5 s, ~18 h).

Binance move proxy = change of the 'fair' column over k minutes (fair = Binance x USDTTMN).
Wallex move = change of mid over the same window.  dev = mid/fair - 1 - baseline (bot's live trailing premium).
Entry = ask of the first tick after the signal tick (about 2.5 s later), or the ask 60 s later (robustness).
Exits evaluated on every tick; taker exits fill at the bid of the next tick; maker TP fills when a later bid >= limit.
"""
import os as _os
from pathlib import Path as _P
HERE = str(_P(__file__).resolve().parent)          # this study's folder (outputs)
REPO = str(_P(__file__).resolve().parents[2])      # repo root (research/candles_30d, research/premium)
MAIN = _os.environ.get("WALLEX_MAIN", str(_P(REPO).parent / "wallex-bot"))   # live bot folder (ticks.csv, team only)
import json
import numpy as np
import pandas as pd

from common import *

FEE2 = (1 - FEE_T) ** 2
FEE_TM = (1 - FEE_T) * (1 - FEE_M)


def prep(t):
    out = {}
    for s, g in t.groupby("symbol"):
        g = g.sort_values("t").drop_duplicates("t").reset_index(drop=True)
        g = g[(g.bid > 0) & (g.ask > 0) & (g.ask >= g.bid)].reset_index(drop=True)
        g["mid"] = (g.bid + g.ask) / 2
        g["dev"] = g.mid / g.fair - 1 - g.baseline
        out[s] = g
    return out


def lagged(g, col, secs):
    """value of col at (t - secs) using the last tick at or before that time (no look-ahead)."""
    idx = np.searchsorted(g.t.values, g.t.values - secs, side="right") - 1
    v = g[col].values[np.clip(idx, 0, None)].astype(float)
    v[idx < 0] = np.nan
    return v


def replay(G, rule, entry_delay=0.0, syms=None):
    k, X, lag, trd, pers = rule["k"], rule["X"], rule["lag"], rule["trd"], rule["pers"]
    mode, S, N = rule["mode"], rule["stop"], rule["N"]
    trades = []
    for s, g in G.items():
        if syms and s not in syms:
            continue
        T = g.t.values; bid = g.bid.values; ask = g.ask.values; mid = g.mid.values; fair = g.fair.values; dev = g.dev.values
        rb = fair / lagged(g, "fair", 60 * k) - 1
        rw = mid / lagged(g, "mid", 60 * k) - 1
        rb_prev = lagged(pd.DataFrame({"t": T, "x": rb}), "x", 60)
        cond = rb >= X
        if lag == "half":
            cond &= rw <= 0.5 * rb
        elif lag == "zero":
            cond &= rw <= 0
        elif lag == "dev04":
            cond &= dev <= -0.004
        elif lag == "dev07":
            cond &= dev <= -0.007
        if trd:   # proxy for 'traded in the signal minute': the book's bid or ask changed during the last 60 s
            b60 = lagged(g, "bid", 60); a60 = lagged(g, "ask", 60)
            cond &= (b60 != bid) | (a60 != ask)
        if pers:
            cond &= rb_prev >= X
        i = 0
        n = len(T)
        while i < n - 2:
            if not cond[i]:
                i += 1
                continue
            # entry
            e = np.searchsorted(T, T[i] + max(entry_delay, 0.001), side="left")
            if e >= n:
                break
            if T[e] - T[i] > 30 + entry_delay:      # data gap: skip
                i += 1
                continue
            ent = ask[e]
            depth_ok = g.ask_qty.values[e] * ask[e] >= 750_000
            tend = T[e] + 60 * N
            p_lim = (1 + (0.003 if mode == "tp03" else 0.006)) * ent / FEE_TM if mode.startswith("tp") else None
            j = e + 1
            exit_i, net, why = None, None, None
            while j < n:
                mark = FEE2 * bid[j] / ent - 1
                if p_lim is not None and bid[j] >= p_lim:
                    exit_i, net, why = j, (0.003 if mode == "tp03" else 0.006), "tp"
                    break
                if T[j] >= tend:
                    exit_i, net, why = j, mark, "time"
                    break
                dec = None
                if mark <= -S:
                    dec = "stop"
                elif mode.startswith("cu"):
                    c = 0.0 if mode == "cu0" else 0.001
                    if dev[j] >= -c and mark >= 0:
                        dec = "catchup"
                if dec:
                    jj = min(j + 1, n - 1)
                    exit_i, net, why = jj, FEE2 * bid[jj] / ent - 1, dec
                    break
                j += 1
            if exit_i is None:
                break
            trades.append({"sym": s, "t_sig": float(T[i]), "t_in": float(T[e]), "t_out": float(T[exit_i]),
                           "rb": float(rb[i]), "rw": float(rw[i]), "dev": float(dev[i]), "ask_in": float(ent),
                           "bid_out": float(bid[exit_i]), "net": float(net), "why": why, "depth_ok": bool(depth_ok),
                           "spread_in": float(ask[e] / bid[e] - 1),
                           "utc": str(pd.to_datetime(T[i], unit="s", utc=True))})
            i = np.searchsorted(T, T[exit_i], side="right")
    return pd.DataFrame(trades)


def summ(d):
    if len(d) == 0:
        return {"n": 0}
    return {"n": int(len(d)), "mean%": round(d.net.mean() * 100, 3), "sum%": round(d.net.sum() * 100, 3),
            "win": round((d.net > 0).mean(), 3), "worst%": round(d.net.min() * 100, 3),
            "per_market": {s: [int(len(x)), round(x.net.mean() * 100, 3)] for s, x in d.groupby("sym")}}


def tick_event_study(G, k, X, Hs=(1, 3, 5, 10, 30), syms=None):
    """All first crossings of a fair-value k-min jump >= X: ask-in (next tick) to bid-out after H minutes, net of fees."""
    rows = []
    for s, g in G.items():
        if syms and s not in syms:
            continue
        T = g.t.values
        rb = g.fair.values / lagged(g, "fair", 60 * k) - 1
        rw = g.mid.values / lagged(g, "mid", 60 * k) - 1
        c = rb >= X
        first = np.where(c & ~np.r_[False, c[:-1]])[0]
        last_t = -1e18
        for i in first:
            if T[i] < last_t + 60 * k:      # de-cluster
                continue
            last_t = T[i]
            e = i + 1
            if e >= len(T):
                continue
            r = {"sym": s, "rb": rb[i], "rw": rw[i], "dev": g.dev.values[i], "spread": g.ask.values[e] / g.bid.values[e] - 1}
            for H in Hs:
                jx = np.searchsorted(T, T[e] + 60 * H)
                if jx >= len(T):
                    r[f"net{H}"] = np.nan; continue
                r[f"net{H}"] = FEE2 * g.bid.values[jx] / g.ask.values[e] - 1
                r[f"mid{H}"] = g.mid.values[jx] / g.mid.values[e] - 1
            rows.append(r)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    t = load_ticks()
    G = prep(t)
    G = {s: g for s, g in G.items() if s in MAIN_MKTS}
    span_h = (t.t.max() - t.t.min()) / 3600
    print("tick span hours", round(span_h, 2))
    res = {"span_hours": round(span_h, 2), "event_study": {}}
    for k in [1, 2, 3, 5]:
        for X in [0.002, 0.003, 0.005, 0.008]:
            d = tick_event_study(G, k, X)
            if len(d) == 0:
                res["event_study"][f"k{k}_X{X}"] = {"n": 0}; continue
            o = {"n": int(len(d)), "rb%": round(d.rb.mean() * 100, 3), "rw%": round(d.rw.mean() * 100, 3), "dev%": round(d.dev.mean() * 100, 3),
                 "spread_at_entry%": round(d.spread.mean() * 100, 3)}
            for H in (1, 3, 5, 10, 30):
                o[f"net{H}%"] = round(d[f"net{H}"].mean() * 100, 3)
                o[f"mid{H}%"] = round(d[f"mid{H}"].mean() * 100, 3)
                o[f"win{H}"] = round((d[f"net{H}"] > 0).mean(), 3)
            res["event_study"][f"k{k}_X{X}"] = o
            print(k, X, o)
    json.dump(res, open(OUT / "tick_event_study.json", "w"), indent=1)
