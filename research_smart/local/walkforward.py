"""Step 2: walk-forward. Every pre-declared (entry config x exit rule) is run as a 5M-toman account,
1M per trade, max 3 positions, one per market, separately on IS (09-22..10-12, first 6 h = volume warm-up)
and OOS (10-12..10-22). Selection rule (declared before running): among configs with >= 20 IS trades,
highest IS account return at the realistic next-minute entry (lag=1). Then report that config's OOS, plus
the OOS distribution of every config so the selection effect is visible."""
import os as _os
from pathlib import Path as _P
HERE = str(_P(__file__).resolve().parent)          # this study's folder (outputs)
REPO = str(_P(__file__).resolve().parents[2])      # repo root (research/candles_30d, research/premium)
MAIN = _os.environ.get("WALLEX_MAIN", str(_P(REPO).parent / "wallex-bot"))   # live bot folder (ticks.csv, team only)
import json
import os
import numpy as np
import pandas as pd
import common as c
import engine as E

D = c.load_all()
grid = next(iter(D.values())).index
N = len(grid)
split_i = int(np.searchsorted(grid, c.SPLIT))
warm_i = 360
A = {m: E.arrays(df) for m, df in D.items()}
DAYS = {"IS": (split_i - warm_i) / 1440, "OOS": (N - split_i) / 1440}


def candidates(cfg):
    v, k, thr, Z = cfg
    out = []
    for m, df in D.items():
        for i in np.flatnonzero(E.signal(df, v, k, thr, Z)):
            out.append((int(i), m))
    out.sort()
    return out


def run(cands, exit_rule, lag, k, t0, t1):
    """Exact event-driven sim: walk signal bars in time order; take if market free and < 3 open."""
    open_, rows = [], []
    for i, m in cands:
        e = i + lag
        if e < t0 or e >= t1:
            continue
        open_ = [(x, mm) for x, mm in open_ if x > e]
        if len(open_) >= 3 or any(mm == m for _, mm in open_):
            continue
        r = E.trade(A[m], i, exit_rule, lag, k)
        if r is None or r[1] >= t1:
            continue
        open_.append((r[1], m))
        rows.append({"market": m, "i": i, "e": r[0], "x": r[1], "net": r[2]})
    return pd.DataFrame(rows, columns=["market", "i", "e", "x", "net"])


def metrics(tr, t0, t1, part):
    taken, eq = E.portfolio(tr, D, t0, t1) if len(tr) else (tr, np.full(t1 - t0, 5e6))
    out = {"n": int(len(tr)), "per_day": round(len(tr) / DAYS[part], 2)}
    if len(tr):
        out.update({"win_pct": round(float((tr.net > 0).mean() * 100), 1), "mean_net_pct": round(float(tr.net.mean() * 100), 3),
                    "sum_net_pct": round(float(tr.net.sum() * 100), 2), "worst_pct": round(float(tr.net.min() * 100), 2),
                    "best_pct": round(float(tr.net.max() * 100), 2),
                    "best_trade_share_of_sum": round(float(tr.net.max() / tr.net.sum()), 2) if tr.net.sum() > 0 else None})
    out["acct_ret_pct"] = round(float(eq[-1] / 5e6 - 1) * 100, 3)
    out["max_dd_pct"] = round(E.max_dd(eq) * 100, 3)
    if part == "OOS":
        out["win31h"] = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in E.windows(eq).items()}
    if len(tr):
        pm = tr.groupby("market").net.agg(["size", "mean", "sum"])
        out["per_market"] = {m: {"n": int(r["size"]), "mean_pct": round(r["mean"] * 100, 3), "sum_pct": round(r["sum"] * 100, 2)}
                             for m, r in pm.iterrows()}
    return out, eq


if __name__ == "__main__":
    res = []
    for cfg in E.configs():
        cands = candidates(cfg)
        k = cfg[1]
        for ex in E.EXITS:
            for lag in (0, 1):
                row = {"variant": cfg[0], "k": k, "thr": cfg[2], "Z": cfg[3], "exit": ex[0], "H": ex[1], "lag": lag}
                for part, (t0, t1) in (("IS", (warm_i, split_i)), ("OOS", (split_i, N))):
                    tr = run(cands, ex, lag, k, t0, t1)
                    m, _ = metrics(tr, t0, t1, part)
                    for key in ("n", "per_day", "win_pct", "mean_net_pct", "sum_net_pct", "worst_pct", "acct_ret_pct", "max_dd_pct"):
                        row[f"{part}_{key}"] = m.get(key)
                    if part == "OOS":
                        row["OOS_win31h_pos"] = m["win31h"].get("share_pos")
                res.append(row)
    res = pd.DataFrame(res)
    res.to_csv(os.path.join(c.OUT, "walkforward_grid.csv"), index=False)

    l1 = res[(res.lag == 1) & (res.IS_n >= 20)].sort_values("IS_acct_ret_pct", ascending=False)
    pd.set_option("display.width", 250)
    cols = ["variant", "k", "thr", "Z", "exit", "H", "IS_n", "IS_mean_net_pct", "IS_acct_ret_pct", "OOS_n", "OOS_mean_net_pct",
            "OOS_acct_ret_pct", "OOS_max_dd_pct", "OOS_win31h_pos"]
    print("Top 15 by IS account return (lag=1, IS n>=20):")
    print(l1[cols].head(15).to_string())
    print("\nconfigs total:", len(res), " lag1:", int((res.lag == 1).sum()))
    for lag in (0, 1):
        r = res[res.lag == lag]
        print(f"lag={lag}: IS acct>0 in {int((r.IS_acct_ret_pct > 0).sum())}/{len(r)}; OOS acct>0 in {int((r.OOS_acct_ret_pct > 0).sum())}/{len(r)};"
              f" OOS mean>0.0 with n>=10: {int(((r.OOS_mean_net_pct > 0) & (r.OOS_n >= 10)).sum())}")
