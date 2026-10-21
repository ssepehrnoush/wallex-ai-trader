"""Sanity check of the trade-rate input to part 2: how often does the 1-minute candle model show a cheap
moment, versus how often the live order-book ticks showed one?

Candle model (bt_fairvalue.py): ask = close x (1 + half spread). If a dislocation minute closed on a
seller hitting the bid, the real ask was a FULL spread above that close, so the model can overstate
cheap moments. Ticks see the real ask. Different time periods (candles end 2026-10-21 14:04, ticks
start 14:19), so this is a rate comparison, not a matched test.

Writes out/calib_freq.json
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from common import MAIN, OUT, TMN8, dump, load_ticks

RES = MAIN / "research"
THR = [0.003, 0.005, 0.007]
ticks = load_ticks()
spr = ticks.groupby("symbol").spread.median().to_dict()


def wallex(sym):
    p = RES / "candles" / f"{sym}_1.csv"
    if not p.exists():
        p = RES / "premium" / f"wallex_{sym}_1m.csv"
    raw = pd.read_csv(p)
    idx = pd.to_datetime(raw.ts.astype("int64"), unit="s", utc=True)
    df = pd.DataFrame({"c": raw.close.astype(float).to_numpy(), "v": raw.volume.astype(float).to_numpy()}, index=idx)
    return df[~df.index.duplicated(keep="last")].sort_index()


def binance(base):
    s = pd.read_csv(RES / "premium" / f"binance_{base}USDT_1m.csv", index_col=0, parse_dates=True)["close"]
    s.index = pd.to_datetime(s.index, utc=True)
    return s[~s.index.duplicated(keep="last")].sort_index()


u = wallex("USDTTMN").c
u = u.reindex(pd.date_range(u.index[0], u.index[-1], freq="1min", tz="UTC")).ffill()
res = {}
for s in TMN8:
    g = binance(s[:-3])
    w = wallex(s)
    df = pd.DataFrame({"c": w.c, "v": w.v}).join((g * u).rename("fair"), how="inner").dropna()
    df = df[df.v > 0]
    hours = (df.index[-1] - df.index[0]).total_seconds() / 3600
    hs = spr[s] / 2
    prem = df.c / df.fair - 1
    base = prem.rolling("180min", min_periods=30).median().shift(1)
    bd_half = df.c * (1 + hs) / df.fair - 1 - base          # the backtest's model
    bd_full = df.c * (1 + 2 * hs) / df.fair - 1 - base      # if the close was on the bid
    last2 = df.index >= df.index[-1] - pd.Timedelta(days=2)
    hod = (df.index.hour >= 14) & (df.index.hour < 18)      # same UTC hours as the tick sample
    hod_hours = 4.0 * len(set(df.index[hod].date))
    tk = ticks[ticks.symbol == s]
    tk_hours = len(tk) * 2.5 / 3600
    r = {"candle_hours": round(hours, 1), "tick_hours": round(tk_hours, 2)}
    for x in THR:
        k = f"{x * 100:.1f}%"
        r[k] = {"candle_min_per_hour_half_spread_model": round(float((bd_half <= -x).sum()) / hours, 3),
                "candle_min_per_hour_full_spread_model": round(float((bd_full <= -x).sum()) / hours, 3),
                "candle_min_per_hour_last2d": round(float((bd_half[last2] <= -x).sum()) / (48.0), 3),
                "candle_min_per_hour_14_18utc": round(float((bd_half[hod] <= -x).sum()) / hod_hours, 3),
                "tick_min_per_hour": round(float((tk.buy_dev <= -x).sum()) * 2.5 / 60 / tk_hours, 3) if tk_hours else None,
                "expected_candle_minutes_in_tick_period": round(float((bd_half <= -x).sum()) / hours * tk_hours, 2),
                "observed_tick_minutes": round(float((tk.buy_dev <= -x).sum()) * 2.5 / 60, 2)}
    res[s] = r
tot = {}
for x in THR:
    k = f"{x * 100:.1f}%"
    tot[k] = {"expected_candle_minutes_in_tick_period": round(sum(res[s][k]["expected_candle_minutes_in_tick_period"] for s in TMN8), 2),
              "observed_tick_minutes": round(sum(res[s][k]["observed_tick_minutes"] for s in TMN8), 2)}
res["_total"] = tot
dump(res, "calib_freq.json")
pd.set_option("display.width", 250)
for s in TMN8:
    print(s, res[s]["candle_hours"], res[s]["tick_hours"], {k: (v["candle_min_per_hour_half_spread_model"], v["candle_min_per_hour_full_spread_model"],
                                                               v["candle_min_per_hour_last2d"], v["candle_min_per_hour_14_18utc"],
                                                               v["tick_min_per_hour"]) for k, v in res[s].items() if k.endswith("%")})
print(tot)
