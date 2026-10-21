"""Adversarial re-check of the live-audit forecast (independent implementation, local files only).

Re-implements the bt_fairvalue.py method (Wallex 1m closes with volume>0, fair = Binance close x
USDTTMN close ffill, ask/bid = close x (1 +/- half spread), baseline = rolling 180-min median of the
close premium, shift(1)), the engine's exit rule (stop -> target needs sell_dev >= -slack AND bid >=
entry x (1 + 2 x taker + 0.05%) -> time), and then adds what the forecast left out:

  A. engine/risk constraints: 120-min per-symbol cooldown after any losing exit (risk.on_exit),
     max 3 open positions across markets (risk.size), global 60 s drop filter (1-min Binance proxy).
  B. fill pessimism: the candle close is a last trade that sits at the bid or the ask; an entry chosen
     because the modelled ask looks cheap is biased toward closes at the bid. Variant: fill both legs
     at the full live spread (close x (1 + 2hs) / close x (1 - 2hs)), signals unchanged.
  C. persistence: require buy_dev <= -entry on two consecutive traded minutes and fill at the second.
  D. day-cluster bootstrap of the per-trade mean (trades cluster in bursts).
  E. how often a 3.6 h window in the 7-day candles has NO minute with buy_dev <= -0.5% in any major
     (today's shadow+live ticks had none), overall and in hours whose Binance volatility matches today.

Writes verify_forecast.json next to this file. Never calls Wallex.
"""
import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import ctypes
import csv
import io
import json
import statistics
import time
from pathlib import Path


def avail_commit_mb() -> int:
    class M(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("a", ctypes.c_ulonglong),
                    ("b", ctypes.c_ulonglong), ("c", ctypes.c_ulonglong), ("d", ctypes.c_ulonglong),
                    ("e", ctypes.c_ulonglong), ("f", ctypes.c_ulonglong), ("g", ctypes.c_ulonglong)]
    m = M()
    m.dwLength = ctypes.sizeof(M)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.d >> 20


# the live bot shares this host and the commit limit is nearly exhausted: never be the process that takes the last MB
for _ in range(60):
    if avail_commit_mb() >= 700:
        break
    time.sleep(5)
else:
    raise SystemExit(f"aborting: only {avail_commit_mb()} MB commit available")

import numpy as np
import pandas as pd

MAIN = Path("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot")
HERE = Path(__file__).resolve().parent
SYMS = ["BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN", "PEPETMN", "BNBTMN", "ARBTMN"]
MAJORS = SYMS[:5]
TAKER = 0.003
SLACK = 0.0005
COOLDOWN_MIN = 120
MAX_POS = 3
MAX_DROP_60S = 0.004
rng = np.random.default_rng(12345)


def wallex(sym):
    p = MAIN / "research" / "candles" / f"{sym}_1.csv"
    if not p.exists():
        p = MAIN / "research" / "premium" / f"wallex_{sym}_1m.csv"
    raw = pd.read_csv(p)
    df = pd.DataFrame({"c": raw.close.astype(float).to_numpy(), "v": raw.volume.astype(float).to_numpy()},
                      index=pd.to_datetime(raw.ts.astype("int64"), unit="s", utc=True))
    return df[~df.index.duplicated(keep="last")].sort_index()


def binance(base):
    s = pd.read_csv(MAIN / "research" / "premium" / f"binance_{base}USDT_1m.csv", index_col=0, parse_dates=True)["close"]
    if s.index.tz is None:
        s.index = s.index.tz_localize("UTC")
    return s.astype(float)


def live_spreads():
    """median spread and share of ticks with spread > 0.6% per market, live run since 16:45 (csv module, low memory)"""
    t0 = pd.Timestamp("2026-10-21 16:45", tz="UTC").timestamp()
    sp = {}
    with open(HERE / "ticks_snapshot.csv", encoding="utf-8", errors="ignore") as fh:
        for r in csv.DictReader(fh):
            try:
                if float(r["t"]) < t0:
                    continue
                sp.setdefault(r["symbol"], []).append(float(r["ask"]) / float(r["bid"]) - 1)
            except (TypeError, ValueError, KeyError, ZeroDivisionError):
                continue
    return ({s: statistics.median(v) for s, v in sp.items()}, {s: sum(1 for x in v if x > 0.006) / len(v) for s, v in sp.items()})


def build(sym, usdt, hs):
    g = binance(sym[:-3])
    w = wallex(sym)
    df = w.join((g * usdt).rename("fair"), how="inner").dropna()
    df = df[df.v > 0].copy()
    prem = df.c / df.fair - 1
    base = prem.rolling("180min", min_periods=30).median().shift(1)
    df["base"] = base
    df["hs"] = hs
    df["ask"], df["bid"] = df.c * (1 + hs), df.c * (1 - hs)
    df["buy_dev"] = df.ask / df.fair - 1 - base
    df["sell_dev"] = df.bid / df.fair - 1 - base
    mom = (g / g.shift(1) - 1).rename("mom")
    df = df.join(mom, how="left")
    return df


def simulate(frames, pol, *, cooldown=False, maxpos=False, momentum=False, fill="model", persist=False):
    """Event-driven over all markets in time order. pol[s] = (entry, stop, hold) or None (disabled)."""
    ev = []
    for s, df in frames.items():
        if pol.get(s) is None:
            continue
        ev.append(pd.DataFrame({"t": df.index, "s": s, "k": np.arange(len(df))}))
    ev = pd.concat(ev).sort_values(["t", "s"], kind="mergesort").to_numpy()
    arr = {s: {c: frames[s][c].to_numpy() for c in ("c", "ask", "bid", "buy_dev", "sell_dev", "hs", "mom")} | {"idx": frames[s].index}
           for s in frames if pol.get(s) is not None}
    open_, cd_until, trades = {}, {}, []
    for t, s, k in ev:
        a = arr[s]
        entry, stop, hold = pol[s]
        if s in open_:
            p = open_[s]
            if k <= p["k"]:
                continue
            bid = a["bid"][k] if fill == "model" else a["c"][k] * (1 - 2 * a["hs"][k])
            why = None
            if bid <= p["e"] * (1 - stop):
                why = "stop"
            elif a["sell_dev"][k] >= -SLACK and bid >= p["e"] * (1 + 2 * TAKER + SLACK):
                why = "target"
            elif (t - p["t"]).total_seconds() >= hold * 60:
                why = "time"
            if why:
                r = bid * (1 - TAKER) / (p["e"] * (1 + TAKER)) - 1
                trades.append({"symbol": s, "t": p["t"], "exit_t": t, "ret_pct": r * 100, "why": why})
                del open_[s]
                if cooldown and r < 0:
                    cd_until[s] = t + pd.Timedelta(minutes=COOLDOWN_MIN)
            continue
        bd = a["buy_dev"][k]
        if not np.isfinite(bd) or bd > -entry:
            continue
        if persist:
            if k == 0 or not np.isfinite(a["buy_dev"][k - 1]) or a["buy_dev"][k - 1] > -entry \
                    or (a["idx"][k] - a["idx"][k - 1]).total_seconds() > 120:
                continue
        if cooldown and s in cd_until and t < cd_until[s]:
            continue
        if maxpos and len(open_) >= MAX_POS:
            continue
        if momentum and np.isfinite(a["mom"][k]) and a["mom"][k] < -MAX_DROP_60S:
            continue
        e = a["ask"][k] if fill == "model" else a["c"][k] * (1 + 2 * a["hs"][k])
        # target test uses the engine's break-even on the real fill; with model fill e_sig == e
        open_[s] = {"k": k, "t": t, "e": e, "e_sig": e}
    return pd.DataFrame(trades)


def summ(tr, days):
    if tr is None or len(tr) == 0:
        return {"trades": 0}
    by = {}
    for s, g in tr.groupby("symbol"):
        by[s] = {"n": int(len(g)), "mean": round(float(g.ret_pct.mean()), 3), "sum": round(float(g.ret_pct.sum()), 2),
                 "win": round(float((g.ret_pct > 0).mean() * 100), 1), "exits": g.why.value_counts().to_dict()}
    return {"trades": int(len(tr)), "per_day": round(len(tr) / days, 3), "mean_ret_pct": round(float(tr.ret_pct.mean()), 3),
            "median_ret_pct": round(float(tr.ret_pct.median()), 3), "sum_ret_pct": round(float(tr.ret_pct.sum()), 2),
            "win_pct": round(float((tr.ret_pct > 0).mean() * 100), 1), "exits": tr.why.value_counts().to_dict(), "by_market": by}


def cluster_ci(tr, unit="D"):
    if len(tr) < 3:
        return None
    key = pd.to_datetime(tr.t).dt.floor(unit)
    groups = [g.ret_pct.to_numpy() for _, g in tr.groupby(key)]
    means = []
    for _ in range(20000):
        pick = rng.integers(0, len(groups), len(groups))
        x = np.concatenate([groups[i] for i in pick])
        means.append(x.mean())
    iid = [rng.choice(tr.ret_pct.to_numpy(), len(tr)).mean() for _ in range(20000)]
    return {"clusters": len(groups), "cluster_ci95": [round(float(np.quantile(means, 0.025)), 3), round(float(np.quantile(means, 0.975)), 3)],
            "iid_ci95": [round(float(np.quantile(iid, 0.025)), 3), round(float(np.quantile(iid, 0.975)), 3)],
            "p_mean_le_0_cluster": round(float((np.array(means) <= 0).mean()), 4)}


def main():
    st = json.loads((HERE / "state_snapshot.json").read_text(encoding="utf-8"))
    cur = st["policies"]
    live_spread, share_wide = live_spreads()
    ucd = wallex("USDTTMN").c
    usdt = ucd.reindex(pd.date_range(ucd.index[0], ucd.index[-1], freq="1min", tz="UTC")).ffill()
    frames = {s: build(s, usdt, live_spread[s] / 2) for s in SYMS}
    days = float(np.mean([(f.index[-1] - f.index[0]).total_seconds() / 86400 for f in frames.values()]))
    end_entries = pd.Timestamp("2026-10-23 17:50", tz="UTC")
    snap_t = pd.Timestamp(st["t"])
    rem = (end_entries - snap_t).total_seconds() / 86400

    pol_cur = {s: ((max(cur[s]["entry_dev"], 0.007), cur[s]["stop_pct"], cur[s]["max_hold_min"]) if cur[s]["enabled"] else None) for s in SYMS}
    w_cur = {s: 0.006 * cur[s]["size_factor"] / (cur[s]["stop_pct"] + 0.003 + 0.0025) for s in SYMS}
    out = {"snapshot_state_t": st["t"], "remaining_days": round(rem, 3), "days": round(days, 3),
           "live_half_spread_pct": {s: round(v * 50, 4) for s, v in live_spread.items()},
           "share_ticks_spread_over_0.6pct": {s: round(v * 100, 2) for s, v in share_wide.items()},
           "policy_used": {s: pol_cur[s] for s in SYMS}}

    variants = {
        "V0_as_author(no constraints)": dict(),
        "V1_cooldown120": dict(cooldown=True),
        "V2_cooldown+max3+momentum": dict(cooldown=True, maxpos=True, momentum=True),
        "V3_full_spread_fills": dict(fill="full"),
        "V4_V2+full_spread_fills": dict(cooldown=True, maxpos=True, momentum=True, fill="full"),
        "V5_persist2min": dict(persist=True),
        "V6_V2+persist2min": dict(cooldown=True, maxpos=True, momentum=True, persist=True),
    }
    res, trades_store = {}, {}
    for name, kw in variants.items():
        tr = simulate(frames, pol_cur, **kw)
        trades_store[name] = tr
        s = summ(tr, days)
        if len(tr):
            # spread filter thinning (fraction of live ticks with spread > 0.6% blocks that share of entries)
            keep = tr.symbol.map(lambda x: 1 - share_wide.get(x, 0)).to_numpy()
            n_eff = float(keep.sum())
            rate = n_eff / days
            acc_w = tr.symbol.map(w_cur).to_numpy()
            mean_acc_per_trade = float((tr.ret_pct.to_numpy() / 100 * acc_w * keep).sum() / max(keep.sum(), 1e-9))
            s["spread_thinned_trades"] = round(n_eff, 2)
            s["expected_trades_remaining"] = round(rate * rem, 2)
            s["expected_account_return_pct"] = round(rate * rem * mean_acc_per_trade * 100, 3)
            s["ci"] = cluster_ci(tr)
            s["ci_6h"] = cluster_ci(tr, "6h")
        res[name] = s
        tr.to_csv(HERE / f"vf_trades_{name.split('_')[0]}.csv", index=False)
    out["variants"] = res

    # walk-forward-style split for V2 (constraints on): first 4 days vs the rest
    t0 = min(f.index[0] for f in frames.values())
    split = t0 + pd.Timedelta(days=4)
    tr = trades_store["V2_cooldown+max3+momentum"]
    out["V2_split"] = {"first_4d": summ(tr[pd.to_datetime(tr.t) < split], 4.0),
                       "last": summ(tr[pd.to_datetime(tr.t) >= split], days - 4.0)}

    # momentum filter: share of unconstrained entries with Binance 1-min return < -0.4%
    tr0 = trades_store["V0_as_author(no constraints)"]
    moms = []
    for _, r in tr0.iterrows():
        moms.append(frames[r.symbol].loc[r.t, "mom"])
    out["V0_entries_with_1m_drop_over_0.4pct"] = int(np.sum(np.array(moms, dtype=float) < -MAX_DROP_60S))

    # E. zero-dislocation windows: 3.6 h windows in 7-day candles with no minute buy_dev <= -0.5% in ANY major
    grid = pd.date_range(t0.ceil("min"), max(f.index[-1] for f in frames.values()), freq="1min", tz="UTC")
    hit = pd.Series(False, index=grid)
    for s in MAJORS:
        b = frames[s].buy_dev
        hit = hit | (b <= -0.005).reindex(grid, fill_value=False)
    ch = np.concatenate([[0], np.cumsum(hit.to_numpy().astype(int))])
    res_e = {}
    for L in (1.83, 3.6):
        m = int(L * 60)
        cnt = ch[m:] - ch[:-m]
        res_e[f"{L}h_majors_any_min_le_-0.5%"] = {"windows": int(len(cnt)), "share_zero": round(float((cnt == 0).mean()), 4)}
    # per-market share of minutes <= -0.5% for majors in 7d candle data vs vol-matched hours
    bvol = {}
    for s in MAJORS + ["PEPETMN", "ARBTMN"]:
        c7 = binance(s[:-3])
        r = np.log(c7).diff()
        hv = r.groupby(r.index.floor("h")).std()
        bt = pd.read_csv(HERE / "binance_today" / f"{s[:-3]}USDT_1m.csv")
        ct = pd.Series(bt.close.astype(float).to_numpy(), index=pd.to_datetime(bt.open_time_ms, unit="ms", utc=True))
        rt = np.log(ct).diff()
        today = rt[rt.index >= pd.Timestamp("2026-10-21 14:15", tz="UTC")]
        today_h = today.groupby(today.index.floor("h")).std().dropna()
        lo, hi = float(today_h.min()), float(today_h.max())
        bd = frames[s].buy_dev.dropna()
        hv_m = hv.reindex(bd.index.floor("h")).to_numpy()
        matched = (hv_m >= lo) & (hv_m <= hi)
        bvol[s] = {"today_hourly_std_range_pct": [round(lo * 100, 4), round(hi * 100, 4)],
                   "7d_share_min_le_-0.5_all_pct": round(float((bd <= -0.005).mean() * 100), 3),
                   "7d_share_min_le_-0.5_volmatched_pct": round(float((bd[matched] <= -0.005).mean() * 100), 3) if matched.any() else None,
                   "volmatched_minutes": int(matched.sum()),
                   "7d_share_min_le_-0.7_volmatched_pct": round(float((bd[matched] <= -0.007).mean() * 100), 3) if matched.any() else None}
    res_e["vol_matched"] = bvol
    out["calm_check"] = res_e

    (HERE / "verify_forecast.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print("remaining days", round(rem, 3), "days", round(days, 3))
    print("policy", pol_cur)
    for k, v in res.items():
        print(f"\n[{k}] trades={v.get('trades')} /day={v.get('per_day')} mean={v.get('mean_ret_pct')} med={v.get('median_ret_pct')} "
              f"sum={v.get('sum_ret_pct')} win={v.get('win_pct')} exits={v.get('exits')}")
        print(f"   spread-thinned={v.get('spread_thinned_trades')} E[N rem]={v.get('expected_trades_remaining')} "
              f"E[acct ret]={v.get('expected_account_return_pct')}% ci_day={v.get('ci')} ci_6h={v.get('ci_6h')}")
        for s, m in (v.get("by_market") or {}).items():
            print(f"     {s:8} {m}")
    print("\nV2 split:", json.dumps(out["V2_split"], default=str)[:800])
    print("V0 entries with 1m drop > 0.4%:", out["V0_entries_with_1m_drop_over_0.4pct"])
    print("\ncalm check:", json.dumps(res_e, indent=1))


if __name__ == "__main__":
    main()
