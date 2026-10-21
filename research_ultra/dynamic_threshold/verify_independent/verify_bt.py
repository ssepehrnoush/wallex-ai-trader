"""Independent re-implementation (written from scratch, NOT using the author's code) of the
dynamic-threshold walk-forward comparison.

Data (read-only from MAIN): Wallex 1m candles (8 TMN markets, ARB from research/premium), Wallex USDTTMN 1m,
Binance 1m closes (+ the 10-14 14:00-17:50 gap fetched by fetch_gap.py into ./data), live tick spreads.

Model: only minutes with Wallex volume > 0; fair = Binance close x USDTTMN close (USDTTMN forward-filled on the
minute grid); premium baseline = rolling 180-min median shifted 1 row; ask/bid = close x (1 +/- half median
live spread). Entry: buy_dev <= -thr, spread <= 0.6%, Binance 1-min return >= -0.4%, not in 120-min post-loss
cooldown, one position per market. Exit (checked from the next traded minute): stop bid <= e(1-0.9%); target
sell_dev >= -0.05% and bid >= e(1+2*0.3%+0.05%); time held >= 25 min (ARB 15). Net = (1-0.003)^2 * bid/ask - 1.

  python verify_bt.py [--spread-mult 1.0] [--time-exit-ffill] [--split-days 4] [--minp 30] [--tag main]
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("--spread-mult", type=float, default=1.0)
ap.add_argument("--time-exit-ffill", action="store_true",
                help="if no trade happens near the time limit, exit at the last known price before it")
ap.add_argument("--split-days", type=float, default=4.0)
ap.add_argument("--minp", type=int, default=30)
ap.add_argument("--no-gap", action="store_true", help="do not use the fetched Binance gap")
ap.add_argument("--day-only", action="store_true", help="only enter when USDTTMN traded in the last 10 min")
ap.add_argument("--reverse", action="store_true", help="choose on the last days, test on the first ones")
ap.add_argument("--sd-nan-block", action="store_true", help="V2: no entry while the std is unknown (default: fall back to the floor)")
ap.add_argument("--tick-cutoff", type=float, default=1792606800, help="use ticks up to this unix time (frozen snapshot)")
ap.add_argument("--tag", default="main")
a = ap.parse_args()

MAIN = Path("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot")
HERE = Path(__file__).resolve().parent
SYMS = ["BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN", "PEPETMN", "BNBTMN", "ARBTMN"]
TAKER, SLACK, STOP, GDROP, MAXSPR, COOL = 0.003, 0.0005, 0.009, 0.004, 0.006, 120
FLOOR = 2 * TAKER + 0.001
BE = 1 + 2 * TAKER + 0.0005
START = pd.Timestamp("2026-10-14 14:05", tz="UTC")
END = pd.Timestamp("2026-10-21 14:05", tz="UTC")
SPLIT = START + pd.Timedelta(days=a.split_days)
THR_GRID = [0.007, 0.008, 0.009, 0.010, 0.012, 0.015]
K_GRID = [1.5, 2.0, 2.5, 3.0, 4.0]


def wallex(sym):
    p = MAIN / "research" / "candles" / f"{sym}_1.csv"
    if not p.exists():
        p = MAIN / "research" / "premium" / f"wallex_{sym}_1m.csv"
    d = pd.read_csv(p)
    d.index = pd.to_datetime(d.ts, unit="s", utc=True)
    return d.sort_index()


def binance(base):
    d = pd.read_csv(MAIN / "research" / "premium" / f"binance_{base}USDT_1m.csv", index_col=0)
    d.index = pd.to_datetime(d.index, utc=True)
    s = d["close"].astype(float)
    if not a.no_gap:
        g = pd.read_csv(HERE / "data" / f"gap_{base}USDT.csv")
        g.index = pd.to_datetime(g.t, utc=True)
        gs = g["close"].astype(float)
        ov = s.index.intersection(gs.index)
        if len(ov):   # alignment check on the overlap
            diff = (s[ov] / gs[ov] - 1).abs().max()
            assert diff < 1e-9, (base, diff)
        s = pd.concat([gs[~gs.index.isin(s.index)], s]).sort_index()
    return s


# ---- live spreads (median ask/bid-1 over all non-smoke tick runs) ----
frames = []
for r in ["shadow_v2", "shadow_v3", "shadow_v4", "shadow_v5", "shadow_v6", "live_main"]:
    t = pd.read_csv(MAIN / "runs" / r / "ticks.csv", on_bad_lines="skip")
    t = t[pd.to_numeric(t.bid, errors="coerce").notna() & pd.to_numeric(t.ask, errors="coerce").notna()]
    t = t[pd.to_numeric(t.t, errors="coerce") <= a.tick_cutoff]
    frames.append(t[["symbol", "bid", "ask"]].astype({"bid": float, "ask": float}))
ticks = pd.concat(frames)
ticks = ticks[(ticks.bid > 0) & (ticks.ask > 0)]
SPREAD = (ticks.ask / ticks.bid - 1).groupby(ticks.symbol).median().to_dict()
NTICK = ticks.groupby("symbol").size().to_dict()

u = wallex("USDTTMN")
grid = pd.date_range(u.index[0].floor("min"), END + pd.Timedelta(hours=3), freq="1min", tz="UTC")
usdt = u["close"].reindex(grid).ffill()
u_traded = (u["volume"] > 0).astype(float).reindex(grid).fillna(0)
u_recent = u_traded.rolling(10, min_periods=1).max()   # USDTTMN traded in the last 10 min (incl. this one)


def prepare(sym):
    w = wallex(sym)
    w = w[(w.volume > 0) & (w.index >= START)]
    b = binance(sym[:-3])
    bg = b.reindex(grid)
    mom = bg / bg.shift(1) - 1
    df = pd.DataFrame({"c": w.close.astype(float)})
    df["fair"] = bg.reindex(df.index) * usdt.reindex(df.index)
    df["mom"] = mom.reindex(df.index)
    df["uday"] = u_recent.reindex(df.index)
    df = df.dropna(subset=["fair"])
    hs = SPREAD[sym] * a.spread_mult / 2
    df["ask"], df["bid"] = df.c * (1 + hs), df.c * (1 - hs)
    prem = df.c / df.fair - 1
    df["base"] = prem.rolling("180min", min_periods=a.minp).median().shift(1)
    df["buy_dev"] = df.ask / df.fair - 1 - df.base
    df["sell_dev"] = df.bid / df.fair - 1 - df.base
    df["sd"] = df.buy_dev.rolling("180min", min_periods=a.minp).std().shift(1)
    return df, hs


def simulate(sym, df, thr, hs):
    """thr: scalar or array aligned with df. Returns list of trade dicts (entries in [START, END))."""
    n = len(df)
    t = df.index.values.astype("datetime64[s]").astype(np.int64)
    ask, bid, bd, sdv = df.ask.values, df.bid.values, df.buy_dev.values, df.sell_dev.values
    mom, uday = df.mom.values, df.uday.values
    thr = np.broadcast_to(np.asarray(thr, dtype=float), (n,))
    hold = (15 if sym == "ARBTMN" else 25) * 60
    endt = int(END.timestamp())
    spread_ok = 2 * hs <= MAXSPR
    out, cool, i = [], -1, 0
    while i < n - 1:
        if t[i] >= endt:
            break
        ok = (t[i] >= cool and spread_ok and np.isfinite(bd[i]) and bd[i] <= -max(thr[i], FLOOR)
              and not (np.isfinite(mom[i]) and mom[i] < -GDROP) and (not a.day_only or uday[i] > 0))
        if not ok:
            i += 1
            continue
        e = ask[i]
        j, why, px, tx = i + 1, "end", None, None
        while j < n:
            if a.time_exit_ffill and t[j] - t[i] > hold + 60:
                why, px, tx = "time", bid[j - 1], t[i] + hold   # no trade near the limit: last known bid
                break
            if bid[j] <= e * (1 - STOP):
                why = "stop"
                break
            if np.isfinite(sdv[j]) and sdv[j] >= -SLACK and bid[j] >= e * BE:
                why = "target"
                break
            if t[j] - t[i] >= hold:
                why = "time"
                break
            j += 1
        j = min(j, n - 1)
        if px is None:
            px, tx = bid[j], t[j]
        r = (1 - TAKER) ** 2 * px / e - 1
        out.append({"symbol": sym, "t": int(t[i]), "texit": int(tx), "net": r * 100, "why": why,
                    "dev": bd[i] * 100, "held_min": (tx - t[i]) / 60})
        if r < 0:
            cool = tx + COOL * 60
        i = j + 1
    return out


def split(trades):
    s = int(SPLIT.timestamp())
    is_ = [x for x in trades if x["t"] < s]
    oos = [x for x in trades if x["t"] >= s]
    return (oos, is_) if a.reverse else (is_, oos)


def stats(tr):
    if not tr:
        return {"n": 0, "mean": None, "sum": 0.0, "win": None, "exits": {}}
    v = np.array([x["net"] for x in tr])
    ex = pd.Series([x["why"] for x in tr]).value_counts().to_dict()
    return {"n": len(tr), "mean": round(v.mean(), 3), "sum": round(v.sum(), 3), "win": round((v > 0).mean() * 100, 1),
            "exits": ex}


data = {s: prepare(s) for s in SYMS}

# ---- fixed thresholds per market ----
fixed = {}   # (sym, thr) -> trades
for s, (df, hs) in data.items():
    for th in THR_GRID:
        fixed[(s, th)] = simulate(s, df, th, hs)

# V4
V4 = {s: fixed[(s, 0.007)] for s in SYMS}
# V1: per-market thr chosen on the selection period by sum net (ties -> lowest thr)
v1_thr, V1 = {}, {}
for s in SYMS:
    best = max(THR_GRID, key=lambda th: (round(sum(x["net"] for x in split(fixed[(s, th)])[0]), 9), -th))
    v1_thr[s] = best
    V1[s] = fixed[(s, best)]
# V2: k x trailing std, k global
vol = {}
for k in K_GRID:
    for s, (df, hs) in data.items():
        thr = np.maximum(FLOOR, k * df.sd.fillna(np.inf if a.sd_nan_block else 0.0).values)   # std unknown -> floor (or block)
        vol[(s, k)] = simulate(s, df, thr, hs)
k_tot = {k: sum(sum(x["net"] for x in split(vol[(s, k)])[0]) for s in SYMS) for k in K_GRID}
k_best = max(K_GRID, key=lambda k: (round(k_tot[k], 9), -k))
V2 = {s: vol[(s, k_best)] for s in SYMS}
# V2b: k per market
v2b_k, V2b = {}, {}
for s in SYMS:
    kb = max(K_GRID, key=lambda k: (round(sum(x["net"] for x in split(vol[(s, k)])[0]), 9), -k))
    v2b_k[s] = kb
    V2b[s] = vol[(s, kb)]
# V3: 0.7% + half spread
V3 = {s: simulate(s, data[s][0], FLOOR + data[s][1], data[s][1]) for s in SYMS}
# ORACLE: per-market thr chosen on the evaluation period itself (not tradable)
OR = {}
for s in SYMS:
    best = max(THR_GRID, key=lambda th: (round(sum(x["net"] for x in split(fixed[(s, th)])[1]), 9), -th))
    OR[s] = fixed[(s, best)]

variants = {"V4_fixed0.7": V4, "V1_per_market": V1, f"V2_vol_k{k_best}": V2, "V2b_vol_k_per_mkt": V2b,
            "V3_0.7+half_spread": V3, "ORACLE": OR}


def flat(V):
    return sorted([x for s in SYMS for x in V[s]], key=lambda x: x["t"])


# ---- block bootstrap of the evaluation-period difference vs V4 ----
rng = np.random.default_rng(12345)
ev_lo, ev_hi = (START, SPLIT) if a.reverse else (SPLIT, END)
nblk = int(np.ceil((ev_hi - ev_lo).total_seconds() / 7200))


def blocks(tr):
    v = np.zeros(nblk)
    for x in tr:
        bi = int((x["t"] - ev_lo.timestamp()) // 7200)
        if 0 <= bi < nblk:
            v[bi] += x["net"]
    return v


b4 = blocks(split(flat(V4))[1])
res = {"tag": a.tag, "args": vars(a), "half_spread_pct": {s: round(data[s][1] * 100, 4) for s in SYMS},
       "ticks_per_symbol": {s: NTICK.get(s, 0) for s in SYMS},
       "traded_minutes": {s: len(data[s][0]) for s in SYMS},
       "v1_thr_pct": {s: round(v1_thr[s] * 100, 2) for s in SYMS}, "k_is_totals": {k: round(v, 3) for k, v in k_tot.items()},
       "k_best": k_best, "v2b_k": v2b_k, "variants": {}}
for name, V in variants.items():
    al = flat(V)
    is_, oos = split(al)
    bv = blocks(oos)
    d = bv - b4
    draws = np.array([d[rng.integers(0, nblk, nblk)].sum() for _ in range(5000)])
    per = {}
    for s in SYMS:
        _, o = split(V[s])
        st = stats(o)
        per[s] = f"{st['n']} / {st['mean']} / {st['sum']}"
    res["variants"][name] = {"IS": stats(is_), "OOS": stats(oos), "OOS_diff_vs_V4": round(d.sum(), 3),
                             "diff_CI90": [round(np.percentile(draws, 5), 2), round(np.percentile(draws, 95), 2)],
                             "P(diff>0)": round((draws > 0).mean(), 3), "OOS_per_market": per}
# single threshold table
res["single_thr"] = {}
for th in THR_GRID:
    al = sorted([x for s in SYMS for x in fixed[(s, th)]], key=lambda x: x["t"])
    i_, o_ = split(al)
    res["single_thr"][f"{th * 100:.1f}"] = {"IS_n": len(i_), "IS_sum": round(sum(x["net"] for x in i_), 3),
                                            "OOS_n": len(o_), "OOS_sum": round(sum(x["net"] for x in o_), 3)}
# dev-band attribution for V4 (where do the marginal trades sit)
al = flat(V4)
i_, o_ = split(al)
bands = [(-9, -1.5), (-1.5, -1.2), (-1.2, -1.0), (-1.0, -0.9), (-0.9, -0.8), (-0.8, -0.7)]
res["v4_dev_bands"] = {}
for lo, hi in bands:
    f = lambda L: [x["net"] for x in L if lo < x["dev"] <= hi]
    res["v4_dev_bands"][f"{lo}..{hi}"] = {"IS_n": len(f(i_)), "IS_sum": round(sum(f(i_)), 3),
                                          "OOS_n": len(f(o_)), "OOS_sum": round(sum(f(o_)), 3)}
# overlap check for the 3-position cap (V4)
ev = sorted([(x["t"], 1) for x in al] + [(x["texit"], -1) for x in al])
cur = mx = 0
for _, dlt in ev:
    cur += dlt
    mx = max(mx, cur)
res["v4_max_concurrent"] = mx
# night share (USDTTMN not traded in the last 10 min at entry)
res["v4_night_share_OOS"] = round(np.mean([data[x["symbol"]][0].uday.get(pd.Timestamp(x["t"], unit="s", tz="UTC"), 1) == 0
                                           for x in o_]), 3) if o_ else None

out = HERE / "out"
out.mkdir(exist_ok=True)
(out / f"result_{a.tag}.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
pd.DataFrame(flat(V4)).to_csv(out / f"trades_V4_{a.tag}.csv", index=False)
pd.DataFrame(flat(V1)).to_csv(out / f"trades_V1_{a.tag}.csv", index=False)

print("tag", a.tag, "| split", SPLIT, "| reverse", a.reverse)
print("half spreads %", res["half_spread_pct"])
print("V1 thr", res["v1_thr_pct"], "| k totals", res["k_is_totals"], "best k", k_best, "| V2b k", v2b_k)
hdr = f"{'variant':22s} {'ISn':>4s} {'ISmean':>7s} {'ISsum':>7s} {'ISwin':>6s} | {'OOSn':>4s} {'mean':>7s} {'sum':>7s} {'win':>6s} {'diff':>7s} {'CI90':>16s} {'P>0':>5s} exits"
print(hdr)
for name, r in res["variants"].items():
    I, O = r["IS"], r["OOS"]
    print(f"{name:22s} {I['n']:4d} {str(I['mean']):>7s} {I['sum']:7.3f} {str(I['win']):>6s} | {O['n']:4d} {str(O['mean']):>7s} "
          f"{O['sum']:7.3f} {str(O['win']):>6s} {r['OOS_diff_vs_V4']:7.2f} {str(r['diff_CI90']):>16s} {r['P(diff>0)']:5.2f} {O['exits']}")
print("per market OOS (n / mean / sum):")
for s in SYMS:
    print(f"  {s:8s}", " | ".join(f"{n[:6]}: {r['OOS_per_market'][s]}" for n, r in res["variants"].items()))
print("single thr:", json.dumps(res["single_thr"]))
print("V4 dev bands:", json.dumps(res["v4_dev_bands"]))
print("V4 max concurrent:", mx, "| V4 OOS night share:", res["v4_night_share_OOS"])
