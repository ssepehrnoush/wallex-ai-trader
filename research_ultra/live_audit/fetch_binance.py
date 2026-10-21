"""Fetch Binance public 1-minute klines (no key) for today's shadow + live window, to compare global
volatility today against the 7-day backtest window. Binance only; never calls Wallex.

    python research_ultra/live_audit/fetch_binance.py
"""
import time
from pathlib import Path

import httpx

OUT = Path(__file__).resolve().parent / "binance_today"
OUT.mkdir(exist_ok=True)
BASES = ["BTC", "ETH", "SOL", "XRP", "DOGE", "PEPE", "BNB", "ARB"]
START = 1792591200          # 2026-10-21 14:00 UTC (covers the shadow runs and the live run)

with httpx.Client(timeout=20) as h:
    for b in BASES:
        rows, t = [], START * 1000
        end = int(time.time()) * 1000
        while t < end:
            r = h.get("https://api.binance.com/api/v3/klines",
                      params={"symbol": f"{b}USDT", "interval": "1m", "startTime": t, "limit": 1000})
            r.raise_for_status()
            k = r.json()
            if not k:
                break
            rows += k
            t = k[-1][0] + 60_000
            time.sleep(0.3)
        with open(OUT / f"{b}USDT_1m.csv", "w", encoding="utf-8") as f:
            f.write("open_time_ms,open,high,low,close,volume\n")
            for x in rows:
                f.write(f"{x[0]},{x[1]},{x[2]},{x[3]},{x[4]},{x[5]}\n")
        print(b, len(rows))
