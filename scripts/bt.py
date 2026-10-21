"""Quick backtest runner used while tuning: python scripts/bt.py [--ai] [--days N] [--label X]"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from wallexbot import backtest
from wallexbot.ai import AICommittee
from wallexbot.config import ROOT, load_config, load_secrets

ap = argparse.ArgumentParser()
ap.add_argument("--ai", action="store_true")
ap.add_argument("--days", type=int, default=0, help="only the last N days")
ap.add_argument("--label", default=None)
ap.add_argument("--config", default=None)
ap.add_argument("--set", action="append", default=[], help="override, e.g. strategy.pb_tp_atr=3")
a = ap.parse_args()

cfg = load_config(a.config).raw
for kv in a.set:
    k, v = kv.split("=", 1)
    sec, key = k.split(".", 1)
    cfg[sec][key] = json.loads(v)

frames = backtest.build_frames(cfg["markets"])
if a.days:
    end = max(f.index[-1] for f in frames.values())
    start = end - pd.Timedelta(days=a.days)
    # keep 3 days of warm-up for indicators, but only trade inside the window
    frames = {s: f[f.index >= start - pd.Timedelta(days=3)] for s, f in frames.items()}
ctx = backtest.context_frame(frames)
cands = backtest.collect_candidates(frames, cfg["strategy"])
if a.days:
    cands = [(s, i, c) for s, i, c in cands if pd.Timestamp(c.ts) >= start]
ai = AICommittee(cfg["ai"], load_secrets(), ROOT / "runs" / "ai_cache") if a.ai else None
verdicts = backtest.judge(cands, frames, ctx, ai)
label = a.label or ("ai" if a.ai else "rules")
res = backtest.run(cfg, frames, cands, verdicts, 2_000_000, label, ROOT / "runs" / "backtests")
res["candidates"] = len(cands)
if ai:
    res["ai_approved"] = sum(1 for v in verdicts.values() if v.get("approved"))
    res["ai_calls"], res["ai_cache_hits"] = ai.calls, ai.cache_hits
print(json.dumps(res, indent=1, ensure_ascii=False))
