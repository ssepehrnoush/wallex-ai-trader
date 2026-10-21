"""Research: would RESTING bids below fair value catch the brief wicks that 2.5-second polling misses?

A resting LIMIT bid sits at fair * (1 + baseline - x). It fills (as maker) when a minute's LOW trades
strictly below it. The position is then sold like the live engine does: when the bid is back near
normal (taker), at the stop, or after max hold. Fair value is known only up to the previous minute
(no look-ahead): bid level for minute t uses fair and baseline of minute t-1.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd

from premium import binance_1m, wallex_1m
from wallexbot.fairvalue import base_of

ap = argparse.ArgumentParser()
ap.add_argument("--symbols", default="BTCTMN,ETHTMN,SOLTMN,XRPTMN,DOGETMN,PEPETMN,BNBTMN,ARBTMN,SANDTMN")
ap.add_argument("--x", default="0.008,0.01,0.012")
ap.add_argument("--stop", type=float, default=0.009)
ap.add_argument("--hold", type=int, default=25)
a = ap.parse_args()
maker, taker = 0.0025, 0.003

ticks = Path(__file__).resolve().parent.parent / "runs" / "live_main" / "ticks.csv"
spreads = {}
if ticks.exists():
    t = pd.read_csv(ticks)
    spreads = ((t.ask / t.bid - 1).groupby(t.symbol).median()).to_dict()

end = int(time.time()) - 120
start = end - 7 * 86400
usdt = wallex_1m("USDTTMN", start, end)["close"]
usdt = usdt.reindex(pd.date_range(usdt.index[0], usdt.index[-1], freq="1min", tz="UTC")).ffill()
out = {}
for s in a.symbols.split(","):
    g = binance_1m(f"{base_of(s)}USDT", start, end)
    w = wallex_1m(s, start, end)
    idx = pd.date_range(w.index[0], w.index[-1], freq="1min", tz="UTC")
    w = w.reindex(idx)
    fair = (g * usdt).reindex(idx).ffill()
    traded = w["volume"].fillna(0) > 0
    close = w["close"].ffill()
    prem = (close / fair - 1).where(traded)
    base = prem.rolling(180, min_periods=30).median().ffill()
    hs = spreads.get(s, 0.003) / 2
    low = w["low"].where(traded).to_numpy()
    cl = close.to_numpy()
    fa_prev = fair.shift(1).to_numpy()
    ba_prev = base.shift(1).to_numpy()
    fa, ba = fair.to_numpy(), base.to_numpy()
    res = {}
    for x in [float(v) for v in a.x.split(",")]:
        rets, i, n = [], 1, len(idx)
        while i < n:
            if not np.isfinite(fa_prev[i]) or not np.isfinite(ba_prev[i]) or not np.isfinite(low[i]):
                i += 1
                continue
            level = fa_prev[i] * (1 + ba_prev[i] - x)
            if low[i] < level:
                e, j, why = level, i, "end"
                while j + 1 < n:
                    j += 1
                    bid = cl[j] * (1 - hs)
                    if bid <= e * (1 - a.stop):
                        why = "stop"; break
                    if np.isfinite(fa[j]) and np.isfinite(ba[j]) and bid / fa[j] - 1 - ba[j] >= -0.0005 and bid > e:
                        why = "target"; break
                    if j - i >= a.hold:
                        why = "time"; break
                r = cl[j] * (1 - hs) * (1 - taker) / (e * (1 + maker)) - 1
                rets.append((r, why))
                i = j + 1
            else:
                i += 1
        if rets:
            r = np.array([v for v, _ in rets])
            res[x] = {"n": len(r), "mean_pct": round(r.mean() * 100, 3), "win_pct": round((r > 0).mean() * 100, 1),
                      "sum_pct": round(r.sum() * 100, 2), "stops": sum(1 for _, w_ in rets if w_ == "stop")}
        else:
            res[x] = {"n": 0}
    out[s] = {"half_spread_pct": round(hs * 100, 3), **{str(k): v for k, v in res.items()}}
    print(s, json.dumps(out[s]))
Path(__file__).resolve().parent.parent.joinpath("runs", "backtests", "wick_summary.json").write_text(json.dumps(out, indent=1))
