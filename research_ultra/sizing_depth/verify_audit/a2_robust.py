"""Audit 2: robustness of the per-trade edge in the author's 'live_tick_ff' backtest (base of every 45 h number).
 (a) reproduce live_tick_ff with an independent copy of the backtest loop
 (b) entry delay: signal on minute i close, buy at the modelled ask of the NEXT traded minute (i+1) if it is
     still <= -0.7% (engine-realistic: the cheap print must still be there) and unconditionally (pure latency)
 (c) bootstrap CI of mean net %/trade, leave-top-5-out, per-half
 (d) full-spread entry + nogap combined (most pessimistic data-hygiene case)
Reads files only."""
import json, numpy as np, pandas as pd
MAINR = "C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot/research"
AUD = "C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot-ultra/research_ultra/sizing_depth/verify_audit/out"
import os; os.makedirs(AUD, exist_ok=True)
TMN8 = ["BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN", "PEPETMN", "BNBTMN", "ARBTMN"]
TAKER, SLACK, STOP, ENTRY = 0.003, 0.0005, 0.009, 0.007
bt = json.load(open("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot-ultra/research_ultra/sizing_depth/out/bt_sizing.json", encoding="utf-8"))
SPR = bt["spreads_tick_pct"]

def wallex(sym):
    p = f"{MAINR}/candles/{sym}_1.csv"
    if not os.path.exists(p):
        p = f"{MAINR}/premium/wallex_{sym}_1m.csv"
    raw = pd.read_csv(p)
    idx = pd.to_datetime(raw.ts.astype("int64"), unit="s", utc=True)
    df = pd.DataFrame({"c": raw.close.astype(float).to_numpy(), "v": raw.volume.astype(float).to_numpy()}, index=idx)
    return df[~df.index.duplicated(keep="last")].sort_index()

def binance(base):
    s = pd.read_csv(f"{MAINR}/premium/binance_{base}USDT_1m.csv", index_col=0, parse_dates=True)["close"]
    s.index = pd.to_datetime(s.index, utc=True)
    return s[~s.index.duplicated(keep="last")].sort_index()

u_all = wallex("USDTTMN")
u_raw = u_all[u_all.v > 0].c
full = pd.date_range(u_raw.index[0], u_raw.index[-1], freq="1min", tz="UTC")
usdt_ff = u_all.c.reindex(full).ffill()
last_real = pd.Series(u_raw.index, index=u_raw.index).reindex(full).ffill()
uage = ((full.to_series() - last_real).dt.total_seconds() / 60)

def bt_run(sym, delay=0, recheck=True, entry_mult=1.0, nogap=False):
    g = binance(sym[:-3]); w = wallex(sym)
    fair = (g * usdt_ff).dropna()
    df = pd.DataFrame({"c": w.c, "v": w.v}).join(fair.rename("fair"), how="inner").dropna()
    df["gret"] = g.pct_change().reindex(df.index)
    df["uage"] = uage.reindex(df.index)
    df = df[df.v > 0]
    hs = SPR[sym] / 100 / 2
    df["ask"], df["bid"] = df.c * (1 + hs * entry_mult), df.c * (1 - hs)
    base = (df.c / df.fair - 1).rolling("180min", min_periods=30).median().shift(1)
    bd = (df.ask / df.fair - 1 - base).to_numpy(); sd = (df.bid / df.fair - 1 - base).to_numpy()
    ask, bid, idx, gret, ua = df.ask.to_numpy(), df.bid.to_numpy(), df.index, df.gret.to_numpy(), df.uage.to_numpy()
    hold = 15 if sym == "ARBTMN" else 25
    out, i, n = [], 0, len(df)
    while i < n - 1 - delay:
        ok = np.isfinite(bd[i]) and bd[i] <= -ENTRY and not (np.isfinite(gret[i]) and gret[i] < -0.004)
        if ok and nogap and not (ua[i] <= 5):
            ok = False
        if not ok:
            i += 1; continue
        k = i + delay
        if delay:
            # the next traded minute must be within 2 minutes, else the signal is stale
            if (idx[k] - idx[i]).total_seconds() > 120 or (recheck and not (np.isfinite(bd[k]) and bd[k] <= -ENTRY)):
                i += 1; continue
        e = ask[k]; j, why = k + 1, "end"
        while j < n:
            if bid[j] <= e * (1 - STOP): why = "stop"; break
            if sd[j] >= -SLACK and bid[j] >= e * (1 + 2 * TAKER + 0.0005): why = "target"; break
            if (idx[j] - idx[k]).total_seconds() >= hold * 60: why = "time"; break
            j += 1
        j = min(j, n - 1)
        r = bid[j] * (1 - TAKER) / (e * (1 + TAKER)) - 1
        out.append({"symbol": sym, "t": idx[k], "ret_pct": r * 100, "why": why})
        i = j + 1
    return pd.DataFrame(out)

def st(tr):
    if not len(tr): return {"trades": 0}
    r = tr.ret_pct
    return {"trades": int(len(r)), "mean": round(r.mean(), 3), "sum": round(r.sum(), 2), "win": round((r > 0).mean() * 100, 1),
            "exits": tr.why.value_counts().to_dict(), "by_mkt": tr.groupby("symbol").ret_pct.agg(["count", "mean"]).round(3).to_dict("index")}

res = {}
for name, kw in {"repro_live_tick_ff": {}, "delay1_recheck": {"delay": 1, "recheck": True},
                 "delay1_no_recheck": {"delay": 1, "recheck": False}, "fullentry_nogap": {"entry_mult": 2.0, "nogap": True},
                 "nogap": {"nogap": True}}.items():
    tr = pd.concat([x for x in (bt_run(s, **kw) for s in TMN8) if len(x)], ignore_index=True)
    res[name] = st(tr)
    if name == "repro_live_tick_ff":
        base_tr = tr
    print(name, {k: v for k, v in res[name].items() if k != "by_mkt"})
    print("   ", {s: (d["count"], d["mean"]) for s, d in res[name]["by_mkt"].items()})

# bootstrap / leave-top-out on the base trades
r = base_tr.ret_pct.to_numpy()
rng = np.random.default_rng(1)
bs = np.array([rng.choice(r, len(r)).mean() for _ in range(20000)])
srt = np.sort(r)
res["base_stats"] = {"n": len(r), "mean": round(r.mean(), 3), "sd": round(r.std(ddof=1), 3), "se": round(r.std(ddof=1) / np.sqrt(len(r)), 3),
                     "boot95": [round(np.quantile(bs, 0.025), 3), round(np.quantile(bs, 0.975), 3)], "p_mean_le_0": round((bs <= 0).mean(), 4),
                     "mean_without_top5": round(srt[:-5].mean(), 3), "mean_without_top3": round(srt[:-3].mean(), 3),
                     "top5_share_of_sum": round(srt[-5:].sum() / r.sum(), 3), "top5": [round(x, 3) for x in srt[-5:]]}
print(res["base_stats"])
json.dump(res, open(f"{AUD}/a2_robust.json", "w", encoding="utf-8"), indent=1, default=str)
