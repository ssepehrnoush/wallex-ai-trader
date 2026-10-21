"""Per-market dynamic entry threshold for the fair-value reversion rule, walk-forward.

Variants (all with the live exits: target = sell_dev >= -0.05% AND bid >= entry*(1+0.65%),
stop = bid <= entry*(1-0.9%), time = 25 min (ARB 15), one position per market, 120-min cooldown
after a losing trade, 60 s global-drop filter, spread <= 0.6%):

  V4 fixed      : 0.7 % everywhere (the live rule)
  V1 per-market : fixed threshold per market from {0.7,0.8,0.9,1.0,1.2,1.5}%, chosen on days 1-4 by sum of net %
  V2 vol-scaled : max(0.7 %, k * trailing 3-hour std of buy_dev (shifted)), one global k from {1.5,2,2.5,3,4}
                  chosen on days 1-4 (V2b: k chosen per market)
  V3 spread     : 0.7 % + half the market's (median live) spread

Data: 7 days of Wallex 1-minute candles, traded minutes only, executable prices modelled as
close * (1 +/- half the median live spread) exactly as scripts/bt_fairvalue.py does.
Days 1-4 = 2026-10-14 14:05 -> 2026-10-18 14:05 UTC (in-sample), days 5-7 = -> 2026-10-21 14:05 (out-of-sample).

  python bt_dynamic.py [--usdt-max-age 1e9] [--tag main]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

import dt_data as D

ap = argparse.ArgumentParser()
ap.add_argument("--usdt-max-age", type=float, default=1e9, help="drop minutes whose USDTTMN price is older than this (min)")
ap.add_argument("--tag", default="main")
ap.add_argument("--boot", type=int, default=5000)
a = ap.parse_args()

OUT = D.HERE / "out"
OUT.mkdir(exist_ok=True)
THRS = [0.007, 0.008, 0.009, 0.010, 0.012, 0.015]
KS = [1.5, 2.0, 2.5, 3.0, 4.0]
T0 = pd.Timestamp("2026-10-14 14:05", tz="UTC")
SPLIT = T0 + pd.Timedelta(days=4)
END = T0 + pd.Timedelta(days=7)
EPOCH = pd.Timestamp("2026-10-14 00:00", tz="UTC")
NOTIONAL = 5_000_000 * 0.006 * 0.4 / (D.STOP + D.TAKER + 0.0025)     # ~827,586 TMN at size_factor 0.4
F = D.TAKER


def tmin(idx) -> np.ndarray:
    return np.asarray((idx - EPOCH) / pd.Timedelta(minutes=1), dtype=float)


T0m, SPLITm, ENDm = (float((x - EPOCH) / pd.Timedelta(minutes=1)) for x in (T0, SPLIT, END))

# ---------------------------------------------------------------- data
u, uage = D.usdt_series()
SPREADS = D.live_spreads()
M = {}
for s in D.SYMS:
    df = D.market_frame(s, u, uage, SPREADS[s] / 2, a.usdt_max_age)
    df = df[df.buy_dev.notna() & (df.index < END + pd.Timedelta(hours=1))]
    M[s] = {"t": tmin(df.index), "ask": df.ask.to_numpy(), "bid": df.bid.to_numpy(), "bd": df.buy_dev.to_numpy(),
            "sd": df.sell_dev.to_numpy(), "gret": df.gret.fillna(0).to_numpy(), "vol": df.vol3h.to_numpy(),
            "spread": df.spread.to_numpy(), "n": len(df)}


# ---------------------------------------------------------------- simulator
def simulate(thr: dict[str, np.ndarray], syms=None, max_pos=None, portfolio=False) -> pd.DataFrame:
    """Event-driven replay over the union of traded minutes. thr[s] = positive threshold per row of M[s]."""
    syms = syms or D.SYMS
    ts = np.concatenate([M[s]["t"] for s in syms])
    ks = np.concatenate([np.full(M[s]["n"], k) for k, s in enumerate(syms)])
    ii = np.concatenate([np.arange(M[s]["n"]) for s in syms])
    order = np.lexsort((ks, ts))
    pos, cool, trades = {}, {}, []
    eq_real, peak, halted_kill, day_pnl = 0.0, 0.0, False, {}
    for o in order:
        t, k, i = ts[o], ks[o], ii[o]
        s = syms[k]
        A = M[s]
        if s in pos:
            e, t_open, i_open, bd0 = pos[s]
            bid = A["bid"][i]
            why = ("stop" if bid <= e * (1 - D.STOP) else
                   "target" if A["sd"][i] >= -D.SLACK and bid >= e * D.BE else
                   "time" if t - t_open >= D.HOLD[s] else None)
            if why:
                r = bid * (1 - F) ** 2 / e - 1
                trades.append((s, t_open, t, r * 100, why, bd0 * 100, thr[s][i_open] * 100))
                del pos[s]
                if r < 0:
                    cool[s] = t + D.COOLDOWN_MIN
                if portfolio:
                    eq_real += NOTIONAL * r
                    peak = max(peak, eq_real)
                    dkey = int((t + 210) // 1440)          # Tehran day (UTC+3:30)
                    day_pnl[dkey] = day_pnl.get(dkey, 0.0) + NOTIONAL * r
                    if peak - eq_real >= 0.03 * 5_000_000:
                        halted_kill = True
            continue
        if not (T0m <= t < ENDm):
            continue
        th = thr[s][i]
        if not np.isfinite(th) or A["bd"][i] > -th:
            continue
        if A["gret"][i] < -D.MAX_DROP_60S or A["spread"][i] > D.MAX_SPREAD:
            continue
        if cool.get(s, -1e18) > t:
            continue
        if max_pos and len(pos) >= max_pos:
            continue
        if portfolio and (halted_kill or day_pnl.get(int((t + 210) // 1440), 0.0) <= -0.015 * 5_000_000):
            continue
        pos[s] = (A["ask"][i], t, i, A["bd"][i])
    for s, (e, t_open, i_open, bd0) in pos.items():          # still open at the end of the data
        A = M[s]
        r = A["bid"][-1] * (1 - F) ** 2 / e - 1
        trades.append((s, t_open, A["t"][-1], r * 100, "end", bd0 * 100, thr[s][i_open] * 100))
    tr = pd.DataFrame(trades, columns=["symbol", "t_open", "t_close", "ret_pct", "why", "entry_dev_pct", "thr_pct"])
    tr["period"] = np.where(tr.t_open < SPLITm, "IS", "OOS")
    tr["minutes"] = tr.t_close - tr.t_open
    tr["t"] = EPOCH + pd.to_timedelta(tr.t_open, unit="min")
    return tr.sort_values("t_open").reset_index(drop=True)


def stats(tr: pd.DataFrame) -> dict:
    n = len(tr)
    if not n:
        return {"trades": 0, "mean_net_pct": None, "sum_net_pct": 0.0, "win_pct": None, "exits": {}, "se_mean": None}
    return {"trades": n, "mean_net_pct": round(tr.ret_pct.mean(), 3), "sum_net_pct": round(tr.ret_pct.sum(), 3),
            "win_pct": round((tr.ret_pct > 0).mean() * 100, 1), "exits": tr.why.value_counts().to_dict(),
            "se_mean": round(tr.ret_pct.std(ddof=1) / np.sqrt(n), 3) if n > 1 else None,
            "median_min_held": round(tr.minutes.median(), 1)}


def by_market(tr: pd.DataFrame) -> dict:
    return {s: stats(tr[tr.symbol == s]) for s in D.SYMS}


def const(v):
    return {s: np.full(M[s]["n"], v) for s in D.SYMS}


def total(tr, period):
    return tr[tr.period == period].ret_pct.sum()


# ---------------------------------------------------------------- variants
res = {"params": {"usdt_max_age_min": a.usdt_max_age, "in_sample": [str(T0), str(SPLIT)], "out_of_sample": [str(SPLIT), str(END)],
                  "half_spreads_pct": {s: round(SPREADS[s] / 2 * 100, 4) for s in D.SYMS},
                  "spreads_pct": {s: round(SPREADS[s] * 100, 4) for s in D.SYMS},
                  "traded_minutes": {s: int(M[s]["n"]) for s in D.SYMS},
                  "notional_tmn": round(NOTIONAL)}}

# per-market sweeps of fixed thresholds (independent markets) -> V1 choice, and the oracle
sweep = {}
for th in THRS:
    tr = simulate(const(th))
    for s in D.SYMS:
        x = tr[tr.symbol == s]
        sweep[(s, th)] = {"IS": stats(x[x.period == "IS"]), "OOS": stats(x[x.period == "OOS"])}


def pick(period: str) -> dict:
    ch = {}
    for s in D.SYMS:
        best = max(THRS, key=lambda th: (round(sweep[(s, th)][period]["sum_net_pct"], 6), -th))   # ties -> lowest
        ch[s] = best
    return ch


v1_choice = pick("IS")
oracle_choice = pick("OOS")
res["fixed_threshold_sweep"] = {f"{s}@{th*100:.1f}": v for (s, th), v in sweep.items()}
res["v1_choice_pct"] = {s: round(v * 100, 2) for s, v in v1_choice.items()}
res["oracle_choice_pct_(chosen_on_OOS,_not_tradable)"] = {s: round(v * 100, 2) for s, v in oracle_choice.items()}

# vol-scaled: one global k (and per-market k)
ksweep, ktr = {}, {}
for k in KS:
    thr = {s: np.fmax(D.FLOOR, k * M[s]["vol"]) for s in D.SYMS}
    tr = simulate(thr)
    ktr[k] = tr
    ksweep[k] = {"IS": stats(tr[tr.period == "IS"]), "OOS": stats(tr[tr.period == "OOS"]),
                 "IS_by_market_sum": tr[tr.period == "IS"].groupby("symbol").ret_pct.sum().round(3).to_dict(),
                 "mean_threshold_pct": {s: round(float(np.nanmean(thr[s])) * 100, 3) for s in D.SYMS},
                 "share_minutes_above_floor": {s: round(float(np.mean(k * M[s]["vol"] > D.FLOOR)), 3) for s in D.SYMS}}
k_best = max(KS, key=lambda k: (round(ksweep[k]["IS"]["sum_net_pct"], 6), -k))
k_pm = {}
for s in D.SYMS:
    k_pm[s] = max(KS, key=lambda k: (round(ksweep[k]["IS_by_market_sum"].get(s, 0.0), 6), -k))
res["vol_k_sweep"] = {str(k): v for k, v in ksweep.items()}
res["v2_k_global"] = k_best
res["v2b_k_per_market"] = k_pm

VARIANTS = {
    "V4_fixed_0.7": const(D.FLOOR),
    "V1_per_market_fixed": {s: np.full(M[s]["n"], v1_choice[s]) for s in D.SYMS},
    "V2_vol_scaled_global_k": {s: np.fmax(D.FLOOR, k_best * M[s]["vol"]) for s in D.SYMS},
    "V2b_vol_scaled_per_market_k": {s: np.fmax(D.FLOOR, k_pm[s] * M[s]["vol"]) for s in D.SYMS},
    "V3_spread_aware": {s: np.full(M[s]["n"], D.FLOOR + SPREADS[s] / 2) for s in D.SYMS},
    "ORACLE_per_market_chosen_on_OOS": {s: np.full(M[s]["n"], oracle_choice[s]) for s in D.SYMS},
}

TR = {}
res["variants"] = {}
for name, thr in VARIANTS.items():
    tr = simulate(thr)
    trp = simulate(thr, max_pos=3, portfolio=True)
    TR[name] = tr
    tr.assign(variant=name).to_csv(OUT / f"trades_{a.tag}_{name}.csv", index=False)
    oos_p = trp[trp.period == "OOS"]
    res["variants"][name] = {
        "IS": stats(tr[tr.period == "IS"]), "OOS": stats(tr[tr.period == "OOS"]),
        "IS_by_market": by_market(tr[tr.period == "IS"]), "OOS_by_market": by_market(tr[tr.period == "OOS"]),
        "OOS_portfolio_max3_dailylimit": stats(oos_p) | {"pnl_tmn_at_size_0.4": round(float((oos_p.ret_pct / 100 * NOTIONAL).sum()))},
        "IS_portfolio_max3_dailylimit": stats(trp[trp.period == "IS"]),
    }

# ---------------------------------------------------------------- noise: block bootstrap of OOS difference vs V4
rng = np.random.default_rng(7)
BLOCK = 120.0                                    # 2-hour blocks of entry time
nblk = int(np.ceil((ENDm - SPLITm) / BLOCK))


def block_sums(tr):
    x = tr[tr.period == "OOS"]
    b = ((x.t_open - SPLITm) // BLOCK).astype(int).clip(0, nblk - 1)
    return np.bincount(b, weights=x.ret_pct, minlength=nblk), np.bincount(b, minlength=nblk)


base_s, base_n = block_sums(TR["V4_fixed_0.7"])
res["bootstrap_vs_V4_OOS"] = {}
for name, tr in TR.items():
    if name == "V4_fixed_0.7":
        continue
    vs, vn = block_sums(tr)
    d = vs - base_s
    idx = rng.integers(0, nblk, size=(a.boot, nblk))
    boot = d[idx].sum(1)
    res["bootstrap_vs_V4_OOS"][name] = {
        "diff_sum_net_pct": round(float(d.sum()), 3), "ci90": [round(float(np.quantile(boot, 0.05)), 3), round(float(np.quantile(boot, 0.95)), 3)],
        "p_diff_gt_0": round(float((boot > 0).mean()), 3), "diff_trades": int(vn.sum() - base_n.sum())}

# ---------------------------------------------------------------- robustness: reverse walk-forward (choose on days 5-7, test on 1-4)
rev = {s: np.full(M[s]["n"], oracle_choice[s]) for s in D.SYMS}
k_rev = max(KS, key=lambda k: (round(ksweep[k]["OOS"]["sum_net_pct"], 6), -k))
tr_rev1 = simulate(rev)
tr_rev2 = simulate({s: np.fmax(D.FLOOR, k_rev * M[s]["vol"]) for s in D.SYMS})
res["reverse_walk_forward_(choose_days5-7,_test_days1-4)"] = {
    "V1_choice_pct": {s: round(v * 100, 2) for s, v in oracle_choice.items()}, "V1_test_IS": stats(tr_rev1[tr_rev1.period == "IS"]),
    "V2_k": k_rev, "V2_test_IS": stats(tr_rev2[tr_rev2.period == "IS"]),
    "V4_same_days": stats(TR["V4_fixed_0.7"][TR["V4_fixed_0.7"].period == "IS"])}

(OUT / f"results_{a.tag}.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")

# ---------------------------------------------------------------- print
print("half spreads %:", res["params"]["half_spreads_pct"])
print("traded minutes:", res["params"]["traded_minutes"])
print("V1 choice:", res["v1_choice_pct"], " oracle:", res["oracle_choice_pct_(chosen_on_OOS,_not_tradable)"])
print("V2 k:", k_best, "per-market k:", k_pm)
for k in KS:
    print(f"  k={k}: IS {ksweep[k]['IS']['trades']} tr sum {ksweep[k]['IS']['sum_net_pct']} | OOS {ksweep[k]['OOS']['trades']} tr sum {ksweep[k]['OOS']['sum_net_pct']}")
print()
print(f"{'variant':34s} | {'IS n':>5s} {'IS mean':>8s} {'IS sum':>8s} {'IS win':>6s} | {'OOS n':>5s} {'mean':>7s} {'sum':>7s} {'win':>6s} | portf OOS n, TMN")
for name, v in res["variants"].items():
    i, o, p = v["IS"], v["OOS"], v["OOS_portfolio_max3_dailylimit"]
    print(f"{name:34s} | {i['trades']:5d} {str(i['mean_net_pct']):>8s} {i['sum_net_pct']:8.3f} {str(i['win_pct']):>6s} | "
          f"{o['trades']:5d} {str(o['mean_net_pct']):>7s} {o['sum_net_pct']:7.3f} {str(o['win_pct']):>6s} | {p['trades']}, {p['pnl_tmn_at_size_0.4']}  exits {o['exits']}")
print()
for name, v in res["bootstrap_vs_V4_OOS"].items():
    print("bootstrap", name, v)
print("reverse WF:", json.dumps(res["reverse_walk_forward_(choose_days5-7,_test_days1-4)"], default=str))
print()
for name in ("V4_fixed_0.7", "V1_per_market_fixed", "V2_vol_scaled_global_k", "V3_spread_aware"):
    print(name, "OOS by market:", {s: (x["trades"], x["mean_net_pct"], x["sum_net_pct"]) for s, x in res["variants"][name]["OOS_by_market"].items()})
    print(name, " IS by market:", {s: (x["trades"], x["mean_net_pct"], x["sum_net_pct"]) for s, x in res["variants"][name]["IS_by_market"].items()})
