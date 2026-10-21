"""Thin Wallex REST client (public market data + private spot orders).

Docs: https://developers.wallex.ir . All private calls send `x-api-key`; a trade-credit
sub-account has its own key, optionally with the `sub-account-client-id` header.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

import httpx

BASE = "https://api.wallex.ir"


class WallexError(RuntimeError):
    pass


@dataclass
class OrderResult:
    client_id: str
    status: str
    side: str
    symbol: str
    price: float
    orig_qty: float
    executed_qty: float
    executed_sum: float
    fee: float
    active: bool
    raw: dict

    @property
    def avg_price(self) -> float:
        return self.executed_sum / self.executed_qty if self.executed_qty else 0.0


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _order(raw: dict) -> OrderResult:
    return OrderResult(
        client_id=str(raw.get("clientOrderId", "")),
        status=str(raw.get("status", "")),
        side=str(raw.get("side", "")),
        symbol=str(raw.get("symbol", "")),
        price=_f(raw.get("price")),
        orig_qty=_f(raw.get("origQty")),
        executed_qty=_f(raw.get("executedQty")),
        executed_sum=_f(raw.get("executedSum")),
        fee=_f(raw.get("fee")),
        active=bool(raw.get("active")),
        raw=raw,
    )


class WallexClient:
    def __init__(self, api_key: str | None = None, subaccount_id: str | None = None,
                 timeout: float = 15.0, min_interval: float = 0.25):
        headers = {"Content-Type": "application/json", "User-Agent": "wallexbot/1.0"}
        if api_key:
            headers["x-api-key"] = api_key
        if subaccount_id:
            headers["sub-account-client-id"] = subaccount_id
        self._http = httpx.Client(base_url=BASE, headers=headers, timeout=timeout)
        self._min_interval = min_interval
        self._last = 0.0
        self.has_key = bool(api_key)

    # -- plumbing ---------------------------------------------------------
    def _request(self, method: str, path: str, retries: int = 3, **kw) -> dict:
        last_err: Exception | None = None
        for attempt in range(retries):
            wait = self._min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                r = self._http.request(method, path, **kw)
                if r.status_code == 429 or r.status_code >= 500:
                    raise WallexError(f"{method} {path} -> HTTP {r.status_code}")
                data = r.json()
                if r.status_code >= 400 or data.get("success") is False:
                    # client errors are not retried: they will not fix themselves
                    raise WallexError(f"{method} {path} -> {r.status_code}: {data.get('message')}")
                return data
            except (httpx.TransportError, WallexError, ValueError) as e:
                last_err = e
                if isinstance(e, WallexError) and "HTTP" not in str(e):
                    raise
                time.sleep(0.8 * (attempt + 1))
        raise WallexError(str(last_err))

    # -- public -----------------------------------------------------------
    def markets(self) -> dict:
        return self._request("GET", "/v1/markets")["result"]["symbols"]

    def depth(self, symbol: str) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
        res = self._request("GET", "/v1/depth", params={"symbol": symbol})["result"]
        bids = [(_f(x["price"]), _f(x["quantity"])) for x in res.get("bid", [])]
        asks = [(_f(x["price"]), _f(x["quantity"])) for x in res.get("ask", [])]
        bids.sort(key=lambda x: -x[0])
        asks.sort(key=lambda x: x[0])
        return bids, asks

    def latest_trades(self, symbol: str) -> list[dict]:
        return self._request("GET", "/v1/trades", params={"symbol": symbol})["result"]["latestTrades"]

    def candles(self, symbol: str, resolution: str, start: int, end: int) -> dict:
        return self._request("GET", "/v1/udf/history", params={
            "symbol": symbol, "resolution": resolution, "from": start, "to": end})

    # -- private ----------------------------------------------------------
    def balances(self) -> dict[str, tuple[float, float]]:
        """asset -> (total value, locked)"""
        res = self._request("GET", "/v1/account/balances")["result"]["balances"]
        return {k: (_f(v.get("value")), _f(v.get("locked"))) for k, v in res.items()}

    def fees(self) -> dict:
        return self._request("GET", "/v1/account/fee")["result"]

    def sub_accounts(self) -> list:
        return self._request("GET", "/sub-accounts")["result"]

    def place_order(self, symbol: str, side: str, order_type: str, quantity: str,
                    price: str | None = None, stop_price: str | None = None,
                    client_id: str | None = None) -> OrderResult:
        body = {"symbol": symbol, "side": side, "type": order_type, "quantity": quantity,
                "client_id": client_id or new_client_id()}
        if price is not None:
            body["price"] = price
        if stop_price is not None:
            body["stop_Price"] = stop_price
        # never retry order creation blindly: a timeout may still have created it
        data = self._request("POST", "/v1/account/orders", retries=1, json=body)
        return _order(data["result"])

    def get_order(self, client_id: str) -> OrderResult:
        return _order(self._request("GET", f"/v1/account/orders/{client_id}")["result"])

    def cancel_order(self, client_id: str) -> OrderResult:
        return _order(self._request("DELETE", f"/v1/account/orders/{client_id}", retries=2)["result"])

    def open_orders(self, symbol: str | None = None) -> list[OrderResult]:
        params = {"symbol": symbol} if symbol else {}
        res = self._request("GET", "/v1/account/openOrders", params=params)["result"]
        rows = res if isinstance(res, list) else [o for v in res.values() for o in v]
        return [_order(o) for o in rows]


def new_client_id(prefix: str = "WB") -> str:
    # docs allow letters (A-Z), digits and underscore only
    return f"{prefix}_{int(time.time())}_{uuid.uuid4().hex[:8].upper()}"
