"""Standalone outcome of every candidate (no portfolio limits): what each setup is worth
after fees, by setup / symbol / regime. Used to decide which rules deserve to exist."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from wallexbot import backtest
from wallexbot.config import load_config


def outcome(f, i, c, fees, slip, valid, be_trig, be_lock):
    o, h, l, cl = f["open"].to_numpy(), f["high"].to_numpy(), f["low"].to_numpy(), f["close"].to_numpy()
    n = len(f)
    fill = None
    for j in range(i + 1, min(i + 1 + valid, n)):
        if l[j] < c.entry:
            fill = j
            break
    if fill is None:
        return None
    entry, stop, tp = c.entry, c.stop, c.tp
    for j in range(fill + 1, n):
        if l[j] <= stop:
            px, fee, why = min(stop, o[j]) * (1 - slip), fees["taker"], "stop"
        elif h[j] >= tp:
            px, fee, why = tp, fees["maker"], "target"
        else:
            if h[j] >= entry * (1 + be_trig):
                stop = max(stop, entry * (1 + be_lock))
            if j - fill >= c.max_hold_bars:
                px, fee, why = cl[j] * (1 - slip), fees["taker"], "time"
            else:
                continue
        ret = px * (1 - fee) / (entry * (1 + fees["maker"])) - 1
        return {"ret": ret, "why": why, "bars": j - fill}
    return None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", action="append", default=[])
    a = ap.parse_args()
    cfg = load_config().raw
    for kv in a.set:
        k, v = kv.split("=", 1)
        sec, key = k.split(".", 1)
        cfg[sec][key] = json.loads(v)
    p, r = cfg["strategy"], cfg["risk"]
    frames = backtest.build_frames(cfg["markets"])
    cands = backtest.collect_candidates(frames, p)
    rows = []
    for s, i, c in cands:
        out = outcome(frames[s], i, c, cfg["fees"], cfg["slippage"], p["entry_valid_bars"],
                      r["breakeven_trigger_pct"], r["breakeven_lock_pct"])
        if out:
            rows.append({"symbol": s, "setup": c.setup, "regime": c.features.get("regime"),
                         "stop_pct": c.stop_pct, "tp_pct": c.tp_pct, **out})
    df = pd.DataFrame(rows)
    print(f"candidates {len(cands)}, filled {len(df)}")
    agg = dict(n=("ret", "size"), mean_pct=("ret", lambda x: x.mean() * 100), win=("ret", lambda x: (x > 0).mean() * 100))
    print(df.groupby("setup").agg(**agg).round(3))
    print(df.groupby(["setup", "symbol"]).agg(**agg).round(3))
    print(df.groupby("why").size())
    print("overall mean %", round(df.ret.mean() * 100, 3))
