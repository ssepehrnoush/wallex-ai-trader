"""Replay the engine's entry and exit rule on the live run's recorded order book, every market switched on.

MAIN/runs/live_main/ticks.csv (not in git, read only) has the real best bid and ask every 2.5 s with the engine's
own fair value and baseline. This answers "what if the AI had left every market on": entry at the real ask when
buy_dev <= -threshold and the spread is within fairvalue.max_spread (optionally only after the signal held for
--persist ticks); exit at the bid on target (sell_dev >= -0.05% and bid >= entry x (1 + 2 x taker + 0.05%)), and
0.3% under the bid on the stop or after --hold minutes, as the engine prices them. Net = (1 - taker)^2 x exit / ask - 1.
Not modelled: the 60 s Binance drop filter, the cooldown after a loss, partial fills. Thresholds under the
engine's fee floor (2 x taker + 0.1% = 0.7% for toman markets) are a rule the engine cannot run; they are for comparison. POLTMN is left out (its live
spread is far above the cap; RESULTS.md 2.6).

  python scripts/live_tick_replay.py --main ../wallex-bot --cutoff 2026-10-22T09:00:00Z --out docs/live_tick_replay.json
"""
import argparse
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from wallexbot.config import ROOT, load_config

ap = argparse.ArgumentParser()
ap.add_argument("--main", default=str(ROOT.parent / "wallex-bot"))
ap.add_argument("--cutoff", default="2026-10-22T09:00:00Z")
ap.add_argument("--thresholds", default="0.005,0.006,0.007,0.008,0.01")
ap.add_argument("--persist", default="1,3")
ap.add_argument("--hold", type=float, default=25)
ap.add_argument("--stop", type=float, default=0.009)
ap.add_argument("--out", default="docs/live_tick_replay.json")
a = ap.parse_args()
cfg = load_config().raw
taker, cap = cfg["fees"]["taker"], cfg["fairvalue"]["max_spread"]

txt = (Path(a.main) / "runs" / "live_main" / "ticks.csv").read_text(encoding="utf-8")
t = pd.read_csv(io.StringIO(txt[:txt.rfind("\n") + 1]), on_bad_lines="skip")
for c in ("t", "bid", "ask", "buy_dev", "sell_dev"):
    t[c] = pd.to_numeric(t[c], errors="coerce")
t = t.dropna(subset=["t", "bid", "ask", "buy_dev", "sell_dev"])
t = t[(t.t < pd.Timestamp(a.cutoff).timestamp()) & (t.symbol != "POLTMN")].sort_values("t")
iso = lambda x: pd.Timestamp(x, unit="s", tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def replay(g: pd.DataFrame, thr: float, persist: int) -> list[dict]:
    T, bid, ask, bd, sd = (g[c].to_numpy() for c in ("t", "bid", "ask", "buy_dev", "sell_dev"))
    ok = (bd <= -thr) & (ask / bid - 1 <= cap)
    streak = np.zeros(len(ok), int)
    for i in range(len(ok)):
        streak[i] = (streak[i - 1] + 1 if i else 1) if ok[i] else 0
    out, i, n = [], 0, len(g)
    while i < n:
        if streak[i] < persist:
            i += 1
            continue
        e, j, why, px = ask[i], i + 1, "open_at_cutoff", None
        while j < n:
            if bid[j] <= e * (1 - a.stop):
                why, px = "stop", bid[j] * 0.997
                break
            if sd[j] >= -0.0005 and bid[j] >= e * (1 + 2 * taker + 0.0005):
                why, px = "target", bid[j]
                break
            if T[j] - T[i] >= a.hold * 60:
                why, px = "time", bid[j] * 0.997
                break
            j += 1
        if px is None:
            j, px = n - 1, bid[n - 1]
        out.append({"symbol": g.symbol.iloc[0], "entry_utc": iso(T[i]), "buy_dev_pct": round(bd[i] * 100, 3),
                    "exit": why, "minutes": round((T[j] - T[i]) / 60, 1),
                    "net_pct": round(((1 - taker) ** 2 * px / e - 1) * 100, 3)})
        i = j + 1
    return out


res = {}
for thr in (float(x) for x in a.thresholds.split(",")):
    for persist in (int(x) for x in a.persist.split(",")):
        trades = [x for _, g in t.groupby("symbol") for x in replay(g, thr, persist)]
        r = [x["net_pct"] for x in trades]
        res[f"thr_{thr:.3f}_persist_{persist}"] = {"trades": len(r), "mean_net_pct": round(float(np.mean(r)), 3) if r else None,
                                                    "wins": int(sum(x > 0 for x in r)), "sum_net_pct": round(float(sum(r)), 2),
                                                    "list": sorted(trades, key=lambda x: x["entry_utc"])}
doc = {"note": ("Engine rule replayed on the live order book (MAIN/runs/live_main/ticks.csv), every market except "
                "POLTMN switched on regardless of the AI policy. See the script docstring for what is not modelled."),
       "ticks_utc": [iso(t.t.min()), iso(t.t.max())], "hours": round((t.t.max() - t.t.min()) / 3600, 2),
       "markets": sorted(t.symbol.unique()), "hold_min": a.hold, "stop": a.stop, "max_spread": cap, "results": res}
(Path(a.out) if Path(a.out).is_absolute() else ROOT / a.out).write_text(json.dumps(doc, indent=1), encoding="utf-8")
for k, v in res.items():
    print(k, {x: v[x] for x in ("trades", "wins", "mean_net_pct", "sum_net_pct")})
