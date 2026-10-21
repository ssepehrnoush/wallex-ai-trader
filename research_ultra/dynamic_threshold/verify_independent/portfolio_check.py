"""Approximate portfolio check: greedy max-3-open filter on the per-market trade lists, 828k TMN per trade, OOS only."""
import pandas as pd
SPLIT = pd.Timestamp("2026-10-18 14:05", tz="UTC").timestamp()
for v in ["V4", "V1"]:
    tr = pd.read_csv(f"out/trades_{v}_main.csv").sort_values("t")
    tr = tr[tr.t >= SPLIT]
    open_, kept = [], []
    for _, x in tr.iterrows():
        open_ = [e for e in open_ if e > x.t]
        if len(open_) < 3:
            open_.append(x.texit)
            kept.append(x)
    k = pd.DataFrame(kept)
    print(v, "OOS trades", len(tr), "kept under max-3", len(k), "sum net %", round(k.net.sum(), 3),
          "P&L TMN @828k", round((k.net / 100 * 828_000).sum()), "| by symbol", k.groupby("symbol").size().to_dict())
