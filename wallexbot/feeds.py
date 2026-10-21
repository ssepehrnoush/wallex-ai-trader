"""Live market feeds: Wallex top of book (per symbol, in parallel) and the global
reference price (Binance book ticker, with OKX as fallback)."""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import httpx

from .client import WallexClient


@dataclass
class Book:
    bid: float
    bid_qty: float
    ask: float
    ask_qty: float
    t: float              # local receive time (unix seconds)

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> float:
        return self.ask / self.bid - 1 if self.bid else 1.0


class GlobalFeed:
    """Mid prices in USDT for a list of base assets."""
    def __init__(self, bases: list[str]):
        self.http = httpx.Client(timeout=6)
        self.source = "binance"
        # one unknown symbol makes Binance reject the whole batch request, so keep only listed ones
        try:
            listed = {x["symbol"] for x in self.http.get("https://api.binance.com/api/v3/exchangeInfo", timeout=20).json()["symbols"]
                      if x.get("status") == "TRADING"}
            bases = [b for b in bases if f"{b}USDT" in listed]
        except Exception:
            pass
        self.bases = bases

    def _binance(self) -> dict[str, float]:
        syms = json.dumps([f"{b}USDT" for b in self.bases], separators=(",", ":"))
        r = self.http.get("https://api.binance.com/api/v3/ticker/bookTicker", params={"symbols": syms})
        r.raise_for_status()
        return {x["symbol"][:-4]: (float(x["bidPrice"]) + float(x["askPrice"])) / 2 for x in r.json()}

    def _okx(self) -> dict[str, float]:
        r = self.http.get("https://www.okx.com/api/v5/market/tickers", params={"instType": "SPOT"})
        r.raise_for_status()
        want = {f"{b}-USDT": b for b in self.bases}
        return {want[x["instId"]]: (float(x["bidPx"]) + float(x["askPx"])) / 2
                for x in r.json()["data"] if x["instId"] in want and x["bidPx"] and x["askPx"]}

    def get(self) -> tuple[dict[str, float], str]:
        """Prices from Binance, or OKX if Binance fails. Partial answers are fine: a market without a
        fresh reference price simply has no fair value this tick and cannot trade."""
        best: dict[str, float] = {}
        for name, fn in (("binance", self._binance), ("okx", self._okx)):
            try:
                px = fn()
            except Exception:
                continue
            if len(px) == len(self.bases):
                self.source = name
                return px, name
            if len(px) > len(best):
                best, self.source = px, name
        return best, (self.source if best else "none")


class WallexBooks:
    def __init__(self, client: WallexClient, symbols: list[str], workers: int = 6):
        self.c = client
        self.symbols = symbols
        self.pool = ThreadPoolExecutor(max_workers=workers)

    def _one(self, s: str):
        try:
            bids, asks = self.c.depth(s)
            if not bids or not asks:
                return s, None
            return s, Book(bids[0][0], bids[0][1], asks[0][0], asks[0][1], time.time())
        except Exception:
            return s, None

    def get(self) -> dict[str, Book]:
        return {s: b for s, b in self.pool.map(self._one, self.symbols) if b is not None}
