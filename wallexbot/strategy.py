"""Deterministic signal layer: proposes long-only trade candidates from closed 15m bars.

The rules here only PROPOSE. Every candidate then goes to the AI committee (ai.py), which
reads the market context and approves, resizes or rejects it, and finally to the risk
manager (risk.py), whose hard limits nobody can override.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import math
import pandas as pd


@dataclass
class Candidate:
    symbol: str
    ts: str                 # 15m bar open time (UTC ISO) whose close produced the signal
    setup: str
    entry: float            # limit price we try to buy at (the signal bar close)
    stop: float
    tp: float
    max_hold_bars: int
    features: dict = field(default_factory=dict)
    why: str = ""

    @property
    def stop_pct(self) -> float:
        return (self.entry - self.stop) / self.entry

    @property
    def tp_pct(self) -> float:
        return (self.tp - self.entry) / self.entry

    def to_dict(self) -> dict:
        d = asdict(self)
        d["stop_pct"] = round(self.stop_pct, 5)
        d["tp_pct"] = round(self.tp_pct, 5)
        return d


FEATURE_KEYS = ["close", "ema20", "ema50", "rsi", "atr_pct", "bb_pos", "vol_z", "ret_1h", "ret_4h",
                "h_ema20", "h_ema50", "h_rsi", "h_adx", "h_atr_pct", "h_slope", "h_close"]


def _feat(row: pd.Series) -> dict:
    out = {}
    for k in FEATURE_KEYS:
        v = row.get(k)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            continue
        out[k] = round(float(v), 6) if abs(float(v)) < 1e6 else round(float(v), 1)
    return out


def regime(row: pd.Series, p: dict) -> str:
    """Coarse hourly regime used to pick which setup is allowed."""
    e20, e50, close, adx = row.get("h_ema20"), row.get("h_ema50"), row.get("h_close"), row.get("h_adx")
    if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in (e20, e50, close, adx)):
        return "unknown"
    spread = (e20 - e50) / close
    if spread > p["trend_min_spread"] and close > e50 and adx >= p["trend_min_adx"]:
        return "up"
    if spread < -p["trend_min_spread"] and close < e50:
        return "down"
    if abs(spread) <= p["range_max_spread"] and adx < p["range_max_adx"]:
        return "range"
    return "mixed"


def propose(symbol: str, f: pd.DataFrame, i: int, p: dict) -> list[Candidate]:
    """Candidates produced by the close of bar i (f is the feature frame from indicators.add_features)."""
    if i < 60:
        return []
    row, prev = f.iloc[i], f.iloc[i - 1]
    if any(math.isnan(float(row.get(k, float("nan")))) for k in ("atr", "rsi", "ema20", "bb_lo")):
        return []
    close, atr_v = float(row["close"]), float(row["atr"])
    reg = regime(row, p)
    feats = _feat(row) | {"regime": reg}
    ts = f.index[i].isoformat()
    out: list[Candidate] = []

    def clamp_stop(dist_pct: float) -> float:
        return min(max(dist_pct, p["stop_min_pct"]), p["stop_max_pct"])

    # A) trend pullback: hourly uptrend, 15m dipped and is turning back up near EMA20
    if reg == "up" and "pullback" in p["setups"]:
        recent_low_rsi = float(f["rsi"].iloc[i - 4:i].min())
        if (recent_low_rsi < p["pb_rsi_dip"] and float(row["rsi"]) > float(prev["rsi"])
                and close > float(row["ema20"]) >= float(prev["low"]) * 0.995 - 1e-9
                and close > float(row["open"])):
            sp = clamp_stop(p["pb_stop_atr"] * atr_v / close)
            tp = max(p["pb_tp_atr"] * atr_v / close, p["min_tp_pct"])
            out.append(Candidate(symbol, ts, "trend_pullback", close, close * (1 - sp), close * (1 + tp),
                                 p["pb_max_hold"], feats,
                                 f"hourly uptrend, 15m RSI dipped to {recent_low_rsi:.0f} and turned up above EMA20"))

    # B) range reversion: no hourly trend, 15m closes back inside the lower Bollinger band after a flush
    if reg in ("range", "mixed") and "reversion" in p["setups"]:
        if (float(prev["close"]) < float(prev["bb_lo"]) and close > float(row["bb_lo"])
                and float(prev["rsi"]) < p["rv_rsi_max"] and close > float(row["open"])):
            sp = clamp_stop(p["rv_stop_atr"] * atr_v / close)
            target = max(float(row["bb_mid"]), close * (1 + p["min_tp_pct"]))
            out.append(Candidate(symbol, ts, "range_reversion", close, close * (1 - sp), target,
                                 p["rv_max_hold"], feats,
                                 f"range regime, flush below lower band (RSI {float(prev['rsi']):.0f}) then close back inside"))

    # C) volume breakout: hourly not down, close above prior 20-bar high on unusual volume
    if reg in ("up", "mixed") and "breakout" in p["setups"]:
        vz = row.get("vol_z")
        if (vz is not None and not math.isnan(float(vz)) and float(vz) > p["bo_vol_z"]
                and close > float(row["hh20"]) and float(row["rsi"]) < p["bo_rsi_max"]):
            sp = clamp_stop(max(p["bo_stop_atr"] * atr_v / close, (close - float(row["low"])) / close))
            tp = max(p["bo_tp_atr"] * atr_v / close, p["min_tp_pct"])
            out.append(Candidate(symbol, ts, "volume_breakout", close, close * (1 - sp), close * (1 + tp),
                                 p["bo_max_hold"], feats,
                                 f"close above 20-bar high on volume z={float(vz):.1f}"))
    return out
