"""Extra views on the main run: all-market fixed-threshold curve, the marginal 0.7-0.9 % band,
exit-reason means, and how the candle-modelled buy_dev compares with the real tick buy_dev."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import dt_data as D

OUT = D.HERE / "out"
R = json.loads((OUT / "results_main.json").read_text(encoding="utf-8"))
res = {}

# 1) one global fixed threshold, summed over the 8 markets (independent markets, from the sweep)
curve = {}
for th in ("0.7", "0.8", "0.9", "1.0", "1.2", "1.5"):
    row = {}
    for per in ("IS", "OOS"):
        xs = [R["fixed_threshold_sweep"][f"{s}@{th}"][per] for s in D.SYMS]
        n = sum(x["trades"] for x in xs)
        sm = sum(x["sum_net_pct"] for x in xs)
        row[per] = {"trades": n, "sum_net_pct": round(sm, 3), "mean_net_pct": round(sm / n, 3) if n else None}
    curve[th] = row
res["global_fixed_threshold_curve"] = curve

# 2) the trades a higher threshold removes: V4 trades whose entry dip was shallower than 0.9 % / 0.8 %
v4 = pd.read_csv(OUT / "trades_main_V4_fixed_0.7.csv")
band = {}
for per in ("IS", "OOS"):
    x = v4[v4.period == per]
    for lo, hi in ((0.7, 0.8), (0.8, 0.9), (0.9, 1.0), (1.0, 99)):
        y = x[(-x.entry_dev_pct >= lo) & (-x.entry_dev_pct < hi)]
        band[f"{per} dip {lo}-{hi}%"] = {"trades": len(y), "sum_net_pct": round(y.ret_pct.sum(), 3),
                                         "mean_net_pct": round(y.ret_pct.mean(), 3) if len(y) else None}
res["V4_trades_by_entry_dip_depth"] = band
res["V4_exit_reason_means"] = {per: v4[v4.period == per].groupby("why").ret_pct.agg(["count", "mean", "sum"]).round(3).to_dict("index")
                               for per in ("IS", "OOS")}
res["V4_trades_per_day_OOS"] = round((v4.period == "OOS").sum() / 3, 1)
res["V4_trades_per_day_IS"] = round((v4.period == "IS").sum() / 4, 1)

# 3) candle-modelled buy_dev vs real tick buy_dev, same UTC hours (14:00-18:15) to be fair
u, age = D.usdt_series()
sp = D.live_spreads()
tk = D.load_ticks()
cmp_ = {}
for s in D.SYMS:
    df = D.market_frame(s, u, age, sp[s] / 2, 1e9)
    h = df.index.hour + df.index.minute / 60
    c = df.buy_dev[(h >= 14) & (h < 18.25)].dropna()
    tt = tk[tk.symbol == s].buy_dev
    cmp_[s] = {"candle_p1_pct": round(c.quantile(0.01) * 100, 3), "candle_share_le_0.7": round(float((c <= -0.007).mean()) * 100, 3),
               "candle_minutes": len(c),
               "tick_p1_pct": round(tt.quantile(0.01) * 100, 3), "tick_share_le_0.7": round(float((tt <= -0.007).mean()) * 100, 3),
               "tick_hours": round(len(tt) * 2.5 / 3600, 2)}
res["candle_vs_tick_buy_dev_same_hours"] = cmp_

(OUT / "summary_extra.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
print(json.dumps(res, indent=1, default=str))
