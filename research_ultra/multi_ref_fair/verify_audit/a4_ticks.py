"""Audit 4: independent check of the tick-level Part B claims, straight from MAIN/runs/*/ticks.csv (read-only snapshot).
  * exact USDT/TMN mid on shadow runs: u = fair(XTMN) / fair(XUSDT) at the same timestamp
  * biggest tick-to-tick jump of u, biggest deviation from a causal 5-min median
  * how many ticks reached recorded buy_dev <= -0.7%, and how many markets were <= -0.4% / -0.25% at one instant
  * signal-rate sanity check: close-based minute signals in the 7-day candles vs tick signals
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

MAIN = Path("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot")
HERE = Path(__file__).resolve().parent
RUNS = ["shadow_v2", "shadow_v3", "shadow_v4", "shadow_v5", "shadow_v6", "live_main"]


def load(r):
    t = pd.read_csv(MAIN / "runs" / r / "ticks.csv", on_bad_lines="skip")
    for c in ("t", "bid", "ask", "fair", "baseline", "buy_dev", "sell_dev"):
        t[c] = pd.to_numeric(t[c], errors="coerce")
    t = t.dropna(subset=["t", "bid", "ask", "fair", "buy_dev", "sell_dev"])
    t["run"] = r
    return t


res = {}
T = pd.concat([load(r) for r in RUNS], ignore_index=True)
tm = T[T.symbol.str.endswith("TMN")]
us = T[T.symbol.str.endswith("USDT")]
res["rows"] = {r: int((tm.run == r).sum()) for r in RUNS}
res["hours"] = {r: round(float((g.t.max() - g.t.min()) / 3600), 2) for r, g in tm.groupby("run")}
res["symbols_per_run"] = {r: sorted(g.symbol.unique().tolist()) for r, g in T.groupby("run")}

m = tm.assign(b=tm.symbol.str[:-3]).merge(us.assign(b=us.symbol.str[:-4])[["t", "b", "fair"]].rename(columns={"fair": "g"}), on=["t", "b"])
u = (m.fair / m.g).groupby([m.run, m.t]).median()
uj = {}
for r, s in u.groupby(level=0):
    s = s.droplevel(0).sort_index()
    jump = (s / s.shift(1) - 1).abs().dropna()
    x = pd.Series(s.values, index=pd.to_datetime(s.index, unit="s"))
    med = x.rolling("300s", min_periods=3).median()
    dev = (x / med - 1).abs().dropna()
    # cross-market consistency: spread of the per-market implied u at one timestamp
    disp = (m[m.run == r].assign(uu=lambda d: d.fair / d.g).groupby("t").uu.agg(lambda v: v.max() / v.min() - 1))
    uj[r] = {"ticks": int(len(s)), "max_tick_jump_pct": round(float(jump.max() * 100), 4),
             "n_jump_gt_0.1pct": int((jump > 0.001).sum()), "max_dev_vs_5min_median_pct": round(float(dev.max() * 100), 4),
             "dev_gt_0.1pct_share": round(float((dev > 0.001).mean() * 100), 3),
             "max_cross_market_u_dispersion_pct": round(float(disp.max() * 100), 5)}
res["u_exact_shadow"] = uj

sig = {}
for r, g in tm.groupby("run"):
    sig[r] = {k: int((g.buy_dev <= -k / 100).sum()) for k in (0.25, 0.4, 0.5, 0.7)}
    sig[r]["min_buy_dev_pct"] = round(float(g.buy_dev.min() * 100), 3)
    sig[r]["min_buy_dev_symbol"] = g.loc[g.buy_dev.idxmin(), "symbol"]
    for thr in (0.004, 0.0025):
        c = g[g.buy_dev <= -thr].groupby("t").symbol.nunique()
        sig[r][f"max_markets_at_once_le_{thr * 100}"] = int(c.max()) if len(c) else 0
res["tick_signals"] = sig

# signal-rate sanity check: share of market-minutes at <= -0.7% (close-based, candles) vs tick-based
a0 = pd.read_csv(HERE / "a3_trades_bt_A0.csv")
cand_hours = 7 * 24
tick_hours = sum(res["hours"].values())
res["rate_check"] = {"candle_A0_entries_per_hour_8mkts": round(len(a0) / cand_hours, 3),
                     "tick_hours": round(tick_hours, 2),
                     "expected_entries_in_tick_period_at_candle_rate": round(len(a0) / cand_hours * tick_hours, 2),
                     "tick_entries_observed": int(sum(v[0.7] for v in sig.values())),
                     "poisson_p0": round(float(np.exp(-len(a0) / cand_hours * tick_hours)), 3)}
print(json.dumps(res, indent=1, default=str))
(HERE / "a4_ticks.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
