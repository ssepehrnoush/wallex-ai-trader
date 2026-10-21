"""Maker exit (resting SELL) vs the current taker exit, same entries, on 7 days of Wallex 1m candles.

Reads recorded data only (MAIN is read-only, nothing is fetched). Entries = the current live rule:
buy_dev <= -0.7% (TMN floor 2 x taker + 0.1%), modelled spread <= 0.6%, Binance 1m drop < 0.4%.
Executable prices are modelled like scripts/bt_fairvalue.py: ask = close x (1 + hs), bid = close x (1 - hs),
hs = half of the median live spread measured in ticks.csv (shadow_v2..v6 + live_main snapshot).

Exit arms (all share the same stop 0.9% / max hold 25 min (ARB 15) taker fallback at the bid):
  cur   : current rule. target = sell_dev >= -0.05% AND bid >= entry x (1 + 2 x taker + 0.05%), taker at bid.
  a     : resting maker ask at P = max(BE, normal mid),  normal mid = fair x (1 + baseline)
  b     : resting maker ask at P = max(BE, normal ask),  normal ask = normal mid x (1 + hs)
  c     : resting maker ask at P = BE = entry x (1 + taker + maker + 0.05%)
  d     : max(a, c)  (identical to a, because every variant is floored at BE; kept for the record)
  a_raw : normal mid WITHOUT the BE floor (informational: can sell below entry)
  aT,bT : a / b plus the current taker target still active (whichever comes first)
P is re-evaluated every minute from information known before that minute (fair of the previous calendar
minute, baseline shift(1)). Conservative fill: minute j > entry minute fills only if volume > 0 and
HIGH > P strictly, and only while the order is still resting (minute start <= entry + max_hold).
Same-minute ambiguity (fill and stop both in minute j): main = pessimistic (stop wins), also reported optimistic.

Two designs:
  paired     : the entries of the current rule (sequential sim), each exit arm applied to the same entries.
  sequential : each arm runs its own sequential sim (no new entry in a market while holding it).

  python research_ultra/maker_exit/bt_maker_exit_candles.py
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

MAIN = Path("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot")
OUT = Path(__file__).resolve().parent
SYMS = ["BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN", "PEPETMN", "BNBTMN", "ARBTMN"]
TICK_RUNS = ["shadow_v2", "shadow_v3", "shadow_v4", "shadow_v5", "shadow_v6", "live_main"]
TAKER, MAKER = 0.003, 0.0025
ENTRY = 2 * TAKER + 0.001          # 0.7 %, the TMN floor (policy entry_dev is 0.7 % too)
SLACK, STOP = 0.0005, 0.009
HOLD = {"ARBTMN": 15}
HOLD_DEFAULT = 25
MAX_SPREAD, MAX_DROP = 0.006, 0.004
BE_TAKER = 1 + 2 * TAKER + 0.0005  # current target floor 1.0065
BE_MAKER = 1 + TAKER + MAKER + 0.0005  # maker break-even floor 1.006
SPLIT = pd.Timestamp("2026-10-18 14:05", tz="UTC")   # days 1-4 | days 5-7 of the candle window
ARMS = ["cur", "cur_thr", "cur_cont", "a", "b", "c", "d", "a_raw", "aT", "bT"]
CUR = ("cur", "cur_thr", "cur_cont")


def read_ticks(run: str) -> pd.DataFrame:
    raw = (MAIN / "runs" / run / "ticks.csv").read_text(encoding="utf-8")
    lines = raw.splitlines()
    if lines and not raw.endswith("\n"):
        lines = lines[:-1]                       # tolerate a partial last line (live file is being appended)
    t = pd.read_csv(io.StringIO("\n".join(lines)), on_bad_lines="skip")
    t["run"] = run
    return t


def live_spreads() -> dict:
    t = pd.concat([read_ticks(r) for r in TICK_RUNS])
    t = t[t.symbol.isin(SYMS)]
    return ((t.ask / t.bid - 1).groupby(t.symbol).median()).to_dict()


def wallex(sym: str) -> pd.DataFrame:
    p = MAIN / "research" / "candles" / f"{sym}_1.csv"
    if not p.exists():
        p = MAIN / "research" / "premium" / f"wallex_{sym}_1m.csv"
    raw = pd.read_csv(p)
    df = raw.set_index(pd.to_datetime(raw.ts.astype("int64"), unit="s", utc=True))[["open", "high", "low", "close", "volume"]]
    return df[~df.index.duplicated(keep="last")].sort_index().astype(float)


def binance(base: str) -> pd.Series:
    s = pd.read_csv(MAIN / "research" / "premium" / f"binance_{base}USDT_1m.csv", index_col=0, parse_dates=True)["close"]
    s.index = pd.to_datetime(s.index, utc=True)
    return s[~s.index.duplicated(keep="last")].sort_index()


def build(sym: str, hs: float, usdt: pd.Series) -> pd.DataFrame:
    """Full minute grid with candle, fair, baseline (known before the minute) and modelled bid/ask."""
    w = wallex(sym)
    g = binance(sym[:-3])
    start = max(w.index[0], g.index[0], usdt.index[0])
    end = min(w.index[-1], g.index[-1], usdt.index[-1])
    grid = pd.date_range(start, end, freq="1min", tz="UTC")
    d = w.reindex(grid)
    d["volume"] = d["volume"].fillna(0.0)
    d["g"] = g.reindex(grid).ffill()
    d["fair"] = d["g"] * usdt.reindex(grid).ffill()
    d["traded"] = d["volume"] > 0
    tr = d[d.traded & d.fair.notna()]
    prem = tr.close / tr.fair - 1
    base_tr = prem.rolling("180min", min_periods=30).median().shift(1)    # known before the minute
    d["base"] = base_tr.reindex(grid).ffill()
    # a non-traded minute inherits the baseline of the last traded minute; a traded minute uses its own shift(1)
    d.loc[tr.index, "base"] = base_tr
    d["ask"] = d.close * (1 + hs)
    d["bid"] = d.close * (1 - hs)
    d["buy_dev"] = d.ask / d.fair - 1 - d.base
    d["sell_dev"] = d.bid / d.fair - 1 - d.base
    d["fair_prev"] = d["fair"].shift(1)                                   # fair known at the start of the minute
    d["mom"] = d["g"] / d["g"].shift(1) - 1                              # 1-minute global move (proxy for 60 s drop)
    return d


def resting_price(arm: str, e: float, fair_prev: float, base: float, hs: float) -> float:
    be = e * BE_MAKER
    mid = fair_prev * (1 + base)
    if arm in ("a", "d", "aT"):
        return max(be, mid)
    if arm in ("b", "bT"):
        return max(be, mid * (1 + hs))
    if arm == "c":
        return be
    if arm == "a_raw":
        return mid
    raise ValueError(arm)


def simulate_exit(arm: str, d: dict, i: int, e: float, hold: float, hs: float, pessimistic: bool) -> dict:
    """d: dict of numpy arrays over TRADED minutes only (plus their grid timestamps). i: entry row."""
    ts, bid, high, sell_dev = d["ts"], d["bid"], d["high"], d["sell_dev"]
    n = len(ts)
    t0 = ts[i]
    maker = arm not in CUR
    taker_target = arm in CUR or arm in ("aT", "bT")
    for j in range(i + 1, n):
        el = (ts[j] - t0) / 60.0                 # minutes between minute starts
        resting = maker and el <= hold          # order still on the book during minute j
        P = resting_price(arm, e, d["fair_prev"][j], d["base"][j], hs) if resting else np.nan
        filled = resting and np.isfinite(P) and high[j] > P
        stop = bid[j] <= e * (1 - STOP)
        tgt = taker_target and np.isfinite(sell_dev[j]) and sell_dev[j] >= -SLACK and bid[j] >= e * BE_TAKER
        thr = max(d["fair"][j] * (1 + d["base"][j] - SLACK), e * BE_TAKER)   # bid level where the target first holds
        if arm == "cur_cont":            # intrabar trigger: the bid implied by the minute HIGH reached the threshold
            tgt = np.isfinite(thr) and high[j] * (1 - hs) >= thr
        tim = el >= hold
        if filled and not (pessimistic and stop):
            return {"why": "maker", "exit": P, "j": j, "held": el + 0.5, "fee_out": MAKER}
        if stop:
            return {"why": "stop", "exit": bid[j], "j": j, "held": el, "fee_out": TAKER}
        if tgt:
            # cur: sells at the minute-close bid (existing backtest). cur_thr / cur_cont: sells at the threshold
            # itself, i.e. what a 2.5 s bot gets if the bid crosses the threshold continuously (taker lower bound)
            px = bid[j] if arm in ("cur", "aT", "bT") else min(bid[j], thr) if arm == "cur_thr" else thr
            return {"why": "target", "exit": px, "j": j, "held": el, "fee_out": TAKER}
        if tim:
            return {"why": "time", "exit": bid[j], "j": j, "held": el, "fee_out": TAKER}
    return {"why": "end", "exit": bid[n - 1], "j": n - 1, "held": (ts[n - 1] - t0) / 60.0, "fee_out": TAKER}


def net(e: float, x: float, fee_out: float) -> float:
    # buy fee taken in coins (engine), sell fee in toman
    return ((1 - TAKER) * (1 - fee_out) * x / e - 1) * 100


def entries_ok(d: dict, i: int) -> bool:
    bd = d["buy_dev"][i]
    if not np.isfinite(bd) or bd > -ENTRY:
        return False
    if np.isfinite(d["mom"][i]) and d["mom"][i] < -MAX_DROP:
        return False
    return True


def run_symbol(sym: str, hs: float, usdt: pd.Series):
    full = build(sym, hs, usdt)
    tr = full[full.traded & full.fair.notna() & full.base.notna()]
    d = {"ts": (tr.index - pd.Timestamp("1970-01-01", tz="UTC")).total_seconds().to_numpy(), "bid": tr.bid.to_numpy(), "ask": tr.ask.to_numpy(),
         "high": tr.high.to_numpy(), "sell_dev": tr.sell_dev.to_numpy(), "buy_dev": tr.buy_dev.to_numpy(),
         "fair_prev": tr.fair_prev.to_numpy(), "fair": tr.fair.to_numpy(), "base": tr.base.to_numpy(), "mom": tr.mom.to_numpy()}
    idx = tr.index
    hold = HOLD.get(sym, HOLD_DEFAULT)
    spread_ok = 2 * hs <= MAX_SPREAD
    paired, seq = [], []
    if not spread_ok:
        return paired, seq, len(tr)
    # sequential sim of the current rule -> the shared entry list
    n = len(tr)
    i = 0
    entries = []
    while i < n - 1:
        if entries_ok(d, i):
            r = simulate_exit("cur", d, i, d["ask"][i], hold, hs, True)
            entries.append(i)
            i = r["j"] + 1
        else:
            i += 1
    for i in entries:
        e = d["ask"][i]
        for arm in ARMS:
            for pess in (True, False):
                r = simulate_exit(arm, d, i, e, hold, hs, pess)
                paired.append({"symbol": sym, "t": str(idx[i]), "period": "d1-4" if idx[i] < SPLIT else "d5-7",
                               "arm": arm, "pessimistic": pess, "why": r["why"], "held_min": round(r["held"], 2),
                               "net_pct": net(e, r["exit"], r["fee_out"]), "entry_dev_pct": d["buy_dev"][i] * 100,
                               "exit_over_entry_pct": (r["exit"] / e - 1) * 100})
    # each arm's own sequential sim (pessimistic only)
    for arm in ARMS:
        i = 0
        while i < n - 1:
            if entries_ok(d, i):
                e = d["ask"][i]
                r = simulate_exit(arm, d, i, e, hold, hs, True)
                seq.append({"symbol": sym, "t": str(idx[i]), "period": "d1-4" if idx[i] < SPLIT else "d5-7", "arm": arm,
                            "why": r["why"], "held_min": round(r["held"], 2), "net_pct": net(e, r["exit"], r["fee_out"])})
                i = r["j"] + 1
            else:
                i += 1
    return paired, seq, len(tr)


def agg(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"n": 0}
    return {"n": int(len(df)), "maker_fill_pct": round(float((df.why == "maker").mean() * 100), 1),
            "mean_net_pct": round(float(df.net_pct.mean()), 3), "sum_net_pct": round(float(df.net_pct.sum()), 2),
            "median_net_pct": round(float(df.net_pct.median()), 3),
            "win_pct": round(float((df.net_pct > 0).mean() * 100), 1), "avg_hold_min": round(float(df.held_min.mean()), 1),
            "exits": {k: int(v) for k, v in df.why.value_counts().items()}}


def diff_stats(dd: pd.Series, period: pd.Series) -> dict:
    dd = dd.dropna()
    if dd.empty:
        return {"n": 0}
    rng = np.random.default_rng(0)
    v = dd.to_numpy()
    boots = rng.choice(v, size=(10000, len(v)), replace=True).mean(axis=1)
    trim = v[np.argsort(-np.abs(v))][3:]          # drop the 3 largest |differences| (outlier check)
    sd = v.std(ddof=1) if len(v) > 1 else np.nan
    return {"n": int(len(v)), "mean_diff_pct": round(float(v.mean()), 3), "median_diff_pct": round(float(np.median(v)), 3),
            "t_stat": round(float(v.mean() / (sd / np.sqrt(len(v)))), 2) if sd and sd > 0 else None,
            "boot95_ci_pct": [round(float(np.percentile(boots, 2.5)), 3), round(float(np.percentile(boots, 97.5)), 3)],
            "mean_diff_without_3_largest_abs_pct": round(float(trim.mean()), 3) if len(trim) else None,
            "better": int((v > 1e-9).sum()), "worse": int((v < -1e-9).sum()), "same": int((np.abs(v) <= 1e-9).sum()),
            "d1-4_mean_diff": round(float(dd[period.loc[dd.index] == "d1-4"].mean()), 3),
            "d5-7_mean_diff": round(float(dd[period.loc[dd.index] == "d5-7"].mean()), 3)}


def wide_spreads() -> dict:
    """The wider spreads the earlier backtests used (shadow_v1 ticks for the majors, /v1/markets for the rest)."""
    a = json.loads((MAIN / "runs/backtests/fv_sweep_0.007_0.004/summary.json").read_text(encoding="utf-8"))["spreads_from_live"]
    b = json.loads((MAIN / "runs/backtests/fv_extra_tmn/summary.json").read_text(encoding="utf-8"))["spreads_from_live"]
    out = {}
    for s in SYMS:
        v = a.get(s, b.get(s))
        if v is not None:
            out[s] = v / 100
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--spreads", choices=["ticks", "wide"], default="ticks")
    a = ap.parse_args()
    tag = "" if a.spreads == "ticks" else "_wide"
    spreads = live_spreads() if a.spreads == "ticks" else wide_spreads()
    u = wallex("USDTTMN")["close"]
    usdt = u.reindex(pd.date_range(u.index[0], u.index[-1], freq="1min", tz="UTC")).ffill()
    paired, seq, minutes = [], [], {}
    for s in SYMS:
        hs = spreads.get(s, 0.003) / 2
        p, q, m = run_symbol(s, hs, usdt)
        paired += p
        seq += q
        minutes[s] = m
        print(s, "half_spread %.3f%%" % (hs * 100), "traded minutes", m, "entries", len(p) // (2 * len(ARMS)), file=sys.stderr)
    P = pd.DataFrame(paired)
    Q = pd.DataFrame(seq)
    P.to_csv(OUT / f"candles_paired_trades{tag}.csv", index=False)
    Q.to_csv(OUT / f"candles_sequential_trades{tag}.csv", index=False)
    res = {"spread_set": a.spreads, "spreads_pct_used": {k: round(v * 100, 3) for k, v in spreads.items()},
           "traded_minutes": minutes, "split": str(SPLIT), "paired": {}, "sequential": {}}
    for mode, pess in (("pessimistic", True), ("optimistic", False)):
        X = P[P.pessimistic == pess]
        out = {}
        for arm in ARMS:
            A = X[X.arm == arm]
            out[arm] = {"all": agg(A), "d1-4": agg(A[A.period == "d1-4"]), "d5-7": agg(A[A.period == "d5-7"]),
                        "per_market": {s: agg(A[A.symbol == s]) for s in SYMS}}
        res["paired"][mode] = out
    for arm in ARMS:
        A = Q[Q.arm == arm]
        res["sequential"][arm] = {"all": agg(A), "d1-4": agg(A[A.period == "d1-4"]), "d5-7": agg(A[A.period == "d5-7"]),
                                  "per_market": {s: agg(A[A.symbol == s]) for s in SYMS}}
    # paired differences (same entries), pessimistic same-minute ordering
    X = P[P.pessimistic].pivot_table(index=["symbol", "t", "period"], columns="arm", values="net_pct").reset_index()
    res["paired_diff"] = {}
    for ref in CUR:
        res["paired_diff"][f"vs_{ref}"] = {arm: diff_stats(X[arm] - X[ref], X["period"]) for arm in ARMS if arm not in CUR}
    (OUT / f"candles_summary{tag}.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    for mode in ("pessimistic", "optimistic"):
        print(f"== paired ({mode}) ==")
        for arm in ARMS:
            r = res["paired"][mode][arm]
            print(f"{arm:8s} all  {r['all']}")
            print(f"         d1-4 {r['d1-4']}")
            print(f"         d5-7 {r['d5-7']}")
    print("== sequential ==")
    for arm in ARMS:
        print(f"{arm:8s}", res["sequential"][arm]["all"])
    print("== paired diff (pessimistic) ==")
    for ref, d in res["paired_diff"].items():
        for arm, v in d.items():
            print(ref, f"{arm:6s}", v)


if __name__ == "__main__":
    main()
