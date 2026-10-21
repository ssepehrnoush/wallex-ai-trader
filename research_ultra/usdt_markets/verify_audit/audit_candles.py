"""Adversarial audit of bt_candles.py (USDT markets, 7-day 1-minute candles).

Re-implements the author's simulator with switches, to test:
  1. timestamp alignment Wallex vs Binance (lead/lag correlation of 1-minute returns)
  2. intra-minute look-ahead: the Wallex close (last trade, any second of the minute) is compared with the
     Binance close at the END of the same minute. Variant 'lagfair' uses the Binance close of the previous
     minute (known before any Wallex trade in minute i).
  3. latency: variant 'nextfill' enters at the NEXT traded Wallex minute (only if within 2 minutes) instead of the
     signal minute's own close.
  4. split robustness: walk-forward with 3/4/5 train days, and the reverse direction.
  5. bootstrap CI of the test mean.
Writes only to verify_audit/out.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from common import (COOLDOWN_AFTER_LOSS_MIN, EXIT_SLACK, FEES_USDT, FLOOR_USDT, MAX_GLOBAL_DROP_60S, MAX_HOLD_MIN,
                    STOP_PCT, USDT_SYMS, binance_1m, save_json, shadow_ticks, stats, wallex_1m)

F = FEES_USDT["taker"]
THRS = [0.0036, 0.0045, 0.006]
tk = shadow_ticks(USDT_SYMS)
spread_med = ((tk.ask / tk.bid - 1).groupby(tk.symbol).median()).to_dict()
rng = np.random.default_rng(7)
out = {}

# ---------- 1. alignment ----------------------------------------------------------------
align = {}
for s in ("BTCUSDT", "ETHUSDT"):
    w = wallex_1m(s)
    g = binance_1m(s[:-4])
    wt = w[w.volume > 0].close
    grid = pd.date_range(max(wt.index[0], g.index[0]), min(wt.index[-1], g.index[-1]), freq="1min", tz="UTC")
    wg = wt.reindex(grid).ffill()
    gg = g.reindex(grid).ffill()
    rw = np.log(wg).diff()
    rg = np.log(gg).diff()
    # corr(Wallex return at t, Binance return at t+k): k>0 means Wallex LEADS Binance (suspicious)
    align[s] = {f"wallex_t_vs_binance_t{k:+d}": round(float(rw.corr(rg.shift(-k))), 3) for k in range(-3, 4)}
out["alignment_corr"] = align


def prepare(sym, model, fairmode):
    g = binance_1m(sym[:-4])
    w = wallex_1m(sym)
    g = g.reindex(pd.date_range(g.index[0], g.index[-1], freq="1min", tz="UTC")).ffill()
    mom = (g / g.shift(1) - 1).rename("mom")
    fair = g.shift(1) if fairmode == "lagfair" else g
    df = pd.DataFrame({"c": w.close, "v": w.volume}).join(fair.rename("fair"), how="inner").join(mom, how="left").dropna(subset=["c", "fair"])
    df = df[df.v > 0].copy()
    sp = spread_med[sym]
    hs = sp / 2
    if model == "mid":
        df["ask"], df["bid"], extra = df.c * (1 + hs), df.c * (1 - hs), 0.0
    else:
        df["ask"], df["bid"], extra = df.c * (1 + sp), df.c * (1 - hs), 0.001
    prem = df.c / df.fair - 1
    b = prem.rolling("180min", min_periods=30).median().shift(1)
    df["buy_dev"] = df.ask / df.fair - 1 - b
    df["sell_dev"] = df.bid / df.fair - 1 - b
    return df, extra


def run(df, sym, thr, extra, nextfill=False):
    thr = max(thr, FLOOR_USDT)
    ask, bid = df.ask.to_numpy(), df.bid.to_numpy()
    bd, sd, mom = df.buy_dev.to_numpy(), df.sell_dev.to_numpy(), df.mom.to_numpy()
    idx = df.index
    tmin = (idx.asi8 // 10**9) / 60.0
    trades, i, cool = [], 0, -1e18
    n = len(df)
    while i < n - 1:
        ok = (np.isfinite(bd[i]) and bd[i] <= -thr and tmin[i] >= cool
              and not (np.isfinite(mom[i]) and mom[i] < -MAX_GLOBAL_DROP_60S))
        if not ok:
            i += 1
            continue
        k = i
        if nextfill:
            k = i + 1
            if tmin[k] - tmin[i] > 2:      # no executable print soon after: skip the signal
                i += 1
                continue
        e = ask[k]
        j, why = k + 1, "end"
        while j < n:
            if bid[j] <= e * (1 - STOP_PCT):
                why = "stop"; break
            if sd[j] >= -EXIT_SLACK and bid[j] >= e * (1 + 2 * F + 0.0005):
                why = "target"; break
            if tmin[j] - tmin[k] >= MAX_HOLD_MIN:
                why = "time"; break
            j += 1
        j = min(j, n - 1)
        px = bid[j] * (1 - (extra if why in ("stop", "time", "end") else 0.0))
        r = px * (1 - F) ** 2 / e - 1
        trades.append({"symbol": sym, "t": idx[i], "ret_pct": r * 100, "why": why, "hold_min": tmin[j] - tmin[k],
                       "gap_to_exit_print_min": (tmin[j] - tmin[j - 1]) if j > k + 1 else tmin[j] - tmin[k]})
        if r < 0:
            cool = tmin[j] + COOLDOWN_AFTER_LOSS_MIN
        i = j + 1
    return trades


def walk(trades_by, split, t_end, direction="fwd"):
    """choose ONE threshold on the train side by sum, report the other side"""
    def side(df, train):
        if direction == "fwd":
            return df[df.t < split] if train else df[df.t >= split]
        return df[df.t >= split] if train else df[df.t < split]
    tr_sum = {thr: side(d, True).ret_pct.sum() if len(d) else 0.0 for thr, d in trades_by.items()}
    best = max(tr_sum, key=tr_sum.get)
    te = side(trades_by[best], False) if len(trades_by[best]) else pd.DataFrame(columns=["ret_pct", "why", "symbol"])
    return best, te


variants = {}
for model in ("mid", "cons"):
    for fairmode in ("same", "lagfair"):
        for nextfill in (False, True):
            key = f"{model}|{fairmode}|{'nextfill' if nextfill else 'samefill'}"
            data = {s: prepare(s, model, fairmode) for s in USDT_SYMS}
            t0 = min(d[0].index[0] for d in data.values())
            t_end = max(d[0].index[-1] for d in data.values())
            trades_by = {}
            for thr in THRS:
                rows = []
                for s, (df, extra) in data.items():
                    rows += run(df, s, thr, extra, nextfill)
                trades_by[thr] = pd.DataFrame(rows)
            v = {"all_7d": {f"{thr * 100:.2f}%": stats(d.ret_pct, d.why) if len(d) else {"n": 0} for thr, d in trades_by.items()}}
            v["all_7d_per_market_0.36"] = {s: stats(g.ret_pct, g.why) for s, g in trades_by[0.0036].groupby("symbol")} if len(trades_by[0.0036]) else {}
            for days in (3, 4, 5):
                split = t0 + pd.Timedelta(days=days)
                for direction in ("fwd", "rev"):
                    best, te = walk(trades_by, split, t_end, direction)
                    test_days = (t_end - split).total_seconds() / 86400 if direction == "fwd" else days
                    st = stats(te.ret_pct, te.why) if len(te) else {"n": 0}
                    st["chosen_thr"] = best * 100
                    st["test_days"] = round(test_days, 2)
                    st["trades_per_day"] = round(len(te) / test_days, 2)
                    if len(te) >= 3:
                        bs = [rng.choice(te.ret_pct.to_numpy(), len(te)).mean() for _ in range(4000)]
                        st["boot90_mean_pct"] = [round(float(np.quantile(bs, 0.05)), 3), round(float(np.quantile(bs, 0.95)), 3)]
                    v[f"wf_{direction}_{days}d"] = st
            variants[key] = v
            if key in ("mid|same|samefill", "mid|lagfair|samefill", "mid|same|nextfill", "mid|lagfair|nextfill"):
                trades_by[0.0036].assign(t=lambda x: x.t.astype(str)).to_csv(
                    f"out/trades_{key.replace('|', '_')}_036.csv", index=False)
out["variants"] = variants
save_json("audit_candles.json", out)

print("ALIGNMENT", json.dumps(align))
for k, v in variants.items():
    a = v["all_7d"]
    print(f"\n== {k}")
    print("  7d all:", {t: (x.get("n"), x.get("mean_pct"), x.get("sum_pct")) for t, x in a.items()})
    print("  7d per mkt @0.36:", {s: (x["n"], x["mean_pct"]) for s, x in v["all_7d_per_market_0.36"].items()})
    for kk in ("wf_fwd_4d", "wf_fwd_3d", "wf_fwd_5d", "wf_rev_4d", "wf_rev_3d"):
        x = v[kk]
        print(f"  {kk}: thr {x['chosen_thr']:.2f} n {x['n']} mean {x.get('mean_pct')} sum {x.get('sum_pct')} "
              f"win {x.get('win_pct')} tpd {x['trades_per_day']} exits {x.get('exits')} boot90 {x.get('boot90_mean_pct')}")
