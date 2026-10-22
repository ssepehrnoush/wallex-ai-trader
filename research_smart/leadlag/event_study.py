"""Step 1: event study. After a Binance k-minute jump >= X (first minute of crossing), what does Wallex do?"""
import os as _os
from pathlib import Path as _P
HERE = str(_P(__file__).resolve().parent)          # this study's folder (outputs)
REPO = str(_P(__file__).resolve().parents[2])      # repo root (research/candles_30d, research/premium)
MAIN = _os.environ.get("WALLEX_MAIN", str(_P(REPO).parent / "wallex-bot"))   # live bot folder (ticks.csv, team only)
import json
import numpy as np, pandas as pd
from common import *

HS = [1, 2, 3, 5, 10, 15, 20, 30]
u = usdt()
P = {s: panel(s, u) for s in MAIN_MKTS + ["SANDTMN"]}
SPREADS_X = dict(SPREADS); SPREADS_X["SANDTMN"] = 0.00562   # median from live ticks


def events(df, k, X, lagfilter):
    B = df["bin"].values; W = df["close"].values
    rb = B / np.roll(B, k) - 1; rb[:k] = np.nan
    rw = W / np.roll(W, k) - 1; rw[:k] = np.nan
    cond = rb >= X
    first = cond & ~np.roll(cond, 1)
    if lagfilter == "lagging":            # Wallex has followed less than half of the Binance move
        first &= rw <= 0.5 * rb
    elif lagfilter == "lagging_traded":
        first &= (rw <= 0.5 * rb) & df["traded"].values
    idx = np.where(first)[0]
    return idx[(idx > 200) & (idx < len(df) - 40)], rb, rw


def study(k, X, lagfilter, entry_off, mkts):
    rows = []
    for s in mkts:
        df = P[s]; h = SPREADS_X[s] / 2
        W = df["close"].values; B = df["bin"].values; dev = df["dev"].values
        idx, rb, rw = events(df, k, X, lagfilter)
        for i in idx:
            e = i + entry_off
            ent = W[e] * (1 + h)
            r = {"mkt": s, "i": i, "rb": rb[i], "rw": rw[i], "dev": dev[i], "is": df.index[i] < SPLIT}
            for H in HS:
                r[f"net{H}"] = (1 - FEE_T) ** 2 * W[e + H] * (1 - h) / ent - 1
                r[f"gw{H}"] = W[e + H] / W[e] - 1               # Wallex last-price catch-up after entry
                r[f"gb{H}"] = B[e + H] / B[e] - 1               # Binance persistence after entry
            r["mfe30"] = (1 - FEE_T) ** 2 * np.nanmax(W[e + 1:e + 31]) * (1 - h) / ent - 1
            rows.append(r)
    return pd.DataFrame(rows)


def summarize(d):
    if len(d) == 0:
        return {"n": 0}
    o = {"n": int(len(d)), "rb_mean%": round(d.rb.mean() * 100, 3), "rw_mean%": round(d.rw.mean() * 100, 3),
         "dev_mean%": round(np.nanmean(d.dev) * 100, 3)}
    for H in HS:
        o[f"net{H}_mean%"] = round(d[f"net{H}"].mean() * 100, 3)
        o[f"gw{H}_mean%"] = round(d[f"gw{H}"].mean() * 100, 3)
        o[f"gb{H}_mean%"] = round(d[f"gb{H}"].mean() * 100, 3)
        o[f"P(gw{H}>0.6%)"] = round((d[f"gw{H}"] > 0.006).mean(), 3)
        o[f"win{H}"] = round((d[f"net{H}"] > 0).mean(), 3)
    o["P(mfe30>0)"] = round((d.mfe30 > 0).mean(), 3)
    return o


res = {}
print("pooled over 8 markets, entry at signal-minute close (off=0) / next minute (off=1); net = after 0.6% fees + spread")
for lag in ["all", "lagging", "lagging_traded"]:
    for k in [1, 2, 3, 5]:
        for X in [0.003, 0.005, 0.008, 0.01, 0.015]:
            for off in [0, 1, 2]:
                d = study(k, X, lag, off, MAIN_MKTS)
                sm = summarize(d)
                res[f"{lag}|k{k}|X{X}|off{off}"] = sm
                if off in (0, 1) and sm["n"] > 0:
                    print(f"{lag:15s} k={k} X={X*100:.1f}% off={off} n={sm['n']:5d} rb={sm['rb_mean%']:.2f} rw={sm['rw_mean%']:.2f} dev={sm['dev_mean%']:.2f} | "
                          + " ".join(f"H{H}:{sm[f'net{H}_mean%']:+.2f}/{sm[f'gw{H}_mean%']:+.2f}/{sm[f'gb{H}_mean%']:+.2f}" for H in [1, 3, 5, 10, 30])
                          + f" | P(gw5>.6)={sm['P(gw5>0.6%)']:.2f} P(gw30>.6)={sm['P(gw30>0.6%)']:.2f} win5={sm['win5']:.2f}")
# per market at a representative setting
permkt = {}
for s in MAIN_MKTS + ["SANDTMN"]:
    for off in [0, 1]:
        d = study(3, 0.005, "lagging", off, [s])
        permkt[f"{s}|k3|X0.005|lagging|off{off}"] = summarize(d)
        sm = permkt[f"{s}|k3|X0.005|lagging|off{off}"]
        if sm["n"]:
            print(f"{s} off={off} n={sm['n']} " + " ".join(f"H{H}:{sm[f'net{H}_mean%']:+.2f}" for H in [1, 3, 5, 10, 30]))
json.dump({"pooled": res, "per_market_k3_X0.5_lagging": permkt}, open(OUT / "event_study.json", "w"), indent=1)
