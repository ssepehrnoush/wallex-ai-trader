"""Check timestamp alignment of Wallex 1m candles vs Binance 1m closes (lag correlation of 1m returns)."""
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bt_maker_exit_candles import wallex, binance
for sym in ["BTCTMN", "ETHTMN", "PEPETMN", "ARBTMN"]:
    w = wallex(sym)
    w = w[w.volume > 0].close
    u = wallex("USDTTMN").close
    g = binance(sym[:-3])
    grid = pd.date_range(max(w.index[0], g.index[0]), min(w.index[-1], g.index[-1]), freq="1min", tz="UTC")
    wr = np.log(w.reindex(grid).ffill()).diff()
    fr = np.log(g.reindex(grid).ffill() * u.reindex(grid).ffill()).diff()
    out = {}
    for lag in (-2, -1, 0, 1, 2, 3):
        out[lag] = round(wr.corr(fr.shift(lag)), 3)
    print(sym, "corr(wallex_ret[t], fair_ret[t-lag])", out)
