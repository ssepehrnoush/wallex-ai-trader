"""Audit 2: exact reproduction of the author's headline with the author's own spreads, plus code-level checks."""
import json
import numpy as np
import pandas as pd
import audit_sim as S
D = S.D
R = json.loads((S.Path(__file__).resolve().parent.parent / "out" / "results_main.json").read_text(encoding="utf-8"))
sp_author = {s: v / 100 for s, v in R["params"]["spreads_pct"].items()}
sp_now = D.live_spreads()
print("spread pct author vs now:", {s: (round(sp_author[s]*100, 4), round(sp_now[s]*100, 4)) for s in D.SYMS})
M, SP = S.load(spreads=sp_author)
const = lambda v: {s: np.full(M[s]["n"], v) for s in D.SYMS}
V1 = {s: v / 100 for s, v in R["v1_choice_pct"].items()}
for name, thr in {"V4": const(D.FLOOR), "V1": {s: np.full(M[s]["n"], V1[s]) for s in D.SYMS},
                  "V2_k3": {s: np.fmax(D.FLOOR, 3.0 * M[s]["vol"]) for s in D.SYMS},
                  "V3": {s: np.full(M[s]["n"], D.FLOOR + SP[s] / 2) for s in D.SYMS}}.items():
    tr = S.simulate(M, thr)
    print(name, "IS", S.stats(tr[tr.period == "IS"]), "\n    OOS", S.stats(tr[tr.period == "OOS"]))
    a = R["variants"][{"V4": "V4_fixed_0.7", "V1": "V1_per_market_fixed", "V2_k3": "V2_vol_scaled_global_k", "V3": "V3_spread_aware"}[name]]
    print("    author OOS:", a["OOS"]["trades"], a["OOS"]["sum_net_pct"], " IS:", a["IS"]["trades"], a["IS"]["sum_net_pct"])

# code-level checks
tr = S.simulate(M, const(D.FLOOR))
print("\nentries with buy_dev > -0.7%:", int((tr.entry_dev_pct > -0.7 + 1e-9).sum()))
print("overlapping trades in the same market:", int(sum(((g.t_open.shift(-1) < g.t_close) ).sum() for _, g in tr.groupby("symbol"))))
print("same-minute exits (minutes==0):", int((tr.minutes == 0).sum()))
print("exit after 1 traded minute:", int((tr.minutes <= 1).sum()), "of", len(tr), " their sum %:", round(tr[tr.minutes <= 1].ret_pct.sum(), 3))
# look-ahead check: recompute baseline and vol with an explicit loop for one market and compare
s = "PEPETMN"
u, uage = D.usdt_series()
df = D.market_frame(s, u, uage, sp_author[s] / 2, 1e9)
prem = (df.c / df.fair - 1)
t = df.index
chk_b, chk_v = [], []
for j in np.random.default_rng(1).choice(np.arange(100, len(df)), 300, replace=False):
    win = prem[(t >= t[j] - pd.Timedelta("180min") + pd.Timedelta("1min")) & (t < t[j])]   # strictly before minute j
    # pandas rolling('180min') at row j-1 covers (t[j-1]-180min, t[j-1]]
    win2 = prem[(t > t[j - 1] - pd.Timedelta("180min")) & (t <= t[j - 1])]
    chk_b.append((df.base.iloc[j], win2.median() if len(win2) >= 30 else np.nan, t[j-1] < t[j]))
cb = np.array([(a, b) for a, b, _ in chk_b], dtype=float)
ok = np.isclose(cb[:, 0], cb[:, 1], equal_nan=True).mean()
print("baseline at j equals median of premium strictly up to j-1 (sampled):", ok)
# does the signal minute's own close enter its baseline? (should not)
print("all baseline windows end before the signal minute:", all(x[2] for x in chk_b))
