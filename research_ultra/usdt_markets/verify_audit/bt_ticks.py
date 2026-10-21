"""Part 1b: the rule replayed on the live 2.5-second order-book ticks of the 5 USDT markets
(shadow runs v2..v6, the only runs that recorded USDT books).

Uses the buy_dev / sell_dev the engine itself computed live (its baseline, warm starts included), the real
bid/ask/top quantities, the engine's entry filters (spread <= 0.6%, global 60 s drop < 0.4% measured on the
recorded fair = Binance price) and the engine's exits. Also measures what a trade could be filled for at the
top of the book (ask_qty * ask, bid_qty * bid, in USDT), and how often each threshold was touched.
Also: the shadow decision logs, where the paper engine really traded USDT markets.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from common import (EXIT_SLACK, FEES_USDT, FLOOR_USDT, MAIN, MAX_GLOBAL_DROP_60S, MAX_HOLD_MIN, MAX_SPREAD, SHADOW_RUNS,
                    STOP_PCT, USDT_SYMS, save_json, shadow_ticks, stats)

F = FEES_USDT["taker"]
THRS = [0.0036, 0.0045, 0.006]
tk = shadow_ticks(USDT_SYMS)
tk["spread"] = tk.ask / tk.bid - 1
tk["ask_usdt"] = tk.ask * tk.ask_qty
tk["bid_usdt"] = tk.bid * tk.bid_qty

out = {"runs": {}, "markets": {}, "replay": {}, "shadow_log_trades": []}
for r, g in tk.groupby("run"):
    out["runs"][r] = {"start_utc": str(pd.to_datetime(g.t.min(), unit="s", utc=True)),
                      "end_utc": str(pd.to_datetime(g.t.max(), unit="s", utc=True)),
                      "minutes": round((g.t.max() - g.t.min()) / 60, 1)}
total_min = sum(v["minutes"] for v in out["runs"].values())
out["total_minutes"] = round(total_min, 1)

for s, g in tk.groupby("symbol"):
    g = g.sort_values("t")
    d = {"ticks": int(len(g)),
         "spread_pct_p25_p50_p75": [round(float(x) * 100, 3) for x in g.spread.quantile([0.25, 0.5, 0.75])],
         "share_spread_over_0.6pct": round(float((g.spread > MAX_SPREAD).mean()) * 100, 1),
         "top_ask_usdt_p25_p50_p75": [round(float(x), 2) for x in g.ask_usdt.quantile([0.25, 0.5, 0.75])],
         "top_bid_usdt_p25_p50_p75": [round(float(x), 2) for x in g.bid_usdt.quantile([0.25, 0.5, 0.75])],
         "buy_dev_pct_p1_p5_p50": [round(float(x) * 100, 3) for x in g.buy_dev.quantile([0.01, 0.05, 0.5])],
         "baseline_pct_median": round(float(g.baseline.median()) * 100, 3)}
    for thr in THRS:
        hit = g.buy_dev <= -thr
        d[f"minutes_buy_dev_below_-{thr * 100:.2f}pct"] = round(float(hit.sum()) * 2.5 / 60, 1)
        if hit.any():
            d[f"top_ask_usdt_median_when_below_-{thr * 100:.2f}pct"] = round(float(g.ask_usdt[hit].median()), 2)
    out["markets"][s] = d


def replay(g: pd.DataFrame, s: str, thr: float) -> list[dict]:
    thr = max(thr, FLOOR_USDT)
    t = g.t.to_numpy()
    bid, ask = g.bid.to_numpy(), g.ask.to_numpy()
    bd, sd, fair = g.buy_dev.to_numpy(), g.sell_dev.to_numpy(), g.fair.to_numpy()
    aq = g.ask_usdt.to_numpy()
    sp = g.spread.to_numpy()
    trades, pos, cool = [], None, -1e18
    for i in range(len(g)):
        if pos is not None:
            e, t0 = pos
            why = None
            if bid[i] <= e * (1 - STOP_PCT):
                why = "stop"
            elif sd[i] >= -EXIT_SLACK and bid[i] >= e * (1 + 2 * F + 0.0005):
                why = "target"
            elif t[i] - t0 >= MAX_HOLD_MIN * 60:
                why = "time"
            if why:
                r = bid[i] * (1 - F) * (1 - F) / e - 1
                trades.append({"symbol": s, "t": float(t0), "ret_pct": r * 100, "why": why, "minutes": (t[i] - t0) / 60,
                               "entry_top_ask_usdt": pos_depth})
                if r < 0:
                    cool = t[i] + 120 * 60
                pos = None
            continue
        if not (np.isfinite(bd[i]) and bd[i] <= -thr) or sp[i] > MAX_SPREAD or t[i] < cool:
            continue
        # global 60 s momentum from the recorded fair (= Binance price for USDT markets)
        k = np.searchsorted(t, t[i] - 60)
        if k < i and t[i] - t[k] < 90 and fair[i] / fair[k] - 1 < -MAX_GLOBAL_DROP_60S:
            continue
        pos = (ask[i], t[i])
        pos_depth = aq[i]
    if pos is not None:
        e, t0 = pos
        r = bid[-1] * (1 - F) * (1 - F) / e - 1
        trades.append({"symbol": s, "t": float(t0), "ret_pct": r * 100, "why": "end", "minutes": (t[-1] - t0) / 60,
                       "entry_top_ask_usdt": pos_depth})
    return trades


for thr in THRS:
    rows = []
    for s, g in tk.groupby("symbol"):
        rows += replay(g.sort_values("t").reset_index(drop=True), s, thr)
    df = pd.DataFrame(rows)
    out["replay"][f"{thr * 100:.2f}%"] = {"all": stats(df.ret_pct, df.why) if len(df) else {"n": 0},
                                          "per_market": {s: stats(x.ret_pct, x.why) for s, x in df.groupby("symbol")} if len(df) else {},
                                          "trades": df.assign(t=pd.to_datetime(df.t, unit="s", utc=True).astype(str)).round(4).to_dict("records") if len(df) else []}

for r in SHADOW_RUNS:
    for line in open(MAIN / "runs" / r / "decisions.jsonl", encoding="utf-8"):
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("event") in ("entry", "exit") and str(e.get("symbol", "")).endswith("USDT"):
            out["shadow_log_trades"].append({k: e.get(k) for k in ("t", "event", "symbol", "price", "spent", "buy_dev_pct", "reason", "ret_pct", "held_s", "pnl_tmn")} | {"run": r})

save_json("tick_replay.json", out)
print("runs", json.dumps(out["runs"]), "total minutes", out["total_minutes"])
for s, d in out["markets"].items():
    print(s, json.dumps(d))
for k, v in out["replay"].items():
    print("thr", k, json.dumps(v["all"]), json.dumps(v["per_market"]))
    for x in v["trades"]:
        print("   ", x)
print("shadow log", out["shadow_log_trades"])
