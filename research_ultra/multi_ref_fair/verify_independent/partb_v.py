"""Independent checks: reference divergence, USDTTMN leg spikes (1m), entries on spikes, tick-level checks."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

MAIN = Path("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot")
HERE = Path(__file__).resolve().parent
D = HERE / "data"
T0, T1 = 1791986700, 1792591440
grid = np.arange(T0, T1 + 60, 60)
BASES = ["BTC", "ETH", "SOL", "XRP", "DOGE", "PEPE", "BNB", "ARB"]
res = {}

# 1) Binance vs median3 divergence on 1m closes
div = {}
for b in BASES:
    r = pd.DataFrame({ex: pd.read_csv(D / f"{ex}_{b}.csv").drop_duplicates("ts").set_index("ts")["close"]
                      for ex in ("binance", "okx", "bybit")}).reindex(grid)
    d = (r.binance / r.median(axis=1) - 1).abs().dropna()
    div[b] = {f">{x}%": round((d > x / 100).mean() * 100, 2) for x in (0.05, 0.1, 0.2)}
    div[b]["n"] = len(d)
res["binance_vs_median3_pct_of_minutes"] = div

# 2) USDTTMN raw vs 5-min median
uw = pd.read_csv(MAIN / "research/candles/USDTTMN_1.csv")
uw = uw[(uw.ts >= T0) & (uw.ts <= T1)].drop_duplicates("ts").set_index("ts")
u = uw.close.reindex(grid).ffill().bfill()
u5 = u.rolling(5, min_periods=1).median()
prev5 = u.shift(1).rolling(5, min_periods=1).median()
dev5 = (u / u5 - 1)
jump = (u / prev5 - 1)
res["usdttmn"] = {"traded_minutes": int((uw.volume > 0).sum()), "grid_minutes": len(grid),
                  "abs_u_vs_med5_gt": {x: round((dev5.abs() > x / 100).mean() * 100, 3) for x in (0.05, 0.1, 0.2)},
                  "abs_u_vs_prev5_gt": {x: round((jump.abs() > x / 100).mean() * 100, 3) for x in (0.05, 0.1, 0.2)},
                  "max_abs_u_vs_med5_pct": round(dev5.abs().max() * 100, 3)}

# 3) A0 entries on u up-spike (my own trade list)
t = pd.read_csv(HERE / "trades_A0_bt.csv")
t["u_dev5"] = dev5.reindex(t.ts).to_numpy() * 100
t["u_jump"] = jump.reindex(t.ts).to_numpy() * 100


def st(x):
    return {"n": len(x), "mean": round(x.ret.mean(), 3) if len(x) else None, "sum": round(x.ret.sum(), 2),
            "win": round((x.ret > 0).mean() * 100, 1) if len(x) else None, "exits": x.why.value_counts().to_dict()}


res["A0_on_u_spike_vs_med5"] = {x: st(t[t.u_dev5 > x]) for x in (0.05, 0.1, 0.2)}
res["A0_on_u_spike_vs_prev5"] = {x: st(t[t.u_jump > x]) for x in (0.05, 0.1, 0.2)}
res["A0_not_on_spike_0.1"] = st(t[~(t.u_dev5 > 0.1)])
cnt = t.groupby("ts").symbol.transform("count")
res["A0_simultaneous_2plus"] = st(t[cnt >= 2]) | {"minutes": int((t.groupby("ts").size() >= 2).sum()),
                                                   "mean_u_dev5": round(t[cnt >= 2].u_dev5.mean(), 3)}

# 4) Ticks
tk = {}
for r in ["shadow_v2", "shadow_v3", "shadow_v4", "shadow_v5", "shadow_v6", "live_main"]:
    x = pd.read_csv(MAIN / "runs" / r / "ticks.csv", on_bad_lines="skip").dropna(subset=["fair", "buy_dev"])
    tm = x[x.symbol.str.endswith("TMN")]
    info = {"rows_tmn": len(tm), "hours": round((x.t.max() - x.t.min()) / 3600, 2),
            "min_buy_dev_pct": round(tm.buy_dev.min() * 100, 3),
            "ticks_buy_dev_le_-0.7": int((tm.buy_dev <= -0.007).sum()),
            "ticks_buy_dev_le_-0.5": int((tm.buy_dev <= -0.005).sum()),
            "timestamps_2plus_le_-0.4": int((tm[tm.buy_dev <= -0.004].groupby("t").size() >= 2).sum()),
            "timestamps_3plus_le_-0.25": int((tm[tm.buy_dev <= -0.0025].groupby("t").size() >= 3).sum())}
    us = x[x.symbol.str.endswith("USDT")]
    if len(us):
        a_ = tm.assign(b=tm.symbol.str[:-3]).set_index(["t", "b"]).fair
        b_ = us.assign(b=us.symbol.str[:-4]).set_index(["t", "b"]).fair
        imp = (a_ / b_).dropna().groupby(level=0).median()
        imp = imp.sort_index()
        j = (imp / imp.shift(1) - 1).abs().dropna()
        ti = pd.to_datetime(imp.index, unit="s")
        m5 = pd.Series(imp.to_numpy(), index=ti).rolling("5min").median().to_numpy()
        info["implied_u_n"] = len(imp)
        info["implied_u_max_tick_jump_pct"] = round(j.max() * 100, 4)
        info["implied_u_max_abs_vs_5min_median_pct"] = round(np.nanmax(np.abs(imp.to_numpy() / m5 - 1)) * 100, 4)
    tk[r] = info
res["ticks"] = tk
(HERE / "partb_summary.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
print(json.dumps(res, indent=1, default=str))
