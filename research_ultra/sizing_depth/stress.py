"""Bad-case stress for part 2: the 7-day backtest at 0.7% has ZERO stop exits, so a bootstrap of its trades
can never show a stop-loss streak. Here a share of trades is replaced by a stop-out at -1.8% of notional
(-0.9% stop, -0.6% two taker fees, -0.3% full crossing of the stop LIMIT) and the 45 h account path is
simulated for size_factor 0.4 / 0.7 / 1.0 and the drawdown rule (1.0, 0.4 once drawdown >= 1%).
Daily loss limit and kill switch are applied as in risk.py (Tehran day ignored: one 45 h block, so the
'daily' line here is "drawdown reached 1.5% at least once", which is stricter than the real per-day rule).

Writes out/stress.json
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from common import EQUITY, OUT, dump

bt = json.loads((OUT / "bt_sizing.json").read_text(encoding="utf-8"))
dt = json.loads((OUT / "depth_ticks.json").read_text(encoding="utf-8"))
tr = pd.read_csv(OUT / "bt_trades_live_tick_ff.csv")
pool = tr.ret_pct.to_numpy() / 100
FILL = {float(k): v / 100 for k, v in dt["entry_episodes"]["0.3%"]["entry_fill_pct_mean"].items()}
xs = sorted(FILL)
fill = lambda f: float(np.interp(f, xs, [FILL[x] for x in xs]))
days = bt["window"]["days"]
lam_base = len(tr) / days * 45 / 24
rng = np.random.default_rng(11)
N = 20000
out = {}
for lam_name, lam in [("backtest_rate", lam_base), ("tick_calibrated_rate_x0.29", lam_base * 0.29)]:
    out[lam_name] = {}
    for p_stop in [0.0, 0.05, 0.10, 0.20]:
        row = {}
        for name, f in [("0.4", 0.4), ("0.7", 0.7), ("1.0", 1.0), ("rule_1.0_to_0.4_if_dd1%", None)]:
            fin, dd15, kill = [], 0, 0
            for _ in range(N):
                k = rng.poisson(lam)
                rets = rng.choice(pool, size=k)
                rets = np.where(rng.random(k) < p_stop, -0.018, rets)
                eq = peak = EQUITY
                halted, hit15 = False, False
                for r in rets:
                    if halted:
                        break
                    ff = f if f is not None else (0.4 if 1 - eq / peak >= 0.01 else 1.0)
                    eq += min(eq * 0.006 * ff / 0.0145, eq * 0.35) * fill(ff) * r
                    peak = max(peak, eq)
                    dd = 1 - eq / peak
                    if dd >= 0.015:
                        hit15 = True
                    if dd >= 0.03:
                        kill += 1
                        halted = True
                fin.append(eq / EQUITY - 1)
                dd15 += hit15
            fa = np.array(fin) * 100
            row[name] = {"mean_pct": round(fa.mean(), 4), "p5_pct": round(np.quantile(fa, 0.05), 4),
                         "share_negative_pct": round((fa < 0).mean() * 100, 2),
                         "share_dd_ge_1.5pct": round(dd15 / N * 100, 2), "share_kill_3pct": round(kill / N * 100, 3)}
        out[lam_name][f"stop_share_{int(p_stop * 100)}%"] = row
out["lambda_trades_45h"] = {"backtest_rate": round(lam_base, 2), "tick_calibrated": round(lam_base * 0.29, 2)}
dump(out, "stress.json")
for k, v in out.items():
    if isinstance(v, dict) and "stop_share_0%" in v:
        for ps, row in v.items():
            print(k, ps, {n: (x["mean_pct"], x["p5_pct"], x["share_negative_pct"], x["share_dd_ge_1.5pct"], x["share_kill_3pct"]) for n, x in row.items()})
print(out["lambda_trades_45h"])
