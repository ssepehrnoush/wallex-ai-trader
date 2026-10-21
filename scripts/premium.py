"""Research: does Wallex lag global prices enough to trade the gap after fees?

fair_TMN(t)  = global_USDT_price(t) * USDTTMN_wallex(t)      (TMN markets)
fair_USDT(t) = global_USDT_price(t)                           (Wallex USDT markets)
premium(t)   = wallex_price(t) / fair(t) - 1

Uses 1-minute closes: Wallex from research/candles (or fetched), global from Binance klines.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import numpy as np
import pandas as pd

from wallexbot.client import WallexClient
from wallexbot.data import CANDLE_DIR, _frame

OUT = CANDLE_DIR.parent / "premium"
OUT.mkdir(exist_ok=True)
BIN = "https://api.binance.com/api/v3/klines"


def binance_1m(sym: str, start: int, end: int) -> pd.Series:
    cache = OUT / f"binance_{sym}_1m.csv"
    if cache.exists():
        s = pd.read_csv(cache, index_col=0, parse_dates=True)["close"]
        if s.index[0].timestamp() <= start + 120 and s.index[-1].timestamp() >= end - 600:
            return s
    rows, t = [], start * 1000
    with httpx.Client(timeout=20) as h:
        while t < end * 1000:
            r = h.get(BIN, params={"symbol": sym, "interval": "1m", "startTime": t, "limit": 1000}).json()
            if not r:
                break
            rows += r
            t = r[-1][0] + 60_000
            time.sleep(0.15)
    s = pd.Series([float(x[4]) for x in rows], index=pd.to_datetime([x[0] for x in rows], unit="ms", utc=True), name="close")
    s.to_csv(cache)
    return s


def wallex_1m(sym: str, start: int, end: int) -> pd.DataFrame:
    p = CANDLE_DIR / f"{sym}_1.csv"
    if p.exists():
        raw = pd.read_csv(p)
        return _frame(raw["ts"], raw["open"], raw["high"], raw["low"], raw["close"], raw["volume"])
    cache = OUT / f"wallex_{sym}_1m.csv"
    if cache.exists():
        raw = pd.read_csv(cache)
        return _frame(raw["ts"], raw["open"], raw["high"], raw["low"], raw["close"], raw["volume"])
    c = WallexClient()
    parts, t = [], start
    while t < end:
        t2 = min(t + 86400, end)
        d = c.candles(sym, "1", t, t2)
        if d.get("s") == "ok" and d.get("t"):
            parts.append(pd.DataFrame({"ts": d["t"], "open": d["o"], "high": d["h"], "low": d["l"], "close": d["c"], "volume": d["v"]}))
        t = t2
    raw = pd.concat(parts).drop_duplicates("ts")
    raw.to_csv(cache, index=False)
    return _frame(raw["ts"], raw["open"], raw["high"], raw["low"], raw["close"], raw["volume"])


def analyze(name, wal: pd.DataFrame, fair: pd.Series, fee_rt: float):
    df = pd.DataFrame({"w": wal["close"], "vol": wal["volume"]}).join(fair.rename("fair"), how="inner")
    df = df[df.vol > 0]                     # only minutes where Wallex actually traded
    df["prem"] = df.w / df.fair - 1
    mu = df.prem.rolling(240, min_periods=60).median()
    df["dev"] = df.prem - mu
    q = df.dev.quantile([0.01, 0.05, 0.5, 0.95, 0.99]).round(5).to_dict()
    # how fast do deviations close? correlation of dev(t) with dev(t+k)
    ac = {k: round(df.dev.autocorr(k), 3) for k in (1, 3, 10)}
    # naive rule: buy when dev < -x, sell when dev >= 0 or after 30 minutes; 1m close fills
    res = {}
    for x in (0.003, 0.005, 0.008, 0.012):
        rets, i, arr, idx = [], 0, df.dev.to_numpy(), df.index
        w = df.w.to_numpy()
        while i < len(df) - 1:
            if arr[i] < -x:
                j = i + 1
                while j < len(df) - 1 and arr[j] < 0 and (idx[j] - idx[i]).total_seconds() < 1800:
                    j += 1
                rets.append(w[j] / w[i] - 1 - fee_rt)
                i = j + 1
            else:
                i += 1
        if rets:
            r = np.array(rets)
            res[x] = {"n": len(r), "mean_pct": round(r.mean() * 100, 3), "win_pct": round((r > 0).mean() * 100, 1),
                      "sum_pct": round(r.sum() * 100, 2)}
    return {"market": name, "minutes": len(df), "prem_median_pct": round(df.prem.median() * 100, 3),
            "dev_quantiles": q, "dev_autocorr": ac, "rule": res}


if __name__ == "__main__":
    end = int(time.time()) - 120
    start = end - 7 * 86400
    usdt = wallex_1m("USDTTMN", start, end)["close"]
    usdt = usdt.reindex(pd.date_range(usdt.index[0], usdt.index[-1], freq="1min", tz="UTC")).ffill()
    out = []
    for base in ("BTC", "ETH", "SOL", "XRP", "DOGE"):
        g = binance_1m(f"{base}USDT", start, end)
        try:
            out.append(analyze(f"{base}TMN", wallex_1m(f"{base}TMN", start, end), g * usdt, 0.0025 + 0.003))
        except Exception as e:
            out.append({"market": f"{base}TMN", "error": str(e)})
        try:
            out.append(analyze(f"{base}USDT", wallex_1m(f"{base}USDT", start, end), g, 0.001 + 0.0013))
        except Exception as e:
            out.append({"market": f"{base}USDT", "error": str(e)})
    (OUT / "premium_summary.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    for r in out:
        print(json.dumps(r))
