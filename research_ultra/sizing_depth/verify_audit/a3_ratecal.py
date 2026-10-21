"""Audit 3: opportunity-rate calibration by threshold, in MINUTES and in EPISODES, candle model vs ticks.
Ticks: author's frozen snapshot (<=18:15 UTC) and an extended snapshot (all complete lines now).
Candle model: the author's live_tick_ff signal definition (half-spread ask, shift(1) 180-min median baseline).
Episode = run of consecutive qualifying candle minutes (gap <= 2 min) / qualifying ticks (gap <= 10 s)."""
import io, json, os, numpy as np, pandas as pd
MAIN = "C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot"
AUD = "C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot-ultra/research_ultra/sizing_depth/verify_audit/out"
TMN8 = ["BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN", "PEPETMN", "BNBTMN", "ARBTMN"]
RUNS = ["shadow_v2", "shadow_v3", "shadow_v4", "shadow_v5", "shadow_v6", "live_main"]
THR = [0.003, 0.004, 0.005, 0.006, 0.007]

def snap(p):
    txt = open(p, encoding="utf-8").read()
    lines = txt.split("\n")
    if not txt.endswith("\n"): lines = lines[:-1]
    return pd.read_csv(io.StringIO("\n".join(lines)), on_bad_lines="skip")
fr = []
for r in RUNS:
    d = snap(f"{MAIN}/runs/{r}/ticks.csv"); d["run"] = r; fr.append(d)
T = pd.concat(fr, ignore_index=True)
for c in ["t", "bid", "ask", "buy_dev"]: T[c] = pd.to_numeric(T[c], errors="coerce")
T = T.dropna(subset=["t", "bid", "ask", "buy_dev"])
T = T[T.symbol.isin(TMN8)].sort_values(["symbol", "t"])
T["spread"] = T.ask / T.bid - 1

def tick_eps(g, x):
    q = g[(g.buy_dev <= -x)]
    if not len(q): return 0
    return int((q.t.diff().fillna(1e9) > 10).sum())

def wallex(sym):
    p = f"{MAIN}/research/candles/{sym}_1.csv"
    if not os.path.exists(p): p = f"{MAIN}/research/premium/wallex_{sym}_1m.csv"
    raw = pd.read_csv(p); idx = pd.to_datetime(raw.ts.astype("int64"), unit="s", utc=True)
    df = pd.DataFrame({"c": raw.close.astype(float).to_numpy(), "v": raw.volume.astype(float).to_numpy()}, index=idx)
    return df[~df.index.duplicated(keep="last")].sort_index()
def binance(base):
    s = pd.read_csv(f"{MAIN}/research/premium/binance_{base}USDT_1m.csv", index_col=0, parse_dates=True)["close"]
    s.index = pd.to_datetime(s.index, utc=True); return s[~s.index.duplicated(keep="last")].sort_index()
u = wallex("USDTTMN").c; u = u.reindex(pd.date_range(u.index[0], u.index[-1], freq="1min", tz="UTC")).ffill()
SPR = json.load(open("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot-ultra/research_ultra/sizing_depth/out/bt_sizing.json", encoding="utf-8"))["spreads_tick_pct"]

res = {}
for label, cutoff in [("frozen_1815", 1792606500.0), ("extended_now", 1e12)]:
    tk_all = T[T.t <= cutoff]
    tot = {f"{x*100:.1f}%": {"cand_min_exp": 0.0, "tick_min_obs": 0.0, "cand_eps_exp": 0.0, "tick_eps_obs": 0} for x in THR}
    hours = {}
    for s in TMN8:
        tk = tk_all[tk_all.symbol == s]
        th = len(tk) * 2.5 / 3600; hours[s] = round(th, 2)
        g = binance(s[:-3]); w = wallex(s)
        df = pd.DataFrame({"c": w.c, "v": w.v}).join((g * u).rename("fair"), how="inner").dropna()
        df = df[df.v > 0]
        ch = (df.index[-1] - df.index[0]).total_seconds() / 3600
        base = (df.c / df.fair - 1).rolling("180min", min_periods=30).median().shift(1)
        bd = df.c * (1 + SPR[s] / 200) / df.fair - 1 - base
        for x in THR:
            k = f"{x*100:.1f}%"
            m = bd <= -x
            idx = df.index[m.fillna(False).to_numpy()]
            eps = int((pd.Series(idx).diff().dt.total_seconds().fillna(1e9) > 120).sum()) if len(idx) else 0
            tot[k]["cand_min_exp"] += m.sum() / ch * th
            tot[k]["cand_eps_exp"] += eps / ch * th
            tot[k]["tick_min_obs"] += (tk.buy_dev <= -x).sum() * 2.5 / 60
            tot[k]["tick_eps_obs"] += tick_eps(tk, x)
    for k, v in tot.items():
        v["ratio_min"] = round(v["tick_min_obs"] / v["cand_min_exp"], 4) if v["cand_min_exp"] else None
        v["ratio_eps"] = round(v["tick_eps_obs"] / v["cand_eps_exp"], 4) if v["cand_eps_exp"] else None
        v["poisson_P(obs_eps<=k | exp)"] = round(float(sum(np.exp(-v["cand_eps_exp"]) * v["cand_eps_exp"] ** i / np.math.factorial(i) for i in range(int(v["tick_eps_obs"]) + 1))), 4) if hasattr(np, "math") else None
        for kk in ("cand_min_exp", "tick_min_obs", "cand_eps_exp"): v[kk] = round(float(v[kk]), 2)
    res[label] = {"tick_hours": hours, "tick_end_utc": str(pd.to_datetime(tk_all.t.max(), unit="s")), "by_threshold": tot}
    print(label, res[label]["tick_end_utc"], hours)
    for k, v in tot.items(): print("  ", k, v)
json.dump(res, open(f"{AUD}/a3_ratecal.json", "w", encoding="utf-8"), indent=1, default=str)
