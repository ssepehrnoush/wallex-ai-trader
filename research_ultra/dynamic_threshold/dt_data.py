"""Shared loaders for the dynamic-threshold study. READ-ONLY on the live bot folder (MAIN).

Never fetches anything: Wallex candles come from MAIN/research csv files, Binance closes from
MAIN/research/premium plus ./data/binance_*_gap.csv (fetched once by fetch_binance_gap.py).
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pandas as pd

MAIN = Path("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot")
HERE = Path(__file__).resolve().parent
SYMS = ["BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN", "PEPETMN", "BNBTMN", "ARBTMN"]
TICK_RUNS = ["shadow_v2", "shadow_v3", "shadow_v4", "shadow_v5", "shadow_v6", "live_main"]

# engine constants (config.json / engine.py / supervisor.py as of 2026-10-21)
TAKER = 0.003
FLOOR = 2 * TAKER + 0.001          # 0.7 %, closed decision
SLACK = 0.0005
STOP = 0.009
BE = 1 + 2 * TAKER + 0.0005        # target needs bid >= entry * BE
HOLD = {s: 25 for s in SYMS} | {"ARBTMN": 15}
MAX_SPREAD = 0.006
MAX_DROP_60S = 0.004
COOLDOWN_MIN = 120


def _read_csv_tolerant(p: Path) -> pd.DataFrame:
    """Read a csv that may be appended to right now: snapshot the bytes, drop a partial last line."""
    raw = p.read_bytes()
    if not raw.endswith(b"\n"):
        raw = raw[: raw.rfind(b"\n") + 1]
    return pd.read_csv(io.BytesIO(raw))


def wallex_1m(sym: str) -> pd.DataFrame:
    p = MAIN / "research" / "candles" / f"{sym}_1.csv"
    if not p.exists():
        p = MAIN / "research" / "premium" / f"wallex_{sym}_1m.csv"
    raw = pd.read_csv(p)
    df = pd.DataFrame({k: raw[k].astype(float).to_numpy() for k in ("open", "high", "low", "close", "volume")},
                      index=pd.to_datetime(raw["ts"].astype("int64"), unit="s", utc=True))
    return df[~df.index.duplicated(keep="last")].sort_index()


def binance_1m(base: str) -> pd.Series:
    a = pd.read_csv(MAIN / "research" / "premium" / f"binance_{base}USDT_1m.csv", index_col=0, parse_dates=True)["close"]
    g = HERE / "data" / f"binance_{base}USDT_gap.csv"
    if g.exists():
        b = pd.read_csv(g, index_col=0, parse_dates=True)["close"]
        a = pd.concat([b, a])
    a = a[~a.index.duplicated(keep="last")].sort_index()
    return a.astype(float)


def live_spreads() -> dict[str, float]:
    """Median live spread (ask/bid - 1) per market over all recorded tick runs."""
    t = load_ticks()
    return ((t.ask / t.bid - 1).groupby(t.symbol).median()).to_dict()


_TICKS = None


def load_ticks() -> pd.DataFrame:
    global _TICKS
    if _TICKS is None:
        parts = []
        for r in TICK_RUNS:
            d = _read_csv_tolerant(MAIN / "runs" / r / "ticks.csv")
            d["run"] = r
            parts.append(d)
        t = pd.concat(parts, ignore_index=True)
        for c in ("t", "bid", "ask", "bid_qty", "ask_qty", "fair", "baseline", "buy_dev", "sell_dev"):
            t[c] = pd.to_numeric(t[c], errors="coerce")
        t = t.dropna(subset=["t", "bid", "ask", "fair", "buy_dev", "sell_dev"])
        t = t[t.symbol.isin(SYMS)].sort_values(["t", "symbol"]).reset_index(drop=True)
        _TICKS = t
    return _TICKS


def market_frame(sym: str, usdt: pd.Series, usdt_age: pd.Series, half_spread: float,
                 max_usdt_age_min: float = 10) -> pd.DataFrame:
    """1-minute frame of one TMN market, traded minutes only, with the engine's quantities.
    Everything at minute i uses only information available at the close of minute i
    (baseline and volatility are shifted by one traded minute)."""
    w = wallex_1m(sym)
    g = binance_1m(sym[:-3])
    fair = (g * usdt.reindex(g.index)).rename("fair")
    df = pd.DataFrame({"c": w.close, "v": w.volume}).join(fair, how="inner")
    df = df.join(usdt_age.rename("uage"), how="left")
    df["gret"] = (g / g.shift(1) - 1).reindex(df.index)          # Binance 1-minute return (60 s drop filter)
    df = df[(df.v > 0) & df.fair.notna() & (df.uage <= max_usdt_age_min)].copy()
    df["ask"], df["bid"] = df.c * (1 + half_spread), df.c * (1 - half_spread)
    prem = df.c / df.fair - 1
    df["base"] = prem.rolling("180min", min_periods=30).median().shift(1)
    df["buy_dev"] = df.ask / df.fair - 1 - df.base
    df["sell_dev"] = df.bid / df.fair - 1 - df.base
    df["vol3h"] = df.buy_dev.rolling("180min", min_periods=30).std().shift(1)
    df["spread"] = 2 * half_spread
    return df


def usdt_series() -> tuple[pd.Series, pd.Series]:
    """USDTTMN close on a full minute grid (ffilled) and the age in minutes of the last traded minute."""
    u = wallex_1m("USDTTMN")
    grid = pd.date_range(u.index[0], u.index[-1], freq="1min", tz="UTC")
    traded = u[u.volume > 0]
    close = traded.close.reindex(grid).ffill()
    mins = (grid - grid[0]) / pd.Timedelta(minutes=1)              # unit-safe (pandas 3 may not use ns)
    last_t = pd.Series(np.where(grid.isin(traded.index), mins, np.nan), index=grid).ffill()
    age = mins - last_t.to_numpy()
    return close, pd.Series(np.asarray(age, dtype=float), index=grid)
