"""Independent audit of the maker-exit candle backtest.

Re-implements the sim (own loop, own arrays incl. open/low) and adds:
  cur        : author's taker model (sell at minute-close bid when the target holds at the close)
  cur_thr    : author's taker lower bound (sell at the threshold bid)
  cur_path   : gap-aware intrabar taker: in minute j, if the OPEN bid already satisfies the target -> sell at open bid
               (a gap: a 2.5 s bot sees the book jump), elif HIGH bid reaches the threshold -> sell at threshold
  cur_next   : target decided on the close of j (as cur), executed at the NEXT traded minute's open bid
               (removes selection on the close print = bid/ask bounce check), if within 3 min, else close bid
  a, b, c    : author's maker variants, fill = high > P (strict)
  a_ge, b_ge : fill = high >= P (touch)
  a_cl, b_cl : fill only if CLOSE > P (very conservative)
Also: bounce statistic on cur target minutes, day-block bootstrap of the paired difference.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bt_maker_exit_candles import SYMS, binance, live_spreads, wallex  # noqa: E402  loaders only (read-only csv)

TAKER, MAKER = 0.003, 0.0025
ENTRY, SLACK, STOP = 0.007, 0.0005, 0.009
HOLD = {"ARBTMN": 15}
BE_T = 1 + 2 * TAKER + 0.0005
BE_M = 1 + TAKER + MAKER + 0.0005
SPLIT = pd.Timestamp("2026-10-18 14:05", tz="UTC")
OUT = Path(__file__).resolve().parent
ARMS = ["cur", "cur_thr", "cur_path", "cur_next", "a", "b", "c", "a_ge", "b_ge", "a_cl", "b_cl"]
MAKERS = ("a", "b", "c", "a_ge", "b_ge", "a_cl", "b_cl")


def frame(sym, hs, usdt):
    w = wallex(sym)
    g = binance(sym[:-3])
    grid = pd.date_range(max(w.index[0], g.index[0], usdt.index[0]), min(w.index[-1], g.index[-1], usdt.index[-1]),
                         freq="1min", tz="UTC")
    d = w.reindex(grid)
    d["volume"] = d.volume.fillna(0)
    gg = g.reindex(grid).ffill()
    d["fair"] = gg * usdt.reindex(grid).ffill()
    d["fair_prev"] = d.fair.shift(1)
    d["mom"] = gg / gg.shift(1) - 1
    tr = d[(d.volume > 0) & d.fair.notna()].copy()
    tr["base"] = (tr.close / tr.fair - 1).rolling("180min", min_periods=30).median().shift(1)
    tr = tr[tr.base.notna()].copy()
    tr["bid"], tr["ask"] = tr.close * (1 - hs), tr.close * (1 + hs)
    tr["buy_dev"] = tr.ask / tr.fair - 1 - tr.base
    tr["sell_dev"] = tr.bid / tr.fair - 1 - tr.base
    return tr


def sim(arm, X, i, e, hold, hs):
    ts, op, hi, lo, cl, fair, fprev, base = X
    t0 = ts[i]
    maker = arm in MAKERS
    n = len(ts)
    for j in range(i + 1, n):
        el = (ts[j] - t0) / 60
        bid = cl[j] * (1 - hs)
        stop = bid <= e * (1 - STOP)
        if maker and el <= hold:
            be = e * BE_M
            mid = fprev[j] * (1 + base[j])
            if arm == "c":
                P = be
            elif arm in ("a", "a_ge", "a_cl"):
                P = max(be, mid)
            else:
                P = max(be, mid * (1 + hs))
            if arm in ("a_ge", "b_ge"):
                f = hi[j] >= P
            elif arm in ("a_cl", "b_cl"):
                f = cl[j] > P
            else:
                f = hi[j] > P
            if f and not stop:
                return "maker", P, MAKER, j
        if stop:
            return "stop", bid, TAKER, j
        thr = max(fair[j] * (1 + base[j] - SLACK), e * BE_T)
        if not maker:
            if arm == "cur_path":
                thr_open = max(fprev[j] * (1 + base[j] - SLACK), e * BE_T)
                ob = op[j] * (1 - hs)
                if ob >= thr_open:
                    return "target", ob, TAKER, j
                if hi[j] * (1 - hs) >= thr:
                    return "target", thr, TAKER, j
            elif bid >= thr:     # == sell_dev >= -slack and bid >= e*BE_T
                if arm == "cur":
                    return "target", bid, TAKER, j
                if arm == "cur_thr":
                    return "target", min(bid, thr), TAKER, j
                if arm == "cur_next":
                    if j + 1 < n and ts[j + 1] - ts[j] <= 180:
                        return "target", op[j + 1] * (1 - hs), TAKER, j
                    return "target", bid, TAKER, j
        if el >= hold:
            return "time", bid, TAKER, j
    return "end", cl[n - 1] * (1 - hs), TAKER, n - 1


def net(e, x, fo):
    return ((1 - TAKER) * (1 - fo) * x / e - 1) * 100


def ag(D):
    return {"n": int(len(D)), "mean": round(float(D.net.mean()), 3), "sum": round(float(D.net.sum()), 2),
            "win": round(float((D.net > 0).mean() * 100), 1), "hold": round(float(D.held.mean()), 1),
            "exits": {k: int(v) for k, v in D.why.value_counts().items()},
            "maker_fill_pct": round(float((D.why == "maker").mean() * 100), 1)}


def main():
    spreads = live_spreads()
    u = wallex("USDTTMN").close
    usdt = u.reindex(pd.date_range(u.index[0], u.index[-1], freq="1min", tz="UTC")).ffill()
    rows, bounce = [], []
    for s in SYMS:
        hs = spreads.get(s, 0.003) / 2
        tr = frame(s, hs, usdt)
        ts = np.array([x.timestamp() for x in tr.index])
        X = (ts, tr.open.values, tr.high.values, tr.low.values, tr.close.values, tr.fair.values,
             tr.fair_prev.values, tr.base.values)
        hold = HOLD.get(s, 25)
        bd, mom = tr.buy_dev.values, tr.mom.values
        i, n = 0, len(tr)
        while i < n - 1:
            if bd[i] <= -ENTRY and not (np.isfinite(mom[i]) and mom[i] < -0.004) and 2 * hs <= 0.006:
                e = tr.ask.values[i]
                why0, px0, fo0, j0 = sim("cur", X, i, e, hold, hs)
                for arm in ARMS:
                    why, px, fo, j = sim(arm, X, i, e, hold, hs)
                    rows.append({"symbol": s, "t": str(tr.index[i]), "period": "d1-4" if tr.index[i] < SPLIT else "d5-7",
                                 "day": str(tr.index[i].date()), "arm": arm, "why": why, "net": net(e, px, fo),
                                 "held": (ts[j] - ts[i]) / 60, "gross_over_entry": (px / e - 1) * 100})
                if why0 == "target":
                    ok = j0 + 1 < n and ts[j0 + 1] - ts[j0] <= 180
                    nxt = tr.open.values[j0 + 1] / tr.close.values[j0] - 1 if ok else np.nan
                    rg = tr.high.values[j0] - tr.low.values[j0]
                    pos = (tr.close.values[j0] - tr.low.values[j0]) / rg if rg > 0 else np.nan
                    bounce.append({"symbol": s, "t": str(tr.index[i]), "close_pos_in_range": pos,
                                   "next_open_vs_close_pct": nxt * 100,
                                   "close_vs_open_pct": (tr.close.values[j0] / tr.open.values[j0] - 1) * 100})
                i = j0 + 1
            else:
                i += 1
        c, o = tr.close.values, tr.open.values
        ok = np.diff(ts) <= 180
        bounce.append({"symbol": s, "t": "UNCOND", "next_open_vs_close_pct": float(np.nanmean((o[1:] / c[:-1] - 1)[ok]) * 100),
                       "close_pos_in_range": np.nan, "close_vs_open_pct": np.nan})
    R = pd.DataFrame(rows)
    R.to_csv(OUT / "audit_paired.csv", index=False)
    B = pd.DataFrame(bounce)
    B.to_csv(OUT / "audit_bounce.csv", index=False)
    res = {"spreads_pct": {k: round(v * 100, 3) for k, v in spreads.items() if k in SYMS}, "arms": {}}
    for arm in ARMS:
        A = R[R.arm == arm]
        res["arms"][arm] = {"all": ag(A), "d1-4": ag(A[A.period == "d1-4"]), "d5-7": ag(A[A.period == "d5-7"]),
                            "per_market": {s: [int((A.symbol == s).sum()), round(float(A[A.symbol == s].net.mean()), 3)]
                                           for s in SYMS if (A.symbol == s).any()}}
    W = R.pivot_table(index=["symbol", "t", "period", "day"], columns="arm", values="net").reset_index()
    rng = np.random.default_rng(1)
    days = W.day.unique()
    res["diff"] = {}
    for ref in ("cur", "cur_thr", "cur_path", "cur_next"):
        for arm in MAKERS:
            v = (W[arm] - W[ref])
            groups = {dd: v[W.day == dd].to_numpy() for dd in days}
            bm = []
            for _ in range(4000):
                pick = rng.choice(days, size=len(days), replace=True)
                bm.append(np.concatenate([groups[dd] for dd in pick]).mean())
            res["diff"][f"{arm}-{ref}"] = {"mean": round(float(v.mean()), 3),
                                          "t": round(float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))), 2),
                                          "dayblock95": [round(float(np.percentile(bm, 2.5)), 3), round(float(np.percentile(bm, 97.5)), 3)],
                                          "d1-4": round(float(v[W.period == "d1-4"].mean()), 3),
                                          "d5-7": round(float(v[W.period == "d5-7"].mean()), 3)}
    Bt = B[B.t != "UNCOND"]
    res["bounce_on_cur_target_minutes"] = {
        "n": int(len(Bt)), "mean_close_pos_in_range": round(float(Bt.close_pos_in_range.mean()), 3),
        "share_close_eq_high": round(float((Bt.close_pos_in_range >= 0.999).mean()), 3),
        "mean_next_open_vs_close_pct": round(float(Bt.next_open_vs_close_pct.mean()), 3),
        "median_next_open_vs_close_pct": round(float(Bt.next_open_vs_close_pct.median()), 3),
        "n_next": int(Bt.next_open_vs_close_pct.notna().sum()),
        "uncond_next_open_vs_close_pct_by_market": B[B.t == "UNCOND"].set_index("symbol").next_open_vs_close_pct.round(4).to_dict()}
    (OUT / "audit_summary.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    for arm in ARMS:
        r = res["arms"][arm]
        print(f"{arm:9s}", r["all"], "| d1-4", r["d1-4"]["mean"], "d5-7", r["d5-7"]["mean"])
    print()
    for k, v in res["diff"].items():
        print(f"{k:16s}", v)
    print()
    print(json.dumps(res["bounce_on_cur_target_minutes"], indent=1))
    for a in ("cur", "cur_thr", "cur_path", "cur_next", "a", "b"):
        print(a, res["arms"][a]["per_market"])


if __name__ == "__main__":
    main()
