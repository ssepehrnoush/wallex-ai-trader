"""Offline test of the LIVE-only code paths with a fake exchange (no network orders, no keys).

Exercises: entry -> target exit, partial fills, stop exit, a sell the account cannot cover (dust),
a failed balance call (must not trip the kill switch), restart adoption, end-of-window flatten.
    python scripts/test_live_paths.py
"""
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wallexbot import engine as E
from wallexbot.client import OrderResult
from wallexbot.config import load_config
from wallexbot.fairvalue import Policy
from wallexbot.feeds import Book


class FakeClient:
    def __init__(self, tmn=5_000_000.0, fill_ratio=1.0):
        self.bal = {"TMN": tmn}
        self.orders = {}
        self.fill_ratio = fill_ratio
        self.fail_balances = False
        self.n = 0

    def balances(self):
        if self.fail_balances:
            raise RuntimeError("simulated balance outage")
        return {k: (v, 0.0) for k, v in self.bal.items()}

    def place_order(self, symbol, side, order_type, quantity, price=None, stop_price=None, client_id=None):
        self.n += 1
        cid = client_id or f"T_{self.n}"
        q, px = float(quantity), float(price)
        ex = q * self.fill_ratio
        base = symbol[:-3]
        if side == "BUY":
            cost = ex * px
            assert self.bal["TMN"] >= cost - 1e-6, "buy without enough toman"
            fee_coin = ex * 0.003
            self.bal["TMN"] -= cost
            self.bal[base] = self.bal.get(base, 0) + ex - fee_coin
            fills = [{"fee": str(fee_coin), "feeAsset": base, "price": str(px), "quantity": str(ex)}]
            fee = fee_coin
        else:
            assert self.bal.get(base, 0) >= ex - 1e-9, f"sell {ex} > held {self.bal.get(base, 0)}"
            got = ex * px
            fee = got * 0.003
            self.bal[base] -= ex
            self.bal["TMN"] += got - fee
            fills = [{"fee": str(fee), "feeAsset": "TMN", "price": str(px), "quantity": str(ex)}]
        raw = {"clientOrderId": cid, "status": "FILLED" if self.fill_ratio == 1 else "PARTIALLY_FILLED",
               "active": self.fill_ratio < 1, "side": side, "symbol": symbol, "price": str(px), "origQty": str(q),
               "executedQty": str(ex), "executedSum": str(ex * px), "fee": str(fee), "fills": fills}
        self.orders[cid] = raw
        return E._order(raw) if hasattr(E, "_order") else __import__("wallexbot.client", fromlist=["_order"])._order(raw)

    def get_order(self, cid):
        from wallexbot.client import _order
        return _order(self.orders[cid])

    def cancel_order(self, cid):
        from wallexbot.client import _order
        self.orders[cid]["active"] = False
        self.orders[cid]["status"] = "CANCELED"
        return _order(self.orders[cid])

    def open_orders(self, symbol=None):
        return []


def make_engine(fake):
    cfg = load_config().raw
    cfg["ai"]["enabled"] = False
    eng = E.Engine(cfg, "shadow", "test_live_paths")       # build offline, then swap in the live executor
    eng.mode = "live"
    eng.priv = fake
    eng.exec = E.LiveExecutor(fake, eng.fees_q, eng.j)
    E.time.sleep = lambda s: None                           # no waiting inside _settle
    eng.symbols = ["BTCTMN"]
    eng.policies = {"BTCTMN": Policy(entry_dev=0.007, stop_pct=0.009, max_hold_min=25, size_factor=1.0)}
    eng.usdttmn = 264000.0
    eng._last_books = {"BTCTMN": Book(22_000_000_000, 1.0, 22_000_100_000, 1.0, time.time())}
    eng.mark(time.time())
    return eng


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        raise SystemExit(1)


if __name__ == "__main__":
    fake = FakeClient()
    eng = make_engine(fake)
    check(abs(eng.state.start_equity - 5_000_000) < 1, "baseline is the 5M start capital")
    now = time.time()
    book = Book(21_900_000_000, 1.0, 21_900_100_000, 1.0, now)
    eng._enter("BTCTMN", book, 22_100_000_000, -0.002, -0.007, eng.policies["BTCTMN"], now, datetime.now(timezone.utc))
    check("BTCTMN" in eng.positions, "entry opens a position")
    p = eng.positions["BTCTMN"]
    check(0 <= fake.bal["BTC"] - p.qty < 1e-8, f"tracked qty = coins held net of fee, floored to the step ({p.qty:.8f} vs {fake.bal['BTC']:.10f})")
    up = Book(22_050_000_000, 1.0, 22_050_100_000, 1.0, now + 300)
    eng._exit("BTCTMN", up, p, "target", 0.0, now + 300)
    check("BTCTMN" not in eng.positions and fake.bal["BTC"] < 1e-8, "target exit sells everything (only sub-step dust left)")
    check(eng.trades and eng.trades[-1]["pnl_tmn"] > 0, f"profitable round trip recorded ({eng.trades[-1]['pnl_tmn']} toman)")

    # partial fills: half fills on entry and exit
    fake.fill_ratio = 0.5
    eng._enter("BTCTMN", book, 22_100_000_000, -0.002, -0.007, eng.policies["BTCTMN"], now, datetime.now(timezone.utc))
    p = eng.positions.get("BTCTMN")
    check(p is not None and 0 <= fake.bal["BTC"] - p.qty < 1e-8, "partial entry tracks only the filled part")
    eng._exit("BTCTMN", up, p, "stop", 0.0, now + 60)
    check(("BTCTMN" in eng.positions) or fake.bal["BTC"] * up.bid < eng.meta["BTCTMN"].min_notional,
          "partial exit keeps the remainder (or drops it as dust)")
    fake.fill_ratio = 1.0
    if "BTCTMN" in eng.positions:
        eng._exit("BTCTMN", up, eng.positions["BTCTMN"], "stop", 0.0, now + 90)
    check("BTCTMN" not in eng.positions, "second exit closes the remainder")

    # phantom position: tracked but coins gone (e.g. sold elsewhere) must not retry forever
    eng.positions["BTCTMN"] = E.Position("BTCTMN", 0.01, 22e9, 220e6, now, 0.0, "x", 22e9)
    eng._exit("BTCTMN", up, eng.positions["BTCTMN"], "time", 0.0, now + 1600)
    check("BTCTMN" not in eng.positions, "position with no coins behind it is dropped as dust")

    # balance outage: mark must skip, not record a 100% drawdown
    fake.fail_balances = True
    dd_before = eng.state.drawdown
    eng.mark(time.time())
    check(eng.state.drawdown == dd_before and not eng.state.halted, "balance outage does not trip the kill switch")
    fake.fail_balances = False

    # restart adoption: coins left in the account become a managed position
    fake.bal["BTC"] = 0.01
    eng.positions.clear()
    eng._reconcile(fake.balances(), {"BTCTMN": up}, why="adopted_holding")
    check("BTCTMN" in eng.positions, "leftover coins are adopted after a restart")

    # flatten switch sells them
    eng.flatten_flag.parent.mkdir(parents=True, exist_ok=True)
    eng.flatten_flag.write_text("")
    eng.positions["BTCTMN"].opened = now
    try:
        eng.books = type("B", (), {"get": lambda self: {"BTCTMN": up, "USDTTMN": Book(263900, 1, 264000, 1, now)}})()
        eng.global_feed = type("G", (), {"get": lambda self: ({"BTC": 83800.0}, "test"), "bases": ["BTC"]})()
        eng.gl_hist = {"BTC": __import__("collections").deque(maxlen=10)}
        eng.step(time.time(), True)
    finally:
        eng.flatten_flag.unlink()
    check("BTCTMN" not in eng.positions and fake.bal.get("BTC", 0) < 1e-9, "FLATTEN switch sells everything to toman")

    # a POST that times out after the exchange accepted it: the fill must still be found and tracked
    class Flaky(FakeClient):
        def place_order(self, *a, **k):
            super().place_order(*a, **k)
            raise RuntimeError("timeout after accept")
    fk = Flaky()
    eng2 = make_engine(fk)
    eng2._enter("BTCTMN", book, 22_100_000_000, -0.002, -0.008, eng2.policies["BTCTMN"], now, datetime.now(timezone.utc))
    check("BTCTMN" in eng2.positions and fk.bal.get("BTC", 0) > 0, "fill found by client id even when the POST raised")

    # one market raising must not stop the others or the step
    eng2.symbols = ["BTCTMN", "ETHTMN"]
    eng2.meta["ETHTMN"] = eng2.meta["BTCTMN"]
    eng2.policies["ETHTMN"] = eng2.policies["BTCTMN"]
    eng2.trackers["ETHTMN"] = eng2.trackers["BTCTMN"]
    def boom(*a, **k): raise ValueError("bad market data")
    real = eng2._step_symbol
    calls = []
    def wrapped(s, *a, **k):
        calls.append(s)
        if s == "BTCTMN": boom()
        return real(s, *a, **k)
    eng2._step_symbol = wrapped
    eng2.books = type("B", (), {"get": lambda self: {"BTCTMN": up, "ETHTMN": up, "USDTTMN": Book(263900, 1, 264000, 1, now)}})()
    eng2.step(time.time(), True)
    check(calls == ["BTCTMN", "ETHTMN"], "an exception in one market does not stop the next market")
    eng2._step_symbol = real

    # global feed down: no fair value, but the stop still fires
    eng2.global_feed = type("G", (), {"get": lambda self: ({}, "none"), "bases": ["BTC"]})()
    p2 = eng2.positions["BTCTMN"]
    crash = Book(p2.entry * 0.98, 1.0, p2.entry * 0.981, 1.0, now)
    eng2.books = type("B", (), {"get": lambda self: {"BTCTMN": crash}})()
    eng2.symbols = ["BTCTMN"]
    eng2.step(time.time(), True)
    check("BTCTMN" not in eng2.positions, "with the global feed down the stop still sells")

    # target must clear both fees: gap closed but price only +0.2% -> hold
    fk.bal["BTC"] = 0.0; fk.bal["TMN"] = 5_000_000
    eng2.positions.clear()
    eng2.state.cooldown_until.clear()      # the stop above correctly put BTCTMN on a 2-hour cooldown
    eng2.state.open_symbols.clear()
    eng2._enter("BTCTMN", book, 22_100_000_000, -0.002, -0.008, eng2.policies["BTCTMN"], now, datetime.now(timezone.utc))
    pe = eng2.positions["BTCTMN"]
    small = Book(pe.entry * 1.002, 1.0, pe.entry * 1.0025, 1.0, now)
    eng2.global_feed = type("G", (), {"get": lambda self: ({"BTC": pe.entry * 1.002 / 264000 / (1 - 0.0022)}, "test"), "bases": ["BTC"]})()
    eng2.books = type("B", (), {"get": lambda self: {"BTCTMN": small, "USDTTMN": Book(264000, 1, 264000, 1, now)}})()
    eng2.step(time.time(), True)
    check("BTCTMN" in eng2.positions, "a 'target' that would lose money after fees is not taken")
    print(f"final toman {fake.bal['TMN']:,.0f}")
