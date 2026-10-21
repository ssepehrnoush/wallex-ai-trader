"""Fill the Binance 1m gap at the start of the 7-day window.

MAIN/research/premium/binance_*_1m.csv start at 2026-10-14 17:41 UTC while the Wallex candles start
at 14:05 UTC. This fetches Binance public klines (api.binance.com, public, 8 requests) for
2026-10-14 13:00 -> 17:45 UTC and saves them under ./data/. Nothing touches Wallex.
"""
import time
from pathlib import Path

import httpx
import pandas as pd

OUT = Path(__file__).resolve().parent / "data"
OUT.mkdir(exist_ok=True)
START = 1791982800          # 2026-10-14 13:00 UTC
END = 1791999900            # 2026-10-14 17:45 UTC
BASES = ["BTC", "ETH", "SOL", "XRP", "DOGE", "PEPE", "BNB", "ARB"]

with httpx.Client(timeout=20) as h:
    for b in BASES:
        rows, t = [], START * 1000
        while t < END * 1000:
            r = h.get("https://api.binance.com/api/v3/klines",
                      params={"symbol": f"{b}USDT", "interval": "1m", "startTime": t, "endTime": END * 1000, "limit": 1000})
            r.raise_for_status()
            d = r.json()
            if not d:
                break
            rows += d
            t = d[-1][0] + 60_000
            time.sleep(0.3)
        s = pd.Series([float(x[4]) for x in rows], index=pd.to_datetime([x[0] for x in rows], unit="ms", utc=True), name="close")
        s.to_csv(OUT / f"binance_{b}USDT_gap.csv")
        print(b, len(s), s.index[0], s.index[-1])
