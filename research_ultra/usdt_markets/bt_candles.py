"""Part 1a: the live fair-value rule on the 5 Wallex USDT markets, 7 days of 1-minute candles.

Same approach as MAIN/scripts/bt_fairvalue.py, made closer to the live engine:
  - fair = Binance 1m close (USDT markets need no USDT/TMN leg)
  - executable prices modelled from the close and the median live spread of that market
    measured in the shadow runs v2..v6 (ticks.csv)
  - only minutes in which Wallex actually traded (volume > 0)
  - baseline = rolling 180-min median premium, shift(1) (known before the minute), min 30 samples
  - entry: buy_dev <= -max(thr, 0.36%), modelled spread <= 0.6%, Binance 1-min drop not worse than -0.4%
  - exits exactly as the engine: stop (bid <= e*(1-0.9%)), target (sell_dev >= -0.05% AND
    bid >= e*(1+2*taker+0.05%)), time (>= 25 min); taker fee on both legs
  - 120-min cooldown on a market after a losing trade (risk.py)
Two execution models:
  mid  : ask = close*(1+half spread), bid = close*(1-half spread)   (= existing backtest)
  cons : ask = close*(1+full spread) (a cheap print is usually a seller hitting the bid, so the real ask
         was a full spread above it), bid = close*(1-half spread), and stop/time exits pay 0.10% extra.
Walk-forward: thresholds 0.36 / 0.45 / 0.60 %; choose on days 1-4, report days 5-7.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from common import (COOLDOWN_AFTER_LOSS_MIN, EXIT_SLACK, FEES_USDT, FLOOR_USDT, MAX_GLOBAL_DROP_60S, MAX_HOLD_MIN,
                    MAX_SPREAD, OUT, STOP_PCT, USDT_SYMS, binance_1m, save_json, shadow_ticks, stats, wallex_1m)

THRS = [0.0036, 0.0045, 0.006]
F = FEES_USDT["taker"]

tk = shadow_ticks(USDT_SYMS)
spread_med = ((tk.ask / tk.bid - 1).groupby(tk.symbol).median()).to_dict()


def prepare(sym: str, model: str):
    base = sym[:-4]
    g = binance_1m(base)
    w = wallex_1m(sym)
    mom = (g / g.shift(1) - 1).rename("mom")
    df = pd.DataFrame({"c": w.close, "v": w.volume}).join(g.rename("fair"), how="inner").join(mom, how="left").dropna(subset=["c", "fair"])
    df = df[df.v > 0].copy()
    sp = spread_med[sym]
    hs = sp / 2
    if model == "mid":
        df["ask"], df["bid"] = df.c * (1 + hs), df.c * (1 - hs)
        extra = 0.0
    else:
        df["ask"], df["bid"] = df.c * (1 + sp), df.c * (1 - hs)
        extra = 0.001
    prem = df.c / df.fair - 1
    b = prem.rolling("180min", min_periods=30).median().shift(1)
    df["buy_dev"] = df.ask / df.fair - 1 - b
    df["sell_dev"] = df.bid / df.fair - 1 - b
    df["usdt_vol"] = df.v * df.c
    return df, sp, extra


def run(df: pd.DataFrame, sym: str, thr: float, sp: float, extra: float) -> list[dict]:
    thr = max(thr, FLOOR_USDT)
    ask, bid = df.ask.to_numpy(), df.bid.to_numpy()
    bd, sd, mom = df.buy_dev.to_numpy(), df.sell_dev.to_numpy(), df.mom.to_numpy()
    vol = df.usdt_vol.to_numpy()
    idx = df.index
    tmin = (idx.asi8 // 10**9) / 60.0
    trades, i, cool_until = [], 0, -1e18
    n = len(df)
    while i < n - 1:
        ok = (np.isfinite(bd[i]) and bd[i] <= -thr and sp <= MAX_SPREAD and tmin[i] >= cool_until
              and not (np.isfinite(mom[i]) and mom[i] < -MAX_GLOBAL_DROP_60S))
        if not ok:
            i += 1
            continue
        e = ask[i]
        j, why = i + 1, "end"
        while j < n:
            if bid[j] <= e * (1 - STOP_PCT):
                why = "stop"
                break
            if sd[j] >= -EXIT_SLACK and bid[j] >= e * (1 + 2 * F + 0.0005):
                why = "target"
                break
            if tmin[j] - tmin[i] >= MAX_HOLD_MIN:
                why = "time"
                break
            j += 1
        j = min(j, n - 1)
        px = bid[j] * (1 - (extra if why in ("stop", "time", "end") else 0.0))
        r = px * (1 - F) * (1 - F) / e - 1
        trades.append({"symbol": sym, "t": idx[i], "ret_pct": r * 100, "why": why, "minutes": tmin[j] - tmin[i],
                       "entry_dev_pct": bd[i] * 100, "entry_min_usdt_vol": vol[i]})
        if r < 0:
            cool_until = tmin[j] + COOLDOWN_AFTER_LOSS_MIN
        i = j + 1
    return trades


res = {"spread_median_pct_from_shadow": {k: round(v * 100, 4) for k, v in spread_med.items()}, "models": {}}
all_rows = []
for model in ("mid", "cons"):
    data = {s: prepare(s, model) for s in USDT_SYMS}
    t0 = min(d[0].index[0] for d in data.values())
    t_end = max(d[0].index[-1] for d in data.values())
    split = t0 + pd.Timedelta(days=4)
    days_test = (t_end - split).total_seconds() / 86400
    out = {"data_start": str(t0), "split": str(split), "data_end": str(t_end), "test_days": round(days_test, 2),
           "markets": {}, "by_threshold": {}}
    trades_by = {}
    for thr in THRS:
        rows = []
        for s, (df, sp, extra) in data.items():
            tr = run(df, s, thr, sp, extra)
            for x in tr:
                x["thr"] = thr
                x["model"] = model
                x["period"] = "train" if x["t"] < split else "test"
            rows += tr
        trades_by[thr] = pd.DataFrame(rows)
        all_rows += rows
    for s, (df, sp, extra) in data.items():
        out["markets"][s] = {"traded_minutes": int(len(df)), "share_of_minutes_traded_pct": round(len(df) / ((t_end - t0).total_seconds() / 60) * 100, 1),
                             "median_usdt_volume_per_traded_minute": round(float(df.usdt_vol.median()), 2),
                             "spread_pct": round(sp * 100, 4)}
    for thr, tdf in trades_by.items():
        k = f"{thr * 100:.2f}%"
        o = {}
        for per in ("train", "test", "all"):
            d = tdf if per == "all" else tdf[tdf.period == per]
            o[per] = stats(d.ret_pct, d.why) if len(d) else {"n": 0}
            o[per]["per_market"] = {s: stats(g.ret_pct, g.why) for s, g in d.groupby("symbol")} if len(d) else {}
        out["by_threshold"][k] = o
    # walk-forward 1: one threshold for all 5 markets, chosen by train sum
    tr_sum = {thr: trades_by[thr][trades_by[thr].period == "train"].ret_pct.sum() for thr in THRS}
    best = max(tr_sum, key=tr_sum.get)
    test = trades_by[best][trades_by[best].period == "test"]
    out["walk_forward_global"] = {"train_sum_pct_by_thr": {f"{k * 100:.2f}%": round(v, 3) for k, v in tr_sum.items()},
                                  "chosen_thr_pct": best * 100, "test": stats(test.ret_pct, test.why),
                                  "test_per_market": {s: stats(g.ret_pct, g.why) for s, g in test.groupby("symbol")},
                                  "test_trades_per_day": round(len(test) / days_test, 2)}
    # walk-forward 2: per market threshold (or disable if every train sum <= 0)
    chosen, tests = {}, []
    for s in USDT_SYMS:
        sums = {thr: trades_by[thr][(trades_by[thr].period == "train") & (trades_by[thr].symbol == s)].ret_pct.sum() for thr in THRS}
        b = max(sums, key=sums.get)
        if sums[b] <= 0:
            chosen[s] = None
            continue
        chosen[s] = b * 100
        tests.append(trades_by[b][(trades_by[b].period == "test") & (trades_by[b].symbol == s)])
    tt = pd.concat(tests) if tests else pd.DataFrame(columns=["ret_pct", "why", "symbol"])
    out["walk_forward_per_market"] = {"chosen_thr_pct": chosen, "test": stats(tt.ret_pct, tt.why),
                                      "test_per_market": {s: stats(g.ret_pct, g.why) for s, g in tt.groupby("symbol")},
                                      "test_trades_per_day": round(len(tt) / days_test, 2)}
    # capacity: what was traded in the entry minute
    t36 = trades_by[0.0036]
    out["entry_minute_usdt_volume_median"] = {s: round(float(g.entry_min_usdt_vol.median()), 2) for s, g in t36.groupby("symbol")}
    res["models"][model] = out

pd.DataFrame(all_rows).to_csv(OUT / "candle_trades.csv", index=False)
save_json("candle_backtest.json", res)
import json
for m, o in res["models"].items():
    print("=====", m, o["data_start"], "split", o["split"], "end", o["data_end"], "test days", o["test_days"])
    for k, v in o["by_threshold"].items():
        print(" thr", k, "| train", {kk: v["train"].get(kk) for kk in ("n", "mean_pct", "sum_pct", "win_pct")},
              "| test", {kk: v["test"].get(kk) for kk in ("n", "mean_pct", "sum_pct", "win_pct", "exits")})
        for s, pm in v["all"]["per_market"].items():
            print("    all", s, {kk: pm.get(kk) for kk in ("n", "mean_pct", "sum_pct", "win_pct", "exits")})
    print(" WF global", json.dumps(o["walk_forward_global"], default=str))
    print(" WF per-market", json.dumps(o["walk_forward_per_market"], default=str))
    print(" markets", o["markets"])
    print(" entry-minute USDT vol", o["entry_minute_usdt_volume_median"])
