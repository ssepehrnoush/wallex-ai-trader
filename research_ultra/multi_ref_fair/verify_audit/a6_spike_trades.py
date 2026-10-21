"""Audit 6: (a) the A0 entries taken on a USDTTMN up-spike (u / median of the previous 5 minutes - 1 > 0.1%),
re-scored under every fill/exit model of a3; (b) independent recount of Part A divergence for PEPE/ARB/DOGE."""
from pathlib import Path

import numpy as np
import pandas as pd

import a3_indep_bt as B

HERE = Path(__file__).resolve().parent
u_prev5 = B.u_raw.rolling(5, min_periods=3).median().shift(1)
spike = (B.u_raw / u_prev5 - 1) * 100
for mode in ("bt", "live", "live_x", "hs2", "lag1", "lag1_2m", "persist"):
    p = HERE / f"a3_trades_{mode}_A0.csv"
    if not p.exists():
        continue
    d = pd.read_csv(p, parse_dates=["t"])
    d["u_spike"] = spike.reindex(d.t).to_numpy()
    for thr in (0.1,):
        x = d[d.u_spike > thr]
        y = d[~(d.u_spike > thr)]
        print(f"{mode:8s} spike>{thr}%: n={len(x)} mean={x.ret_pct.mean():.3f} sum={x.ret_pct.sum():.2f} win={(x.ret_pct > 0).mean() * 100:.0f}%"
              f" | others: n={len(y)} mean={y.ret_pct.mean():.3f}")

print()
for s in ("PEPETMN", "ARBTMN", "DOGETMN", "BTCTMN"):
    g = B.GL[s]
    dev = (g["bin"] / g["med"] - 1).loc[B.GRID[0]:B.GRID[-1]].dropna().abs()
    print(s, "binance vs median3 |dev| > 0.05/0.1/0.2%:", [round(float((dev > k / 100).mean() * 100), 2) for k in (0.05, 0.1, 0.2)], "n", len(dev))
