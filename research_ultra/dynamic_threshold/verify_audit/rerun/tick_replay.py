"""Replay the threshold variants on the recorded live order-book ticks (2.5 s), small sample.

Uses buy_dev / sell_dev exactly as the engine logged them (shadow_v2..v6 and live_main), real bid/ask.
Entry: buy_dev <= -threshold, spread <= 0.6 %, fair-value 60 s move >= -0.4 % (proxy for the global
60 s drop filter: fair = Binance mid x USDTTMN mid), market baseline ready (>= 240 ticks of that
market in the previous 180 min). Exits as the engine: stop / target (sell_dev >= -0.05 % and bid >=
entry*1.0065) / time (25 min, ARB 15). One position per market, 120 min cooldown after a loss.
Thresholds use the choices made on candle days 1-4 (results_main.json).
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import dt_data as D

OUT = D.HERE / "out"
R = json.loads((OUT / "results_main.json").read_text(encoding="utf-8"))
V1 = {s: v / 100 for s, v in R["v1_choice_pct"].items()}
K = R["v2_k_global"]
KPM = R["v2b_k_per_market"]
F = D.TAKER

t = D.load_ticks().copy()
t["spread"] = t.ask / t.bid - 1
frames = {}
for s, g in t.groupby("symbol"):
    g = g.sort_values("t").reset_index(drop=True)
    ts = pd.to_datetime(g.t, unit="s", utc=True)
    gi = g.set_index(ts)
    g["vol3h"] = gi.buy_dev.rolling("180min", min_periods=240).std().shift(1).to_numpy()
    g["n3h"] = gi.buy_dev.rolling("180min").count().shift(1).fillna(0).to_numpy()
    prev = pd.merge_asof(pd.DataFrame({"tt": g.t - 60}), g[["t", "fair"]].rename(columns={"t": "tt", "fair": "f60"}),
                         on="tt", direction="backward")
    g["mom60"] = (g.fair / prev.f60.to_numpy() - 1).fillna(0)
    frames[s] = g


def replay(thr_fn) -> pd.DataFrame:
    trades = []
    for s, g in frames.items():
        pos, cool = None, -1e18
        T, bid, ask, bd, sd = g.t.to_numpy(), g.bid.to_numpy(), g.ask.to_numpy(), g.buy_dev.to_numpy(), g.sell_dev.to_numpy()
        sp, mom, n3h = g.spread.to_numpy(), g.mom60.to_numpy(), g.n3h.to_numpy()
        th_all = thr_fn(s, g)
        for i in range(len(g)):
            if pos:
                e, t0, b0, th0 = pos
                why = ("stop" if bid[i] <= e * (1 - D.STOP) else
                       "target" if sd[i] >= -D.SLACK and bid[i] >= e * D.BE else
                       "time" if T[i] - t0 >= D.HOLD[s] * 60 else None)
                if why:
                    r = bid[i] * (1 - F) ** 2 / e - 1
                    trades.append((s, t0, T[i], r * 100, why, b0 * 100, th0 * 100))
                    pos = None
                    if r < 0:
                        cool = T[i] + D.COOLDOWN_MIN * 60
                continue
            if n3h[i] < 240 or cool > T[i]:
                continue
            th = th_all[i]
            if bd[i] <= -th and sp[i] <= D.MAX_SPREAD and mom[i] >= -D.MAX_DROP_60S:
                pos = (ask[i], T[i], bd[i], th)
        if pos:
            e, t0, b0, th0 = pos
            trades.append((s, t0, T[-1], (bid[-1] * (1 - F) ** 2 / e - 1) * 100, "open_at_end", b0 * 100, th0 * 100))
    return pd.DataFrame(trades, columns=["symbol", "t_open", "t_close", "ret_pct", "why", "entry_dev_pct", "thr_pct"])


VAR = {
    "V4_fixed_0.7": lambda s, g: np.full(len(g), D.FLOOR),
    "V1_per_market_fixed": lambda s, g: np.full(len(g), V1[s]),
    "V2_vol_scaled_global_k": lambda s, g: np.fmax(D.FLOOR, K * g.vol3h.to_numpy()),
    "V2b_vol_scaled_per_market_k": lambda s, g: np.fmax(D.FLOOR, KPM[s] * g.vol3h.to_numpy()),
    "V3_spread_aware": lambda s, g: D.FLOOR + g.spread.to_numpy() / 2,
}

res = {"tick_rows": int(len(t)), "hours_by_symbol": {}, "variants": {}, "buy_dev_dist": {}}
for s, g in frames.items():
    hrs = len(g) * 2.5 / 3600
    bd = g.buy_dev
    # "episodes" = separate dips: consecutive ticks below the level count once
    ep = {}
    for lv in (0.005, 0.006, 0.007, 0.008, 0.009, 0.010):
        below = (bd <= -lv).to_numpy()
        ep[f"{lv*100:.1f}"] = int(np.sum(below[1:] & ~below[:-1]) + int(below[0]))
    res["hours_by_symbol"][s] = round(hrs, 2)
    res["buy_dev_dist"][s] = {"min_pct": round(bd.min() * 100, 3), "p1_pct": round(bd.quantile(0.01) * 100, 3),
                              "median_spread_pct": round(g.spread.median() * 100, 3),
                              "median_vol3h_pct": round(float(np.nanmedian(g.vol3h)) * 100, 3) if g.vol3h.notna().any() else None,
                              "dip_episodes_below_pct": ep}
for name, fn in VAR.items():
    tr = replay(fn)
    tr.to_csv(OUT / f"ticks_trades_{name}.csv", index=False)
    res["variants"][name] = {"trades": len(tr), "sum_net_pct": round(tr.ret_pct.sum(), 3) if len(tr) else 0.0,
                             "mean_net_pct": round(tr.ret_pct.mean(), 3) if len(tr) else None,
                             "win_pct": round((tr.ret_pct > 0).mean() * 100, 1) if len(tr) else None,
                             "exits": tr.why.value_counts().to_dict(),
                             "by_market": {s: [int((tr.symbol == s).sum()), round(tr[tr.symbol == s].ret_pct.sum(), 3)] for s in D.SYMS}}

(OUT / "results_ticks.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
print("hours per symbol:", res["hours_by_symbol"])
for s, v in res["buy_dev_dist"].items():
    print(s, v)
for k, v in res["variants"].items():
    print(k, v)
