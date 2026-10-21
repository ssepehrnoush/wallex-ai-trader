"""Directional check of hold-45 variants (in-bounds) per half, 8 vs 7 markets, two baseline warm-ups. Independent code."""
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from vsim import MKTS, build, simulate, tick_spreads
SPLIT = int(pd.Timestamp("2026-10-18 14:05", tz="UTC").timestamp())
sp, _ = tick_spreads(); spreads = {s: sp[s] for s in MKTS}
for minp in (30, 60):
    data = build(spreads, 1.0, minp)
    for label, mk in (("8m", MKTS), ("7m", [m for m in MKTS if m != "BNBTMN"])):
        cur = simulate(data, 0.009, 25, 0.0005, markets=mk)
        for c in [(0.009, 45, 0.0), (0.009, 45, 0.0005), (0.009, 45, 0.001), (0.012, 45, 0.001), (0.009, 35, 0.0005)]:
            tr = simulate(data, *c, markets=mk)
            h = lambda x, a: x[(x.entry_t < SPLIT) if a else (x.entry_t >= SPLIT)].net_pct.sum()
            print(f"mp{minp} {label} {c[0]*100:.1f}/{c[1]}/{c[2]*100:.2f}: n {len(tr)} vs {len(cur)}  diff full {tr.net_pct.sum()-cur.net_pct.sum():+.2f}  d1-4 {h(tr,1)-h(cur,1):+.2f}  d5-7 {h(tr,0)-h(cur,0):+.2f}  TMN diff {tr.pnl_tmn.sum()-cur.pnl_tmn.sum():+,.0f}")
