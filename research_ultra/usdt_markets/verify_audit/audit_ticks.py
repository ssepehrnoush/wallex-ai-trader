"""Adversarial audit of bt_ticks.py (shadow v2..v6 2.5 s ticks, USDT markets).

  1. the author's replay reproduced, plus: entry at the NEXT tick's ask (about 2.5 s order latency), depth-capped
     fills (engine buys at most the top-ask quantity) and the live exit crossing 0.3% below the bid on stop/time.
  2. signal context: spread, top-ask USDT, Binance move over the previous 60/180 s, other markets signalling at the
     same time (are the episodes independent?).
  3. how representative the 104-minute shadow window was: Binance BTC 1-minute absolute moves in that window vs the
     7-day distribution.
  4. sampling uncertainty of the tick scenario C in toman.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from common import (EXIT_SLACK, FEES_USDT, FLOOR_USDT, MAX_GLOBAL_DROP_60S, MAX_HOLD_MIN, MAX_SPREAD, STOP_PCT,
                    USDT_SYMS, binance_1m, save_json, shadow_ticks, stats)

F = FEES_USDT["taker"]
SIZE_USDT = 3.13           # engine size at size_factor 0.4 (827,586 TMN / 264,602)
tk = shadow_ticks(USDT_SYMS)
tk["spread"] = tk.ask / tk.bid - 1
tk["ask_usdt"] = tk.ask * tk.ask_qty


def replay(g, s, thr, latency_ticks=0, cross=0.0):
    thr = max(thr, FLOOR_USDT)
    t, bid, ask = g.t.to_numpy(), g.bid.to_numpy(), g.ask.to_numpy()
    bd, sd, fair, sp, aq = g.buy_dev.to_numpy(), g.sell_dev.to_numpy(), g.fair.to_numpy(), g.spread.to_numpy(), g.ask_usdt.to_numpy()
    trades, pos, cool = [], None, -1e18
    i = 0
    n = len(g)
    while i < n:
        if pos is not None:
            e, t0, fill, ctx = pos
            why = None
            if bid[i] <= e * (1 - STOP_PCT):
                why = "stop"
            elif sd[i] >= -EXIT_SLACK and bid[i] >= e * (1 + 2 * F + 0.0005):
                why = "target"
            elif t[i] - t0 >= MAX_HOLD_MIN * 60:
                why = "time"
            if why:
                px = bid[i] * (1 - (cross if why in ("stop", "time") else 0.0))
                r = px * (1 - F) ** 2 / e - 1
                trades.append({"symbol": s, "t": float(t0), "ret_pct": r * 100, "why": why, "minutes": (t[i] - t0) / 60,
                               "fill_usdt": fill} | ctx)
                if r < 0:
                    cool = t[i] + 120 * 60
                pos = None
            i += 1
            continue
        if not (np.isfinite(bd[i]) and bd[i] <= -thr) or sp[i] > MAX_SPREAD or t[i] < cool:
            i += 1
            continue
        k = np.searchsorted(t, t[i] - 60)
        if k < i and t[i] - t[k] < 90 and fair[i] / fair[k] - 1 < -MAX_GLOBAL_DROP_60S:
            i += 1
            continue
        k3 = np.searchsorted(t, t[i] - 180)
        ctx = {"sig_buy_dev_pct": round(bd[i] * 100, 3), "sig_spread_pct": round(sp[i] * 100, 3),
               "sig_top_ask_usdt": round(aq[i], 2),
               "binance_60s_pct": round((fair[i] / fair[k] - 1) * 100, 3) if k < i else None,
               "binance_180s_pct": round((fair[i] / fair[k3] - 1) * 100, 3) if k3 < i else None}
        j = i + latency_ticks
        if j >= n or t[j] - t[i] > 10:
            i += 1
            continue
        ctx["fill_buy_dev_pct"] = round(bd[j] * 100, 3)
        fill = min(SIZE_USDT, aq[j])
        pos = (ask[j], t[j], fill, ctx)
        i = j + 1
    if pos is not None:
        e, t0, fill, ctx = pos
        r = bid[-1] * (1 - F) ** 2 / e - 1
        trades.append({"symbol": s, "t": float(t0), "ret_pct": r * 100, "why": "end", "minutes": (t[-1] - t0) / 60, "fill_usdt": fill} | ctx)
    return trades


out = {"replays": {}}
for thr in (0.0036, 0.0045, 0.006):
    for lat in (0, 1, 2):
        for cross in (0.0, 0.003):
            rows = []
            for s, g in tk.groupby("symbol"):
                rows += replay(g.sort_values("t").reset_index(drop=True), s, thr, lat, cross)
            df = pd.DataFrame(rows)
            key = f"thr{thr * 100:.2f}|lat{lat}|cross{cross * 100:.1f}"
            o = stats(df.ret_pct, df.why) if len(df) else {"n": 0}
            if len(df):
                o["depth_weighted_mean_pct"] = round(float((df.ret_pct * df.fill_usdt).sum() / df.fill_usdt.sum()), 3)
                o["sum_usdt_pnl"] = round(float((df.ret_pct / 100 * df.fill_usdt).sum()), 4)
                o["trades"] = df.assign(t=pd.to_datetime(df.t, unit="s", utc=True).dt.strftime("%H:%M:%S")).round(3).to_dict("records")
            out["replays"][key] = o

# --- signal clustering: every tick where buy_dev <= -0.36% and spread <= 0.6%
hit = tk[(tk.buy_dev <= -FLOOR_USDT) & (tk.spread <= MAX_SPREAD)].copy()
hit["ts"] = pd.to_datetime(hit.t, unit="s", utc=True)
out["signal_ticks_by_minute"] = hit.groupby(hit.ts.dt.floor("1min")).symbol.apply(lambda x: sorted(set(x))).astype(str).to_dict()
out["signal_ticks_by_minute"] = {str(k): v for k, v in out["signal_ticks_by_minute"].items()}

# --- representativeness of the shadow window: Binance BTC 1-minute |return|
g = binance_1m("BTC")
r = (g / g.shift(1) - 1).abs() * 100
w0, w1 = pd.Timestamp("2026-10-21 14:19", tz="UTC"), pd.Timestamp("2026-10-21 16:06", tz="UTC")
win = r[(r.index >= w0) & (r.index <= w1)]
ref = r[(r.index >= pd.Timestamp("2026-10-14 17:41", tz="UTC")) & (r.index < w0)]
r5 = (g / g.shift(5) - 1) * 100
out["binance_btc_activity"] = {
    "shadow_window_mean_abs_1m_pct": round(float(win.mean()), 4), "7d_mean_abs_1m_pct": round(float(ref.mean()), 4),
    "shadow_window_share_abs_1m_gt_0.15pct": round(float((win > 0.15).mean()) * 100, 2),
    "7d_share_abs_1m_gt_0.15pct": round(float((ref > 0.15).mean()) * 100, 2),
    "shadow_window_max_5m_up_pct": round(float(r5[(r5.index >= w0) & (r5.index <= w1)].max()), 3),
    "7d_5m_up_gt_0.3pct_events_per_day": round(float(((r5 > 0.3) & ~(r5.shift(1) > 0.3)).loc[ref.index].sum()) / ((w0 - pd.Timestamp("2026-10-14 17:41", tz="UTC")).total_seconds() / 86400), 2),
    "shadow_window_5m_up_gt_0.3pct_events": int(((r5 > 0.3) & ~(r5.shift(1) > 0.3)).loc[win.index].sum()),
    "shadow_window_hours": round((w1 - w0).total_seconds() / 3600, 2),
}
save_json("audit_ticks.json", out)
for k, v in out["replays"].items():
    print(k, {kk: v.get(kk) for kk in ("n", "mean_pct", "sum_pct", "win_pct", "se_mean_pct", "depth_weighted_mean_pct", "sum_usdt_pnl", "exits")})
print()
for x in out["replays"]["thr0.36|lat0|cross0.0"].get("trades", []):
    print("  lat0", x)
for x in out["replays"]["thr0.36|lat1|cross0.3"].get("trades", []):
    print("  lat1+cross", x)
print("signal minutes:", json.dumps(out["signal_ticks_by_minute"]))
print("binance activity:", json.dumps(out["binance_btc_activity"]))
