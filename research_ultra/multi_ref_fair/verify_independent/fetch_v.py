"""Independent fetch of 1m klines from Binance, OKX, Bybit (public REST only).
Writes verify_independent/data/<ex>_<BASE>.csv with columns ts(open time, unix s),close.
"""
import sys
import time
from pathlib import Path

import httpx

OUT = Path(__file__).resolve().parent / "data"
OUT.mkdir(exist_ok=True)
START = 1791982800          # 2026-10-14 13:00 UTC
END = 1792591800            # 2026-10-21 14:10 UTC
BASES = ["BTC", "ETH", "SOL", "XRP", "DOGE", "PEPE", "BNB", "ARB"]
cli = httpx.Client(timeout=20)


def get(url, params):
    for k in range(5):
        try:
            r = cli.get(url, params=params)
            if r.status_code == 200:
                return r.json()
            print("http", r.status_code, url, r.text[:200])
        except Exception as e:  # noqa
            print("err", e)
        time.sleep(1 + k)
    raise RuntimeError(url)


def binance(base):
    rows, t = {}, START * 1000
    while t < END * 1000:
        js = get("https://api.binance.com/api/v3/klines",
                 {"symbol": f"{base}USDT", "interval": "1m", "startTime": t, "endTime": END * 1000, "limit": 1000})
        if not js:
            break
        for k in js:
            rows[int(k[0]) // 1000] = k[4]
        t = int(js[-1][0]) + 60000
        time.sleep(0.15)
    return rows


def okx(base):
    rows, after = {}, (END + 60) * 1000
    while True:
        js = get("https://www.okx.com/api/v5/market/history-candles",
                 {"instId": f"{base}-USDT", "bar": "1m", "after": after, "limit": 100})
        d = js.get("data", [])
        if not d:
            break
        for k in d:
            rows[int(k[0]) // 1000] = k[4]
        oldest = min(int(k[0]) for k in d)
        if oldest <= START * 1000:
            break
        after = oldest
        time.sleep(0.12)
    return rows


def bybit(base):
    rows, s = {}, START * 1000
    while s < END * 1000:
        e = min(s + 999 * 60000, END * 1000)
        js = get("https://api.bybit.com/v5/market/kline",
                 {"category": "spot", "symbol": f"{base}USDT", "interval": "1", "start": s, "end": e, "limit": 1000})
        lst = js.get("result", {}).get("list", [])
        for k in lst:
            rows[int(k[0]) // 1000] = k[4]
        s = e + 60000
        time.sleep(0.15)
    return rows


which = sys.argv[1:] or ["binance", "okx", "bybit"]
for ex in which:
    fn = {"binance": binance, "okx": okx, "bybit": bybit}[ex]
    for b in BASES:
        p = OUT / f"{ex}_{b}.csv"
        if p.exists():
            continue
        try:
            rows = fn(b)
        except Exception as e:  # noqa
            print("FAILED", ex, b, e)
            continue
        ks = sorted(k for k in rows if START <= k <= END)
        p.write_text("ts,close\n" + "".join(f"{k},{rows[k]}\n" for k in ks), encoding="utf-8")
        print(ex, b, len(ks), ks[0] if ks else None, ks[-1] if ks else None, flush=True)
