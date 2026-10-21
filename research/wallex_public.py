"""Minimal rate-limited client for Wallex PUBLIC market-data endpoints.

No API key, no account endpoints, no order endpoints. Read-only market data only.
"""
from __future__ import annotations

import time

import requests

BASE = "https://api.wallex.ir"
MIN_INTERVAL = 0.3  # seconds between requests (~3 req/s, well under the documented limits)

_session = requests.Session()
_session.headers.update({"User-Agent": "wallex-hackathon-research/0.1", "Accept": "application/json"})
_last = 0.0


def get(path: str, params: dict | None = None, retries: int = 4, timeout: float = 30.0) -> dict:
    """GET a public endpoint with a simple global rate limit and exponential backoff."""
    global _last
    for attempt in range(retries + 1):
        wait = MIN_INTERVAL - (time.monotonic() - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()
        try:
            r = _session.get(BASE + path, params=params, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"HTTP {r.status_code}", response=r)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as exc:
            if attempt == retries:
                raise
            backoff = 2 ** attempt
            print(f"  [retry {attempt + 1}/{retries}] {path} {params} -> {exc}; sleeping {backoff}s")
            time.sleep(backoff)
    raise RuntimeError("unreachable")


def markets() -> dict:
    return get("/v1/markets")["result"]["symbols"]


def depth_all() -> dict:
    return get("/v2/depth/all")["result"]


def depth(symbol: str) -> dict:
    return get("/v1/depth", {"symbol": symbol})["result"]


def udf_history(symbol: str, resolution: str, frm: int, to: int) -> dict:
    return get("/v1/udf/history", {"symbol": symbol, "resolution": resolution, "from": frm, "to": to})
