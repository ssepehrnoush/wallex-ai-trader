"""Part B on the live order-book ticks (2.5 s): how jumpy is the USDT/TMN leg inside `fair`, do several
toman markets look "cheap" at the same instant, and is that caused by the USDT/TMN leg?

USDT/TMN mid at each tick:
  * shadow_v2..v6 also logged the Wallex USDT markets, whose fair is the Binance mid alone, so
    u = fair(XTMN) / fair(XUSDT) exactly (same timestamp, same Binance mid).
  * live_main has only toman markets: u is estimated as the median over BTC ETH SOL XRP DOGE BNB of
    fair(XTMN) / Binance 1-second close (data/binance_<B>_1s.csv). The estimator is validated on the
    shadow runs, where the exact u is known.

Reads MAIN/runs/*/ticks.csv read-only (live_main is a snapshot; a partial last line is skipped).

  python research_ultra/multi_ref_fair/ticks_usdt_leg.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)
MAIN = Path("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot")
RUNS = ["shadow_v2", "shadow_v3", "shadow_v4", "shadow_v5", "shadow_v6", "live_main"]
SEC_BASES = ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB"]
TAKER, ENTRY, SLACK, STOP = 0.003, 0.007, 0.0005, 0.009
HOLD = {"ARBTMN": 15}


def load(run: str) -> pd.DataFrame:
    t = pd.read_csv(MAIN / "runs" / run / "ticks.csv", on_bad_lines="skip")
    for c in ("t", "bid", "ask", "bid_qty", "ask_qty", "fair", "baseline", "buy_dev", "sell_dev"):
        t[c] = pd.to_numeric(t[c], errors="coerce")
    t = t.dropna(subset=["t", "bid", "ask", "fair", "baseline", "buy_dev", "sell_dev"])
    t["run"] = run
    return t


def sec_close(base: str) -> pd.Series:
    raw = pd.read_csv(DATA / f"binance_{base}_1s.csv")
    s = pd.Series(raw["close"].astype(float).to_numpy(), index=raw["ts"].astype("int64").to_numpy())
    s = s[~s.index.duplicated(keep="last")].sort_index()
    full = np.arange(s.index[0], s.index[-1] + 1)
    return s.reindex(full).ffill()


def u_estimate(tm: pd.DataFrame, g1s: dict, lag: int) -> pd.Series:
    """median over bases of fair(XTMN)/binance_1s_close(X) at second floor(t)+lag."""
    est = []
    for b, s in g1s.items():
        x = tm[tm.symbol == f"{b}TMN"]
        sec = np.floor(x.t.to_numpy()).astype("int64") + lag
        ok = (sec >= s.index[0]) & (sec <= s.index[-1])
        g = np.full(len(x), np.nan)
        g[ok] = s.reindex(sec[ok]).to_numpy()
        est.append(pd.Series(x.fair.to_numpy() / g, index=x.t.to_numpy(), name=b))
    return pd.concat(est, axis=1).median(axis=1)


def roll_med(s: pd.Series, seconds: int) -> pd.Series:
    """causal time-window median (window ends at the current tick)."""
    x = pd.Series(s.to_numpy(), index=pd.to_datetime(s.index, unit="s"))
    return pd.Series(x.rolling(f"{seconds}s", min_periods=3).median().to_numpy(), index=s.index)


def sim_ticks(x: pd.DataFrame, dev_col: str, sell_col: str, hold_min: float) -> list[dict]:
    """bt_fairvalue exits on 2.5 s ticks: target (sell_dev >= -slack and bid > entry), stop, time."""
    t, ask, bid = x.t.to_numpy(), x.ask.to_numpy(), x.bid.to_numpy()
    bd, sd, spr = x[dev_col].to_numpy(), x[sell_col].to_numpy(), (x.ask / x.bid - 1).to_numpy()
    gap = np.r_[0, np.diff(t)]
    i, out = 0, []
    while i < len(x) - 1:
        if bd[i] <= -ENTRY and spr[i] <= 0.006:
            e, j, why = ask[i], i + 1, "end"
            while j < len(x):
                if gap[j] > 120:          # run restarted / data gap: close at the last seen bid
                    j -= 1; why = "gap"; break
                if bid[j] <= e * (1 - STOP):
                    why = "stop"; break
                if sd[j] >= -SLACK and bid[j] > e:
                    why = "target"; break
                if t[j] - t[i] >= hold_min * 60:
                    why = "time"; break
                j += 1
            j = min(j, len(x) - 1)
            out.append({"t": t[i], "ret_pct": (bid[j] * (1 - TAKER) / (e * (1 + TAKER)) - 1) * 100, "why": why,
                        "secs": t[j] - t[i], "entry_dev_pct": bd[i] * 100})
            i = j + 1
        else:
            i += 1
    return out


def main():
    ticks = pd.concat([load(r) for r in RUNS], ignore_index=True)
    tm = ticks[ticks.symbol.str.endswith("TMN")].copy()
    us = ticks[ticks.symbol.str.endswith("USDT")]
    g1s = {b: sec_close(b) for b in SEC_BASES if (DATA / f"binance_{b}_1s.csv").exists()}

    # exact u on shadow runs
    m = tm.assign(base=tm.symbol.str[:-3]).merge(
        us.assign(base=us.symbol.str[:-4])[["t", "base", "fair"]].rename(columns={"fair": "g"}), on=["t", "base"], how="inner")
    u_exact = (m.fair / m.g).groupby(m.t).median()

    # estimator calibration on shadow (choose the lag that best reproduces the exact u)
    shadow_tm = tm[tm.run != "live_main"]
    calib = {}
    # the recording PC clock turned out ~40 s behind exchange time, so scan a wide lag range
    for lag in range(-2, 46):
        est = u_estimate(shadow_tm, g1s, lag)
        err = (est.reindex(u_exact.index) / u_exact - 1).dropna()
        calib[lag] = {"n": int(len(err)), "median_abs_err_pct": round(float(err.abs().median() * 100), 4),
                      "p95_abs_err_pct": round(float(err.abs().quantile(0.95) * 100), 4),
                      "p99_abs_err_pct": round(float(err.abs().quantile(0.99) * 100), 4)}
    best = min(calib, key=lambda k: calib[k]["p95_abs_err_pct"])
    calib = {k: v for k, v in calib.items() if k in (0, 20) or abs(k - best) <= 2}

    live_tm = tm[tm.run == "live_main"]
    u_live = u_estimate(live_tm, g1s, best)
    # one u per timestamp: exact for shadow, estimate for live
    U = pd.concat([u_exact, u_live]).sort_index()
    U = U[~U.index.duplicated()]
    run_of_t = tm.drop_duplicates("t").set_index("t").run
    res_runs = {}
    U_med = pd.Series(dtype=float)
    for r in RUNS:
        ts = run_of_t[run_of_t == r].index
        u = U.reindex(ts).dropna()
        if len(u) < 10:
            continue
        med = roll_med(u, 300)
        U_med = pd.concat([U_med, med])
        dv = (u / med - 1).dropna()
        # tick-to-tick jumps of the leg
        jump = (u / u.shift(1) - 1).dropna()
        res_runs[r] = {"ticks": int(len(u)), "exact": r != "live_main",
                       "abs_u_vs_med5_gt_pct": {str(k): round(float((dv.abs() > k / 100).mean() * 100), 3) for k in (0.05, 0.1, 0.2)},
                       "max_abs_u_vs_med5_pct": round(float(dv.abs().max() * 100), 3),
                       "abs_tick_jump_gt_0.1_pct_count": int((jump.abs() > 0.001).sum()),
                       "max_abs_tick_jump_pct": round(float(jump.abs().max() * 100), 3)}
    U_med = U_med[~U_med.index.duplicated()]

    tm["u"] = tm.t.map(U)
    tm["u_med5"] = tm.t.map(U_med)
    tm["u_dev_pct"] = (tm.u / tm.u_med5 - 1) * 100
    # robust-u version of the deviations (baseline kept as recorded, to isolate the leg)
    fr = tm.fair * (tm.u_med5 / tm.u)
    tm["buy_dev_rob"] = np.where(fr.notna(), tm.ask / fr - 1 - tm.baseline, tm.buy_dev)
    tm["sell_dev_rob"] = np.where(fr.notna(), tm.bid / fr - 1 - tm.baseline, tm.sell_dev)

    # ---- simultaneous "discounts": k+ toman markets with buy_dev <= -thr at the same tick ----
    piv_mid = tm.assign(mid=(tm.bid + tm.ask) / 2).pivot_table(index="t", columns="symbol", values="mid")
    piv_fair = tm.pivot_table(index="t", columns="symbol", values="fair")
    piv_bd = tm.pivot_table(index="t", columns="symbol", values="buy_dev")
    piv_sd = tm.pivot_table(index="t", columns="symbol", values="sell_dev")

    def episodes_for(k: int, thr: float):
        flag = tm[tm.buy_dev <= -thr]
        cnt = flag.groupby("t").symbol.count()
        ev_t = cnt[cnt >= k].index.to_numpy()
        eps, cur = [], []
        for t in ev_t:
            if cur and t - cur[-1] > 30:
                eps.append(cur); cur = []
            cur.append(t)
        if cur:
            eps.append(cur)
        rows = []
        for ep in eps:
            t0 = ep[0]
            syms = flag[flag.t == t0].symbol.tolist()
            row = {"t0": str(pd.Timestamp(t0, unit="s", tz="UTC")), "run": run_of_t.get(t0), "ticks": len(ep),
                   "markets": syms, "n_markets": len(syms),
                   "u_dev_pct_at_t0": round(float(U.get(t0, np.nan) / U_med.get(t0, np.nan) * 100 - 100), 3),
                   "min_buy_dev_pct": round(float(piv_bd.loc[t0, syms].min() * 100), 3)}
            # was Binance alone off vs the 3-exchange median in that minute (exchange time = local + lag)?
            mt = (int(t0) + best) // 60 * 60
            bvm = {}
            for s_ in syms:
                closes = {}
                for ex in ("binance", "okx", "bybit"):
                    p = DATA / f"{ex}_{s_[:-3]}_1m.csv"
                    if p.exists():
                        d = pd.read_csv(p).set_index("ts")["close"]
                        closes[ex] = [float(d.get(mt - 60, np.nan)), float(d.get(mt, np.nan))]
                if "binance" in closes:
                    for k_, lab in ((0, "prev_min"), (1, "this_min")):
                        med_ = float(np.nanmedian([v[k_] for v in closes.values()]))
                        bvm[f"{s_}_{lab}"] = round((closes["binance"][k_] / med_ - 1) * 100, 4)
            row["binance_vs_median_pct"] = bvm
            for h in (60, 300):
                later = piv_mid.index[piv_mid.index >= t0 + h]
                if not len(later):
                    continue
                t1 = later[0]
                row[f"mid_chg_{h}s_pct"] = round(float(((piv_mid.loc[t1, syms] / piv_mid.loc[t0, syms]) - 1).mean() * 100), 3)
                row[f"fair_chg_{h}s_pct"] = round(float(((piv_fair.loc[t1, syms] / piv_fair.loc[t0, syms]) - 1).mean() * 100), 3)
                row[f"sell_dev_{h}s_pct"] = round(float(piv_sd.loc[t1, syms].mean() * 100), 3)
            rows.append(row)
        return rows

    ep_sets = {f"{k}plus_le_{thr * 100:.2f}pct": episodes_for(k, thr) for k, thr in ((3, 0.004), (2, 0.004), (3, 0.0025))}
    ep_rows = [dict(r, set=name) for name, rows in ep_sets.items() for r in rows]
    pd.DataFrame(ep_rows).to_csv(OUT / "simultaneous_discount_episodes.csv", index=False)

    # ---- tick-level replay of the current rule: recorded fair vs robust-u fair ----
    sims = {"recorded": [], "robust_u": []}
    for (r, s), x in tm.sort_values("t").groupby(["run", "symbol"]):
        for name, (dc, sc) in {"recorded": ("buy_dev", "sell_dev"), "robust_u": ("buy_dev_rob", "sell_dev_rob")}.items():
            for tr in sim_ticks(x, dc, sc, HOLD.get(s, 25)):
                sims[name].append({"run": r, "symbol": s, **tr})
    sim_res = {}
    for k, v in sims.items():
        d = pd.DataFrame(v)
        d.to_csv(OUT / f"tick_trades_{k}.csv", index=False)
        sim_res[k] = {"n": int(len(d)), "sum_pct": round(float(d.ret_pct.sum()), 3) if len(d) else 0,
                      "mean_pct": round(float(d.ret_pct.mean()), 3) if len(d) else None,
                      "exits": d.why.value_counts().to_dict() if len(d) else {},
                      "list": [{"run": x["run"], "symbol": x["symbol"], "t": str(pd.Timestamp(x["t"], unit="s", tz="UTC")),
                                "ret_pct": round(x["ret_pct"], 3), "why": x["why"], "secs": round(x["secs"]),
                                "entry_dev_pct": round(x["entry_dev_pct"], 3)} for x in v]}

    # how far below the threshold do ticks get, raw vs robust (signal counts per tick)
    sig = {}
    for part, x in (("shadow_exact_u", tm[tm.run != "live_main"]), ("live_main_estimated_u", tm[tm.run == "live_main"])):
        sig[part] = {f"ticks_buy_dev_le_{k}": {"recorded": int((x.buy_dev <= -k / 100).sum()),
                                               "robust_u": int((x.buy_dev_rob <= -k / 100).sum())} for k in (0.3, 0.4, 0.5, 0.7)}
        sig[part]["ticks"] = int(len(x))
        sig[part]["u_available_ticks"] = int(x.u.notna().sum())
    hours = {r: round(float((g.t.max() - g.t.min()) / 3600), 2) for r, g in tm.groupby("run")}

    res = {"hours_per_run": hours, "estimator_calibration_on_shadow": calib, "chosen_lag_s": int(best),
           "usdttmn_leg_per_run": res_runs,
           "simultaneous_episodes": {name: {"count": len(rows),
                                            "u_dev_gt_0.1_at_t0": int(sum(1 for e in rows if e["u_dev_pct_at_t0"] > 0.1)),
                                            "u_dev_gt_0.05_at_t0": int(sum(1 for e in rows if e["u_dev_pct_at_t0"] > 0.05)),
                                            "rows": rows} for name, rows in ep_sets.items()},
           "tick_signal_counts": sig, "tick_replay_0.7": sim_res}
    (OUT / "ticks_usdt_leg_summary.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "simultaneous_episodes"}, indent=1, default=str)[:6000])
    print("episodes:", len(ep_rows))
    for e in ep_rows:
        print(e)


if __name__ == "__main__":
    main()
