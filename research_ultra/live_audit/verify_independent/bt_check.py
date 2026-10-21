"""Independent candle backtest of the live rule + forecast (own code; reads MAIN csv files read-only, no network).

Joint minute-by-minute simulation over the 8 TMN markets with the engine's exit rules, plus the risk rules the
author's forecast ignored: max 3 open positions and a 120-minute per-market cooldown after a losing trade.
Executable prices: close x (1 +/- half of the live_main median spread). Baseline: trailing 180-min median of
the premium over traded minutes, shift(1) (known before the minute). Fees: taker 0.3% on both legs.
Also: Binance/Wallex timestamp alignment check, zero-entry window shares, walk-forward split, Poisson forecast,
and a "persistence" variant that needs the dislocation to still be there at the next traded minute
(<= 2 minutes later) and buys at that minute's modelled ask.
"""
import json
import math
import os
import sys

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
import numpy as np
import pandas as pd

MAIN = "C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot"
SYMS = ["BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN", "PEPETMN", "BNBTMN", "ARBTMN"]
TAKER = 0.003


def wallex(sym):
    p = f"{MAIN}/research/candles/{sym}_1.csv"
    if not os.path.exists(p):
        p = f"{MAIN}/research/premium/wallex_{sym}_1m.csv"
    d = pd.read_csv(p)
    d.index = pd.to_datetime(d.ts, unit="s", utc=True)
    return d[~d.index.duplicated()].sort_index()


def binance(base):
    d = pd.read_csv(f"{MAIN}/research/premium/binance_{base}USDT_1m.csv", index_col=0)
    d.index = pd.to_datetime(d.index, utc=True)
    return d["close"][~d.index.duplicated()].sort_index()


# live median spreads (own computation from live_main ticks, partial last line tolerated)
spreads = {}
with open(f"{MAIN}/runs/live_main/ticks.csv", encoding="utf-8") as fh:
    rows = fh.read().split("\n")[1:-1]
acc = {}
for ln in rows:
    p = ln.split(",")
    if len(p) == 10:
        acc.setdefault(p[1], []).append(float(p[3]) / float(p[2]) - 1)
for s, v in acc.items():
    spreads[s] = float(np.median(v))

u = wallex("USDTTMN")["close"]
u = u.reindex(pd.date_range(u.index[0], u.index[-1], freq="1min", tz="UTC")).ffill()

frames, align = {}, {}
for s in SYMS:
    w = wallex(s)
    g = binance(s[:-3])
    # alignment check on traded minutes: std of the 1-minute change of premium for Binance shifts -1, 0, +1
    al = {}
    for k in (-1, 0, 1):
        gg = g.shift(k)
        df = pd.DataFrame({"c": w.close, "v": w.volume}).join((gg * u).rename("fair"), how="inner").dropna()
        df = df[df.v > 0]
        prem = df.c / df.fair - 1
        al[k] = round(float(prem.diff().std() * 100), 4)
    align[s] = al
    df = pd.DataFrame({"c": w.close, "v": w.volume}).join((g * u).rename("fair"), how="inner").dropna()
    df = df[df.v > 0].copy()
    hs = spreads[s] / 2
    df["ask"], df["bid"] = df.c * (1 + hs), df.c * (1 - hs)
    prem = df.c / df.fair - 1
    df["base"] = prem.rolling("180min", min_periods=30).median().shift(1)
    df["bd"] = df.ask / df.fair - 1 - df.base
    df["sd"] = df.bid / df.fair - 1 - df.base
    frames[s] = df
print("alignment (std of 1-min premium change %, Binance shift -1/0/+1):", align)

T0 = max(f.index[0] for f in frames.values())
T1 = min(f.index[-1] for f in frames.values())
DAYS = (T1 - T0).total_seconds() / 86400
print(f"common window {T0} .. {T1} = {DAYS:.3f} days")

POL_CUR = {s: dict(thr=0.007, stop=0.008, hold=20, on=True) for s in SYMS}
for s in ("ETHTMN", "PEPETMN", "ARBTMN"):
    POL_CUR[s]["thr"] = 0.008
POL_CUR["ARBTMN"]["hold"] = 15
POL_CUR["BNBTMN"]["on"] = False
POL_07 = {s: dict(thr=0.007, stop=0.009, hold=25, on=True) for s in SYMS}
POL_07["ARBTMN"]["hold"] = 15


def simulate(pol, risk=True, persist=False, cooldown_min=120, max_pos=3):
    # per-market arrays
    data = {}
    for s in SYMS:
        f = frames[s][(frames[s].index >= T0) & (frames[s].index <= T1)]
        data[s] = (f.index.values.astype("datetime64[s]").astype(np.int64), f.ask.values, f.bid.values,
                   f.bd.values, f.sd.values)
    allt = np.unique(np.concatenate([data[s][0] for s in SYMS]))
    ptr = {s: 0 for s in SYMS}
    pos, cool, trades = {}, {}, []
    pending = {}   # persistence: s -> minute index of first signal
    for t in allt:
        for s in SYMS:
            ts_, ask, bid, bd, sd = data[s]
            i = ptr[s]
            if i >= len(ts_) or ts_[i] != t:
                continue
            ptr[s] = i + 1
            P = pol[s]
            if s in pos:
                e, t_in, edev = pos[s]
                why = None
                if bid[i] <= e * (1 - P["stop"]):
                    why = "stop"
                elif sd[i] >= -0.0005 and bid[i] >= e * (1 + 2 * TAKER + 0.0005):
                    why = "target"
                elif t - t_in >= P["hold"] * 60:
                    why = "time"
                if why:
                    r = bid[i] * (1 - TAKER) ** 2 / e - 1
                    trades.append(dict(symbol=s, t=int(t_in), t_out=int(t), ret=r * 100, why=why, edev=edev * 100))
                    del pos[s]
                    if risk and r < 0:
                        cool[s] = t + cooldown_min * 60
                continue          # no re-entry in the same minute
            if not P["on"] or not np.isfinite(bd[i]):
                pending.pop(s, None)
                continue
            sig = bd[i] <= -P["thr"]
            if persist:
                prev = pending.pop(s, None)
                if sig and prev is None:
                    pending[s] = t
                    continue
                if not (sig and prev is not None and t - prev <= 120):
                    if sig:
                        pending[s] = t
                    continue
            elif not sig:
                continue
            if risk and (cool.get(s, 0) > t or len(pos) >= max_pos):
                continue
            pos[s] = (ask[i], t, bd[i])
    return pd.DataFrame(trades)


def summarize(tr, notional_frac, label):
    out = {"label": label, "trades": int(len(tr))}
    if not len(tr):
        return out
    rate = len(tr) / DAYS
    rem = (pd.Timestamp("2026-10-23 17:50", tz="UTC") - pd.Timestamp("2026-10-21 18:40", tz="UTC")).total_seconds() / 86400
    lam = rate * rem
    # Poisson 90% interval of the count
    def pq(lmb, p):
        c, k, term = 0.0, 0, math.exp(-lmb)
        while True:
            c += term
            if c >= p:
                return k
            k += 1
            term *= lmb / k
    m, sd = tr.ret.mean(), tr.ret.std(ddof=1)
    half = len(tr) // 2
    tr = tr.sort_values("t")
    day4 = T0.value // 10**9 + 4 * 86400
    a, b = tr[tr.t < day4], tr[tr.t >= day4]
    out.update({
        "per_day": round(rate, 2), "mean_net_pct": round(m, 3), "sum_net_pct": round(tr.ret.sum(), 2),
        "median_net_pct": round(tr.ret.median(), 3), "win_pct": round(100 * (tr.ret > 0).mean(), 1),
        "t_stat": round(m / (sd / math.sqrt(len(tr))), 2) if len(tr) > 1 else None,
        "exits": tr.why.value_counts().to_dict(),
        "per_market": {s: {"n": int(len(g)), "mean": round(g.ret.mean(), 3), "sum": round(g.ret.sum(), 2),
                           "exits": g.why.value_counts().to_dict()} for s, g in tr.groupby("symbol")},
        "first4d": {"n": int(len(a)), "per_day": round(len(a) / 4, 2), "mean": round(a.ret.mean(), 3) if len(a) else None},
        "last": {"n": int(len(b)), "per_day": round(len(b) / (DAYS - 4), 2), "mean": round(b.ret.mean(), 3) if len(b) else None},
        "remaining_days": round(rem, 3), "expected_trades": round(lam, 1),
        "poisson90": [pq(lam, 0.05), pq(lam, 0.95)],
        "expected_account_ret_pct": round(lam * m * notional_frac, 3),
        "distinct_days_with_trades": int(pd.to_datetime(tr.t, unit="s").dt.date.nunique()),
    })
    # share of sliding windows (step 5 min) with zero entries
    ent = np.sort(tr.t.values)
    starts = np.arange(T0.value // 10**9, T1.value // 10**9, 300)
    zs = {}
    for h in (1.7, 1.9, 3.8, 6, 12, 24):
        ends = starts + h * 3600
        ok = ends <= T1.value // 10**9
        cnt = np.searchsorted(ent, ends[ok]) - np.searchsorted(ent, starts[ok])
        zs[f"{h}h"] = round(100 * float((cnt == 0).mean()), 1)
    out["zero_window_pct"] = zs
    # market concentration
    top = tr.symbol.value_counts()
    out["share_PEPE_ARB_BNB_pct"] = round(100 * top[[x for x in ("PEPETMN", "ARBTMN", "BNBTMN") if x in top]].sum() / len(tr), 1)
    # largest single-cluster dependence: trades within 60 min of another trade
    out["trades_in_clusters_60min"] = int(sum(1 for i, x in enumerate(ent)
                                            if (i > 0 and x - ent[i - 1] <= 3600) or (i + 1 < len(ent) and ent[i + 1] - x <= 3600)))
    return out


res = {"spreads_live_median_pct": {k: round(v * 100, 3) for k, v in spreads.items()}, "alignment": align,
       "days": round(DAYS, 3)}
notional_cur = 5e6 * 0.006 * 0.3 / (0.008 + 0.003 + 0.0025) / 5e6
notional_07 = min(5e6 * 0.006 * 0.4 / (0.009 + 0.003 + 0.0025), 0.35 * 5e6) / 5e6
variants = [
    ("current_policy_no_risk_rules", POL_CUR, dict(risk=False), notional_cur),
    ("current_policy_with_cooldown_maxpos", POL_CUR, dict(risk=True), notional_cur),
    ("current_policy_persist_1min", POL_CUR, dict(risk=True, persist=True), notional_cur),
    ("all_0.7_no_risk_rules", POL_07, dict(risk=False), notional_07),
    ("all_0.7_with_cooldown_maxpos", POL_07, dict(risk=True), notional_07),
]
for name, pol, kw, nf in variants:
    tr = simulate(pol, **kw)
    s = summarize(tr, nf, name)
    res[name] = s
    if len(tr):
        tr.to_csv(f"trades_{name}.csv", index=False)
    print(json.dumps(s, default=str))

# candle buy_dev tail vs live ticks: share of sliding 2h / 4h windows where NO traded minute reached -0.5 / -0.6 %
tails = {}
for s in SYMS:
    f = frames[s][(frames[s].index >= T0) & (frames[s].index <= T1)]
    tsec = f.index.values.astype("datetime64[s]").astype(np.int64)
    bd = f.bd.values
    starts = np.arange(tsec[0], tsec[-1], 300)
    d = {"p1_pct": round(float(np.nanpercentile(bd, 1) * 100), 3),
         "pct_traded_minutes_le_-0.5": round(100 * float(np.nanmean(bd <= -0.005)), 2),
         "traded_minutes_per_day": round(len(f) / DAYS)}
    for th in (0.005, 0.006):
        hit = np.sort(tsec[bd <= -th])
        for h in (2, 4):
            ends = starts + h * 3600
            ok = ends <= tsec[-1]
            cnt = np.searchsorted(hit, ends[ok]) - np.searchsorted(hit, starts[ok])
            d[f"zero_win_{h}h_le_-{th*100:.1f}"] = round(100 * float((cnt == 0).mean()), 1)
    tails[s] = d
res["candle_tails"] = tails
print(json.dumps(tails, indent=0))
json.dump(res, open("bt_check.json", "w"), indent=1, default=str)
