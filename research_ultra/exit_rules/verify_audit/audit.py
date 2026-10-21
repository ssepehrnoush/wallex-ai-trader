"""Adversarial audit of the exit-rule study (research_ultra/exit_rules).

Read-only on MAIN. Writes only into this folder. Checks:
  1. timestamp alignment Wallex vs Binance (look-ahead in fair?)
  2. how long "25-min" time exits really last in the volume>0 minute grid
  3. independent re-implementation (per market, engine order, cooldown; no cross-market caps)
  4. bounce-pessimistic fill model (entry on a bid print, target on an ask print)
  5. per-day differences, leave-one-day-out
  6. other split points (rolling origin) for the walk-forward pick
  7. other entry thresholds (0.8 / 0.9 tradable; 0.5 / 0.6 diagnostic only) for hold 45 vs 25

  python research_ultra/exit_rules/verify_audit/audit.py
"""
import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "rerun"))       # the author's common.py, copied unchanged (spreads snapshot too)
import common as C  # noqa: E402

OUT = HERE
SPLIT = int(pd.Timestamp("2026-10-18 14:05", tz="UTC").timestamp())
CUR = (0.009, 25, 0.0005)
PICK = (0.009, 45, 0.0)
H45 = (0.009, 45, 0.0005)
res = {}
rng = np.random.default_rng(123)


def p(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- 1. alignment
def alignment():
    u = C._wallex("USDTTMN")["close"]
    u = u.reindex(pd.date_range(u.index[0], u.index[-1], freq="1min", tz="UTC")).ffill()
    out = {}
    for s in ("BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN"):
        w = C._wallex(s)
        w = w[w.volume > 0]["close"]
        g = C._binance(s[:-3])
        grid = pd.date_range(max(w.index[0], g.index[0]), min(w.index[-1], g.index[-1]), freq="1min", tz="UTC")
        wf = w.reindex(grid).ffill()
        fair = (g.reindex(grid).ffill() * u.reindex(grid).ffill())
        rw = np.log(wf).diff()
        rf = np.log(fair).diff()
        traded = w.reindex(grid).notna()
        row = {}
        for k in range(-3, 4):
            # corr(Wallex return at t, fair return at t+k); k<0 means fair earlier (Wallex lags)
            x = pd.concat([rw, rf.shift(-k)], axis=1).dropna()
            x = x[traded.reindex(x.index)]
            row[k] = round(float(x.corr().iloc[0, 1]), 3)
        out[s] = row
    return out


# ---------------------------------------------------------------- helpers
def load_extra(spread_mult=1.0):
    """common.load_markets plus raw close/fair/base kept, so other fill models can be built."""
    mk = C.load_markets(spread_mult=spread_mult)
    spreads = C.live_spreads()
    u = C._wallex("USDTTMN")["close"]
    u = u.reindex(pd.date_range(u.index[0], u.index[-1], freq="1min", tz="UTC")).ffill()
    for s, m in mk.items():
        w = C._wallex(s)
        g = C._binance(s[:-3])
        fair = (g * u).dropna()
        df = w.join(fair.rename("fair"), how="inner").dropna(subset=["fair"])
        df = df[df.volume > 0]
        prem = df.close / df.fair - 1
        base = prem.rolling("180min", min_periods=30).median().shift(1)
        assert len(df) == len(m.t)
        m.close = df.close.to_numpy()
        m.fair = df.fair.to_numpy()
        m.base = base.to_numpy()
    return mk


def bounce_model(mk):
    """entry ask = close x (1 + 2hs) (signal and fill), target decided and filled on close x (1 - 2hs),
    stop / time at close x (1 - hs) (unchanged)."""
    for s, m in mk.items():
        hs = m.hs
        m.ask = m.close * (1 + 2 * hs)
        m.buy_dev = m.ask / m.fair - 1 - m.base
        m.bid_t = m.close * (1 - 2 * hs)
        m.sell_dev_t = m.bid_t / m.fair - 1 - m.base
    return mk


def exit_of_bounce(m, i, stop, hold_min, slack, stop_mode="close", t_end=None):
    e = m.ask[i]
    stop_px, be_px = e * (1 - stop), e * (1 + C.BE)
    n, j = len(m.t), i + 1
    while j < n:
        if t_end is not None and m.t[j] > t_end:
            return j - 1, m.bid[j - 1], "end"
        if m.bid[j] <= stop_px:
            return j, m.bid[j], "stop"
        if m.sell_dev_t[j] >= -slack and m.bid_t[j] >= be_px:
            return j, m.bid_t[j], "target"
        if m.t[j] - m.t[i] >= hold_min * 60:
            return j, m.bid[j], "time"
        j += 1
    return n - 1, m.bid[n - 1], "end"


def run_cfg(mk, cfg, t0=None, t1=None, entry=C.ENTRY, per_sym=None):
    sig = C.signals(mk, entry=entry, t0=t0, t1=t1)
    t_end = (t1 - 60) if t1 is not None else None
    return C.simulate(mk, sig, *cfg, t_end=t_end, per_sym=per_sym)


def pdiff(a, b):
    ka = a.set_index(["symbol", "t_entry"]).ret_pct if len(a) else pd.Series(dtype=float)
    kb = b.set_index(["symbol", "t_entry"]).ret_pct if len(b) else pd.Series(dtype=float)
    u = ka.index.union(kb.index)
    return ka.reindex(u).fillna(0) - kb.reindex(u).fillna(0)


def boot(d, n=10000):
    v = d.to_numpy()
    if not len(v):
        return {"diff": 0.0, "n_pairs": 0}
    bs = rng.choice(v, size=(n, len(v)), replace=True).sum(1)
    return {"diff": round(float(v.sum()), 3), "n_pairs": int(len(v)), "n_changed": int((np.abs(v) > 1e-9).sum()),
            "ci90": [round(float(np.quantile(bs, .05)), 3), round(float(np.quantile(bs, .95)), 3)],
            "p_gt0": round(float((bs > 0).mean()), 3)}


def st(tr):
    return C.stats(tr)


# ---------------------------------------------------------------- 3. independent re-implementation
def indep(mk, cfg, t0, t1, per_sym=None, cooldown=True):
    """Own loop, written from engine.py, per market. One position per market, 120-min cooldown after a
    loss, no cross-market caps (max 3 / exposure / daily limit) -> an upper bound on the trade count."""
    rows = []
    for s, m in mk.items():
        stop, hold, slack = (per_sym or {}).get(s, cfg)
        n = len(m.t)
        i = 0
        cd_until = -1
        while i < n - 1:
            t = m.t[i]
            if t < t0 or t >= t1:
                i += 1
                continue
            ok = np.isfinite(m.buy_dev[i]) and m.buy_dev[i] <= -C.ENTRY and m.gdrop[i] >= -0.004 and t > cd_until
            if not ok:
                i += 1
                continue
            e = m.ask[i]
            why, j = "end", i + 1
            while j < n:
                if t1 is not None and m.t[j] > t1 - 60:
                    j -= 1
                    why = "end"
                    break
                b = m.bid[j]
                if b <= e * (1 - stop):
                    why = "stop"; break
                if m.sell_dev[j] >= -slack and b >= e * (1 + 2 * C.TAKER + 0.0005):
                    why = "target"; break
                if m.t[j] - t >= hold * 60:
                    why = "time"; break
                j += 1
            j = min(j, n - 1)
            r = m.bid[j] / e * (1 - C.TAKER) ** 2 - 1
            rows.append({"symbol": s, "t_entry": int(t), "t_exit": int(m.t[j]), "why": why, "ret_pct": r * 100,
                         "minutes": (m.t[j] - t) / 60, "pnl_tmn": 0.0})
            if cooldown and r < 0:
                cd_until = m.t[j] + 120 * 60
            i = j + 1 if j > i else i + 1
    return pd.DataFrame(rows)


def main():
    p("== 1. alignment: corr(Wallex 1m log-return at t, fair return at t+k)")
    res["alignment_corr"] = alignment()
    for s, r in res["alignment_corr"].items():
        p("  ", s, r)

    mk = load_extra()
    t_first = min(int(m.t[0]) for m in mk.values())
    t_last = max(int(m.t[-1]) for m in mk.values()) + 60
    halves = {"is": (t_first, SPLIT), "oos": (SPLIT, t_last)}

    # ------------------------------------------------ 2. time-exit durations
    cur_oos = run_cfg(mk, CUR, t0=SPLIT)
    cur_is = run_cfg(mk, CUR, t1=SPLIT)
    both = pd.concat([cur_is, cur_oos])
    te = both[both.why == "time"]
    res["time_exit_minutes_current"] = {"n": int(len(te)), "quantiles": {str(q): round(float(te.minutes.quantile(q)), 1) for q in (0, .25, .5, .75, .9, 1)},
                                        "by_market_max": te.groupby("symbol").minutes.max().round(1).to_dict(),
                                        "share_over_30min": round(float((te.minutes > 30).mean()), 3)}
    p("== 2. time exits (current, hold 25): minutes held", res["time_exit_minutes_current"])
    res["mean_by_exit_current"] = both.groupby("why").ret_pct.agg(["count", "mean", "sum"]).round(3).to_dict()
    p("   mean by exit:", res["mean_by_exit_current"])

    # ------------------------------------------------ 3. independent re-implementation
    p("== 3. independent loop (no cross-market caps) vs author's simulate")
    ind = {}
    for name, cfg, per in (("current", CUR, None), ("hold45", H45, None), ("pick_0.9_45_0", PICK, None),
                           ("hold15", (0.009, 15, 0.0005), None), ("hold10", (0.009, 10, 0.0005), None),
                           ("stop0.6", (0.006, 25, 0.0005), None), ("X0.1", (0.009, 25, 0.001), None),
                           ("ARB_live_0.8_15", CUR, {"ARBTMN": (0.008, 15, 0.0005)})):
        ind[name] = {}
        for h, (a, b) in halves.items():
            mine = indep(mk, cfg, a, b, per)
            theirs = run_cfg(mk, cfg, t0=a if h == "oos" else None, t1=b if h == "is" else None, per_sym=per)
            ind[name][h] = {"mine": st(mine), "author": st(theirs)}
            ind[name][h + "_trades"] = mine
    base_ = ind["current"]
    summ = {}
    for name in ind:
        summ[name] = {}
        for h in halves:
            mi, au = ind[name][h]["mine"], ind[name][h]["author"]
            summ[name][h] = {"mine_n": mi["n"], "mine_sum": mi["sum_pct"], "mine_mean": mi["mean_pct"], "mine_win": mi["win_pct"],
                             "mine_exits": mi["exits"], "author_n": au["n"], "author_sum": au["sum_pct"]}
            if name != "current":
                summ[name][h]["mine_diff_vs_current"] = boot(pdiff(ind[name][h + "_trades"], base_[h + "_trades"]))
        p("  ", name, json.dumps(summ[name]))
    res["independent"] = summ

    # 7-market (no BNB) with the independent loop
    mk7 = {k: v for k, v in mk.items() if k != "BNBTMN"}
    s7 = {}
    for name, cfg in (("hold45", H45), ("pick_0.9_45_0", PICK), ("hold10", (0.009, 10, 0.0005))):
        s7[name] = {}
        for h, (a, b) in halves.items():
            s7[name][h] = boot(pdiff(indep(mk7, cfg, a, b), indep(mk7, CUR, a, b)))
    res["independent_no_bnb_diff_vs_current"] = s7
    p("   no-BNB independent diffs:", json.dumps(s7))

    # ------------------------------------------------ 4. bounce-pessimistic fill model, full walk-forward
    p("== 4. bounce-pessimistic model (entry on bid print, target on ask print)")
    mkb = bounce_model(load_extra())
    orig = C.exit_of
    C.exit_of = exit_of_bounce
    try:
        configs = list(itertools.product([0.006, 0.009, 0.012, 0.015], [10, 15, 25, 45], [0.0, 0.0005, 0.001, 0.002, 0.003]))
        g = []
        tr = {}
        for c in configs:
            a = run_cfg(mkb, c, t1=SPLIT)
            b = run_cfg(mkb, c, t0=SPLIT)
            tr[c] = (a, b)
            g.append({"cfg": c, "is_sum": st(a)["sum_pct"], "oos_sum": st(b)["sum_pct"], "is_n": len(a), "oos_n": len(b)})
        g = pd.DataFrame(g)
        cur_row = g[g.cfg == CUR].iloc[0]
        dist = g.cfg.apply(lambda c: abs(c[0] - CUR[0]) / 0.003 + abs(np.log(c[1] / CUR[1])) / np.log(45 / 25) + abs(c[2] - CUR[2]) / 0.0005)
        g["_d"] = dist
        best = g.sort_values(["is_sum", "_d"], ascending=[False, True]).iloc[0]
        bA = best.cfg
        res["bounce_model"] = {
            "current_is": st(tr[CUR][0]), "current_oos": st(tr[CUR][1]),
            "pick_A": list(bA), "pick_A_is": st(tr[bA][0]), "pick_A_oos": st(tr[bA][1]),
            "pick_A_oos_diff": boot(pdiff(tr[bA][1], tr[CUR][1])),
            "hold45_oos_diff": boot(pdiff(tr[H45][1], tr[CUR][1])), "hold45_is_diff": boot(pdiff(tr[H45][0], tr[CUR][0])),
            "hold10_oos_diff": boot(pdiff(tr[(0.009, 10, 0.0005)][1], tr[CUR][1])),
            "hold10_is_diff": boot(pdiff(tr[(0.009, 10, 0.0005)][0], tr[CUR][0])),
            "pick_A_rank_oos": int((g.oos_sum > g[g.cfg == bA].oos_sum.iloc[0]).sum()) + 1,
            "share_beating_current_oos": round(float((g.oos_sum > cur_row.oos_sum).mean()), 3),
        }
        bestB = g.sort_values(["oos_sum", "_d"], ascending=[False, True]).iloc[0].cfg
        res["bounce_model"]["pick_B"] = list(bestB)
        res["bounce_model"]["pick_B_is_diff"] = boot(pdiff(tr[bestB][0], tr[CUR][0]))
        p("  ", json.dumps(res["bounce_model"], default=str))
    finally:
        C.exit_of = orig

    # ------------------------------------------------ 5. per day (UTC), full 7 days, author's simulate
    p("== 5. per-day differences (full 7 days, author's simulate)")
    full_cur = run_cfg(mk, CUR)
    perday = {}
    for name, cfg in (("hold45", H45), ("pick", PICK), ("hold10", (0.009, 10, 0.0005)), ("hold15", (0.009, 15, 0.0005))):
        d = pdiff(run_cfg(mk, cfg), full_cur)
        day = pd.to_datetime(d.index.get_level_values(1), unit="s", utc=True).strftime("%m-%d")
        pdv = d.groupby(day.values).sum().round(3)
        perday[name] = {"per_day": pdv.to_dict(), "days_positive": int((pdv > 1e-9).sum()), "days_negative": int((pdv < -1e-9).sum()),
                        "total": round(float(d.sum()), 3),
                        "leave_one_day_out_min": round(float(min(d.sum() - x for x in pdv)), 3),
                        "leave_one_day_out_max": round(float(max(d.sum() - x for x in pdv)), 3)}
        p("  ", name, perday[name])
    res["per_day"] = perday

    # ------------------------------------------------ 6. other split points (rolling origin)
    p("== 6. other split points: pick on [start, split), test on [split, end)")
    configs = list(itertools.product([0.006, 0.009, 0.012, 0.015], [10, 15, 25, 45], [0.0, 0.0005, 0.001, 0.002]))  # within bounds
    splits = {}
    for sp in ("2026-10-17 14:05", "2026-10-18 14:05", "2026-10-19 14:05", "2026-10-20 14:05"):
        S = int(pd.Timestamp(sp, tz="UTC").timestamp())
        rows = []
        cache = {}
        for c in configs:
            a, b = run_cfg(mk, c, t1=S), run_cfg(mk, c, t0=S)
            cache[c] = (a, b)
            rows.append({"cfg": c, "is_sum": st(a)["sum_pct"], "_d": abs(c[0] - CUR[0]) / 0.003 + abs(np.log(c[1] / CUR[1])) / np.log(45 / 25) + abs(c[2] - CUR[2]) / 0.0005})
        gg = pd.DataFrame(rows).sort_values(["is_sum", "_d"], ascending=[False, True])
        bc = gg.iloc[0].cfg
        splits[sp] = {"pick": list(bc), "oos_pick": st(cache[bc][1]), "oos_current": st(cache[CUR][1]),
                      "oos_diff": boot(pdiff(cache[bc][1], cache[CUR][1])),
                      "oos_diff_no_bnb": round(float(pdiff(cache[bc][1][cache[bc][1].symbol != "BNBTMN"],
                                                           cache[CUR][1][cache[CUR][1].symbol != "BNBTMN"]).sum()), 3)}
        p("  ", sp, json.dumps({k: v for k, v in splits[sp].items() if k != "oos_pick" and k != "oos_current"}),
          "n", splits[sp]["oos_pick"]["n"], splits[sp]["oos_current"]["n"])
    res["rolling_origin"] = splits

    # ------------------------------------------------ 7. other entry thresholds: hold 45 / pick / hold 10 vs current
    p("== 7. other entry thresholds (0.8/0.9 tradable; 0.5/0.6 diagnostic only, below the fee floor)")
    ent = {}
    for E in (0.005, 0.006, 0.007, 0.008, 0.009):
        ent[str(E * 100)] = {}
        for h, (a, b) in halves.items():
            kw = dict(entry=E, t0=a if h == "oos" else None, t1=b if h == "is" else None)
            cur = run_cfg(mk, CUR, **kw)
            ent[str(E * 100)][h] = {"current": {k: st(cur)[k] for k in ("n", "mean_pct", "sum_pct")}}
            for name, cfg in (("hold45", H45), ("pick", PICK), ("hold10", (0.009, 10, 0.0005))):
                ent[str(E * 100)][h][name] = boot(pdiff(run_cfg(mk, cfg, **kw), cur))
        p("  ", E * 100, json.dumps({h: {k: (v if k == "current" else (v["diff"], v.get("ci90"))) for k, v in d.items()} for h, d in ent[str(E * 100)].items()}))
    res["entry_thresholds"] = ent

    (OUT / "audit_results.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
