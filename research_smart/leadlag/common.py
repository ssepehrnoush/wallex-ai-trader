"""Shared data loading for the lead-lag study. READ ONLY on the wallex-bot folders."""
import os as _os
from pathlib import Path as _P
HERE = str(_P(__file__).resolve().parent)          # this study's folder (outputs)
REPO = str(_P(__file__).resolve().parents[2])      # repo root (research/candles_30d, research/premium)
MAIN = _os.environ.get("WALLEX_MAIN", str(_P(REPO).parent / "wallex-bot"))   # live bot folder (ticks.csv, team only)
import json
from pathlib import Path

import numpy as np
import pandas as pd

ULTRA = Path(rf"{REPO}\research")
TICKS = Path(rf"{MAIN}\runs\live_main\ticks.csv")
OUT = Path(__file__).resolve().parent

FEE_T = 0.003
FEE_M = 0.0025
START = pd.Timestamp("2026-09-22 07:00", tz="UTC")
END = pd.Timestamp("2026-10-22 07:00", tz="UTC")       # exclusive
SPLIT = pd.Timestamp("2026-10-12 07:00", tz="UTC")     # IS < SPLIT <= OOS

SPREADS = json.loads((ULTRA / "live_spreads_shadow_v1.json").read_text())["spreads"]
MAIN_MKTS = [m for m in ["BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN", "PEPETMN", "BNBTMN", "ARBTMN"]
             if SPREADS[m] <= 0.006]
EXTRA_MKTS = ["POLTMN", "SANDTMN"]   # no spread in the json; spread taken from the tick file if used

GRID = pd.date_range(START, END - pd.Timedelta(minutes=1), freq="1min", tz="UTC")


def wallex(sym: str) -> pd.DataFrame:
    df = pd.read_csv(ULTRA / "candles_30d" / f"{sym}_1.csv")
    df.index = pd.to_datetime(df["ts"].astype("int64"), unit="s", utc=True)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df.reindex(GRID)
    traded = df["volume"].fillna(0) > 0
    close_tr = df["close"].where(traded)
    out = pd.DataFrame(index=GRID)
    out["close"] = close_tr.ffill()            # last traded price (stale on no-trade minutes)
    out["high"] = df["high"].where(traded)
    out["low"] = df["low"].where(traded)
    out["traded"] = traded
    out["vol"] = df["volume"].fillna(0)
    return out


def binance(base: str) -> pd.Series:
    s = pd.read_csv(ULTRA / "premium" / f"binance_{base}USDT_1m.csv", index_col=0, parse_dates=True)["close"]
    s = s[~s.index.duplicated(keep="last")].sort_index()
    if s.index.tz is None:
        s.index = s.index.tz_localize("UTC")
    return s.reindex(GRID).ffill()


def usdt() -> pd.Series:
    u = wallex("USDTTMN")
    return u["close"].ffill().bfill()


def panel(sym: str, u: pd.Series | None = None, med_win: int = 180) -> pd.DataFrame:
    """Minute panel for one toman market. Everything at row t uses data up to the close of minute t."""
    if u is None:
        u = usdt()
    w = wallex(sym)
    b = binance(sym[:-3])
    df = w.copy()
    df["bin"] = b
    df["usdt"] = u
    df["fair"] = b * u
    df["prem"] = df["close"] / df["fair"] - 1
    # trailing median premium over traded minutes of the previous med_win minutes (excludes t)
    pt = df["prem"].where(df["traded"])
    df["prem_med"] = pt.shift(1).rolling(med_win, min_periods=med_win // 4).median()
    df["dev"] = df["prem"] - df["prem_med"]
    return df


def load_ticks() -> pd.DataFrame:
    lines = TICKS.read_text().splitlines()
    hdr = lines[0].split(",")
    rows = [ln.split(",") for ln in lines[1:]]
    rows = [r for r in rows if len(r) == len(hdr)]
    t = pd.DataFrame(rows, columns=hdr)
    for c in hdr:
        if c != "symbol":
            t[c] = pd.to_numeric(t[c], errors="coerce")
    t = t.dropna()
    return t.sort_values(["symbol", "t"]).reset_index(drop=True)
