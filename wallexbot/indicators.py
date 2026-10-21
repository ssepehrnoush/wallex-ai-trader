"""Indicators on closed candles. Every function takes a pandas DataFrame with
open/high/low/close/volume indexed by UTC timestamp and never looks ahead."""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    a = atr(df, n)
    pdi = 100 * pd.Series(plus, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    mdi = 100 * pd.Series(minus, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean().fillna(0)


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    mid = close.rolling(n).mean()
    sd = close.rolling(n).std(ddof=0)
    return mid - k * sd, mid, mid + k * sd


def resample(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Aggregate to a higher timeframe, keeping only fully closed bars.
    Bars are labelled by their OPEN time, like the exchange."""
    rule = f"{minutes}min"
    out = df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    return out


def add_features(df15: pd.DataFrame, df60: pd.DataFrame) -> pd.DataFrame:
    """Return df15 with 15m features and the latest CLOSED 1h features joined on."""
    f = df15.copy()
    c = f["close"]
    f["ema20"] = ema(c, 20)
    f["ema50"] = ema(c, 50)
    f["rsi"] = rsi(c, 14)
    f["atr"] = atr(f, 14)
    f["atr_pct"] = f["atr"] / c
    lo, mid, hi = bollinger(c, 20, 2.0)
    f["bb_lo"], f["bb_mid"], f["bb_hi"] = lo, mid, hi
    f["bb_pos"] = (c - lo) / (hi - lo).replace(0, np.nan)
    f["vol_z"] = (f["volume"] - f["volume"].rolling(40).mean()) / f["volume"].rolling(40).std(ddof=0).replace(0, np.nan)
    f["hh20"] = f["high"].rolling(20).max().shift(1)       # prior 20-bar high, excludes current bar
    f["ret_1h"] = c.pct_change(4)
    f["ret_4h"] = c.pct_change(16)

    h = df60.copy()
    h["h_ema20"] = ema(h["close"], 20)
    h["h_ema50"] = ema(h["close"], 50)
    h["h_rsi"] = rsi(h["close"], 14)
    h["h_adx"] = adx(h, 14)
    h["h_atr_pct"] = atr(h, 14) / h["close"]
    h["h_slope"] = h["h_ema20"].pct_change(3)
    h["h_close"] = h["close"]
    h = h[["h_ema20", "h_ema50", "h_rsi", "h_adx", "h_atr_pct", "h_slope", "h_close"]]
    # an hourly bar opened at T closes at T+60m; it becomes known to the 15m bar opened at T+45m
    # (which closes at T+60m). Shift the hourly index to its close time, then as-of join on 15m close time.
    h.index = h.index + pd.Timedelta(minutes=60)
    h.index.name = "h_time"
    f["close_time"] = f.index + pd.Timedelta(minutes=15)
    f.index.name = "ts"
    f = pd.merge_asof(f.reset_index().sort_values("close_time"), h.reset_index().sort_values("h_time"),
                      left_on="close_time", right_on="h_time", direction="backward")
    return f.set_index("ts")
