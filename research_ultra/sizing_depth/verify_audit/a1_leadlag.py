"""Audit 1: timestamp alignment of Wallex 1m candles vs Binance 1m closes (look-ahead check).
If Wallex ts were the bar CLOSE time while Binance index is the OPEN time, the backtest's fair value at
minute i would use a Binance price 60 s in the future. Test: correlation of 1m returns at lags."""
import numpy as np, pandas as pd
MAIN = "C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot/research"
def wallex(sym):
    raw = pd.read_csv(f"{MAIN}/candles/{sym}_1.csv")
    idx = pd.to_datetime(raw.ts.astype("int64"), unit="s", utc=True)
    df = pd.DataFrame({"c": raw.close.astype(float).to_numpy(), "v": raw.volume.astype(float).to_numpy()}, index=idx)
    return df[~df.index.duplicated(keep="last")].sort_index()
def binance(base):
    s = pd.read_csv(f"{MAIN}/premium/binance_{base}USDT_1m.csv", index_col=0, parse_dates=True)["close"]
    s.index = pd.to_datetime(s.index, utc=True)
    return s[~s.index.duplicated(keep="last")].sort_index()
for sym in ["BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN"]:
    w = wallex(sym)
    full = pd.date_range(w.index[0], w.index[-1], freq="1min", tz="UTC")
    wc = w.c.reindex(full)
    vol = w.v.reindex(full).fillna(0)
    # only minutes where both this and previous minute traded
    wr = np.log(wc).diff()
    ok = (vol > 0) & (vol.shift(1) > 0)
    g = binance(sym[:-3]).reindex(full)
    gr = np.log(g).diff()
    out = {}
    for lag in [-2, -1, 0, 1, 2]:
        # lag>0: Wallex return at t vs Binance return at t-lag (Binance leads)
        x = pd.concat([wr[ok], gr.shift(lag)[ok]], axis=1).dropna()
        out[lag] = round(x.corr().iloc[0, 1], 3)
    print(sym, "corr(wallex_ret[t], binance_ret[t-lag]) by lag:", out, "n_ok", int(ok.sum()))
