"""Audit 3: independent re-implementation of the 7-day 1m backtest (A0 Binance, A1 median3, B1 robust u, AB both)
plus robustness variants the author did not run. Reads MAIN read-only, refs from ../data. Writes only here.

Modes (each run for all 4 fair variants):
  bt        : author/bt_fairvalue exits (target bid > entry), fill at the signal minute's modelled ask
  live      : live break-even target (bid >= entry*1.0065)
  live_x    : live exits + things the live engine does that the author did not model:
              60 s global-drop filter (approx: global 1m return < -0.4% blocks entry), max 3 open positions,
              120-min per-symbol cooldown after a losing exit, stop also checked on the minute LOW
  lag1      : live exits, but the fill is the NEXT traded minute's ask (signal/fill decoupled: kills bid-ask bounce)
  hs2       : live exits, full spread instead of half (close assumed at the bad side of the book)
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
MAIN = Path("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot")
SYMS = ["BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN", "PEPETMN", "BNBTMN", "ARBTMN"]
TK, ENTRY, SLACK, STOP = 0.003, 0.007, 0.0005, 0.009
SPLIT = pd.Timestamp("2026-10-18 14:05", tz="UTC")
CUT = pd.Timestamp("2026-10-21 18:15", tz="UTC").timestamp()
VARIANTS = {"A0": ("bin", "raw"), "A1": ("med", "raw"), "B1": ("bin", "med5"), "AB": ("med", "med5")}


def candles(p):
    r = pd.read_csv(p)
    df = pd.DataFrame({"c": r.close.astype(float).values, "l": r.low.astype(float).values, "v": r.volume.astype(float).values},
                      index=pd.to_datetime(r.ts.astype("int64"), unit="s", utc=True))
    return df[~df.index.duplicated(keep="last")].sort_index()


def ref(ex, b):
    r = pd.read_csv(DATA / f"{ex}_{b}_1m.csv")
    s = pd.Series(r.close.astype(float).values, index=pd.to_datetime(r.ts.astype("int64"), unit="s", utc=True))
    return s[~s.index.duplicated(keep="last")].sort_index()


def spreads():
    acc = {}
    for run in ["shadow_v2", "shadow_v3", "shadow_v4", "shadow_v5", "shadow_v6", "live_main"]:
        t = pd.read_csv(MAIN / "runs" / run / "ticks.csv", on_bad_lines="skip")
        for c in ("t", "bid", "ask"):
            t[c] = pd.to_numeric(t[c], errors="coerce")
        t = t.dropna(subset=["t", "bid", "ask"])
        t = t[t.t <= CUT]
        for s, g in t.groupby("symbol"):
            acc.setdefault(s, []).append((g.ask / g.bid - 1).to_numpy())
    return {s: float(np.median(np.concatenate(v))) for s, v in acc.items()}


SPR = spreads()
U = candles(MAIN / "research/candles/USDTTMN_1.csv")
GRID = pd.date_range(U.index[0], U.index[-1], freq="1min")
u_raw = U.c.reindex(GRID).ffill()
u_med5 = u_raw.rolling(5, min_periods=1).median()
GL = {}
for _s in SYMS:
    _g = pd.DataFrame({ex: ref(ex, _s[:-3]) for ex in ("binance", "okx", "bybit")})
    _g = _g.reindex(pd.date_range(_g.index[0], _g.index[-1], freq="1min")).ffill(limit=5)
    GL[_s] = {"bin": _g.binance, "med": _g.median(axis=1)}


def _w(s):
    p = MAIN / f"research/candles/{s}_1.csv"
    if not p.exists():
        p = MAIN / f"research/premium/wallex_{s}_1m.csv"
    d = candles(p)
    return d[(d.v > 0) & (d.index >= GRID[0]) & (d.index <= GRID[-1])]


W = {s: _w(s) for s in SYMS}


def prep(s, gk, uk, hsmult):
    d = W[s]
    g = GL[s][gk]
    u = u_raw if uk == "raw" else u_med5
    fair = (g * u).reindex(d.index)
    gret = (g / g.shift(1) - 1).reindex(d.index)       # 1m global return ending at this minute (proxy of the 60 s drop)
    ok = fair.notna().to_numpy()
    d, fair, gret = d[ok], fair[ok], gret[ok]
    hs = SPR.get(s, 0.003) / 2 * hsmult
    ask, bid, lowbid = d.c.values * (1 + hs), d.c.values * (1 - hs), d.l.values * (1 - hs)
    prem = pd.Series(d.c.values / fair.values - 1, index=d.index)
    base = prem.rolling("180min", min_periods=30).median().shift(1).values
    return {"t": d.index, "ask": ask, "bid": bid, "lowbid": lowbid, "bd": ask / fair.values - 1 - base,
            "sd": bid / fair.values - 1 - base, "gret": gret.values}


def run(mode, gk, uk):
    hsm = 2.0 if mode == "hs2" else 1.0
    P = {s: prep(s, gk, uk, hsm) for s in SYMS}
    beg = mode != "bt"
    extra = mode == "live_x"
    lag = mode in ("lag1", "lag1_2m", "persist")
    pos, cool, pending, out = {}, {}, {}, []
    tidx = {s: {t: k for k, t in enumerate(P[s]["t"])} for s in SYMS}
    for T in GRID:
        for s in SYMS:
            k = tidx[s].get(T)
            if k is None:
                continue
            p = P[s]
            hold = 15 if s == "ARBTMN" else 25
            if s in pending:                       # lag1: fill at this (next traded) row's ask
                sig_t, sig_dev = pending.pop(s)
                if mode == "lag1_2m" and (T - sig_t).total_seconds() > 120:
                    continue                       # next print too late: the order would have been cancelled
                if mode == "persist" and not (np.isfinite(p["bd"][k]) and p["bd"][k] <= -ENTRY):
                    continue                       # dip did not persist into the next traded minute
                pos[s] = {"e": p["ask"][k], "t0": T, "dev": sig_dev}
                continue
            if s in pos:
                q = pos[s]
                e = q["e"]
                why = None
                stopped_low = extra and p["lowbid"][k] <= e * (1 - STOP)
                if p["bid"][k] <= e * (1 - STOP) or stopped_low:
                    why = "stop"
                elif p["sd"][k] >= -SLACK and (p["bid"][k] >= e * (1 + 2 * TK + 0.0005) if beg else p["bid"][k] > e):
                    why = "target"
                elif (T - q["t0"]).total_seconds() >= hold * 60:
                    why = "time"
                if why:
                    xp = p["bid"][k]
                    if why == "stop" and extra and p["bid"][k] > e * (1 - STOP):
                        xp = e * (1 - STOP)          # stopped inside the minute on the low: fill at the stop level
                    r = xp * (1 - TK) / (e * (1 + TK)) - 1
                    out.append({"symbol": s, "t": q["t0"], "ret_pct": r * 100, "why": why,
                                "minutes": (T - q["t0"]).total_seconds() / 60, "entry_dev_pct": q["dev"] * 100})
                    del pos[s]
                    if extra and r < 0:
                        cool[s] = T + pd.Timedelta(minutes=120)
                continue
            bd = p["bd"][k]
            if not (np.isfinite(bd) and bd <= -ENTRY) or k == len(p["t"]) - 1:
                continue
            if extra:
                if s in cool and T < cool[s]:
                    continue
                if np.isfinite(p["gret"][k]) and p["gret"][k] < -0.004:
                    continue
                if len(pos) + len(pending) >= 3:
                    continue
            if lag:
                pending[s] = (T, bd)
            else:
                pos[s] = {"e": p["ask"][k], "t0": T, "dev": bd}
    for s, q in pos.items():                       # still open at the end: close at the last bid
        p = P[s]
        r = p["bid"][-1] * (1 - TK) / (q["e"] * (1 + TK)) - 1
        out.append({"symbol": s, "t": q["t0"], "ret_pct": r * 100, "why": "end", "minutes": np.nan, "entry_dev_pct": q["dev"] * 100})
    return pd.DataFrame(out).sort_values("t").reset_index(drop=True)


def st(d):
    if not len(d):
        return {"n": 0, "mean": None, "sum": 0.0, "win": None}
    return {"n": int(len(d)), "mean": round(float(d.ret_pct.mean()), 3), "sum": round(float(d.ret_pct.sum()), 2),
            "win": round(float((d.ret_pct > 0).mean() * 100), 1), "exits": d.why.value_counts().to_dict()}


PUMP = (pd.Timestamp("2026-10-20 13:30", tz="UTC"), pd.Timestamp("2026-10-20 15:00", tz="UTC"))


def no_pump(d):
    return d[~((d.symbol == "PEPETMN") & (d.t >= PUMP[0]) & (d.t <= PUMP[1]))]


if __name__ == "__main__":
    modes = sys.argv[1:] or ["bt", "live", "live_x", "lag1", "hs2"]
    res = {"spreads_pct": {s: round(SPR[s] * 100, 3) for s in SYMS}}
    for mode in modes:
        R = {}
        for v, (gk, uk) in VARIANTS.items():
            d = run(mode, gk, uk)
            d.to_csv(HERE / f"a3_trades_{mode}_{v}.csv", index=False)
            days = d.assign(day=((d.t - GRID[0]).dt.total_seconds() // 86400).astype(int)).groupby("day").ret_pct.sum()
            R[v] = {"all": st(d), "first4d": st(d[d.t < SPLIT]), "last3d": st(d[d.t >= SPLIT]),
                    "ex_pepe_pump": st(no_pump(d)), "ex_abs_gt2": st(d[d.ret_pct.abs() <= 2]),
                    "per_market": {s: st(d[d.symbol == s]) for s in SYMS},
                    "per_day_sum": {int(k): round(float(x), 2) for k, x in days.items()}}
        for v in ("A1", "B1", "AB"):
            a, b = R["A0"], R[v]
            dd = pd.Series(b["per_day_sum"]).sub(pd.Series(a["per_day_sum"]), fill_value=0)
            R[f"diff_{v}_minus_A0"] = {"all": round(b["all"]["sum"] - a["all"]["sum"], 2),
                                       "first4d": round(b["first4d"]["sum"] - a["first4d"]["sum"], 2),
                                       "last3d": round(b["last3d"]["sum"] - a["last3d"]["sum"], 2),
                                       "ex_pepe_pump": round(b["ex_pepe_pump"]["sum"] - a["ex_pepe_pump"]["sum"], 2),
                                       "ex_abs_gt2": round(b["ex_abs_gt2"]["sum"] - a["ex_abs_gt2"]["sum"], 2),
                                       "per_day": {int(k): round(float(x), 2) for k, x in dd.items()},
                                       "days_pos": int((dd > 0.005).sum()), "days_neg": int((dd < -0.005).sum())}
        res[mode] = R
        print("==", mode, flush=True)
        for v in VARIANTS:
            print(" ", v, R[v]["all"], "| 4d", R[v]["first4d"]["sum"], R[v]["first4d"]["n"], "| 3d", R[v]["last3d"]["sum"],
                  R[v]["last3d"]["n"], "| ex-pump", R[v]["ex_pepe_pump"]["sum"], "| ex>2", R[v]["ex_abs_gt2"]["sum"])
        for v in ("A1", "B1", "AB"):
            print("  diff", v, R[f"diff_{v}_minus_A0"])
    (HERE / f"a3_summary_{'_'.join(modes)}.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
