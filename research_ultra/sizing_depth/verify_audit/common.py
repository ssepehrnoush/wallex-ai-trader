"""Shared helpers for the sizing / depth study. READ-ONLY access to the live bot folder (MAIN)."""
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

TICK_CUTOFF = 1792606500.0      # 2026-10-21 18:15:00 UTC (live_main is still being appended)
TICK_RUNS = ["shadow_v2", "shadow_v3", "shadow_v4", "shadow_v5", "shadow_v6", "live_main"]
TMN8 = ["BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN", "PEPETMN", "BNBTMN", "ARBTMN"]

EQUITY = 5_000_000.0
RISK_PER_TRADE = 0.006
STOP = 0.009
TAKER, MAKER = 0.003, 0.0025
MAX_POS_PCT, MAX_EXP_PCT = 0.35, 0.70
MIN_NOTIONAL = 60_000.0
EXCH_MIN_NOTIONAL = 50_000.0     # research/markets_snapshot.csv: min_notional_tmn for all 8 markets
ENTRY_MIN_FILL = EXCH_MIN_NOTIONAL * 1.05   # engine: qty*ask >= meta.min_notional*1.05 or no order


def planned_notional(size_factor: float, equity: float = EQUITY) -> float:
    """Same arithmetic as risk.RiskManager.size for an empty book of positions."""
    risk_toman = equity * RISK_PER_TRADE * size_factor
    n = risk_toman / (STOP + TAKER + MAKER)
    return min(n, equity * MAX_POS_PCT)


def read_csv_snapshot(p: Path) -> pd.DataFrame:
    """Read a csv that may still be appended to: drop a partial last line."""
    txt = p.read_text(encoding="utf-8")
    lines = txt.split("\n")
    if not txt.endswith("\n"):
        lines = lines[:-1]
    return pd.read_csv(io.StringIO("\n".join(lines)), on_bad_lines="skip")


def load_ticks(tmn_only: bool = True) -> pd.DataFrame:
    fr = []
    for r in TICK_RUNS:
        d = read_csv_snapshot(MAIN / "runs" / r / "ticks.csv")
        d["run"] = r
        fr.append(d)
    t = pd.concat(fr, ignore_index=True)
    for c in ["t", "bid", "ask", "bid_qty", "ask_qty", "fair", "baseline", "buy_dev", "sell_dev"]:
        t[c] = pd.to_numeric(t[c], errors="coerce")
    t = t.dropna(subset=["t", "bid", "ask", "bid_qty", "ask_qty", "fair", "buy_dev", "sell_dev"])
    t = t[t.t <= TICK_CUTOFF]          # frozen snapshot so every script sees the same rows
    if tmn_only:
        t = t[t.symbol.isin(TMN8)]
    t["ask_n"] = t.ask * t.ask_qty
    t["bid_n"] = t.bid * t.bid_qty
    t["spread"] = t.ask / t.bid - 1
    return t.sort_values(["symbol", "t"]).reset_index(drop=True)


def read_jsonl(p: Path) -> list[dict]:
    out = []
    txt = p.read_text(encoding="utf-8")
    for line in txt.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue           # partial last line of a file still being written
    return out


def dump(obj, name: str):
    def conv(o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return None if not np.isfinite(o) else float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        return str(o)
    (OUT / name).write_text(json.dumps(obj, indent=1, ensure_ascii=False, default=conv), encoding="utf-8")
