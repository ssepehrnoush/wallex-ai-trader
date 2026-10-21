"""Sanity check: reproduce MAIN/runs/backtests/fv_sweep_0.007_0.004 (bt_fairvalue.py logic) with this loader.
Legacy rule: target = sell_dev >= -slack AND bid > entry (not the engine's break-even), fee as
bid(1-f)/(ask(1+f)) - 1, spreads of shadow_v1, no global-drop filter, no portfolio rules."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import MAIN, OUT, load_markets, read_ticks  # noqa: E402

t = read_ticks(MAIN / "runs" / "shadow_v1" / "ticks.csv")
sp = ((t.ask / t.bid - 1).groupby(t.symbol).median()).to_dict()
for s in ("PEPETMN", "BNBTMN", "ARBTMN"):     # not in shadow_v1: fv_extra_tmn used live spreads
    sp[s] = {"PEPETMN": 0.0004, "BNBTMN": 0.00417, "ARBTMN": 0.00469}[s]
mk = load_markets(spreads=sp)
f = 0.003
res = {}
for s, m in mk.items():
    trades, i, n = [], 0, len(m.t)
    while i < n - 1:
        if np.isfinite(m.buy_dev[i]) and m.buy_dev[i] <= -0.007:
            e, j, why = m.ask[i], i + 1, "end"
            while j < n:
                if m.bid[j] <= e * (1 - 0.009):
                    why = "stop"; break
                if m.sell_dev[j] >= -0.0005 and m.bid[j] > e:
                    why = "target"; break
                if m.t[j] - m.t[i] >= 25 * 60:
                    why = "time"; break
                j += 1
            j = min(j, n - 1)
            trades.append((m.bid[j] * (1 - f) / (e * (1 + f)) - 1, why))
            i = j + 1
        else:
            i += 1
    r = pd.DataFrame(trades, columns=["r", "why"])
    res[s] = {"trades": len(r), "mean_ret_pct": round(r.r.mean() * 100, 3) if len(r) else None,
              "sum_ret_pct": round(r.r.sum() * 100, 2) if len(r) else 0, "exits": r.why.value_counts().to_dict() if len(r) else {}}
ref = json.loads((MAIN / "runs" / "backtests" / "fv_sweep_0.007_0.004" / "summary.json").read_text())["markets"]
for s in res:
    print(s, "mine:", res[s], "| ref:", {k: ref[s][k] for k in ("trades", "mean_ret_pct", "sum_ret_pct", "exits")} if s in ref else "-")
(OUT / "sanity_legacy.json").write_text(json.dumps({"mine": res, "ref_fv_sweep_0.007": {s: ref[s] for s in ref if s in res}}, indent=1))
