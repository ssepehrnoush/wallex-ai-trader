"""Audit 1: timestamp convention of Wallex 1m candles vs Binance/OKX/Bybit 1m candles (look-ahead check),
and what volume-0 USDTTMN rows contain. Read-only on MAIN."""
import numpy as np, pandas as pd
from pathlib import Path
MAIN = Path("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot")
DATA = Path(__file__).resolve().parent.parent / "data"

def wl(p):
    r = pd.read_csv(p)
    return pd.DataFrame({"c": r.close.astype(float).values, "o": r.open.astype(float).values, "v": r.volume.astype(float).values},
                        index=pd.to_datetime(r.ts.astype("int64"), unit="s", utc=True))
def ref(ex, b):
    r = pd.read_csv(DATA / f"{ex}_{b}_1m.csv")
    return pd.Series(r.close.astype(float).values, index=pd.to_datetime(r.ts.astype("int64"), unit="s", utc=True))

u = wl(MAIN / "research/candles/USDTTMN_1.csv")
print("USDTTMN rows", len(u), "vol>0", int((u.v > 0).sum()))
z = u[u.v == 0]
print("vol0 rows where close != prev close:", int((z.c != u.c.shift(1).reindex(z.index)).sum()), "of", len(z))
print("vol0 rows where open != close:", int((z.o != z.c).sum()))
grid = pd.date_range(u.index[0], u.index[-1], freq="1min")
print("grid len", len(grid), "missing rows", len(grid.difference(u.index)))
# lead/lag: Wallex USDT market vs Binance (no USDTTMN leg involved)
for sym, b in (("BTCUSDT", "BTC"), ("ETHUSDT", "ETH"), ("XRPUSDT", "XRP")):
    w = wl(MAIN / f"research/premium/wallex_{sym}_1m.csv")
    w = w[w.v > 0]
    g = ref("binance", b)
    lw = np.log(w.c)
    out = {}
    for k in (-2, -1, 0, 1, 2):
        gs = np.log(g.shift(k))           # k>0: binance from k minutes EARLIER aligned to wallex minute
        lev = (lw - gs.reindex(lw.index)).dropna()
        out[k] = round(float((lev - lev.rolling(60, min_periods=10).median()).abs().median() * 1e4), 2)
    print(sym, "median |premium - 60m median| (bp) by binance shift k:", out)
for sym in ("BTCTMN", "ETHTMN", "SOLTMN"):
    w = wl(MAIN / f"research/candles/{sym}_1.csv"); w = w[w.v > 0]
    g = ref("binance", sym[:-3])
    uu = u.c.reindex(grid).ffill()
    out = {}
    for k in (-2, -1, 0, 1, 2):
        f = (g * uu).shift(k)
        lev = (np.log(w.c) - np.log(f.reindex(w.index))).dropna()
        out[k] = round(float((lev - lev.rolling(60, min_periods=10).median()).abs().median() * 1e4), 2)
    print(sym, "median |premium - 60m median| (bp) by fair shift k:", out)
