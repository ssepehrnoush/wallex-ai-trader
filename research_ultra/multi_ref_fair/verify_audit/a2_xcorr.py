"""Audit 2: cross-correlation of 1m log returns, Wallex vs Binance, to detect a label offset (look-ahead).
corr(dW[T], dB[T-k]); a clearly positive k=-1 or k=-2 value would mean Wallex 'knows' later Binance moves."""
import numpy as np, pandas as pd
from pathlib import Path
MAIN = Path("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot")
DATA = Path(__file__).resolve().parent.parent / "data"
def wl(p):
    r = pd.read_csv(p); s = pd.Series(r.close.astype(float).values, index=pd.to_datetime(r.ts.astype("int64"), unit="s", utc=True))
    return s
def ref(ex, b):
    r = pd.read_csv(DATA / f"{ex}_{b}_1m.csv")
    return pd.Series(r.close.astype(float).values, index=pd.to_datetime(r.ts.astype("int64"), unit="s", utc=True))
for name, p, b in (("BTCUSDT", MAIN/"research/premium/wallex_BTCUSDT_1m.csv", "BTC"), ("ETHUSDT", MAIN/"research/premium/wallex_ETHUSDT_1m.csv", "ETH"),
                   ("BTCTMN", MAIN/"research/candles/BTCTMN_1.csv", "BTC"), ("ETHTMN", MAIN/"research/candles/ETHTMN_1.csv", "ETH"),
                   ("PEPETMN", MAIN/"research/candles/PEPETMN_1.csv", "PEPE")):
    w = wl(p); g = ref("binance", b)
    grid = pd.date_range(w.index[0], w.index[-1], freq="1min")
    lw = np.log(w.reindex(grid).ffill()); lg = np.log(g.reindex(grid))
    dw, dg = lw.diff(), lg.diff()
    print(name, {k: round(float(dw.corr(dg.shift(k))), 3) for k in (-3, -2, -1, 0, 1, 2, 3)})
