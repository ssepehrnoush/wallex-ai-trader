"""Candle loading: research CSVs for backtests, live REST for shadow/live."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

from .config import ROOT

CANDLE_DIR = ROOT / "research" / "candles"


def _frame(ts, o, h, l, c, v) -> pd.DataFrame:
    # plain arrays: a pandas Series here would be aligned on its own index and turn into NaN
    cols = {k: np.asarray(x, dtype=float) for k, x in
            (("open", o), ("high", h), ("low", l), ("close", c), ("volume", v))}
    df = pd.DataFrame(cols, index=pd.DatetimeIndex(pd.to_datetime(np.asarray(ts, dtype="int64"), unit="s", utc=True)))
    df.index.name = "ts"
    df = df.astype(float)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


def load_csv(symbol: str, res: str = "15", start=None, end=None) -> pd.DataFrame:
    p = CANDLE_DIR / f"{symbol}_{res}.csv"
    raw = pd.read_csv(p)
    df = _frame(raw["ts"], raw["open"], raw["high"], raw["low"], raw["close"], raw["volume"])
    if start is not None:
        df = df[df.index >= pd.Timestamp(start, tz="UTC")]
    if end is not None:
        df = df[df.index < pd.Timestamp(end, tz="UTC")]
    return fill_gaps(df, int(res))


def fill_gaps(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Wallex omits bars with no trades. Insert flat zero-volume bars so indicator
    windows mean the same wall-clock time in backtest and live."""
    if df.empty:
        return df
    idx = pd.date_range(df.index[0], df.index[-1], freq=f"{minutes}min", tz="UTC", name="ts")
    out = df.reindex(idx)
    out["close"] = out["close"].ffill()
    for k in ("open", "high", "low"):
        out[k] = out[k].fillna(out["close"])
    out["volume"] = out["volume"].fillna(0.0)
    return out


def fetch_live(client, symbol: str, minutes: int = 15, bars: int = 400) -> pd.DataFrame:
    """Recent CLOSED bars from Wallex. The still-forming bar is dropped."""
    now = int(time.time())
    start = now - bars * minutes * 60
    d = client.candles(symbol, str(minutes), start, now)
    if d.get("s") != "ok" or not d.get("t"):
        raise RuntimeError(f"no candles for {symbol}: {d.get('s')}")
    df = _frame(d["t"], d["o"], d["h"], d["l"], d["c"], d["v"])
    df = df[df.index + pd.Timedelta(minutes=minutes) <= pd.Timestamp(now, unit="s", tz="UTC")]
    return fill_gaps(df, minutes)
