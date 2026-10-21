"""Independent re-implementation of the A0/A1/B1/AB comparison (does NOT import the author's code).

Rule (as in scripts/bt_fairvalue.py, re-written from scratch):
  minutes = Wallex 1m candles with volume > 0 inside [2026-10-14 14:05, 2026-10-21 14:04] UTC
  ask/bid = close * (1 +/- half median live spread)
  prem = close/fair - 1 ; base = time-rolling 180min median(prem), min 30 obs, shifted by one row
  buy_dev = ask/fair - 1 - base ; sell_dev = bid/fair - 1 - base
  entry buy_dev <= -thr ; exits checked from the next traded minute: stop (bid <= e*(1-0.009)),
  target (sell_dev >= -0.0005 and bid > e   [live: bid >= e*1.0065]), time (>= hold min; ARB 15)
  ret = bid*(1-0.003)/(e*(1+0.003)) - 1 ; next entry search starts after the exit minute.
Variants: A0 binance*u, A1 median(binance,okx,bybit)*u, B1 binance*med5(u), AB median3*med5(u)
Usage: bt_v.py [--thr 0.007] [--spreads cut|all|shadow] [--u5 ffill|traded] [--tag name]
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

MAIN = Path("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot")
HERE = Path(__file__).resolve().parent
D = HERE / "data"
T0, T1 = 1791986700, 1792591440
SPLIT = T0 + 4 * 86400
BASES = ["BTC", "ETH", "SOL", "XRP", "DOGE", "PEPE", "BNB", "ARB"]
TAKER = 0.003

ap = argparse.ArgumentParser()
ap.add_argument("--thr", type=float, default=0.007)
ap.add_argument("--spreads", default="cut")
ap.add_argument("--u5", default="ffill")
ap.add_argument("--arbhold", type=int, default=15)
ap.add_argument("--tag", default="")
ap.add_argument("--quiet", action="store_true")
a = ap.parse_args()


def wallex(sym):
    p = MAIN / "research" / "candles" / f"{sym}_1.csv"
    if not p.exists():
        p = MAIN / "research" / "premium" / f"wallex_{sym}_1m.csv"
    w = pd.read_csv(p)
    w = w[(w.ts >= T0) & (w.ts <= T1)].drop_duplicates("ts").set_index("ts").sort_index()
    return w


def ref(ex, b):
    p = D / f"{ex}_{b}.csv"
    if not p.exists():  # provisional only: author's cache (verified byte-equal closes on the files I have)
        print("PROVISIONAL: using author cache for", ex, b)
        p = HERE.parent / "data" / f"{ex}_{b}_1m.csv"
    r = pd.read_csv(p).drop_duplicates("ts").set_index("ts")["close"].astype(float)
    return r


def spreads(mode):
    runs = ["shadow_v2", "shadow_v3", "shadow_v4", "shadow_v5", "shadow_v6"]
    if mode == "live":
        runs = []
    if mode in ("cut", "all", "live"):
        runs.append("live_main")
    frames = []
    for r in runs:
        t = pd.read_csv(MAIN / "runs" / r / "ticks.csv", on_bad_lines="skip")
        t = t.dropna(subset=["bid", "ask"])
        if r == "live_main" and mode == "cut":
            t = t[t.t <= 1792606500]           # 2026-10-21 18:15 UTC (PC clock)
        frames.append(t[["symbol", "bid", "ask"]])
    t = pd.concat(frames)
    t = t[(t.bid > 0) & (t.ask > 0)]
    return (t.ask / t.bid - 1).groupby(t.symbol).median().to_dict()


SPR = spreads(a.spreads)
grid = np.arange(T0, T1 + 60, 60)
uw = wallex("USDTTMN")
u = uw["close"].reindex(grid).ffill().bfill()
if a.u5 == "ffill":
    u5 = u.rolling(5, min_periods=1).median()
else:  # median of traded USDTTMN closes in the last 5 minutes (incl. current); fall back to last close
    ut = uw.loc[uw.volume > 0, "close"].reindex(grid)
    u5 = ut.rolling(5, min_periods=1).median().fillna(u)


def run(sym, fair, live_exit):
    w = wallex(sym)
    df = pd.DataFrame({"c": w["close"], "v": w["volume"]}).join(fair.rename("fair"), how="inner")
    df = df[(df.v > 0)].dropna()
    hs = SPR[sym] / 2
    ask = (df.c * (1 + hs)).to_numpy()
    bid = (df.c * (1 - hs)).to_numpy()
    prem = df.c / df.fair - 1
    ti = pd.to_datetime(df.index, unit="s", utc=True)
    base = pd.Series(prem.to_numpy(), index=ti).rolling("180min", min_periods=30).median().shift(1).to_numpy()
    fv = df.fair.to_numpy()
    bdev = ask / fv - 1 - base
    sdev = bid / fv - 1 - base
    ts = df.index.to_numpy()
    hold = (a.arbhold if sym == "ARBTMN" else 25) * 60
    out, i, n = [], 0, len(df)
    while i < n - 1:
        if not (np.isfinite(bdev[i]) and bdev[i] <= -a.thr):
            i += 1
            continue
        e = ask[i]
        tgt = e * (1 + 2 * TAKER + 0.0005) if live_exit else None
        j, why = i + 1, "end"
        while j < n:
            if bid[j] <= e * (1 - 0.009):
                why = "stop"; break
            okp = (bid[j] >= tgt) if live_exit else (bid[j] > e)
            if sdev[j] >= -0.0005 and okp:
                why = "target"; break
            if ts[j] - ts[i] >= hold:
                why = "time"; break
            j += 1
        j = min(j, n - 1)
        r = bid[j] * (1 - TAKER) / (e * (1 + TAKER)) - 1
        out.append({"symbol": sym, "ts": int(ts[i]), "ret": r * 100, "why": why, "dev": bdev[i] * 100,
                    "mins": (ts[j] - ts[i]) / 60})
        i = j + 1
    return out


refs = {b: pd.DataFrame({ex: ref(ex, b) for ex in ("binance", "okx", "bybit")}).reindex(grid) for b in BASES}
results = {}
for var in ("A0", "A1", "B1", "AB"):
    for le in (False, True):
        tr = []
        for b in BASES:
            g = refs[b]["binance"] if var in ("A0", "B1") else refs[b].median(axis=1, skipna=False)
            uu = u if var in ("A0", "A1") else u5
            tr += run(f"{b}TMN", g * uu, le)
        results[(var, le)] = pd.DataFrame(tr)


def st(t):
    if len(t) == 0:
        return {"n": 0, "mean": None, "sum": 0.0, "win": None, "exits": {}}
    return {"n": len(t), "mean": round(t.ret.mean(), 3), "sum": round(t.ret.sum(), 2),
            "win": round((t.ret > 0).mean() * 100, 1), "exits": t.why.value_counts().to_dict()}


summ = {"params": vars(a), "half_spreads_pct": {k: round(v * 50, 4) for k, v in SPR.items() if k.endswith("TMN")},
        "spreads_pct": {k: round(v * 100, 3) for k, v in SPR.items() if k.endswith("TMN")}}
for (var, le), t in results.items():
    k = f"{var}_{'live' if le else 'bt'}"
    s = st(t)
    s["first4d"] = st(t[t.ts < SPLIT]) if len(t) else st(t)
    s["last3d"] = st(t[t.ts >= SPLIT]) if len(t) else st(t)
    s["per_mkt"] = {m: (len(g), round(g.ret.mean(), 2), round(g.ret.sum(), 2)) for m, g in t.groupby("symbol")} if len(t) else {}
    summ[k] = s
    t.to_csv(HERE / f"trades_{var}_{'live' if le else 'bt'}{a.tag}.csv", index=False)


def changed(v0, v1, le):
    t0, t1 = results[(v0, le)], results[(v1, le)]
    k0 = set(zip(t0.symbol, t0.ts)); k1 = set(zip(t1.symbol, t1.ts))
    gone = t0[[(s, x) not in k1 for s, x in zip(t0.symbol, t0.ts)]]
    new = t1[[(s, x) not in k0 for s, x in zip(t1.symbol, t1.ts)]]
    net = t1.ret.sum() - t0.ret.sum()
    big0, big1 = t0[t0.ret.abs() <= 2], t1[t1.ret.abs() <= 2]
    return {"gone": st(gone), "new": st(new), "net_total": round(net, 2),
            "net_changed_set": round(new.ret.sum() - gone.ret.sum(), 2),
            "net_excl_abs_gt2": round(big1.ret.sum() - big0.ret.sum(), 2),
            "net_first4d": round(t1[t1.ts < SPLIT].ret.sum() - t0[t0.ts < SPLIT].ret.sum(), 2),
            "net_last3d": round(t1[t1.ts >= SPLIT].ret.sum() - t0[t0.ts >= SPLIT].ret.sum(), 2),
            "net_by_mkt": {m: round(t1[t1.symbol == m].ret.sum() - t0[t0.symbol == m].ret.sum(), 2)
                           for m in sorted(set(t0.symbol) | set(t1.symbol))},
            "big_trades_v0": t0[t0.ret.abs() > 2][["symbol", "ts", "ret"]].round(2).values.tolist(),
            "big_trades_v1": t1[t1.ret.abs() > 2][["symbol", "ts", "ret"]].round(2).values.tolist(),
            "gone_list": gone[["symbol", "ts", "ret", "dev"]].round(3).values.tolist(),
            "new_list": new[["symbol", "ts", "ret", "dev"]].round(3).values.tolist()}


for v1 in ("A1", "B1", "AB"):
    for le in (False, True):
        summ[f"A0->{v1}_{'live' if le else 'bt'}"] = changed("A0", v1, le)

(HERE / f"summary{a.tag}.json").write_text(json.dumps(summ, indent=1, default=str), encoding="utf-8")
print("spreads%", summ["spreads_pct"])
for k in ("A0", "A1", "B1", "AB"):
    for e in ("bt", "live"):
        s = summ[f"{k}_{e}"]
        print(f"{k}_{e:4s} n={s['n']:3d} mean={s['mean']} sum={s['sum']} win={s['win']} ex={s['exits']} "
              f"| 4d {s['first4d']['sum']} ({s['first4d']['n']}) 3d {s['last3d']['sum']} ({s['last3d']['n']})")
for v1 in ("A1", "B1", "AB"):
    for e in ("bt", "live"):
        c = summ[f"A0->{v1}_{e}"]
        print(f"A0->{v1}_{e:4s} gone n={c['gone']['n']} sum={c['gone']['sum']} | new n={c['new']['n']} sum={c['new']['sum']} "
              f"win={c['new']['win']} | net={c['net_total']} exclBig={c['net_excl_abs_gt2']} 4d={c['net_first4d']} 3d={c['net_last3d']}")
if not a.quiet:
    for k in ("A0_bt",):
        print(k, "per market", summ[k]["per_mkt"])
