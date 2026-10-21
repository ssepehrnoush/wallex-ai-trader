"""Independent fetch of the Binance 1m gap (10-14 14:00 -> 17:50 UTC) from the PUBLIC Binance REST API.
Writes only under verify_independent/data. One request per symbol."""
import time
from pathlib import Path
import httpx
import pandas as pd

OUT = Path(__file__).resolve().parent / "data"
OUT.mkdir(exist_ok=True)
start = pd.Timestamp("2026-10-14 14:00", tz="UTC")
end = pd.Timestamp("2026-10-14 17:50", tz="UTC")
for b in ["BTC", "ETH", "SOL", "XRP", "DOGE", "PEPE", "BNB", "ARB"]:
    r = httpx.get("https://api.binance.com/api/v3/klines",
                  params={"symbol": f"{b}USDT", "interval": "1m", "startTime": int(start.timestamp() * 1000),
                          "endTime": int(end.timestamp() * 1000), "limit": 1000}, timeout=20)
    r.raise_for_status()
    k = r.json()
    df = pd.DataFrame({"t": pd.to_datetime([x[0] for x in k], unit="ms", utc=True), "close": [float(x[4]) for x in k]})
    df.to_csv(OUT / f"gap_{b}USDT.csv", index=False)
    print(b, len(df), df.t.iloc[0], df.t.iloc[-1])
    time.sleep(0.3)
