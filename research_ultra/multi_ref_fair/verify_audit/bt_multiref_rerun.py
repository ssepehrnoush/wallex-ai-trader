"""7-day 1-minute backtest: does a multi-exchange median global price, and/or a robust USDT/TMN leg,
change the current fair-value rule's entries, and are the changed trades good or bad?

Same approach as scripts/bt_fairvalue.py (executable prices = Wallex 1m close x (1 +/- half the median
live spread), only minutes in which Wallex traded, baseline = rolling 180-min median premium shifted by
one bar, taker fee on both legs, exits target / stop 0.9% / time 25 min (ARB 15)), entry floor 0.7%.

Fair-value variants (all causal):
  A0  binance close x USDTTMN close            (= the current rule, as bt_fairvalue.py)
  A1  median(binance, okx, bybit) x USDTTMN close
  B1  binance x rolling 5-min median of USDTTMN close (window ends at the current minute)
  AB  median3 x rolling 5-min median of USDTTMN

Reads Wallex candles from the MAIN folder (read-only) and reference candles from ./data (fetch_refs.py).

  python research_ultra/multi_ref_fair/bt_multiref.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
OUT = HERE / "rerun_out"
OUT.mkdir(exist_ok=True)
MAIN = Path("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot")

SYMS = ["BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN", "PEPETMN", "BNBTMN", "ARBTMN"]
TAKER = 0.003
ENTRY = 2 * TAKER + 0.001          # 0.7% floor (TMN)
SLACK, STOP = 0.0005, 0.009
HOLD = {"ARBTMN": 15}
SPLIT = pd.Timestamp("2026-10-18 14:05", tz="UTC")     # first 4 days | last ~3 days
SPREAD_CUTOFF = pd.Timestamp("2026-10-21 18:15", tz="UTC").timestamp()   # ticks.csv local-clock time
RUNS = ["shadow_v2", "shadow_v3", "shadow_v4", "shadow_v5", "shadow_v6", "live_main"]


def wallex(sym: str) -> pd.DataFrame:
    p = MAIN / "research" / "candles" / f"{sym}_1.csv"
    if not p.exists():
        p = MAIN / "research" / "premium" / f"wallex_{sym}_1m.csv"
    raw = pd.read_csv(p)
    df = pd.DataFrame({"c": raw["close"].astype(float).to_numpy(), "v": raw["volume"].astype(float).to_numpy()},
                      index=pd.to_datetime(raw["ts"].astype("int64"), unit="s", utc=True))
    return df[~df.index.duplicated(keep="last")].sort_index()


def ref(ex: str, base: str) -> pd.Series:
    raw = pd.read_csv(DATA / f"{ex}_{base}_1m.csv")
    s = pd.Series(raw["close"].astype(float).to_numpy(), index=pd.to_datetime(raw["ts"].astype("int64"), unit="s", utc=True))
    return s[~s.index.duplicated(keep="last")].sort_index()


def live_spreads() -> dict:
    parts = []
    for r in RUNS:
        p = MAIN / "runs" / r / "ticks.csv"
        if p.exists():
            t = pd.read_csv(p, on_bad_lines="skip")
            t = t[pd.to_numeric(t["ask"], errors="coerce").notna() & pd.to_numeric(t["bid"], errors="coerce").notna()]
            t = t[pd.to_numeric(t["t"], errors="coerce") <= SPREAD_CUTOFF]     # frozen snapshot: reproducible
            parts.append(t[["symbol", "bid", "ask"]].astype({"bid": float, "ask": float}))
    t = pd.concat(parts)
    return ((t.ask / t.bid - 1).groupby(t.symbol).median()).to_dict()


def simulate(df: pd.DataFrame, fair: pd.Series, hs: float, hold_min: float, be_guard: bool = False) -> list[dict]:
    d = df.join(fair.rename("fair"), how="inner").dropna()
    ask, bid = d.c.to_numpy() * (1 + hs), d.c.to_numpy() * (1 - hs)
    prem = pd.Series(d.c.to_numpy() / d.fair.to_numpy() - 1, index=d.index)
    base = prem.rolling("180min", min_periods=30).median().shift(1).to_numpy()   # known before the minute
    f = d.fair.to_numpy()
    buy_dev, sell_dev = ask / f - 1 - base, bid / f - 1 - base
    idx = d.index
    i, out = 0, []
    while i < len(d) - 1:
        if np.isfinite(buy_dev[i]) and buy_dev[i] <= -ENTRY:
            e, j, why = ask[i], i + 1, "end"
            tgt_px = e * (1 + 2 * TAKER + 0.0005) if be_guard else e
            while j < len(d):
                if bid[j] <= e * (1 - STOP):
                    why = "stop"; break
                if sell_dev[j] >= -SLACK and (bid[j] >= tgt_px if be_guard else bid[j] > e):
                    why = "target"; break
                if (idx[j] - idx[i]).total_seconds() >= hold_min * 60:
                    why = "time"; break
                j += 1
            j = min(j, len(d) - 1)
            r = bid[j] * (1 - TAKER) / (e * (1 + TAKER)) - 1
            out.append({"t": idx[i], "ret_pct": r * 100, "why": why, "minutes": (idx[j] - idx[i]).total_seconds() / 60,
                        "entry_dev_pct": buy_dev[i] * 100})
            i = j + 1
        else:
            i += 1
    return out


def stats(tr: pd.DataFrame) -> dict:
    if not len(tr):
        return {"n": 0, "mean_pct": None, "sum_pct": 0.0, "win_pct": None, "exits": {}}
    return {"n": int(len(tr)), "mean_pct": round(tr.ret_pct.mean(), 3), "sum_pct": round(tr.ret_pct.sum(), 2),
            "win_pct": round((tr.ret_pct > 0).mean() * 100, 1), "exits": tr.why.value_counts().to_dict()}


def main():
    spreads = live_spreads()
    usdt = wallex("USDTTMN")
    grid = pd.date_range(usdt.index[0], usdt.index[-1], freq="1min", tz="UTC")
    u_raw = usdt.c.reindex(grid).ffill()
    u_med5 = u_raw.rolling(5, min_periods=1).median()                 # last 5 minutes incl. current: causal
    u_prev5 = u_raw.rolling(5, min_periods=3).median().shift(1)       # strictly before the minute (spike detector)
    u_spike = u_raw / u_prev5 - 1

    # ---------- Part B stats on the USDTTMN leg ----------
    ud = (u_raw / u_med5 - 1).dropna().abs()
    partB_leg = {"minutes": int(len(ud)),
                 "usdttmn_traded_minutes": int((usdt.v > 0).sum()),
                 "abs_raw_vs_med5_gt_pct": {str(k): round(float((ud > k / 100).mean() * 100), 3) for k in (0.05, 0.1, 0.2, 0.3, 0.5)},
                 "abs_spike_vs_prev5_gt_pct": {str(k): round(float((u_spike.abs() > k / 100).mean() * 100), 3) for k in (0.05, 0.1, 0.2, 0.3, 0.5)},
                 "p99_abs_raw_vs_med5_pct": round(float(ud.quantile(0.99) * 100), 3),
                 "max_abs_raw_vs_med5_pct": round(float(ud.max() * 100), 3)}

    # ---------- Part A stats on the references ----------
    partA_ref, globals_ = {}, {}
    for s in SYMS:
        b = s[:-3]
        cols = {}
        for ex in ("binance", "okx", "bybit"):
            p = DATA / f"{ex}_{b}_1m.csv"
            if p.exists():
                cols[ex] = ref(ex, b)
        g = pd.DataFrame(cols)
        g = g.reindex(pd.date_range(g.index[0], g.index[-1], freq="1min", tz="UTC")).ffill(limit=5)
        med = g.median(axis=1, skipna=True)
        globals_[b] = (g["binance"], med, g)
        w = g.loc[(g.index >= grid[0]) & (g.index <= grid[-1])]
        mw = med.loc[w.index]
        dev = (w["binance"] / mw - 1).dropna()
        rec = {"minutes": int(len(dev)), "exchanges": list(cols),
               "binance_vs_median_abs_gt_pct": {str(k): round(float((dev.abs() > k / 100).mean() * 100), 3) for k in (0.05, 0.1, 0.2)},
               "binance_above_median_gt_0.1_pct": round(float((dev > 0.001).mean() * 100), 3),
               "p99_abs_pct": round(float(dev.abs().quantile(0.99) * 100), 3),
               "max_abs_pct": round(float(dev.abs().max() * 100), 3)}
        for ex in ("okx", "bybit"):
            if ex in w:
                dx = (w[ex] / mw - 1).dropna()
                rec[f"{ex}_vs_median_abs_gt_0.1_pct"] = round(float((dx.abs() > 0.001).mean() * 100), 3)
        partA_ref[s] = rec

    # ---------- backtests ----------
    variants = {"A0": ("bin", "raw"), "A1": ("med", "raw"), "B1": ("bin", "med5"), "AB": ("med", "med5")}
    trades = {v: [] for v in variants}
    trades_be = {v: [] for v in variants}
    ctx_rows = {}
    for s in SYMS:
        b = s[:-3]
        w = wallex(s)
        g_bin, g_med, g_all = globals_[b]
        w = w[(w.v > 0) & (w.index >= grid[0]) & (w.index <= grid[-1])]
        hs = spreads.get(s, 0.003) / 2
        for v, (gk, uk) in variants.items():
            g = g_bin if gk == "bin" else g_med
            u = u_raw if uk == "raw" else u_med5
            fair = (g * u).dropna()
            for tr in simulate(w, fair, hs, HOLD.get(s, 25)):
                trades[v].append({"symbol": s, **tr})
            for tr in simulate(w, fair, hs, HOLD.get(s, 25), be_guard=True):
                trades_be[v].append({"symbol": s, **tr})
        # context at every minute (for labelling entries)
        ctx = pd.DataFrame({"bin_vs_med_pct": (g_bin / g_med - 1) * 100,
                            "u_raw_vs_med5_pct": (u_raw / u_med5 - 1) * 100,
                            "u_spike_vs_prev5_pct": u_spike * 100})
        ctx_rows[s] = ctx

    T = {v: pd.DataFrame(x) for v, x in trades.items()}
    TB = {v: pd.DataFrame(x) for v, x in trades_be.items()}
    for v in T:
        T[v].to_csv(OUT / f"trades_{v}.csv", index=False)
        TB[v].to_csv(OUT / f"trades_{v}_beguard.csv", index=False)

    def label(df):
        if not len(df):
            return df
        df = df.copy()
        for c in ("bin_vs_med_pct", "u_raw_vs_med5_pct", "u_spike_vs_prev5_pct"):
            df[c] = [float(ctx_rows[r.symbol].get(c).get(r.t, np.nan)) for r in df.itertuples()]
        return df

    def summarize(Td):
        res = {}
        for v, tr in Td.items():
            r = {"all": stats(tr)}
            if len(tr):
                r["first4d"] = stats(tr[tr.t < SPLIT])
                r["last3d"] = stats(tr[tr.t >= SPLIT])
                r["per_market"] = {s: stats(tr[tr.symbol == s]) for s in SYMS}
            res[v] = r
        return res

    def changed(a: str, bname: str, Td):
        A, B = Td[a], Td[bname]
        ka = set(zip(A.symbol, A.t)) if len(A) else set()
        kb = set(zip(B.symbol, B.t)) if len(B) else set()
        gone = A[[k not in kb for k in zip(A.symbol, A.t)]] if len(A) else A
        new = B[[k not in ka for k in zip(B.symbol, B.t)]] if len(B) else B
        gone, new = label(gone), label(new)

        def net(g, n):
            return round(float((n.ret_pct.sum() if len(n) else 0) - (g.ret_pct.sum() if len(g) else 0)), 2)
        small = lambda d: d[d.ret_pct.abs() <= 2.0] if len(d) else d      # drop the one PEPE pump event (|ret| > 2%)
        early = lambda d: d[d.t < SPLIT] if len(d) else d
        late = lambda d: d[d.t >= SPLIT] if len(d) else d
        return {"disappeared": stats(gone), "appeared": stats(new),
                "net_change_sum_pct": round((B.ret_pct.sum() if len(B) else 0) - (A.ret_pct.sum() if len(A) else 0), 2),
                "net_change_changed_set_pct": net(gone, new),
                "net_change_excl_abs_ret_gt_2pct": net(small(gone), small(new)),
                "net_change_first4d": net(early(gone), early(new)), "net_change_last3d": net(late(gone), late(new)),
                "disappeared_with_u_up_spike_gt_0.1": stats(gone[gone.u_spike_vs_prev5_pct > 0.1]) if len(gone) else stats(gone),
                "appeared_with_u_below_med5_lt_-0.05": stats(new[new.u_raw_vs_med5_pct < -0.05]) if len(new) else stats(new),
                "disappeared_binance_above_median_gt_0.1": stats(gone[gone.bin_vs_med_pct > 0.1]) if len(gone) else stats(gone),
                "appeared_binance_below_median_lt_-0.1": stats(new[new.bin_vs_med_pct < -0.1]) if len(new) else stats(new),
                "net_by_market": {s: net(gone[gone.symbol == s] if len(gone) else gone, new[new.symbol == s] if len(new) else new)
                                  for s in SYMS},
                "disappeared_list": [{k: (str(v) if k == "t" else (round(v, 3) if isinstance(v, float) else v)) for k, v in r.items()}
                                     for r in gone.to_dict("records")],
                "appeared_list": [{k: (str(v) if k == "t" else (round(v, 3) if isinstance(v, float) else v)) for k, v in r.items()}
                                  for r in new.to_dict("records")]}

    # entries of the current rule that coincide with a USDTTMN spike, and how they ended
    a0 = label(T["A0"])
    spike_dep = {}
    for thr in (0.05, 0.1, 0.2):
        m = a0[a0.u_spike_vs_prev5_pct > thr] if len(a0) else a0
        spike_dep[f"u_up_spike_gt_{thr}"] = stats(m)
    a0.to_csv(OUT / "trades_A0_labelled.csv", index=False)
    # simultaneous signals: same minute, 2+ markets entering under A0
    simult = {}
    if len(a0):
        c = a0.groupby("t").symbol.count()
        multi = a0[a0.t.isin(c[c >= 2].index)]
        simult = {"minutes_with_2plus_entries": int((c >= 2).sum()), "trades_in_them": stats(multi),
                  "their_u_spike_mean_pct": round(float(multi.u_spike_vs_prev5_pct.mean()), 3) if len(multi) else None}

    res = {"spreads_used_pct": {s: round(spreads.get(s, 0.003) * 100, 3) for s in SYMS},
           "window": [str(grid[0]), str(grid[-1])], "split": str(SPLIT),
           "partA_reference_divergence": partA_ref, "partB_usdttmn_leg": partB_leg,
           "backtest_bt_exits": summarize(T), "backtest_live_breakeven_exits": summarize(TB),
           "changes_bt_exits": {f"A0->{v}": changed("A0", v, T) for v in ("A1", "B1", "AB")},
           "changes_live_exits": {f"A0->{v}": {k: x for k, x in changed("A0", v, TB).items() if not k.endswith("_list")}
                                  for v in ("A1", "B1", "AB")},
           "A0_entries_on_usdttmn_up_spike": spike_dep, "A0_simultaneous_entries": simult}
    (OUT / "bt_multiref_summary.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("spreads_used_pct", "window", "partB_usdttmn_leg", "A0_entries_on_usdttmn_up_spike",
                                          "A0_simultaneous_entries")}, indent=1, default=str))
    for v in variants:
        print(v, "bt-exits", res["backtest_bt_exits"][v]["all"], "| be-guard", res["backtest_live_breakeven_exits"][v]["all"])
    for k, x in res["changes_bt_exits"].items():
        print(k, "gone", x["disappeared"], "new", x["appeared"], "net", x["net_change_sum_pct"])
        print("   ", {kk: vv for kk, vv in x.items() if kk.startswith(("net_", "disappeared_w", "appeared_w", "disappeared_b", "appeared_b"))})


if __name__ == "__main__":
    main()
