"""Audit 5: the PEPE 10-20 ~14:00 event that dominates the A0->A1 and A0->B1 differences: is it real data,
and why do the variants enter at different minutes? Also: how many A0 entries are single-print dips."""
from pathlib import Path

import numpy as np
import pandas as pd

import a3_indep_bt as B

HERE = Path(__file__).resolve().parent
s = "PEPETMN"
w = B.W[s]
lo, hi = pd.Timestamp("2026-10-20 13:50", tz="UTC"), pd.Timestamp("2026-10-20 14:30", tz="UTC")
g = B.GL[s]
for v, (gk, uk) in B.VARIANTS.items():
    p = B.prep(s, gk, uk, 1.0)
    df = pd.DataFrame({"bd_pct": p["bd"] * 100, "sd_pct": p["sd"] * 100, "ask": p["ask"]}, index=p["t"])
    if v == "A0":
        base = df
    else:
        base[f"bd_{v}"] = df.bd_pct
base["w_close"] = w.c
base["w_vol"] = w.v
base["bin"] = g["bin"].reindex(base.index)
base["med3"] = g["med"].reindex(base.index)
base["u"] = B.u_raw.reindex(base.index)
base["u_med5"] = B.u_med5.reindex(base.index)
pd.set_option("display.width", 250)
print(base.loc[lo:hi].round(4).to_string())

# single-print check over all A0 entries (bt mode)
a0 = pd.read_csv(HERE / "a3_trades_bt_A0.csv", parse_dates=["t"])
out = []
for r in a0.itertuples():
    p = B.prep(r.symbol, "bin", "raw", 1.0)
    k = int(np.searchsorted(p["t"], r.t))
    nxt = p["bd"][k + 1] if k + 1 < len(p["t"]) else np.nan
    gap = (p["t"][k + 1] - p["t"][k]).total_seconds() / 60 if k + 1 < len(p["t"]) else np.nan
    out.append({"symbol": r.symbol, "t": r.t, "ret": r.ret_pct, "bd": p["bd"][k] * 100, "bd_next": nxt * 100, "gap_min": gap})
o = pd.DataFrame(out)
o["persists"] = o.bd_next <= -0.7
print("\nA0 entries:", len(o), "| dip persists into next traded minute:", int(o.persists.sum()),
      "| next print back above -0.35%:", int((o.bd_next > -0.35).sum()), "| median gap to next print (min):", float(o.gap_min.median()))
print("mean ret if persists:", round(float(o[o.persists].ret.mean()), 3), "n", int(o.persists.sum()),
      "| if not:", round(float(o[~o.persists].ret.mean()), 3), "n", int((~o.persists).sum()))
print(o.groupby("symbol").agg(n=("ret", "size"), persists=("persists", "sum"), mean_ret=("ret", "mean"), med_gap=("gap_min", "median")).round(3))
o.to_csv(HERE / "a5_single_print.csv", index=False)
