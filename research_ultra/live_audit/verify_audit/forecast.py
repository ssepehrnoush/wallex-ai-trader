"""Forecast for the live bot: trades and net return for the rest of the window, from the 7-day
1-minute backtest of the SAME rule (scripts/bt_fairvalue.py method), and a check of whether today
is unusually calm.

Local files only (MAIN/research/candles, MAIN/research/premium, MAIN/runs/*/ticks.csv); Binance klines
for today come from fetch_binance.py. Never calls Wallex. Outputs forecast.json and bt_trades_*.csv here.

Method (same as scripts/bt_fairvalue.py):
  fair = Binance 1m close x Wallex USDTTMN 1m close (ffill); only Wallex minutes with volume > 0;
  ask/bid = close x (1 +/- half spread); baseline = rolling 180-min median premium, shift(1) (no look-ahead);
  enter when buy_dev <= -entry; exit target / stop / time; both legs taker 0.3%.
Variants:
  repro    : majors, 0.7%, shadow_v1 spreads, bt exit rule  -> must equal runs/backtests/fv_sweep_0.007_0.004
  bt_orig  : all 8 TMN, 0.7%, original spreads (shadow_v1 + the fv_extra_tmn snapshot), bt exit rule
  live_rule: all 8 TMN, the LIVE policy (0.7%, stop 0.9%, hold 25; ARB stop 0.8% hold 15), target only when
             bid >= entry x (1 + 2 x taker + 0.05%) as engine._break_even, spreads = live_main median
"""
import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import io
import json
from pathlib import Path

import numpy as np
import pandas as pd

MAIN = Path("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot")
HERE = Path(__file__).resolve().parent
SYMS = ["BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN", "PEPETMN", "BNBTMN", "ARBTMN"]
MAJORS = SYMS[:5]
TAKER = 0.003
ENTRY = 0.007
END_ENTRIES = pd.Timestamp("2026-10-23 17:50:00", tz="UTC")
EQUITY = 5_000_000
NOTIONAL = EQUITY * 0.006 * 0.4 / (0.009 + 0.003 + 0.0025)     # 827,586 TMN at size_factor 0.4
rng = np.random.default_rng(20261021)


def snapshot_csv(p: Path) -> pd.DataFrame:
    raw = p.read_bytes().decode("utf-8", errors="ignore")
    t = pd.read_csv(io.StringIO(raw[: raw.rfind("\n") + 1]))
    for c in ("t", "bid", "ask", "buy_dev"):
        t[c] = pd.to_numeric(t[c], errors="coerce")
    return t.dropna(subset=["t", "bid", "ask", "buy_dev"])


def wallex(sym: str) -> pd.DataFrame:
    p = MAIN / "research" / "candles" / f"{sym}_1.csv"
    if not p.exists():
        p = MAIN / "research" / "premium" / f"wallex_{sym}_1m.csv"
    raw = pd.read_csv(p)
    df = pd.DataFrame({"c": raw.close.astype(float).to_numpy(), "v": raw.volume.astype(float).to_numpy()},
                      index=pd.to_datetime(raw.ts.astype("int64"), unit="s", utc=True))
    return df[~df.index.duplicated(keep="last")].sort_index()


def binance(base: str) -> pd.Series:
    s = pd.read_csv(MAIN / "research" / "premium" / f"binance_{base}USDT_1m.csv", index_col=0, parse_dates=True)["close"]
    if s.index.tz is None:
        s.index = s.index.tz_localize("UTC")
    return s


def frame(sym: str, usdt: pd.Series, hs: float) -> pd.DataFrame:
    g = binance(sym[:-3])
    w = wallex(sym)
    fair = g * usdt
    df = w.join(fair.rename("fair"), how="inner").dropna()
    df = df[df.v > 0].copy()
    df["ask"], df["bid"] = df.c * (1 + hs), df.c * (1 - hs)
    prem = df.c / df.fair - 1
    base = prem.rolling("180min", min_periods=30).median().shift(1)
    df["buy_dev"] = df.ask / df.fair - 1 - base
    df["sell_dev"] = df.bid / df.fair - 1 - base
    return df


def simulate(df: pd.DataFrame, sym: str, entry: float, stop: float, hold: float, slack: float, live_rule: bool):
    bd, sd = df.buy_dev.to_numpy(), df.sell_dev.to_numpy()
    ask, bid, idx = df.ask.to_numpy(), df.bid.to_numpy(), df.index
    i, out = 0, []
    while i < len(df) - 1:
        if np.isfinite(bd[i]) and bd[i] <= -entry:
            e = ask[i]
            tgt = e * (1 + 2 * TAKER + 0.0005) if live_rule else None
            j, why = i + 1, "end"
            while j < len(df):
                if bid[j] <= e * (1 - stop):
                    why = "stop"
                    break
                if sd[j] >= -slack and (bid[j] >= tgt if live_rule else bid[j] > e):
                    why = "target"
                    break
                if (idx[j] - idx[i]).total_seconds() >= hold * 60:
                    why = "time"
                    break
                j += 1
            j = min(j, len(df) - 1)
            r = bid[j] * (1 - TAKER) / (e * (1 + TAKER)) - 1
            out.append({"symbol": sym, "t": idx[i], "ret_pct": r * 100, "why": why,
                        "minutes": (idx[j] - idx[i]).total_seconds() / 60, "entry_dev_pct": bd[i] * 100})
            i = j + 1
        else:
            i += 1
    return out


def summarize(tr: pd.DataFrame, days: float) -> dict:
    if tr.empty:
        return {"trades": 0, "per_day": 0.0, "mean_ret_pct": None, "sum_ret_pct": 0.0, "win_pct": None, "exits": {}}
    return {"trades": int(len(tr)), "per_day": round(len(tr) / days, 3), "mean_ret_pct": round(float(tr.ret_pct.mean()), 3),
            "median_ret_pct": round(float(tr.ret_pct.median()), 3),
            "sum_ret_pct": round(float(tr.ret_pct.sum()), 3), "win_pct": round(float((tr.ret_pct > 0).mean() * 100), 1),
            "exits": tr.why.value_counts().to_dict()}


def poisson_quantiles(mu: float, qs=(0.05, 0.5, 0.95)) -> dict:
    """Quantiles of Poisson(mu) without scipy (cumulative pmf)."""
    k, p, cdf, out = 0, np.exp(-mu), 0.0, {}
    todo = list(qs)
    while todo:
        cdf += p
        while todo and cdf >= todo[0]:
            out[f"q{int(todo.pop(0) * 100)}"] = k
        k += 1
        p *= mu / k
        if k > 10_000:
            break
    out["p_zero"] = round(float(np.exp(-mu)), 4)
    return out


def main():
    now = pd.Timestamp.now(tz="UTC").floor("min")
    rem_days = (END_ENTRIES - now).total_seconds() / 86400
    # spreads
    sweep = json.loads((MAIN / "runs/backtests/fv_sweep_0.007_0.004/summary.json").read_text())
    extra = json.loads((MAIN / "runs/backtests/fv_extra_tmn/summary.json").read_text())
    orig_spread = {s: extra["spreads_from_live"].get(s, sweep["spreads_from_live"].get(s)) / 100 for s in SYMS}
    for s in MAJORS:
        orig_spread[s] = sweep["spreads_from_live"][s] / 100
    live = snapshot_csv(MAIN / "runs/live_main/ticks.csv")
    live = live[live.t >= pd.Timestamp("2026-10-21 16:45", tz="UTC").timestamp()]
    live_spread = ((live.ask / live.bid - 1).groupby(live.symbol).median()).to_dict()

    ucd = wallex("USDTTMN").c
    usdt = ucd.reindex(pd.date_range(ucd.index[0], ucd.index[-1], freq="1min", tz="UTC")).ffill()
    live_pol = {s: (0.009, 25) for s in SYMS} | {"ARBTMN": (0.008, 15)}

    # the policy in force right now (latest AI review, as written to state.json every minute)
    st = json.loads((MAIN / "runs/live_main/state.json").read_text(encoding="utf-8"))
    cur = st["policies"]
    cur_enabled = [s for s in SYMS if cur[s]["enabled"]]
    weight = {s: EQUITY * 0.006 * cur[s]["size_factor"] / (cur[s]["stop_pct"] + 0.003 + 0.0025) / EQUITY for s in SYMS}

    variants, frames_live = {}, {}
    all_trades = {}
    for name, syms, spreads, rule in (("repro", MAJORS, orig_spread, False), ("bt_orig", SYMS, orig_spread, False),
                                      ("live_rule", SYMS, live_spread, True), ("current_policy", cur_enabled, live_spread, True)):
        trades, days = [], {}
        for s in syms:
            df = frame(s, usdt, spreads[s] / 2)
            if name == "live_rule":
                frames_live[s] = df
            stop, hold = live_pol[s] if rule else (0.009, 25)
            entry = ENTRY
            if name == "current_policy":
                entry, stop, hold = max(cur[s]["entry_dev"], ENTRY), cur[s]["stop_pct"], cur[s]["max_hold_min"]
            trades += simulate(df, s, entry, stop, hold, 0.0005, rule)
            days[s] = (df.index[-1] - df.index[0]).total_seconds() / 86400
        tr = pd.DataFrame(trades)
        all_trades[name] = tr
        tr.to_csv(HERE / f"bt_trades_{name}.csv", index=False)
        per = {s: summarize(tr[tr.symbol == s] if len(tr) else tr, days[s]) | {"half_spread_pct": round(spreads[s] * 50, 3),
                                                                               "days": round(days[s], 2)} for s in syms}
        variants[name] = {"markets": per, "pooled": summarize(tr, float(np.mean(list(days.values()))))}

    # reproduction check against the stored sweep
    repro_ok = {s: (variants["repro"]["markets"][s]["trades"], sweep["markets"][s]["trades"]) for s in MAJORS}

    # walk-forward style stability: first 4 days vs last 3 days (rate and mean), live_rule variant
    tr = all_trades["live_rule"]
    t0 = min(f.index[0] for f in frames_live.values())
    split = t0 + pd.Timedelta(days=4)
    t1 = max(f.index[-1] for f in frames_live.values())
    halves = {}
    for lab, m in (("first_4d", tr.t < split), ("last_3d", tr.t >= split)):
        d = 4.0 if lab == "first_4d" else (t1 - split).total_seconds() / 86400
        halves[lab] = summarize(tr[m], d) | {"days": round(d, 2)}
    by_day = tr.groupby(tr.t.dt.date).size().to_dict() if len(tr) else {}

    # forecast: the policy in force now, 0.7% on the markets enabled now (BNB off), and all 8 at 0.7%
    enabled_now = cur_enabled
    span_days = {s: variants["live_rule"]["markets"][s]["days"] for s in SYMS}
    fc = {}
    for lab, syms, vnames in (("current_policy", cur_enabled, ("current_policy",)), ("enabled_now", enabled_now, ("live_rule", "bt_orig")),
                              ("all_8", SYMS, ("live_rule", "bt_orig"))):
        for vname in vnames:
            v = variants[vname]["markets"]
            n = {s: v[s]["trades"] for s in syms}
            rate = sum(n[s] / span_days[s] for s in syms)
            mu = rate * rem_days
            # predictive: lambda_s ~ Gamma(n+0.5, 1/days) (Jeffreys), then Poisson over the remaining days
            lam = sum(rng.gamma(n[s] + 0.5, 1 / span_days[s], 200_000) for s in syms)
            N = rng.poisson(lam * rem_days)
            pool = all_trades[vname]
            if len(pool):
                pool = pool[pool.symbol.isin(syms)]
                # account weight per trade: notional / equity (current policy: per-market size and stop; else 0.4 / 0.9%)
                w = pool.symbol.map(weight).to_numpy() if vname == "current_policy" else np.full(len(pool), NOTIONAL / EQUITY)
                acc_pool = pool.ret_pct.to_numpy() / 100 * w
                pool = pool.ret_pct.to_numpy() / 100
            else:
                pool, acc_pool = np.array([]), np.array([])
            if len(pool):
                # account return: returns (times each trade's notional share) bootstrapped from the backtest trades
                acc = np.array([rng.choice(acc_pool, k).sum() if k else 0.0 for k in N[:50_000]]) * 100
                ret = {"mean_pct": round(float(acc.mean()), 3), "q5_pct": round(float(np.quantile(acc, 0.05)), 3),
                       "q50_pct": round(float(np.quantile(acc, 0.5)), 3), "q95_pct": round(float(np.quantile(acc, 0.95)), 3),
                       "mean_tmn": round(float(acc.mean() / 100 * EQUITY)),
                       "p_negative": round(float((acc < 0).mean()), 3)}
            else:
                ret = {}
            fc[f"{lab}/{vname}"] = {"markets": syms, "bt_trades": sum(n.values()), "rate_per_day": round(rate, 3),
                                    "expected_trades": round(mu, 2), "poisson_fixed_rate": poisson_quantiles(mu),
                                    "predictive_q5_q50_q95": [int(np.quantile(N, q)) for q in (0.05, 0.5, 0.95)],
                                    "predictive_p_zero": round(float((N == 0).mean()), 3),
                                    "mean_net_ret_per_trade_pct": round(float(pool.mean() * 100), 3) if len(pool) else None,
                                    "account_return": ret}

    # is today calm? (a) chance that a window as long as the live run has zero backtest entries
    ent = all_trades["live_rule"]
    ent = ent[ent.symbol.isin(enabled_now)].t.sort_values().to_numpy() if len(ent) else np.array([])
    grid = pd.date_range(t0, t1, freq="1min", tz="UTC")
    live_h = (live.t.max() - live.t.min()) / 3600
    zero_win = {}
    for L in (live_h, 3.8, 6.0, 12.0, 24.0):
        ends = grid + pd.Timedelta(hours=L)
        ok = ends <= t1
        cnt = np.searchsorted(ent, ends[ok].to_numpy()) - np.searchsorted(ent, grid[ok].to_numpy())
        zero_win[f"{L:.2f}h"] = round(float((cnt == 0).mean()), 3)

    # (b) dev distribution: 7-day minute dev (live spreads) vs live ticks (last tick per minute, and minute minimum)
    dist = {}
    live["m"] = pd.to_datetime(live.t, unit="s", utc=True).dt.floor("min")
    for s in SYMS:
        d7 = frames_live[s].buy_dev.dropna()
        g = live[live.symbol == s]
        last = g.groupby("m").buy_dev.last()
        mn = g.groupby("m").buy_dev.min()
        row = {}
        for lab, x in (("7d_minutes", d7), ("live_last_tick_per_min", last), ("live_min_per_min", mn)):
            row[lab] = {"n": int(len(x)), "p1_pct": round(float(x.quantile(0.01) * 100), 3),
                        "p50_pct": round(float(x.median() * 100), 3),
                        **{f"share<=-{k}%": round(float((x <= -k / 100).mean() * 100), 3) for k in (0.3, 0.5, 0.7)}}
        # per-day share of minutes <= -0.5% in the 7 days, to show the day-to-day spread
        day = (d7 <= -0.005).groupby(d7.index.date).mean() * 100
        row["7d_daily_share<=-0.5%"] = {str(k): round(float(v), 3) for k, v in day.items()}
        dist[s] = row

    # (c) global volatility today vs the 7 days: hourly std of Binance 1m log returns and hourly range
    vol = {}
    for s in SYMS:
        b = s[:-3]
        c7 = binance(b)
        r7 = np.log(c7).diff().dropna()
        h7 = r7.groupby(r7.index.floor("h")).agg(["std", "count"])
        h7 = h7[h7["count"] >= 50]["std"] * 100
        bt = pd.read_csv(HERE / "binance_today" / f"{b}USDT_1m.csv")
        ct = pd.Series(bt.close.astype(float).to_numpy(), index=pd.to_datetime(bt.open_time_ms, unit="ms", utc=True))
        rt = np.log(ct).diff().dropna()
        rt_live = rt[rt.index >= pd.Timestamp("2026-10-21 16:45", tz="UTC")]
        ht = rt_live.groupby(rt_live.index.floor("h")).agg(["std", "count"])
        ht = ht[ht["count"] >= 30]["std"] * 100
        live_std = float(rt_live.std() * 100)
        vol[s] = {"7d_hourly_std_median_pct": round(float(h7.median()), 4),
                  "7d_hourly_std_p25_pct": round(float(h7.quantile(0.25)), 4),
                  "7d_hourly_std_p75_pct": round(float(h7.quantile(0.75)), 4),
                  "live_1m_std_pct": round(live_std, 4),
                  "live_percentile_vs_7d_hours": round(float((h7 < live_std).mean() * 100), 1),
                  "live_hours": {str(k)[11:16]: round(float(v), 4) for k, v in ht.items()}}
    # (d) are backtest entries tied to global moves? |15-min Binance return| at entry vs all minutes
    move = {}
    for s in SYMS:
        c7 = binance(s[:-3])
        r15 = (c7 / c7.shift(15) - 1).abs() * 100
        e = all_trades["live_rule"]
        et = e[e.symbol == s].t if len(e) else []
        at = r15.reindex(pd.DatetimeIndex(et)).dropna() if len(et) else pd.Series(dtype=float)
        move[s] = {"all_minutes_median_abs15_pct": round(float(r15.median()), 3),
                   "entry_minutes_median_abs15_pct": round(float(at.median()), 3) if len(at) else None, "entries": int(len(at))}

    out = {"now_utc": str(now), "entries_allowed_until": str(END_ENTRIES), "remaining_days": round(rem_days, 3),
           "notional_tmn_per_trade": round(NOTIONAL), "spreads_original_pct": {k: round(v * 100, 3) for k, v in orig_spread.items()},
           "spreads_live_median_pct": {k: round(v * 100, 3) for k, v in live_spread.items()},
           "repro_check_trades(mine, stored)": repro_ok, "variants": variants, "live_rule_halves": halves,
           "live_rule_trades_by_day": {str(k): int(v) for k, v in by_day.items()},
           "forecast": fc, "share_of_windows_with_zero_entries_enabled_now": zero_win,
           "dev_distribution": dist, "global_volatility": vol, "entries_vs_global_moves": move,
           "live_hours": round(live_h, 2), "enabled_now": enabled_now, "state_json_t": st.get("t"),
           "current_policy": {s: {k: cur[s][k] for k in ("enabled", "entry_dev", "stop_pct", "max_hold_min", "size_factor")} for s in SYMS},
           "notional_share_of_equity_current": {s: round(w, 4) for s, w in weight.items()}}
    (HERE / "forecast.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")

    print("now", now, "remaining days", round(rem_days, 3), "enabled now", enabled_now)
    print("repro (mine, stored):", repro_ok)
    for name, v in variants.items():
        print(f"\n[{name}] pooled: {v['pooled']}")
        for s, m in v["markets"].items():
            print(f"  {s:8} hs={m['half_spread_pct']:.3f}% days={m['days']} trades={m['trades']} /day={m['per_day']} "
                  f"mean={m['mean_ret_pct']} sum={m['sum_ret_pct']} win={m['win_pct']} exits={m['exits']}")
    print("\nlive_rule halves:", halves)
    print("live_rule trades by day:", out["live_rule_trades_by_day"])
    print("\nforecast:")
    for k, f in fc.items():
        print(f"  {k}: bt_trades={f['bt_trades']} rate/day={f['rate_per_day']} E[N]={f['expected_trades']} "
              f"fixed-rate {f['poisson_fixed_rate']} predictive q5/q50/q95={f['predictive_q5_q50_q95']} P0={f['predictive_p_zero']} "
              f"mean/trade={f['mean_net_ret_per_trade_pct']} account={f['account_return']}")
    print("\nshare of windows with zero entries (enabled markets, live_rule):", zero_win)
    print("\ndev distribution (share of minutes, %):")
    for s, r in dist.items():
        a, b, c = r["7d_minutes"], r["live_last_tick_per_min"], r["live_min_per_min"]
        print(f"  {s:8} 7d p1={a['p1_pct']:.3f} <=-0.5:{a['share<=-0.5%']:.3f} <=-0.7:{a['share<=-0.7%']:.3f} | "
              f"live(last) p1={b['p1_pct']:.3f} <=-0.5:{b['share<=-0.5%']:.3f} | live(min) p1={c['p1_pct']:.3f} <=-0.5:{c['share<=-0.5%']:.3f} <=-0.7:{c['share<=-0.7%']:.3f}")
    print("\nglobal volatility (hourly std of 1m returns, %):")
    for s, r in vol.items():
        print(f"  {s:8} 7d median={r['7d_hourly_std_median_pct']} IQR=[{r['7d_hourly_std_p25_pct']},{r['7d_hourly_std_p75_pct']}] "
              f"live={r['live_1m_std_pct']} pct-rank={r['live_percentile_vs_7d_hours']}")
    print("\nentries vs global moves:", move)


if __name__ == "__main__":
    main()
