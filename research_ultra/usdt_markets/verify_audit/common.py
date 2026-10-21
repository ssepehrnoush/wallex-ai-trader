"""Shared loaders for the USDT-markets study. READ-ONLY access to the live bot folder (MAIN).

Never fetches anything from Wallex: every Wallex number comes from the recorded csv files.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pandas as pd

MAIN = Path("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot")
HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)

USDT_SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
SHADOW_RUNS = ["shadow_v2", "shadow_v3", "shadow_v4", "shadow_v5", "shadow_v6"]

FEES_USDT = {"maker": 0.001, "taker": 0.0013}
FEES_TMN = {"maker": 0.0025, "taker": 0.003}
FLOOR_USDT = 2 * FEES_USDT["taker"] + 0.001     # 0.36%
EXIT_SLACK = 0.0005
STOP_PCT = 0.009
MAX_HOLD_MIN = 25
MAX_SPREAD = 0.006
MAX_GLOBAL_DROP_60S = 0.004
COOLDOWN_AFTER_LOSS_MIN = 120


def read_csv_snapshot(p: Path) -> pd.DataFrame:
    """Read a csv that may still be appended to: drop a partial last line."""
    raw = p.read_bytes()
    if not raw.endswith(b"\n"):
        raw = raw[: raw.rfind(b"\n") + 1]
    return pd.read_csv(io.BytesIO(raw))


def wallex_1m(sym: str) -> pd.DataFrame:
    p = MAIN / "research" / "premium" / f"wallex_{sym}_1m.csv"
    if not p.exists():
        p = MAIN / "research" / "candles" / f"{sym}_1.csv"
    raw = pd.read_csv(p)
    df = raw.set_index(pd.to_datetime(raw.ts.astype("int64"), unit="s", utc=True))[["open", "high", "low", "close", "volume"]]
    return df[~df.index.duplicated(keep="last")].sort_index().astype(float)


def binance_1m(base: str) -> pd.Series:
    s = pd.read_csv(MAIN / "research" / "premium" / f"binance_{base}USDT_1m.csv", index_col=0)["close"]
    s.index = pd.to_datetime(s.index, utc=True)
    return s[~s.index.duplicated(keep="last")].sort_index().astype(float)


def shadow_ticks(symbols=None) -> pd.DataFrame:
    parts = []
    for r in SHADOW_RUNS:
        t = read_csv_snapshot(MAIN / "runs" / r / "ticks.csv")
        t["run"] = r
        parts.append(t)
    t = pd.concat(parts, ignore_index=True)
    if symbols:
        t = t[t.symbol.isin(symbols)]
    return t.sort_values(["t", "symbol"]).reset_index(drop=True)


def save_json(name: str, obj) -> Path:
    p = OUT / name
    p.write_text(json.dumps(obj, indent=1, ensure_ascii=False, default=float), encoding="utf-8")
    return p


def stats(rets: pd.Series | np.ndarray, whys=None) -> dict:
    r = pd.Series(rets, dtype=float)
    d = {"n": int(len(r))}
    if len(r):
        d |= {"mean_pct": round(r.mean(), 3), "sum_pct": round(r.sum(), 3), "median_pct": round(r.median(), 3),
              "win_pct": round((r > 0).mean() * 100, 1), "worst_pct": round(r.min(), 3), "best_pct": round(r.max(), 3)}
        if len(r) > 1:
            d["se_mean_pct"] = round(r.std(ddof=1) / np.sqrt(len(r)), 3)
    if whys is not None and len(r):
        d["exits"] = pd.Series(whys).value_counts().to_dict()
    return d
