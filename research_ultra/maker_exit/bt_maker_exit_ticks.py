"""Maker exit vs taker exit on the recorded live order-book ticks (2.5 s), TMN markets only.

Runs: shadow_v2..v6 (about 14:20-16:05 UTC) + live_main (16:45 UTC to the snapshot). buy_dev / sell_dev /
fair / baseline are the values the engine itself computed live. Read-only, partial last line tolerated.

Entries (engine rule): buy_dev <= -threshold, spread <= 0.6%, fair (proxy for global price) not down > 0.4% in
60 s, one position per market. threshold 0.7% is the live rule; 0.5/0.4/0.3% are run ONLY to exercise the exit
mechanics (they are below the fee floor and are not a proposal).
Exits, checked every tick like the engine: stop bid <= entry x 0.991, time held >= 25 min (ARB 15), taker exit
at the bid. Arms: cur (taker target sell_dev >= -0.05% and bid >= entry x 1.0065), a/b/c/aT/bT as in the candle
script; P re-evaluated every 60 s from the latest fair/baseline, floored at entry x 1.006.
Fill rules for the resting ask at P:
  conservative : a later tick has bid >= P  (someone bids P: the only rule a top-of-book feed can prove)
  upper bound  : a later tick has ask > P   (all asks at or below P are gone: filled or pulled; NOT proof)
A position open when its segment ends (data gap > 60 s or end of file) closes "end" at the last bid.

Also: calibration of the candle model. For every tick where sell_dev first reaches -0.05% after having been
<= -0.3% in the previous 10 min (a taker-target style crossing), compare the bid at that crossing tick with the
bid at the last tick of the same calendar minute (what a 1-minute-close backtest sells at).

  python research_ultra/maker_exit/bt_maker_exit_ticks.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bt_maker_exit_candles import (BE_MAKER, BE_TAKER, HOLD, HOLD_DEFAULT, MAKER, MAX_DROP, MAX_SPREAD, SLACK,  # noqa: E402
                                   STOP, SYMS, TAKER, TICK_RUNS, read_ticks)

OUT = Path(__file__).resolve().parent
ARMS = ["cur", "a", "b", "c", "aT", "bT"]
THRESHOLDS = [0.007, 0.005, 0.004, 0.003]


def load() -> pd.DataFrame:
    t = pd.concat([read_ticks(r) for r in TICK_RUNS], ignore_index=True)
    t = t[t.symbol.isin(SYMS)].copy()
    for c in ("t", "bid", "ask", "fair", "baseline", "buy_dev", "sell_dev"):
        t[c] = pd.to_numeric(t[c], errors="coerce")
    t = t.dropna(subset=["t", "bid", "ask", "fair", "baseline", "buy_dev", "sell_dev"])
    return t.sort_values(["symbol", "t"]).reset_index(drop=True)


def segments(x: pd.DataFrame):
    gap = x.t.diff().fillna(0) > 60
    seg = gap.cumsum()
    for _, g in x.groupby(seg):
        yield g.reset_index(drop=True)


def price_for(arm, e, fair, base, hs):
    be = e * BE_MAKER
    mid = fair * (1 + base)
    if arm in ("a", "aT"):
        return max(be, mid)
    if arm in ("b", "bT"):
        return max(be, mid * (1 + hs))
    if arm == "c":
        return be
    raise ValueError(arm)


def sim_exit(arm, g, i, e, hold, rule):
    t, bid, ask, fair, base, sd = (g.t.values, g.bid.values, g.ask.values, g.fair.values, g.baseline.values,
                                   g.sell_dev.values)
    maker = arm != "cur"
    tt = arm in ("cur", "aT", "bT")
    t0 = t[i]
    hs = (ask[i] / bid[i] - 1) / 2
    P = price_for(arm, e, fair[i], base[i], hs) if maker else np.nan
    last_reprice = t0
    for j in range(i + 1, len(g)):
        held = (t[j] - t0) / 60
        if maker:
            filled = bid[j] >= P if rule == "conservative" else ask[j] > P
            if filled:
                return "maker", P, MAKER, held, j
        if bid[j] <= e * (1 - STOP):
            return "stop", bid[j], TAKER, held, j
        if tt and sd[j] >= -SLACK and bid[j] >= e * BE_TAKER:
            return "target", bid[j], TAKER, held, j
        if held >= hold:
            return "time", bid[j], TAKER, held, j
        if maker and t[j] - last_reprice >= 60:      # re-price once a minute with the new fair value
            hs_j = (ask[j] / bid[j] - 1) / 2
            P = price_for(arm, e, fair[j], base[j], hs_j)
            last_reprice = t[j]
    j = len(g) - 1
    return "end", bid[j], TAKER, (t[j] - t0) / 60, j


def net(e, x, fee_out):
    return ((1 - TAKER) * (1 - fee_out) * x / e - 1) * 100


def entry_ok(g, i, th):
    if g.buy_dev.values[i] > -th:
        return False
    if g.ask.values[i] / g.bid.values[i] - 1 > MAX_SPREAD:
        return False
    t, f = g.t.values, g.fair.values
    k = np.searchsorted(t, t[i] - 60)
    if k < i and f[i] / f[k] - 1 < -MAX_DROP:
        return False
    return True


def run(T: pd.DataFrame):
    rows = []
    for s, x in T.groupby("symbol"):
        hold = HOLD.get(s, HOLD_DEFAULT)
        for g in segments(x):
            for th in THRESHOLDS:
                i = 0
                while i < len(g) - 1:
                    if not entry_ok(g, i, th):
                        i += 1
                        continue
                    e = g.ask.values[i]
                    why0, x0, f0, h0, j0 = sim_exit("cur", g, i, e, hold, "conservative")
                    for arm in ARMS:
                        for rule in ("conservative", "upper_bound"):
                            if arm == "cur" and rule == "upper_bound":
                                continue
                            why, px, fo, held, _ = sim_exit(arm, g, i, e, hold, rule)
                            rows.append({"threshold_pct": th * 100, "symbol": s, "t": float(g.t.values[i]), "run": g.run.values[i],
                                         "arm": arm, "rule": rule, "why": why, "held_min": round(held, 2),
                                         "net_pct": net(e, px, fo), "entry_dev_pct": g.buy_dev.values[i] * 100})
                    i = j0 + 1          # entries follow the current rule's own sequence (paired design)
    return pd.DataFrame(rows)


def calibration(T: pd.DataFrame) -> dict:
    out = []
    for s, x in T.groupby("symbol"):
        for g in segments(x):
            t, sd, bid = g.t.values, g.sell_dev.values, g.bid.values
            last_low = -1e18
            prev_ok = True
            for j in range(len(g)):
                if sd[j] <= -0.003:
                    last_low = t[j]
                ok = sd[j] >= -SLACK
                if ok and not prev_ok and t[j] - last_low <= 600:
                    minute_end = (np.floor(t[j] / 60) + 1) * 60
                    k = np.searchsorted(t, minute_end) - 1          # last tick of the same calendar minute
                    out.append({"symbol": s, "t": t[j], "sell_dev_at_cross_pct": sd[j] * 100,
                                "bid_close_vs_cross_pct": (bid[k] / bid[j] - 1) * 100,
                                "max_bid_rest_of_minute_vs_cross_pct": (bid[j:k + 1].max() / bid[j] - 1) * 100})
                prev_ok = ok
    c = pd.DataFrame(out)
    if c.empty:
        return {"n": 0}
    c.to_csv(OUT / "ticks_calibration_crossings.csv", index=False)
    return {"n": int(len(c)), "per_symbol": c.symbol.value_counts().to_dict(),
            "mean_sell_dev_at_cross_pct": round(c.sell_dev_at_cross_pct.mean(), 3),
            "median_sell_dev_at_cross_pct": round(c.sell_dev_at_cross_pct.median(), 3),
            "mean_bid_minute_close_vs_cross_pct": round(c.bid_close_vs_cross_pct.mean(), 3),
            "median_bid_minute_close_vs_cross_pct": round(c.bid_close_vs_cross_pct.median(), 3),
            "mean_max_bid_rest_of_minute_vs_cross_pct": round(c.max_bid_rest_of_minute_vs_cross_pct.mean(), 3)}


def agg(df):
    if df.empty:
        return {"n": 0}
    return {"n": int(len(df)), "maker_fill_pct": round((df.why == "maker").mean() * 100, 1),
            "mean_net_pct": round(df.net_pct.mean(), 3), "sum_net_pct": round(df.net_pct.sum(), 2),
            "win_pct": round((df.net_pct > 0).mean() * 100, 1), "avg_hold_min": round(df.held_min.mean(), 1),
            "exits": df.why.value_counts().to_dict()}


def main():
    T = load()
    span = {r: [str(pd.to_datetime(g.t.min(), unit="s")), str(pd.to_datetime(g.t.max(), unit="s")), int(len(g))]
            for r, g in T.groupby("run")}
    R = run(T)
    R.to_csv(OUT / "ticks_trades.csv", index=False)
    res = {"runs": span, "hours_total": round(sum((pd.Timestamp(v[1]) - pd.Timestamp(v[0])).total_seconds() for v in span.values()) / 3600, 2),
           "min_buy_dev_pct_per_symbol": (T.groupby("symbol").buy_dev.min() * 100).round(3).to_dict(),
           "median_spread_pct": ((T.ask / T.bid - 1).groupby(T.symbol).median() * 100).round(3).to_dict(),
           "by_threshold": {}}
    for th in THRESHOLDS:
        X = R[R.threshold_pct == th * 100] if len(R) else R
        d = {}
        for arm in ARMS:
            for rule in ("conservative", "upper_bound"):
                if arm == "cur" and rule == "upper_bound":
                    continue
                A = X[(X.arm == arm) & (X.rule == rule)] if len(X) else X
                d[f"{arm}|{rule}"] = {"all": agg(A), "per_market": {s: agg(A[A.symbol == s]) for s in sorted(A.symbol.unique())} if len(A) else {}}
        res["by_threshold"][f"{th * 100:.1f}%"] = d
    res["calibration_target_crossings"] = calibration(T)
    (OUT / "ticks_summary.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "by_threshold"}, indent=1))
    for th, d in res["by_threshold"].items():
        print("\n== threshold", th)
        for k, v in d.items():
            print(f"  {k:22s} {v['all']}")


if __name__ == "__main__":
    main()
