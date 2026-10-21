"""45-h expectation per size_factor from my own backtest trades and my own depth fills; bad-case table.
Own code. Light: reads v_trades_*.csv and v_depth.json only."""
import json
import numpy as np
import pandas as pd

E = 5_000_000
dep = json.load(open("v_depth.json"))
fill = dep["episodes"]["0.3%"]["fill"]            # entry-episode fill at the -0.3% proxy
w = {k: v / E for k, v in dep["planned_notional"].items()}
H = 45.0
rng = np.random.default_rng(11)
res = {"weights_pct_equity": {k: round(v * 100, 2) for k, v in w.items()}, "fill_used": fill, "scen": {}}
for name in ["base_ff", "fullentry_ff", "fresh5", "fullentry_fresh5"]:
    tr = pd.read_csv(f"v_trades_{name}_0.007.csv")
    days = 6.849
    lam = len(tr) / days * H / 24
    m = tr.ret.mean()
    lin = {k: round(lam * m * w[k] * fill[k] * 100, 3) for k in w}
    # bootstrap 45 h: Poisson count, trades resampled; P(P&L<0) (= P(0.7 worse than 0.4))
    sims = []
    r = tr.ret.to_numpy()
    for _ in range(20000):
        n = rng.poisson(lam)
        sims.append(rng.choice(r, n).sum() if n else 0.0)
    sims = np.array(sims)
    res["scen"][name] = {"trades_45h": round(lam, 1), "mean_pct": round(m * 100, 3), "linear_45h_pct": lin,
                         "gain_0.7_vs_0.4_pp": round(lin["0.7"] - lin["0.4"], 3),
                         "P_sum_neg_45h": round(float((sims < 0).mean()), 3),
                         "p5_45h_pct_at_0.7": round(float(np.percentile(sims, 5) * w["0.7"] * fill["0.7"] * 100), 3)}
# Non-overlapping 45 h blocks of the base backtest (how many independent windows exist)
tr = pd.read_csv("v_trades_base_ff_0.007.csv", parse_dates=["t_in"])
t0 = pd.Timestamp("2026-10-14 17:41", tz="UTC")
blk = ((tr.t_in - t0).dt.total_seconds() // (45 * 3600)).astype(int)
res["independent_45h_blocks"] = {int(b): {"n": int(len(g)), "sum_pct": round(float(g.ret.sum() * 100), 2)} for b, g in tr.groupby(blk)}
# bad case
for cost in (0.015, 0.018, 0.021):
    res[f"stop_cost_{cost}"] = {k: {"per_stop_pct_eq": round(v * cost * 100, 3), "three_stops": round(3 * v * cost * 100, 3),
                                    "stops_to_daily_1.5": int(np.ceil(0.015 / (v * cost) - 1e-12)),
                                    "stops_to_kill_3": int(np.ceil(0.03 / (v * cost) - 1e-12))} for k, v in w.items()}
# current AI policy (18:25 review): size 0.3, stop 0.8%
cur = E * 0.006 * 0.3 / (0.008 + 0.0055)
res["current_ai_notional"] = round(cur)
json.dump(res, open("v_account.json", "w"), indent=1)
print(json.dumps(res, indent=1))
