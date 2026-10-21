"""Does the 1-minute candle backtest under-count signals compared with what the live engine sees in the book?

Calibration on markets that have BOTH 7-day candles and live 2.5 s ticks:
  - signal episodes in ticks: runs of consecutive ticks with buy_dev <= -thr and spread <= 0.6% (a new episode
    starts after >= 60 s without a signal), per hour of recorded ticks
  - candle backtest trades per hour (out/candle_trades.csv for USDT; MAIN/runs/backtests/fv_sweep_* for TMN)
TMN ticks: shadow_v2..v6 + live_main (snapshot). USDT ticks: shadow_v2..v6.
"""
from __future__ import annotations

import json

import pandas as pd

from common import MAIN, OUT, read_csv_snapshot, save_json, shadow_ticks

TMN = ["BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN"]
USDT = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]


def episodes(t: pd.DataFrame, thr: float) -> tuple[int, float]:
    n, hours = 0, 0.0
    for s, g in t.groupby("symbol"):
        g = g.sort_values("t")
        hours += sum((x.t.max() - x.t.min()) / 3600 for _, x in g.groupby("run"))
        hit = g[(g.buy_dev <= -thr) & (g.ask / g.bid - 1 <= 0.006)]
        if len(hit):
            n += 1 + int((hit.t.diff() > 60).sum())
    return n, hours


sh = shadow_ticks(TMN + USDT)
lm = read_csv_snapshot(MAIN / "runs" / "live_main" / "ticks.csv")
lm["run"] = "live_main"
tmn_ticks = pd.concat([sh[sh.symbol.isin(TMN)], lm[lm.symbol.isin(TMN)]], ignore_index=True)
usdt_ticks = sh[sh.symbol.isin(USDT)]

out = {}
for thr in (0.006, 0.007):
    n, h = episodes(tmn_ticks, thr)
    out[f"TMN_ticks_{thr * 100:.1f}%"] = {"episodes": n, "market_hours": round(h, 2), "per_market_day": round(n / h * 24, 2) if h else None}
# candle backtest TMN reference (existing outputs, 7 days, 5 markets)
for d, thr in (("fv_sweep_0.006_0.0035", 0.006), ("fv_sweep_0.007_0.004", 0.007)):
    m = json.loads((MAIN / "runs" / "backtests" / d / "summary.json").read_text(encoding="utf-8"))["markets"]
    n = sum(m[s]["trades"] for s in TMN if s in m)
    out[f"TMN_candles_{thr * 100:.1f}%"] = {"trades_7d_5_markets": n, "per_market_day": round(n / (7 * 5), 2)}
for thr in (0.0036, 0.0045, 0.006):
    n, h = episodes(usdt_ticks, thr)
    out[f"USDT_ticks_{thr * 100:.2f}%"] = {"episodes": n, "market_hours": round(h, 2), "per_market_day": round(n / h * 24, 2) if h else None}
ct = pd.read_csv(OUT / "candle_trades.csv")
days = 6.85
for thr in (0.0036, 0.0045, 0.006):
    x = ct[(ct.model == "mid") & (ct.thr == thr)]
    out[f"USDT_candles_mid_{thr * 100:.2f}%"] = {"trades": int(len(x)), "per_market_day": round(len(x) / (days * 5), 3)}
save_json("freq_calibration.json", out)
print(json.dumps(out, indent=1))
