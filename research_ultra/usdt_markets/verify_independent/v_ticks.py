"""Independent replay of the live rule on the recorded 2.5 s ticks of the 5 USDT markets (shadow_v2..v6).

Uses the engine's own live buy_dev / sell_dev columns (computed in real time, so no look-ahead).
Entry: buy_dev <= -max(thr, 0.36%), spread <= 0.6%, fair (= Binance mid) 60 s change >= -0.4%.
Fill at the ask (depth: top ask qty recorded). Exits on later ticks: stop (bid <= e*(1-stop)), target
(sell_dev >= -0.05% AND bid >= e*(1+2*0.13%+0.05%)), time (>= 25 min); exit at the bid
(optionally a penalty on stop/time). Taker 0.13% both legs. Open at the end of data = marked at the last bid.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

MAIN = Path("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot")
OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
TAKER = 0.0013

fr = []
for r in ["shadow_v2", "shadow_v3", "shadow_v4", "shadow_v5", "shadow_v6"]:
    t = pd.read_csv(MAIN / "runs" / r / "ticks.csv")
    t["run"] = r
    fr.append(t[t.symbol.isin(SYMS)])
T = pd.concat(fr).sort_values("t").reset_index(drop=True)
T["spread"] = T.ask / T.bid - 1
span_min = (T.t.max() - T.t.min()) / 60
print("ticks", len(T), "span min", round(span_min, 1), pd.to_datetime(T.t.min(), unit="s"), "->",
      pd.to_datetime(T.t.max(), unit="s"))
print("median spread %", (T.groupby("symbol").spread.median() * 100).round(3).to_dict())
print("share spread>0.6%", (T.groupby("symbol").spread.apply(lambda s: (s > 0.006).mean()) * 100).round(1).to_dict())
print("median top ask USDT", (T.assign(v=T.ask * T.ask_qty).groupby("symbol").v.median()).round(1).to_dict())


def mom60(g):
    # fair 60 s ago (last tick at or before t-60)
    tt, f = g.t.to_numpy(), g.fair.to_numpy()
    k = np.searchsorted(tt, tt - 60, side="right") - 1
    out = np.full(len(g), np.nan)
    ok = k >= 0
    out[ok] = f[ok] / f[k[ok]] - 1
    return out


def replay(thr, stop=0.009, pen=0.0, cooldown=True):
    thr = max(thr, 2 * TAKER + 0.001)
    trades = []
    for s, g in T.groupby("symbol"):
        g = g.reset_index(drop=True)
        m = mom60(g)
        tt, A, B = g.t.to_numpy(), g.ask.to_numpy(), g.bid.to_numpy()
        bd, sd, spr = g.buy_dev.to_numpy(), g.sell_dev.to_numpy(), g.spread.to_numpy()
        aq = g.ask_qty.to_numpy()
        i, n, cd = 0, len(g), -1
        while i < n:
            if not (bd[i] <= -thr and spr[i] <= 0.006 and tt[i] >= cd and not (np.isfinite(m[i]) and m[i] < -0.004)):
                i += 1
                continue
            e = A[i]
            why, j = "end", i + 1
            while j < n:
                if B[j] <= e * (1 - stop):
                    why = "stop"; break
                if sd[j] >= -0.0005 and B[j] >= e * (1 + 2 * TAKER + 0.0005):
                    why = "target"; break
                if tt[j] - tt[i] >= 25 * 60:
                    why = "time"; break
                j += 1
            j = min(j, n - 1)
            px = B[j] * (1 - (pen if why in ("stop", "time") else 0))
            r = px * (1 - TAKER) / (e * (1 + TAKER)) - 1
            trades.append({"sym": s, "t": pd.to_datetime(tt[i], unit="s").strftime("%H:%M:%S"), "ret": round(r * 100, 3),
                           "why": why, "held_min": round((tt[j] - tt[i]) / 60, 1), "dev": round(bd[i] * 100, 3),
                           "top_ask_usdt": round(A[i] * aq[i], 2)})
            if r < 0 and cooldown:
                cd = tt[j] + 7200
            i = j + 1
    return trades


def episodes(thr, gap=60):
    out = {}
    for s, g in T.groupby("symbol"):
        q = g[g.buy_dev <= -thr]
        if q.empty:
            out[s] = 0
            continue
        out[s] = int(1 + (np.diff(q.t.to_numpy()) > gap).sum())
    return out


res = {}
for thr in [0.0036, 0.0045, 0.006]:
    for pen in [0.0, 0.001]:
        tr = replay(thr, pen=pen)
        r = np.array([x["ret"] for x in tr])
        key = f"thr{thr}|pen{pen}"
        res[key] = {"n": len(tr), "mean": round(r.mean(), 3) if len(r) else None, "sum": round(r.sum(), 3) if len(r) else 0,
                    "win": round((r > 0).mean() * 100, 1) if len(r) else None, "trades": tr}
        print(key, {k: v for k, v in res[key].items() if k != "trades"})
        for x in tr:
            print("   ", x)
ep = {f"{thr}": episodes(thr) for thr in [0.0036, 0.0045, 0.006]}
mh = span_min * 5 / 60
for k, v in ep.items():
    tot = sum(v.values())
    print("episodes thr", k, v, "total", tot, "per market-day", round(tot / (mh / 24), 2), "market-hours", round(mh, 2))
# stop 0.8% (USDT default policy) sensitivity
tr = replay(0.0036, stop=0.008)
print("stop0.8% thr0.36:", [(x["sym"], x["ret"], x["why"]) for x in tr])
(OUT / "v_ticks.json").write_text(json.dumps({"replay": res, "episodes": ep, "market_hours": mh}, indent=1), encoding="utf-8")
