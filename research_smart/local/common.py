"""Shared data prep for the Wallex-local demand-surge study (read-only on source data).

Per market: full 1-minute UTC grid, Wallex close (ffill) / volume (0 when no trade),
fair = Binance close x USDTTMN close (both ffill), premium = close / fair - 1,
executable prices: buy = close * (1 + h), sell = close * (1 - h), h = half median spread.
All features at bar t use data up to and including bar t's close only.
"""
import os as _os
from pathlib import Path as _P
HERE = str(_P(__file__).resolve().parent)          # this study's folder (outputs)
REPO = str(_P(__file__).resolve().parents[2])      # repo root (research/candles_30d, research/premium)
MAIN = _os.environ.get("WALLEX_MAIN", str(_P(REPO).parent / "wallex-bot"))   # live bot folder (ticks.csv, team only)
import json
import os
import numpy as np
import pandas as pd

RES = rf"{REPO}\research"
TICKS = rf"{MAIN}\runs\live_main\ticks.csv"
OUT = os.path.dirname(os.path.abspath(__file__))

FEE = 0.003  # taker, per side
FEE_MULT = (1 - FEE) ** 2
MAX_SPREAD = 0.006

START = pd.Timestamp("2026-09-22 07:00", tz="UTC")
SPLIT = pd.Timestamp("2026-10-12 07:00", tz="UTC")
END = pd.Timestamp("2026-10-22 07:00", tz="UTC")

ALL = ["BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN", "PEPETMN", "BNBTMN", "ARBTMN", "POLTMN", "SANDTMN"]


def spreads():
    s = json.load(open(os.path.join(RES, "live_spreads_shadow_v1.json")))["spreads"]
    w = json.load(open(os.path.join(RES, "live_spreads_watch.json")))["spreads"]
    out = {k: v for k, v in s.items() if k.endswith("TMN")}
    out.update(w)
    return out


def tradable():
    sp = spreads()
    return [m for m in ALL if sp.get(m, 1) <= MAX_SPREAD]


def _wallex(sym, grid):
    d = pd.read_csv(os.path.join(RES, "candles_30d", f"{sym}_1.csv"))
    d.index = pd.to_datetime(d.ts, unit="s", utc=True)
    d = d[~d.index.duplicated()]
    d = d.reindex(grid)
    d["traded"] = d.volume.fillna(0) > 0
    d["volume"] = d.volume.fillna(0)
    d["close"] = d.close.ffill()
    d["high"] = d.high.fillna(d.close)
    d["low"] = d.low.fillna(d.close)
    return d[["close", "high", "low", "volume", "traded"]]


def _binance(base, grid):
    b = pd.read_csv(os.path.join(RES, "premium", f"binance_{base}USDT_1m.csv"), index_col=0)
    b.index = pd.to_datetime(b.index, utc=True)
    b = b[~b.index.duplicated()]
    return b.close.reindex(grid.union(b.index)).ffill().reindex(grid)


def load_market(sym, k_list=(3, 5, 15), vol_window=1440):
    grid = pd.date_range(START, END - pd.Timedelta(minutes=1), freq="1min")
    w = _wallex(sym, grid)
    u = _wallex("USDTTMN", grid)
    # USDTTMN: mark fair invalid if the last real USDT trade is more than 120 min old
    last_u = pd.Series(np.where(u.traded, np.arange(len(u)), np.nan), index=grid).ffill()
    u_age = np.arange(len(u)) - last_u.values
    base = sym.replace("TMN", "")
    g = _binance(base, grid)
    df = pd.DataFrame(index=grid)
    df["close"] = w.close
    df["high"] = w.high
    df["low"] = w.low
    df["vol"] = w.volume * w.close  # toman value
    df["traded"] = w.traded
    df["usdt"] = u.close
    df["glob"] = g
    df["fair"] = g * u.close
    # USDTTMN does not trade ~17:30-05:30 UTC (other TMN markets do); ffill is used, age kept for info.
    df["usdt_age"] = u_age
    df["fair_ok"] = df.fair.notna()
    df["prem"] = df.close / df.fair - 1
    df["prem"] = df.prem.where(df.fair_ok)
    h = spreads()[sym] / 2
    df["buy"] = df.close * (1 + h)
    df["sell"] = df.close * (1 - h)
    df["h"] = h
    for k in k_list:
        df[f"r{k}"] = df.close / df.close.shift(k) - 1                 # Wallex k-min return
        df[f"g{k}"] = df.fair / df.fair.shift(k) - 1                   # global (fair) k-min return
        df[f"dp{k}"] = df.prem - df.prem.shift(k)                      # premium change
        vs = df.vol.rolling(k, min_periods=k).sum()
        df[f"v{k}"] = vs
        # trailing baseline: median of k-bar sums over the previous 24 h, EXCLUDING the current window
        base_med = vs.shift(k).rolling(vol_window, min_periods=360).median()
        base_mean = vs.shift(k).rolling(vol_window, min_periods=360).mean()
        df[f"vb{k}"] = np.maximum(base_med, 0.25 * base_mean)
        df[f"vr{k}"] = vs / df[f"vb{k}"]
        df[f"trk{k}"] = df.traded.astype(int).rolling(k).sum()        # bars with trades in window
    return df


def load_all(markets=None):
    markets = markets or tradable()
    cache = os.path.join(OUT, "cache_markets.pkl")
    if os.path.exists(cache):
        d = pd.read_pickle(cache)
        if all(m in d for m in markets):
            return {m: d[m] for m in markets}
    d = {m: load_market(m) for m in markets}
    pd.to_pickle(d, cache)
    return d


def net_ret(entry_buy, exit_sell):
    return FEE_MULT * exit_sell / entry_buy - 1
