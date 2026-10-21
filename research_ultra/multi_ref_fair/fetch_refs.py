"""Fetch 1-minute candles from Binance, OKX and Bybit (public REST only) for the 8 live bases,
over the same window as the recorded Wallex candles, plus Binance 1-second klines over the
live_main period (used only to back out the USDT/TMN mid hidden inside the live `fair` column).

Writes CSVs under research_ultra/multi_ref_fair/data/ (cache: an existing file is not refetched).
Never touches Wallex.

  python research_ultra/multi_ref_fair/fetch_refs.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
DATA.mkdir(parents=True, exist_ok=True)

BASES = ["BTC", "ETH", "SOL", "XRP", "DOGE", "PEPE", "BNB", "ARB"]
START = int(pd.Timestamp("2026-10-14 13:00", tz="UTC").timestamp())     # 1h margin before the Wallex candles
END = (int(time.time()) // 60) * 60 - 60                                  # last closed minute
SEC_START = int(pd.Timestamp("2026-10-21 13:50", tz="UTC").timestamp())   # covers shadow_v2..live_main
SEC_BASES = ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB"]

h = httpx.Client(timeout=20, headers={"User-Agent": "research-script"})
log = []


def get(url, params, tries=4):
    for k in range(tries):
        try:
            r = h.get(url, params=params)
            if r.status_code == 200:
                return r.json()
            log.append(f"{url} {params} -> HTTP {r.status_code} {r.text[:120]}")
        except Exception as e:  # network hiccup: back off and retry
            log.append(f"{url} {params} -> {type(e).__name__} {e}")
        time.sleep(1.0 + 2 * k)
    return None


def save(rows, path):
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).drop_duplicates("ts")
    df = df.sort_values("ts")
    df.to_csv(path, index=False)
    return df


def binance(base, interval="1m", start=START, end=END, step=60):
    path = DATA / (f"binance_{base}_1m.csv" if interval == "1m" else f"binance_{base}_1s.csv")
    if path.exists():
        return pd.read_csv(path)
    rows, t = [], start * 1000
    while t < end * 1000:
        r = get("https://api.binance.com/api/v3/klines",
                {"symbol": f"{base}USDT", "interval": interval, "startTime": t, "endTime": end * 1000, "limit": 1000})
        if not r:
            break
        rows += [[x[0] // 1000, float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])] for x in r]
        t = r[-1][0] + step * 1000
        time.sleep(0.12)
    return save(rows, path)


def okx(base):
    path = DATA / f"okx_{base}_1m.csv"
    if path.exists():
        return pd.read_csv(path)
    rows, after = [], (END + 60) * 1000
    while after > START * 1000:
        r = get("https://www.okx.com/api/v5/market/history-candles",
                {"instId": f"{base}-USDT", "bar": "1m", "after": after, "limit": 100})
        if not r or r.get("code") != "0" or not r.get("data"):
            log.append(f"okx {base} stop at {after}: {str(r)[:160]}")
            break
        d = r["data"]       # newest first
        rows += [[int(x[0]) // 1000, float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])] for x in d]
        after = int(d[-1][0])
        time.sleep(0.12)
    return save([x for x in rows if START <= x[0] <= END], path)


def bybit(base):
    path = DATA / f"bybit_{base}_1m.csv"
    if path.exists():
        return pd.read_csv(path)
    rows, end = [], END * 1000
    while end >= START * 1000:
        r = get("https://api.bybit.com/v5/market/kline",
                {"category": "spot", "symbol": f"{base}USDT", "interval": "1", "start": START * 1000, "end": end, "limit": 1000})
        if not r or r.get("retCode") != 0 or not r["result"]["list"]:
            log.append(f"bybit {base} stop at {end}: {str(r)[:160]}")
            break
        d = r["result"]["list"]   # newest first
        rows += [[int(x[0]) // 1000, float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])] for x in d]
        end = int(d[-1][0]) - 60_000
        time.sleep(0.12)
    return save(rows, path)


def ticks_meta():
    """Price tick size of each pair on each exchange (a coarse tick makes that exchange's price jumpy)."""
    path = DATA / "tick_sizes.json"
    if path.exists():
        return json.loads(path.read_text())
    out = {}
    for b in BASES:
        m = {}
        r = get("https://api.binance.com/api/v3/exchangeInfo", {"symbol": f"{b}USDT"})
        try:
            m["binance"] = float(next(f["tickSize"] for f in r["symbols"][0]["filters"] if f["filterType"] == "PRICE_FILTER"))
        except Exception:
            m["binance"] = None
        r = get("https://www.okx.com/api/v5/public/instruments", {"instType": "SPOT", "instId": f"{b}-USDT"})
        try:
            m["okx"] = float(r["data"][0]["tickSz"])
        except Exception:
            m["okx"] = None
        r = get("https://api.bybit.com/v5/market/instruments-info", {"category": "spot", "symbol": f"{b}USDT"})
        try:
            m["bybit"] = float(r["result"]["list"][0]["priceFilter"]["tickSize"])
        except Exception:
            m["bybit"] = None
        out[b] = m
        time.sleep(0.2)
    path.write_text(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    print("window", pd.Timestamp(START, unit="s", tz="UTC"), "->", pd.Timestamp(END, unit="s", tz="UTC"), flush=True)
    summary = {}
    for b in BASES:
        for name, fn in (("binance", binance), ("okx", okx), ("bybit", bybit)):
            t0 = time.time()
            df = fn(b)
            summary[f"{name}_{b}"] = {"rows": len(df),
                                      "first": str(pd.Timestamp(int(df.ts.iloc[0]), unit="s", tz="UTC")) if len(df) else None,
                                      "last": str(pd.Timestamp(int(df.ts.iloc[-1]), unit="s", tz="UTC")) if len(df) else None}
            print(name, b, summary[f"{name}_{b}"], f"{time.time() - t0:.1f}s", flush=True)
    for b in SEC_BASES:
        df = binance(b, "1s", SEC_START, END + 60, step=1)
        summary[f"binance1s_{b}"] = {"rows": len(df)}
        print("binance 1s", b, len(df), flush=True)
    summary["tick_sizes"] = ticks_meta()
    summary["errors"] = log[-50:]
    (DATA / "fetch_summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary["tick_sizes"], indent=1))
    print("errors:", len(log))
