"""Step 2: rule grid search, parameters chosen on in-sample (first 20 days) only, reported on the last 10 days.

Timing convention (minute bars; row t = state at the close of minute t):
  signal at close of t  ->  buy at close of t+off times (1+h)   (off=1 primary, off=2 robustness, off=0 optimistic)
  exit decisions at close of j (j >= entry+1) -> sell at close of j+1 times (1-h)   (taker)
  time exit at close of entry+N; limit take-profit = resting maker sell filled when a later minute's high > limit.
"""
import os as _os
from pathlib import Path as _P
HERE = str(_P(__file__).resolve().parent)          # this study's folder (outputs)
REPO = str(_P(__file__).resolve().parents[2])      # repo root (research/candles_30d, research/premium)
MAIN = _os.environ.get("WALLEX_MAIN", str(_P(REPO).parent / "wallex-bot"))   # live bot folder (ticks.csv, team only)
import itertools
import json
import pickle
import time

import numba
import numpy as np
import pandas as pd

from common import *

FEE2 = (1 - FEE_T) ** 2
FEE_TM = (1 - FEE_T) * (1 - FEE_M)
NMAX = 30

KS = [1, 2, 3, 5]
XS = [0.003, 0.005, 0.007, 0.010, 0.015]
LAGS = ["none", "half", "zero", "dev04", "dev07"]
TRADS = [0, 1]
PERS = [0, 1]
EXIT_MODES = ["cu0", "cu10", "tp03", "tp06"]      # catch-up (dev>=0 / dev>=-0.1%) with fees covered, or maker limit TP 0.3% / 0.6% net
STOPS = [0.005, 0.009, 0.015, 9.0]
NS = [3, 5, 10, 20, 30]
OFFS = [0, 1, 2]


def build(sym, u):
    df = panel(sym, u)
    a = {
        "W": df["close"].values.astype(float),
        "Hi": df["high"].values.astype(float),       # nan when no trade
        "dev": df["dev"].values.astype(float),
        "tr": df["traded"].values.astype(bool),
        "B": df["bin"].values.astype(float),
        "ts": df.index,
    }
    a["is"] = (df.index < SPLIT)
    return a


def exit_outcomes(a, E, h, mode, S, N):
    """Vectorised exits for entry minutes E. Returns (exit_minute, net)."""
    W, Hi, dev = a["W"], a["Hi"], a["dev"]
    ent = W[E] * (1 + h)
    J = E[:, None] + np.arange(1, N + 1)[None, :]          # columns 1..N
    C = W[J]
    mark = FEE2 * C * (1 - h) / ent[:, None] - 1           # net if sold at close of that column
    n = len(E)
    big = N + 1
    # stop decision at column j (1..N-1) -> sell at j+1
    stopd = (mark[:, :N - 1] <= -S) if N > 1 else np.zeros((n, 0), bool)
    first_stop = np.where(stopd.any(1), stopd.argmax(1) + 1, big) if N > 1 else np.full(n, big)
    if mode.startswith("cu"):
        c = 0.0 if mode == "cu0" else 0.001
        D = dev[J]
        cud = ((D[:, :N - 1] >= -c) & (mark[:, :N - 1] >= 0)) if N > 1 else np.zeros((n, 0), bool)
        first_cu = np.where(cud.any(1), cud.argmax(1) + 1, big) if N > 1 else np.full(n, big)
        d = np.minimum(first_stop, first_cu)
        sell_col = np.where(d < big, d + 1, N)               # 1-based column of the sell close
        net = mark[np.arange(n), sell_col - 1]
        return E + sell_col, net
    tp = 0.003 if mode == "tp03" else 0.006
    p = (1 + tp) * ent / FEE_TM
    Hm = Hi[J]
    fill = (Hm > p[:, None])
    first_fill = np.where(fill.any(1), fill.argmax(1) + 1, big)
    use_fill = first_fill <= np.minimum(first_stop, N)
    sell_col = np.where(first_stop < big, first_stop + 1, N)
    net = np.where(use_fill, tp, mark[np.arange(n), sell_col - 1])
    ex = np.where(use_fill, E + first_fill, E + sell_col)
    return ex, net


@numba.njit(cache=True)
def greedy(nxt):
    n = len(nxt)
    out = np.zeros(n, np.bool_)
    pos = 0
    while pos < n:
        out[pos] = True
        pos = nxt[pos]
    return out


def signal_masks(a, sup):
    """Boolean masks over the superset minutes for every signal config."""
    W, B, dev, tr = a["W"], a["B"], a["dev"], a["tr"]
    masks = {}
    for k in KS:
        rb = np.full(len(B), np.nan); rb[k:] = B[k:] / B[:-k] - 1
        rw = np.full(len(W), np.nan); rw[k:] = W[k:] / W[:-k] - 1
        rb_prev = np.roll(rb, 1)
        for X in XS:
            base = rb[sup] >= X
            for lag in LAGS:
                if lag == "none":
                    m = base
                elif lag == "half":
                    m = base & (rw[sup] <= 0.5 * rb[sup])
                elif lag == "zero":
                    m = base & (rw[sup] <= 0.0)
                elif lag == "dev04":
                    m = base & (dev[sup] <= -0.004)
                else:
                    m = base & (dev[sup] <= -0.007)
                for trd in TRADS:
                    m2 = m & tr[sup] if trd else m
                    for ps in PERS:
                        m3 = m2 & (rb_prev[sup] >= X) if ps else m2
                        masks[(k, X, lag, trd, ps)] = m3
    return masks


def main():
    t0 = time.time()
    u = usdt()
    exits = list(itertools.product(EXIT_MODES, STOPS, NS))
    rows = []
    trades_store = {}
    for sym in MAIN_MKTS:
        a = build(sym, u)
        h = SPREADS[sym] / 2
        B = a["B"]
        rbmax = np.zeros(len(B))
        for k in KS:
            r = np.full(len(B), -1.0); r[k:] = B[k:] / B[:-k] - 1
            rbmax = np.maximum(rbmax, r)
        sup = np.where(rbmax >= min(XS))[0]
        sup = sup[(sup > 200) & (sup < len(B) - NMAX - 4)]
        sup = sup[~np.isnan(a["dev"][sup])]
        masks = signal_masks(a, sup)
        is_sup = a["is"][sup]
        outs = {}
        for off in OFFS:
            E = sup + off
            for ex in exits:
                outs[(off, ex)] = exit_outcomes(a, E, h, *ex)
        print(sym, len(sup), "superset minutes", round(time.time() - t0, 1), "s", flush=True)
        for sk, m in masks.items():
            idx = np.where(m)[0]
            if len(idx) == 0:
                continue
            tsig = sup[idx]
            for off in OFFS:
                for ex in exits:
                    exm, net = outs[(off, ex)]
                    exm = exm[idx]; nt = net[idx]
                    nxt = np.searchsorted(tsig, exm, side="left")
                    take = greedy(nxt)
                    sel_is = is_sup[idx][take]
                    v = nt[take]
                    rows.append((sym, *sk, *ex, off,
                                 int(sel_is.sum()), float(v[sel_is].sum()), float((v[sel_is] > 0).sum()), float(v[sel_is].min()) if sel_is.any() else np.nan,
                                 int((~sel_is).sum()), float(v[~sel_is].sum()), float((v[~sel_is] > 0).sum()), float(v[~sel_is].min()) if (~sel_is).any() else np.nan))
        print(sym, "done", round(time.time() - t0, 1), "s", len(rows), flush=True)
    cols = ["sym", "k", "X", "lag", "trd", "pers", "mode", "stop", "N", "off",
            "n_is", "sum_is", "win_is", "worst_is", "n_oos", "sum_oos", "win_oos", "worst_oos"]
    R = pd.DataFrame(rows, columns=cols)
    R.to_parquet(OUT / "grid_per_market.parquet") if False else R.to_pickle(OUT / "grid_per_market.pkl")
    print("saved", len(R), round(time.time() - t0, 1), "s")


if __name__ == "__main__":
    main()
