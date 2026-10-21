"""Diagnostic: distribution of the trailing 3h std of buy_dev, and which V4 IS trades a k=1.5 vol threshold drops."""
import sys
sys.argv = ["verify_bt.py", "--tag", "diag"]
import numpy as np, pandas as pd
exec(open("verify_bt.py", encoding="utf-8").read().split("data = {s: prepare(s) for s in SYMS}")[0])
for s in SYMS:
    df, hs = prepare(s)
    q = df.sd.quantile([0.1, 0.5, 0.9]).round(5).tolist()
    v4 = simulate(s, df, 0.007, hs)
    k15 = simulate(s, df, np.maximum(FLOOR, 1.5 * df.sd.fillna(np.inf).values), hs)
    sdat = [round(float(df.sd.get(pd.Timestamp(x["t"], unit="s", tz="UTC"), np.nan)) * 100, 3) for x in v4]
    print(s, "sd p10/50/90 %", [round(x * 100, 3) for x in q], "| V4 n", len(v4), "k1.5 n", len(k15), "| sd at V4 entries %", sdat[:14])
