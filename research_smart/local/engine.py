"""Signal + trade engine for the demand-surge family. No look-ahead: a signal at bar i uses
only data up to bar i's close; entry is at bar i close (instant) or bar i+1 close (lag=1);
conditional exits trigger on bar j close and fill at j (instant) or j+1 (lag=1)."""
import os as _os
from pathlib import Path as _P
HERE = str(_P(__file__).resolve().parent)          # this study's folder (outputs)
REPO = str(_P(__file__).resolve().parents[2])      # repo root (research/candles_30d, research/premium)
MAIN = _os.environ.get("WALLEX_MAIN", str(_P(REPO).parent / "wallex-bot"))   # live bot folder (ticks.csv, team only)
import numpy as np
import pandas as pd
import common as c

# ---- pre-declared grid (fixed before looking at results) ----
KS = (3, 5, 15)
A_Y = (0.005, 0.010, 0.015)       # premium rise over k minutes
B_X = (0.010, 0.020, 0.030)       # Wallex k-min return
ZS = (3.0, 5.0)                   # volume / trailing-24h median of k-bar volume
HOLDS = (5, 10, 15, 30, 60, 120)  # event study horizons
EXITS = [("fixed", H) for H in (15, 30, 60, 120)] + \
        [("prem_half", H) for H in (30, 60, 120)] + \
        [("trail15", H) for H in (30, 60, 120)]
STOP = -0.02                       # hard stop on (fee-free) price move, used by conditional exits
COOLDOWN_ES = 60                   # event-study de-dup per market (minutes)


def add_prem_baseline(df):
    if "prem_med" not in df:
        df["prem_med"] = df.prem.shift(1).rolling(1440, min_periods=360).median()
    return df


def signal(df, variant, k, thr, Z):
    add_prem_baseline(df)
    vol_ok = (df[f"vr{k}"] >= Z) & df.traded & (df[f"trk{k}"] >= 2)
    if variant == "A":   # premium surge: premium up >= thr over k, Wallex itself up, premium above its normal level
        s = (df[f"dp{k}"] >= thr) & (df[f"r{k}"] > 0) & (df.prem > df.prem_med)
    elif variant == "B":  # local outperformance: Wallex up >= thr and global move at most half of it
        s = (df[f"r{k}"] >= thr) & ((df[f"r{k}"] - df[f"g{k}"]) >= thr / 2)
    elif variant == "C":  # as B but the world is also up (no fighting the tape)
        s = (df[f"r{k}"] >= thr) & ((df[f"r{k}"] - df[f"g{k}"]) >= thr / 2) & (df[f"g{k}"] > 0)
    else:
        raise ValueError(variant)
    return (s & vol_ok).fillna(False).values


def dedupe(idx, gap):
    out, last = [], -10**9
    for i in idx:
        if i - last >= gap:
            out.append(i)
            last = i
    return np.array(out, dtype=int)


def trade(arr, i, exit_rule, lag, k):
    """Return (entry_idx, exit_idx, net) for a signal at bar i. arr: dict of numpy arrays."""
    n = len(arr["close"])
    e = i + lag
    kind, H = exit_rule
    last = e + H
    if last >= n:
        return None
    buy = arr["buy"][e]
    x = last
    if kind != "fixed":
        close = arr["close"]
        ce = close[e]
        if kind == "prem_half":
            # pre-surge premium = premium k bars before the signal; exit when half the surge is given back
            p0, p1 = arr["prem"][i - k], arr["prem"][i]
            lvl = p0 + 0.5 * (p1 - p0)
            for j in range(e + 1, last + 1):
                if arr["prem"][j] <= lvl or close[j] / ce - 1 <= STOP:
                    x = min(j + lag, last)
                    break
        elif kind == "trail15":
            pk = ce
            for j in range(e + 1, last + 1):
                pk = max(pk, close[j])
                if close[j] / pk - 1 <= -0.015 or close[j] / ce - 1 <= STOP:
                    x = min(j + lag, last)
                    break
    net = c.net_ret(buy, arr["sell"][x])
    return e, x, net


def arrays(df):
    return {k: df[k].values.astype(float) for k in ("close", "buy", "sell", "prem", "fair")}


def configs():
    out = []
    for k in KS:
        for Z in ZS:
            for Y in A_Y:
                out.append(("A", k, Y, Z))
            for X in B_X:
                out.append(("B", k, X, Z))
                out.append(("C", k, X, Z))
    return out


def portfolio(trades, D, t0, t1, size=1_000_000, cap=5_000_000, max_pos=3):
    """trades: DataFrame(market, e, x, net) sorted by e (minute indices on the common grid).
    One position per market, at most max_pos open. Returns taken trades and minute equity curve on [t0, t1)."""
    grid_n = len(next(iter(D.values())))
    taken, open_ = [], []  # open_: list of (x, market)
    for r in trades.sort_values(["e", "market"]).itertuples():
        if r.e < t0 or r.e >= t1:
            continue
        open_ = [(x, m) for x, m in open_ if x > r.e]
        if len(open_) >= max_pos or any(m == r.market for _, m in open_):
            continue
        open_.append((r.x, r.market))
        taken.append(r)
    taken = pd.DataFrame(taken)
    eq = np.zeros(grid_n)
    if len(taken):
        for r in taken.itertuples():
            df = D[r.market]
            buy = df.buy.values[r.e]
            sell = df.sell.values
            seg = np.arange(r.e, r.x)
            eq[seg] += size * (c.FEE_MULT * sell[seg] / buy - 1)
            eq[r.x:] += size * r.net
    eq = cap + eq
    return taken, eq[t0:t1]


def max_dd(eq):
    pk = np.maximum.accumulate(eq)
    return float((eq / pk - 1).min())


def windows(eq, length=31 * 60, step=60):
    rets = [eq[s + length - 1] / eq[s] - 1 for s in range(0, len(eq) - length + 1, step)]
    rets = np.array(rets)
    if not len(rets):
        return {}
    return {"n": int(len(rets)), "share_pos": float((rets > 1e-12).mean()), "share_zero": float((np.abs(rets) <= 1e-12).mean()),
            "share_neg": float((rets < -1e-12).mean()), "median_pct": float(np.median(rets) * 100),
            "worst_pct": float(rets.min() * 100), "best_pct": float(rets.max() * 100)}
