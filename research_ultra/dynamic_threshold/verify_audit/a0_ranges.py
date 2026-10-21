import sys
sys.path.insert(0, "rerun")
import pandas as pd, numpy as np
import dt_data as D
for s in D.SYMS + ["USDTTMN"]:
    w = D.wallex_1m(s)
    print(s, w.index[0], w.index[-1], "rows", len(w), "traded", int((w.volume > 0).sum()))
for b in ["BTC", "PEPE", "ARB"]:
    a = pd.read_csv(D.MAIN / "research" / "premium" / f"binance_{b}USDT_1m.csv", index_col=0, parse_dates=True)["close"]
    g = pd.read_csv(D.HERE / "data" / f"binance_{b}USDT_gap.csv", index_col=0, parse_dates=True)["close"]
    ov = a.index.intersection(g.index)
    print(b, "main", a.index[0], a.index[-1], "gap", g.index[0], g.index[-1], "overlap", len(ov), "maxabsdiff", float((a[ov] - g[ov]).abs().max()) if len(ov) else None)
    full = D.binance_1m(b)
    grid = pd.date_range(full.index[0], full.index[-1], freq="1min")
    print("   binance missing minutes:", len(grid.difference(full.index)))
t = D.load_ticks()
for r, g in t.groupby("run"):
    print(r, pd.to_datetime(g.t.min(), unit="s", utc=True), pd.to_datetime(g.t.max(), unit="s", utc=True), len(g))
