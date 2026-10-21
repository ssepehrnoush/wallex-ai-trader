"""Check Part A against what the live bot really uses: the Binance MID (bookTicker), not the 1m close.

From the live/shadow ticks, Binance mid = fair(XTMN) / u, with u the USDT/TMN mid (exact on shadow runs via
the USDT markets, estimated on live_main from Binance 1 s closes; see ticks_usdt_leg.py, error p95 ~0.015%).
Ticks that fall in the last 5 seconds of an exchange minute are compared with the OKX and Bybit 1m closes of
that minute: median3 = median(binance_mid, okx_close, bybit_close).

  python research_ultra/multi_ref_fair/live_mid_check.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import ticks_usdt_leg as T

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
LAG = 39       # local clock -> exchange time (measured in ticks_usdt_leg.py)


def main():
    ticks = pd.concat([T.load(r) for r in T.RUNS], ignore_index=True)
    tm = ticks[ticks.symbol.str.endswith("TMN")].copy()
    us = ticks[ticks.symbol.str.endswith("USDT")]
    m = tm.assign(base=tm.symbol.str[:-3]).merge(
        us.assign(base=us.symbol.str[:-4])[["t", "base", "fair"]].rename(columns={"fair": "g"}), on=["t", "base"], how="inner")
    u_exact = (m.fair / m.g).groupby(m.t).median()
    g1s = {b: T.sec_close(b) for b in T.SEC_BASES}
    u_live = T.u_estimate(tm[tm.run == "live_main"], g1s, LAG)
    U = pd.concat([u_exact, u_live])
    U = U[~U.index.duplicated()]
    tm["u"] = tm.t.map(U)
    tm = tm.dropna(subset=["u"])
    tm["bmid"] = tm.fair / tm.u
    tm["ex_t"] = tm.t + LAG
    tm["minute"] = (tm.ex_t // 60 * 60).astype("int64")
    tm = tm[(tm.ex_t - tm.minute) >= 55]          # last 5 s of the minute ~ the 1m close
    res = {}
    for s, x in tm.groupby("symbol"):
        b = s[:-3]
        refs = {}
        for ex in ("okx", "bybit"):
            d = pd.read_csv(T.DATA / f"{ex}_{b}_1m.csv").set_index("ts")["close"]
            refs[ex] = x.minute.map(d).to_numpy()
        med = np.nanmedian(np.vstack([x.bmid.to_numpy(), refs["okx"], refs["bybit"]]), axis=0)
        dev = pd.Series(x.bmid.to_numpy() / med - 1).dropna()
        steps = pd.Series(np.unique(np.round(np.log(x[x.run != "live_main"].bmid.to_numpy()), 5)))   # exact u only, log-price levels
        steps = np.exp(steps)
        res[s] = {"ticks_compared": int(len(dev)),
                  "binance_mid_vs_median_abs_gt_pct": {str(k): round(float((dev.abs() > k / 100).mean() * 100), 2) for k in (0.05, 0.1, 0.2)},
                  "p95_abs_pct": round(float(dev.abs().quantile(0.95) * 100), 4) if len(dev) else None,
                  "binance_mid_typical_step_pct": round(float((steps.pct_change().dropna().median()) * 100), 4) if len(steps) > 2 else None}
    (OUT / "live_mid_check.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
