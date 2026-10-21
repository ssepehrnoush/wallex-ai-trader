"""Part 2 (+ inputs to part 3): 7-day 1-minute backtest of the fair-value rule at the 0.7% TMN entry,
all 8 live TMN markets, then an account-level replay at size_factor 0.25 / 0.4 / 0.7 / 1.0 with the
risk manager's caps, the top-of-book depth cap (part 1), cooldown, daily loss limit and kill switch.

Reads candles/Binance closes from the live bot folder as files only (never fetches anything).

Rule variants
  orig : exactly scripts/bt_fairvalue.py (target = sell_dev >= -slack and bid > entry, hold 25)
  live : as the live engine (target also needs bid >= entry x (1 + 2 x taker + 0.05%), ARB hold 15,
         skip entries when the global 1-minute return < -0.4%)
Spread variants
  sv1  : the spreads the existing backtests used (shadow_v1 ticks; live API snapshot for PEPE/BNB/ARB)
  tick : median spread from the newer ticks (shadow_v2..v6 + live_main), all 8 markets
USDT gap: USDTTMN 1m candles have a 10.3 h hole (2026-10-20); 'nogap' drops minutes whose
USDTTMN price is older than 5 minutes (the original forward-fills it).

Writes out/bt_sizing.json, out/bt_trades_<variant>.csv
"""
from __future__ import annotations

import json
import math
from datetime import timedelta, timezone

import numpy as np
import pandas as pd

from common import EQUITY, MAIN, MIN_NOTIONAL, OUT, TMN8, dump, load_ticks, planned_notional

RES = MAIN / "research"
TAKER = 0.003
SLACK, STOP = 0.0005, 0.009
ENTRY = 0.007
TEHRAN = timezone(timedelta(hours=3, minutes=30))
SIZES = [0.25, 0.4, 0.7, 1.0]

# spreads used by runs/backtests/fv_sweep_0.007_0.004 and fv_extra_tmn (summary.json, in %)
SV1 = {"BTCTMN": 0.059, "ETHTMN": 0.186, "SOLTMN": 0.268, "XRPTMN": 0.377, "DOGETMN": 0.162,
       "PEPETMN": 0.04, "BNBTMN": 0.417, "ARBTMN": 0.469}
ticks = load_ticks()
TICKSPR = (ticks.groupby("symbol").spread.median() * 100).round(4).to_dict()

# depth fill fractions measured in part 1 (out/depth_ticks.json): pooled engine-like entry episodes
dt = json.loads((OUT / "depth_ticks.json").read_text(encoding="utf-8"))
FILL = {"none": {str(s): 1.0 for s in SIZES},
        "base": {k: v / 100 for k, v in dt["entry_episodes"]["0.3%"]["entry_fill_pct_mean"].items()},
        "pess": {k: v / 100 for k, v in dt["entry_episodes"]["0.4%"]["entry_fill_pct_mean"].items()}}


def wallex(sym: str) -> pd.DataFrame:
    p = RES / "candles" / f"{sym}_1.csv"
    if not p.exists():
        p = RES / "premium" / f"wallex_{sym}_1m.csv"
    raw = pd.read_csv(p)
    idx = pd.to_datetime(raw.ts.astype("int64"), unit="s", utc=True)
    df = pd.DataFrame({"c": raw.close.astype(float).to_numpy(), "v": raw.volume.astype(float).to_numpy()}, index=idx)
    return df[~df.index.duplicated(keep="last")].sort_index()


def binance(base: str) -> pd.Series:
    s = pd.read_csv(RES / "premium" / f"binance_{base}USDT_1m.csv", index_col=0, parse_dates=True)["close"]
    s.index = pd.to_datetime(s.index, utc=True)
    return s[~s.index.duplicated(keep="last")].sort_index()


u_raw = wallex("USDTTMN")
u_raw = u_raw[u_raw.v > 0].c       # real USDT/TMN prints only for the age test
full = pd.date_range(u_raw.index[0], u_raw.index[-1], freq="1min", tz="UTC")
usdt_ff = wallex("USDTTMN").c.reindex(full).ffill()               # what bt_fairvalue.py does
last_real = pd.Series(u_raw.index, index=u_raw.index).reindex(full).ffill()
usdt_age_min = ((full.to_series() - last_real).dt.total_seconds() / 60).reindex(full)


def backtest(sym: str, rule: str, spreads: dict, nogap: bool, entry_mult: float = 1.0) -> pd.DataFrame:
    g = binance(sym[:-3])
    w = wallex(sym)
    fair = (g * usdt_ff).dropna()
    df = pd.DataFrame({"c": w.c, "v": w.v}).join(fair.rename("fair"), how="inner").dropna()
    df = df.join(g.rename("g"), how="left")
    df["gret"] = g.pct_change().reindex(df.index)                    # global 1m return at that minute
    df["uage"] = usdt_age_min.reindex(df.index)
    df = df[df.v > 0]
    hs = spreads[sym] / 100 / 2
    df["ask"], df["bid"] = df.c * (1 + hs * entry_mult), df.c * (1 - hs)   # entry_mult 2: close was on the bid
    prem = df.c / df.fair - 1
    base = prem.rolling("180min", min_periods=30).median().shift(1)
    bd = (df.ask / df.fair - 1 - base).to_numpy()
    sd = (df.bid / df.fair - 1 - base).to_numpy()
    ask, bid, idx = df.ask.to_numpy(), df.bid.to_numpy(), df.index
    gret, uage = df.gret.to_numpy(), df.uage.to_numpy()
    hold = 15 if (rule == "live" and sym == "ARBTMN") else 25
    trades, i, n = [], 0, len(df)
    while i < n - 1:
        ok = np.isfinite(bd[i]) and bd[i] <= -ENTRY
        if ok and nogap and not (uage[i] <= 5):
            ok = False
        if ok and rule == "live" and np.isfinite(gret[i]) and gret[i] < -0.004:
            ok = False
        if not ok:
            i += 1
            continue
        e = ask[i]
        j, why = i + 1, "end"
        while j < n:
            if bid[j] <= e * (1 - STOP):
                why = "stop"
                break
            tgt_px = e * (1 + 2 * TAKER + 0.0005) if rule == "live" else e * (1 + 1e-12)
            if sd[j] >= -SLACK and bid[j] >= tgt_px:
                why = "target"
                break
            if (idx[j] - idx[i]).total_seconds() >= hold * 60:
                why = "time"
                break
            j += 1
        j = min(j, n - 1)
        r = bid[j] * (1 - TAKER) / (e * (1 + TAKER)) - 1
        trades.append({"symbol": sym, "t": idx[i], "t_exit": idx[j], "ret_pct": r * 100, "why": why,
                       "minutes": (idx[j] - idx[i]).total_seconds() / 60, "entry_dev_pct": bd[i] * 100})
        i = j + 1
    out = pd.DataFrame(trades)
    out.attrs["span"] = (df.index[0], df.index[-1], len(df))
    return out


def stats(tr: pd.DataFrame) -> dict:
    if not len(tr):
        return {"trades": 0}
    return {"trades": int(len(tr)), "mean_ret_pct": round(tr.ret_pct.mean(), 3), "sum_ret_pct": round(tr.ret_pct.sum(), 2),
            "median_ret_pct": round(tr.ret_pct.median(), 3), "win_pct": round((tr.ret_pct > 0).mean() * 100, 1),
            "exits": tr.why.value_counts().to_dict(), "worst_pct": round(tr.ret_pct.min(), 3)}


# ---------------------------------------------------------------- run variants
VARIANTS = {"orig_sv1_ff": ("orig", SV1, False), "live_sv1_ff": ("live", SV1, False),
            "live_tick_ff": ("live", TICKSPR, False), "live_tick_nogap": ("live", TICKSPR, True),
            "orig_tick_ff": ("orig", TICKSPR, False), "live_tick_ff_fullentry": ("live", TICKSPR, False, 2.0)}
all_res, tr_by_var = {}, {}
span0 = None
for vname, (rule, spr, nogap, *em) in VARIANTS.items():
    frames = []
    per = {}
    for s in TMN8:
        tr = backtest(s, rule, spr, nogap, em[0] if em else 1.0)
        frames.append(tr)
        a, b, nmin = tr.attrs["span"]
        span0 = span0 or (a, b)
        per[s] = stats(tr) | {"minutes_traded": int(nmin), "half_spread_pct": round(spr[s] / 2, 4)}
    tr = pd.concat([f for f in frames if len(f)], ignore_index=True).sort_values("t")
    tr_by_var[vname] = tr
    tr.to_csv(OUT / f"bt_trades_{vname}.csv", index=False)
    all_res[vname] = {"markets": per, "all": stats(tr)}

# common span for rates (Binance start to Wallex candle end)
T0 = max(binance("BTC").index[0], wallex("BTCTMN").index[0])
T1 = min(binance("BTC").index[-1], wallex("BTCTMN").index[-1])
DAYS = (T1 - T0).total_seconds() / 86400
SPLIT = T0 + pd.Timedelta(days=4)
for vname, tr in tr_by_var.items():
    a, b = tr[tr.t < SPLIT], tr[tr.t >= SPLIT]
    all_res[vname]["walk_forward"] = {"first_4d": stats(a) | {"per_day": round(len(a) / 4, 2)},
                                     "last_part": stats(b) | {"days": round(DAYS - 4, 2), "per_day": round(len(b) / (DAYS - 4), 2)}}
    all_res[vname]["trades_per_day"] = round(len(tr) / DAYS, 2)
    all_res[vname]["per_market_trades_per_day"] = (tr.groupby("symbol").size() / DAYS).round(3).to_dict()


# ---------------------------------------------------------------- account replay
def replay(tr: pd.DataFrame, size_rule, fill: dict, t_from=None, t_to=None, eq0=EQUITY) -> dict:
    """Chronological account replay. size_rule(state)->size_factor. Returns equity path stats."""
    tr = tr.copy()
    if t_from is not None:
        tr = tr[(tr.t >= t_from) & (tr.t < t_to)]
    events = sorted([(r.t, 1, i) for i, r in tr.iterrows()] + [(r.t_exit, 0, i) for i, r in tr.iterrows()],
                    key=lambda x: (x[0], x[1]))
    eq, peak, start = eq0, eq0, eq0
    open_pos: dict[int, tuple] = {}
    cooldown: dict[str, pd.Timestamp] = {}
    day, day_start, halted = None, eq0, ""
    taken, skipped, pnl_list, min_eq, max_dd = 0, {}, [], eq0, 0.0
    for t, kind, i in events:
        r = tr.loc[i]
        d = t.tz_convert(TEHRAN).date()
        if d != day:
            day, day_start = d, eq
            if halted.startswith("daily"):
                halted = ""
        if kind == 0:
            if i not in open_pos:
                continue
            notional, sym = open_pos.pop(i)
            pnl = notional * r.ret_pct / 100
            eq += pnl
            pnl_list.append(pnl)
            peak = max(peak, eq)
            min_eq = min(min_eq, eq)
            max_dd = max(max_dd, 1 - eq / peak)
            if pnl < 0:
                cooldown[sym] = t + pd.Timedelta(minutes=120)
            if 1 - eq / peak >= 0.03:
                halted = "kill"
            elif eq / day_start - 1 <= -0.015 and not halted:
                halted = "daily"
            continue
        why = None
        if halted:
            why = halted
        elif r.symbol in cooldown and cooldown[r.symbol] > t:
            why = "cooldown"
        elif len(open_pos) >= 3:
            why = "max_positions"
        if why:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        state = {"dd": 1 - eq / peak, "day_ret": eq / day_start - 1, "ret": eq / start - 1}
        f = size_rule(state)
        risk = 0.006 * (0.5 if state["ret"] >= 0.03 else 1.0)
        notional = min(eq * risk * f / 0.0145, eq * 0.35)
        exposure = sum(v[0] for v in open_pos.values())
        notional = min(notional, eq * 0.70 - exposure)
        if notional < MIN_NOTIONAL:
            skipped["min_notional/exposure"] = skipped.get("min_notional/exposure", 0) + 1
            continue
        notional *= fill_for(f, fill)
        open_pos[i] = (notional, r.symbol)
        taken += 1
    return {"trades_taken": taken, "skipped": skipped, "final_ret_pct": round((eq / eq0 - 1) * 100, 4),
            "max_dd_pct": round(max_dd * 100, 4), "min_equity_pct": round((min_eq / eq0 - 1) * 100, 4),
            "pnl_tmn": round(eq - eq0)}


def fill_for(f: float, fill: dict) -> float:
    keys = sorted(float(k) for k in fill)
    # linear interpolation of the measured fill fraction between the measured size factors
    xs = keys
    ys = [fill[str(k) if str(k) in fill else f"{k}"] for k in keys]
    return float(np.interp(f, xs, ys))


def fixed(f):
    return lambda st: f


def rule_dd(f_hi, f_lo=0.4, dd=0.01, day=-0.0075):
    return lambda st: f_lo if (st["dd"] >= dd or st["day_ret"] <= day) else f_hi


POLICIES = {"fixed_0.25": fixed(0.25), "fixed_0.4": fixed(0.4), "fixed_0.7": fixed(0.7), "fixed_1.0": fixed(1.0),
            "rule_1.0_to_0.4_if_dd1%": rule_dd(1.0), "rule_0.7_to_0.4_if_dd1%": rule_dd(0.7)}
HOURS = 45.0
acct = {}
for vname in ["live_tick_ff", "live_tick_nogap", "live_sv1_ff", "orig_sv1_ff", "live_tick_ff_fullentry"]:
    tr = tr_by_var[vname]
    acct[vname] = {}
    for dname, fill in FILL.items():
        acct[vname][dname] = {}
        for pname, pol in POLICIES.items():
            full_ = replay(tr, pol, fill)
            wf_a = replay(tr, pol, fill, T0, SPLIT)
            wf_b = replay(tr, pol, fill, SPLIT, T1 + pd.Timedelta(minutes=1))
            per_hour = full_["final_ret_pct"] / (DAYS * 24)
            acct[vname][dname][pname] = {"7d": full_, "first_4d": wf_a, "last_part": wf_b,
                                         "expected_45h_ret_pct_linear": round(per_hour * HOURS, 4),
                                         "expected_45h_tmn": round(per_hour * HOURS / 100 * EQUITY),
                                         "last_part_scaled_45h_pct": round(wf_b["final_ret_pct"] / ((DAYS - 4) * 24) * HOURS, 4)}

# ---------------------------------------------------------------- 45-hour windows (historical, overlapping)
win = {}
tr = tr_by_var["live_tick_ff"]
starts = pd.date_range(T0, T1 - pd.Timedelta(hours=HOURS), freq="3h")
for pname in ["fixed_0.4", "fixed_0.7", "fixed_1.0", "rule_1.0_to_0.4_if_dd1%"]:
    rets, dds = [], []
    for st in starts:
        r = replay(tr, POLICIES[pname], FILL["base"], st, st + pd.Timedelta(hours=HOURS))
        rets.append(r["final_ret_pct"])
        dds.append(r["max_dd_pct"])
    rets, dds = np.array(rets), np.array(dds)
    win[pname] = {"windows": int(len(rets)), "mean_pct": round(rets.mean(), 4), "p5_pct": round(np.quantile(rets, 0.05), 4),
                  "min_pct": round(rets.min(), 4), "max_pct": round(rets.max(), 4),
                  "share_negative_pct": round((rets < 0).mean() * 100, 1), "max_dd_worst_pct": round(dds.max(), 4)}

# ---------------------------------------------------------------- Monte Carlo 45 h (Poisson counts, pooled bootstrap)
rng = np.random.default_rng(7)
tr = tr_by_var["live_tick_ff"]
lam = len(tr) / DAYS * HOURS / 24
rets_pool = tr.ret_pct.to_numpy() / 100
mc = {}
N = 20000
for pname, f in [("fixed_0.4", 0.4), ("fixed_0.7", 0.7), ("fixed_1.0", 1.0), ("rule_1.0_to_0.4_if_dd1%", None)]:
    finals, dd_hits, kill_hits, daily_hits = [], 0, 0, 0
    for _ in range(N):
        k = rng.poisson(lam)
        eq = peak = EQUITY
        maxdd = 0.0
        for r in rng.choice(rets_pool, size=k, replace=True):
            ff = f if f is not None else (0.4 if 1 - eq / peak >= 0.01 else 1.0)
            notional = min(eq * 0.006 * ff / 0.0145, eq * 0.35) * fill_for(ff, FILL["base"])
            eq += notional * r
            peak = max(peak, eq)
            maxdd = max(maxdd, 1 - eq / peak)
            if maxdd >= 0.03:
                kill_hits += 1
                break
        finals.append(eq / EQUITY - 1)
        dd_hits += maxdd >= 0.01
        daily_hits += maxdd >= 0.015
    fa = np.array(finals) * 100
    mc[pname] = {"paths": N, "lambda_trades": round(lam, 2), "mean_pct": round(fa.mean(), 4), "median_pct": round(np.median(fa), 4),
                 "p5_pct": round(np.quantile(fa, 0.05), 4), "p1_pct": round(np.quantile(fa, 0.01), 4),
                 "share_negative_pct": round((fa < 0).mean() * 100, 2), "share_dd_ge_1pct": round(dd_hits / N * 100, 3),
                 "share_dd_ge_1.5pct": round(daily_hits / N * 100, 3), "share_kill_3pct": round(kill_hits / N * 100, 3)}

# ---------------------------------------------------------------- bad case: consecutive stop-outs
bad = {}
stops_hist = pd.concat([tr_by_var[v] for v in ["live_tick_ff", "orig_sv1_ff"]])
stops_hist = stops_hist[stops_hist.why == "stop"]
for f in [0.25, 0.4, 0.7, 1.0]:
    n = planned_notional(f)
    loss_typ = n * (0.009 + 0.006)             # stop at -0.9% plus two taker fees
    loss_cross = n * (0.009 + 0.006 + 0.003)   # plus the full 0.3% crossing of a stop LIMIT
    worst_hist = n * abs(min(stops_hist.ret_pct.min(), -1.5)) / 100 if len(stops_hist) else loss_typ
    max_conc = min(3, math.floor(0.70 / (n / EQUITY) + 1e-9)) if n else 0
    conc_notional = min(3 * n, 0.70 * EQUITY)
    bad[str(f)] = {"planned_notional": round(n), "loss_per_stop_tmn": round(loss_typ), "loss_per_stop_pct_equity": round(loss_typ / EQUITY * 100, 3),
                   "loss_per_stop_with_0.3pct_cross_pct_equity": round(loss_cross / EQUITY * 100, 3),
                   "three_stops_pct_equity": round(3 * loss_typ / EQUITY * 100, 3),
                   "three_stops_with_cross_pct_equity": round(3 * loss_cross / EQUITY * 100, 3),
                   "stops_to_daily_limit_1.5pct": math.ceil(0.015 * EQUITY / loss_typ),
                   "stops_to_kill_3pct": math.ceil(0.03 * EQUITY / loss_typ),
                   "max_open_positions_by_caps": max_conc + (1 if 0.70 * EQUITY - max_conc * n >= MIN_NOTIONAL and max_conc < 3 else 0),
                   "all_open_stop_at_once_pct_equity": round(conc_notional * 0.018 / EQUITY * 100, 3)}
bad["historical_stop_trades"] = stats(stops_hist) if len(stops_hist) else {"trades": 0}

res = {"window": {"start": str(T0), "end": str(T1), "days": round(DAYS, 3), "walk_forward_split": str(SPLIT)},
       "spreads_tick_pct": TICKSPR, "spreads_sv1_pct": SV1, "depth_fill_fraction": FILL,
       "backtests": all_res, "account_replay": acct, "windows_45h_live_tick_ff_base_depth": win,
       "monte_carlo_45h_live_tick_ff_base_depth": mc, "bad_case": bad}
dump(res, "bt_sizing.json")

pd.set_option("display.width", 250)
print("window", res["window"])
for v, r in all_res.items():
    print(f"\n== {v}: all {r['all']} per_day {r['trades_per_day']}")
    print("   WF first4d", r["walk_forward"]["first_4d"], "\n   WF last", r["walk_forward"]["last_part"])
    print(pd.DataFrame(r["markets"]).T[["trades", "mean_ret_pct", "sum_ret_pct", "win_pct", "exits", "half_spread_pct"]].to_string())
print("\n== account replay (7d final %, maxDD %, 45h linear %, last-part scaled 45h %)")
for v, dd in acct.items():
    for dn, pp in dd.items():
        print(v, dn, {p: (x["7d"]["final_ret_pct"], x["7d"]["max_dd_pct"], x["expected_45h_ret_pct_linear"], x["last_part_scaled_45h_pct"], x["7d"]["trades_taken"]) for p, x in pp.items()})
print("\n== 45h windows", json.dumps(win, indent=0))
print("\n== MC", json.dumps(mc, indent=0))
print("\n== bad", json.dumps(bad, indent=0, default=str))
