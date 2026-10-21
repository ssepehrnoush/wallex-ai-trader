"""Historical backtest of the fair-value reversion rule on 1-minute data (last 7 days).

Wallex history has no order book, only 1m last-trade candles, so executable prices are
modelled: buy at close * (1 + half_spread), sell at close * (1 - half_spread), where
half_spread is that market's median live spread measured by the shadow run (ticks.csv).
Fair value uses Binance 1m closes (x Wallex USDT/TMN 1m close for toman markets).
Only minutes in which Wallex actually traded are used (no stale prices).

  python scripts/bt_fairvalue.py [--ticks runs/shadow_v1/ticks.csv] [--entry-tmn 0.008 --entry-usdt 0.005]
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from premium import binance_1m, wallex_1m  # noqa: E402  (same folder)
from wallexbot.config import ROOT, load_config
from wallexbot.fairvalue import base_of, quote_of

ap = argparse.ArgumentParser()
ap.add_argument("--ticks", default=str(ROOT / "runs" / "shadow_v1" / "ticks.csv"))
ap.add_argument("--entry-tmn", type=float, default=0.008)
ap.add_argument("--entry-usdt", type=float, default=0.005)
ap.add_argument("--slack", type=float, default=0.0005)
ap.add_argument("--stop", type=float, default=0.009)
ap.add_argument("--hold", type=int, default=25)
ap.add_argument("--days", type=float, default=7)
ap.add_argument("--out", default=str(ROOT / "runs" / "backtests" / "fairvalue"))
a = ap.parse_args()

cfg = load_config().raw
fees = {"TMN": cfg["fees"], "USDT": cfg["fees_usdt"]}
syms = cfg["fairvalue"]["symbols"]

spreads = {}
tp = Path(a.ticks)
if tp.exists():
    t = pd.read_csv(tp)
    spreads = ((t.ask / t.bid - 1).groupby(t.symbol).median()).to_dict()

end = int(time.time()) - 120
start = int(end - a.days * 86400)
usdt = wallex_1m("USDTTMN", start, end)["close"]
usdt = usdt.reindex(pd.date_range(usdt.index[0], usdt.index[-1], freq="1min", tz="UTC")).ffill()

all_trades, summary = [], {}
for s in syms:
    q = quote_of(s)
    g = binance_1m(f"{base_of(s)}USDT", start, end)
    w = wallex_1m(s, start, end)
    fair = g * usdt if q == "TMN" else g
    df = pd.DataFrame({"c": w["close"], "v": w["volume"]}).join(fair.rename("fair"), how="inner").dropna()
    df = df[(df.v > 0) & (df.index >= pd.Timestamp(start, unit="s", tz="UTC"))]
    hs = spreads.get(s, 0.003) / 2
    df["ask"], df["bid"] = df.c * (1 + hs), df.c * (1 - hs)
    prem = df.c / df.fair - 1
    base = prem.rolling("180min", min_periods=30).median().shift(1)      # baseline known before the minute
    buy_dev = (df.ask / df.fair - 1 - base).to_numpy()
    sell_dev = (df.bid / df.fair - 1 - base).to_numpy()
    ask, bid, idx = df.ask.to_numpy(), df.bid.to_numpy(), df.index
    entry_dev = a.entry_tmn if q == "TMN" else a.entry_usdt
    f = fees[q]["taker"]
    i, trades = 0, []
    while i < len(df) - 1:
        if np.isfinite(buy_dev[i]) and buy_dev[i] <= -entry_dev:
            e = ask[i]
            j = i + 1
            why = "end"
            while j < len(df):
                if bid[j] <= e * (1 - a.stop):
                    why = "stop"
                    break
                if sell_dev[j] >= -a.slack and bid[j] > e:
                    why = "target"
                    break
                if (idx[j] - idx[i]).total_seconds() >= a.hold * 60:
                    why = "time"
                    break
                j += 1
            j = min(j, len(df) - 1)
            r = bid[j] * (1 - f) / (e * (1 + f)) - 1
            trades.append({"symbol": s, "t": str(idx[i]), "ret_pct": r * 100, "why": why,
                           "minutes": (idx[j] - idx[i]).total_seconds() / 60, "entry_dev_pct": buy_dev[i] * 100})
            i = j + 1
        else:
            i += 1
    tr = pd.DataFrame(trades)
    all_trades += trades
    summary[s] = {"half_spread_pct": round(hs * 100, 3), "minutes_traded": len(df), "trades": len(tr),
                  "mean_ret_pct": round(tr.ret_pct.mean(), 3) if len(tr) else None,
                  "win_pct": round((tr.ret_pct > 0).mean() * 100, 1) if len(tr) else None,
                  "sum_ret_pct": round(tr.ret_pct.sum(), 2) if len(tr) else 0,
                  "exits": tr.why.value_counts().to_dict() if len(tr) else {}}

out = Path(a.out)
out.mkdir(parents=True, exist_ok=True)
pd.DataFrame(all_trades).to_csv(out / "trades.csv", index=False)
res = {"params": vars(a), "spreads_from_live": {k: round(v * 100, 3) for k, v in spreads.items()}, "markets": summary}
(out / "summary.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
print(json.dumps(summary, indent=1))
