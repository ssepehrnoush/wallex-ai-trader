import os as _os
from pathlib import Path as _P
HERE = str(_P(__file__).resolve().parent)          # this study's folder (outputs)
REPO = str(_P(__file__).resolve().parents[2])      # repo root (research/candles_30d, research/premium)
MAIN = _os.environ.get("WALLEX_MAIN", str(_P(REPO).parent / "wallex-bot"))   # live bot folder (ticks.csv, team only)
import numpy as np, pandas as pd
from common import OUT
R = pd.read_pickle(OUT / "grid_per_market.pkl")
key = ["k", "X", "lag", "trd", "pers", "mode", "stop", "N"]
P = R.groupby(key + ["off"]).agg(n_is=("n_is", "sum"), sum_is=("sum_is", "sum"), win_is=("win_is", "sum"), worst_is=("worst_is", "min"),
                                n_oos=("n_oos", "sum"), sum_oos=("sum_oos", "sum"), win_oos=("win_oos", "sum"), worst_oos=("worst_oos", "min")).reset_index()
for p in ["is", "oos"]:
    P[f"mean_{p}"] = P[f"sum_{p}"] / P[f"n_{p}"] * 100
    P[f"sum_{p}"] *= 100
    P[f"win_{p}"] = P[f"win_{p}"] / P[f"n_{p}"]
    P[f"worst_{p}"] *= 100
W = P.pivot_table(index=key, columns="off", values=["n_is", "mean_is", "sum_is", "n_oos", "mean_oos", "sum_oos", "win_is", "win_oos", "worst_is", "worst_oos"])
W.columns = [f"{a}_o{b}" for a, b in W.columns]
W = W.reset_index()
W.to_pickle(OUT / "grid_pooled.pkl")
print("configs", len(W))
for off in [0, 1, 2]:
    m = W[W[f"n_is_o{off}"] >= 30]
    print(f"off={off}: configs with n_is>=30: {len(m)}; IS mean>0: {(m[f'mean_is_o{off}'] > 0).sum()}; OOS mean>0 among those: {((m[f'mean_is_o{off}'] > 0) & (m[f'mean_oos_o{off}'] > 0)).sum()}; "
          f"median IS mean {m[f'mean_is_o{off}'].median():.3f}; best IS mean {m[f'mean_is_o{off}'].max():.3f}")
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40)
cols = key + ["n_is_o1", "mean_is_o1", "sum_is_o1", "win_is_o1", "mean_is_o2", "mean_is_o0", "n_oos_o1", "mean_oos_o1", "sum_oos_o1", "mean_oos_o2", "mean_oos_o0"]
m = W[W.n_is_o1 >= 30].sort_values("mean_is_o1", ascending=False)
print("\nTop 25 by IS mean (off=1), n_is>=30")
print(m[cols].head(25).round(3).to_string(index=False))
m2 = W[W.n_is_o1 >= 20].sort_values("sum_is_o1", ascending=False)
print("\nTop 15 by IS sum (off=1), n_is>=20")
print(m2[cols].head(15).round(3).to_string(index=False))
# rank correlation IS vs OOS
mm = W[(W.n_is_o1 >= 30) & (W.n_oos_o1 >= 10)]
print("\nSpearman IS mean vs OOS mean (off1):", round(mm.mean_is_o1.corr(mm.mean_oos_o1, method="spearman"), 3), "n configs", len(mm))
top = mm.sort_values("mean_is_o1", ascending=False).head(50)
print("top-50 IS configs: OOS mean of means", round(top.mean_oos_o1.mean(), 3), "frac OOS>0", round((top.mean_oos_o1 > 0).mean(), 3))
# lower thresholds: any config with n_is>=10
m3 = W[W.n_is_o1 >= 10].sort_values("mean_is_o1", ascending=False)
print("\nTop 10 by IS mean (off=1), n_is>=10")
print(m3[cols].head(10).round(3).to_string(index=False))
