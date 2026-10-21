"""Extreme taker-pessimistic bound: a candle-close target is treated as a possible spike print and only counts when
the NEXT traded minute (within 3 min) also opens with the modelled bid above the threshold; the sale is at that next
open bid. Otherwise the position keeps running (stop / later target / time). Same entries as audit_candles.py.
Compared with maker a / b (high > P). Purpose: does ANY plausible taker reference make the maker exit clearly positive
in both halves?"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_candles as A  # noqa: E402
from bt_maker_exit_candles import SYMS, live_spreads, wallex  # noqa: E402


def sim_conf(X, i, e, hold, hs):
    ts, op, hi, lo, cl, fair, fprev, base = X
    t0, n = ts[i], len(ts)
    for j in range(i + 1, n):
        el = (ts[j] - t0) / 60
        bid = cl[j] * (1 - hs)
        if bid <= e * (1 - A.STOP):
            return "stop", bid, A.TAKER, j
        thr = max(fair[j] * (1 + base[j] - A.SLACK), e * A.BE_T)
        if bid >= thr and j + 1 < n and ts[j + 1] - ts[j] <= 180:
            thr2 = max(fprev[j + 1] * (1 + base[j + 1] - A.SLACK), e * A.BE_T)
            ob = op[j + 1] * (1 - hs)
            if ob >= thr2:
                return "target", ob, A.TAKER, j + 1
        if el >= hold:
            return "time", bid, A.TAKER, j
    return "end", cl[n - 1] * (1 - hs), A.TAKER, n - 1


def main():
    spreads = live_spreads()
    u = wallex("USDTTMN").close
    usdt = u.reindex(pd.date_range(u.index[0], u.index[-1], freq="1min", tz="UTC")).ffill()
    rows = []
    for s in SYMS:
        hs = spreads.get(s, 0.003) / 2
        tr = A.frame(s, hs, usdt)
        ts = np.array([x.timestamp() for x in tr.index])
        X = (ts, tr.open.values, tr.high.values, tr.low.values, tr.close.values, tr.fair.values, tr.fair_prev.values, tr.base.values)
        hold = A.HOLD.get(s, 25)
        bd, mom = tr.buy_dev.values, tr.mom.values
        i, n = 0, len(tr)
        while i < n - 1:
            if bd[i] <= -A.ENTRY and not (np.isfinite(mom[i]) and mom[i] < -0.004) and 2 * hs <= 0.006:
                e = tr.ask.values[i]
                _, _, _, j0 = A.sim("cur", X, i, e, hold, hs)
                per = "d1-4" if tr.index[i] < A.SPLIT else "d5-7"
                for arm in ("cur", "a", "b"):
                    why, px, fo, j = A.sim(arm, X, i, e, hold, hs)
                    rows.append({"symbol": s, "t": str(tr.index[i]), "period": per, "arm": arm, "why": why, "net": A.net(e, px, fo)})
                why, px, fo, j = sim_conf(X, i, e, hold, hs)
                rows.append({"symbol": s, "t": str(tr.index[i]), "period": per, "arm": "cur_conf", "why": why, "net": A.net(e, px, fo)})
                i = j0 + 1
            else:
                i += 1
    R = pd.DataFrame(rows)
    R.to_csv(A.OUT / "audit_conf_paired.csv", index=False)
    for arm in ("cur", "cur_conf", "a", "b"):
        D = R[R.arm == arm]
        print(arm, len(D), round(D.net.mean(), 3), round(D.net.sum(), 2), D.why.value_counts().to_dict(),
              "d1-4", round(D[D.period == "d1-4"].net.mean(), 3), "d5-7", round(D[D.period == "d5-7"].net.mean(), 3))
    W = R.pivot_table(index=["symbol", "t", "period"], columns="arm", values="net").reset_index()
    rng = np.random.default_rng(2)
    for arm in ("a", "b"):
        v = (W[arm] - W["cur_conf"]).to_numpy()
        bt = rng.choice(v, size=(10000, len(v))).mean(axis=1)
        print(f"{arm}-cur_conf mean {v.mean():.3f} t {v.mean() / (v.std(ddof=1) / np.sqrt(len(v))):.2f} "
              f"iid95 [{np.percentile(bt, 2.5):.3f}, {np.percentile(bt, 97.5):.3f}] "
              f"d1-4 {(W[arm] - W.cur_conf)[W.period == 'd1-4'].mean():.3f} d5-7 {(W[arm] - W.cur_conf)[W.period == 'd5-7'].mean():.3f}")


if __name__ == "__main__":
    main()
