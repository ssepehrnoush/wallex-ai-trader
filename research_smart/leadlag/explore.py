import os as _os
from pathlib import Path as _P
HERE = str(_P(__file__).resolve().parent)          # this study's folder (outputs)
REPO = str(_P(__file__).resolve().parents[2])      # repo root (research/candles_30d, research/premium)
MAIN = _os.environ.get("WALLEX_MAIN", str(_P(REPO).parent / "wallex-bot"))   # live bot folder (ticks.csv, team only)
import numpy as np, pandas as pd
from common import *
u = usdt()
print("usdt traded frac", wallex("USDTTMN")["traded"].mean())
for sym in MAIN_MKTS + EXTRA_MKTS:
    df = panel(sym, u)
    rb = np.log(df["bin"]).diff()
    rw = np.log(df["close"]).diff()
    # cross-corr: corr(rw_t, rb_{t-L})
    cc = {L: round(rw.corr(rb.shift(L)), 3) for L in [-2, -1, 0, 1, 2, 3, 5]}
    counts = {}
    for k in [1, 2, 3, 5]:
        rk = df["bin"] / df["bin"].shift(k) - 1
        counts[k] = [int((rk >= x).sum()) for x in [0.003, 0.005, 0.008, 0.01, 0.015]]
    print(sym, "traded%", round(df["traded"].mean()*100, 1), "devq", df["dev"].quantile([.01, .5, .99]).round(4).tolist(), "cc", cc)
    print("   jumps k:[>=.3,.5,.8,1,1.5%]", counts)
