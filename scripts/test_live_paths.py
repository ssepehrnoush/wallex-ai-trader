"""Offline test of the LIVE-only code paths with a fake exchange (no network orders, no keys).

Exercises: entry -> target exit, partial fills, stop exit, a sell the account cannot cover (dust),
a failed balance call (must not trip the kill switch), restart adoption, end-of-window flatten,
a warm start and the AI tools' tick reader over a ticks.csv with torn lines.
    python scripts/test_live_paths.py

Fully offline by itself: Wallex /v1/markets is served from research/markets_snapshot.csv and any other
Wallex request raises; the global price feed is a fixed stub; every HTTP send is blocked. The engine's
tick rows go to memory, never to runs/test_live_paths/ticks.csv, because the live AI tools and the
engine warm start read runs/*/ticks.csv and fake rows (an ETHTMN row at a BTC price) must not leak there.
"""
import csv
import io
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx

from wallexbot import client as WC
from wallexbot import config as CFG
from wallexbot import engine as E
from wallexbot.client import OrderResult
from wallexbot.config import load_config
from wallexbot.fairvalue import Policy
from wallexbot.feeds import Book

# -- offline guards (installed before any engine is built) -----------------------------------------
SNAPSHOT = ROOT / "research" / "markets_snapshot.csv"
USDT_DEFAULT = {"stepSize": 6, "tickSize": 4, "minNotional": 1.0, "isMarketTypeEnable": True}
wallex_calls: list[str] = []
http_attempts: list[str] = []


class _Snapshot(dict):
    """Market rules from the saved snapshot; USDT-quoted markets (absent from it) get a safe default."""
    def __missing__(self, symbol):
        if symbol.endswith("USDT"):
            return dict(USDT_DEFAULT)
        raise KeyError(symbol)


def _snapshot_markets() -> dict:
    out = _Snapshot()
    with open(SNAPSHOT, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            out[r["symbol"]] = {"stepSize": int(r["qty_decimals"]), "tickSize": int(r["price_decimals"]),
                                "minNotional": float(r["min_notional_tmn"]),
                                "isMarketTypeEnable": r["market_orders_enabled"] == "True"}
    return out


def _offline_request(self, method, path, retries=3, **kw):
    wallex_calls.append(f"{method} {path}")
    if method == "GET" and path == "/v1/markets":
        return {"result": {"symbols": _snapshot_markets()}}
    raise RuntimeError(f"offline test: Wallex {method} {path} blocked")


def _no_http(self, request, *a, **k):
    http_attempts.append(str(request.url))
    raise RuntimeError(f"offline test: HTTP {request.method} {request.url} blocked")


async def _no_http_async(self, request, *a, **k):
    _no_http(self, request)


class OfflineGlobalFeed:
    """Stand-in for the Binance/OKX feed: a fixed BTC price (83,800 USDT, about 22.1e9 toman at 264,000)."""
    PRICES = {"BTC": 83800.0}

    def __init__(self, bases):
        self.bases = list(bases)
        self.source = "offline"

    def get(self):
        return {b: px for b, px in self.PRICES.items() if b in self.bases}, "offline"


WC.WallexClient._request = _offline_request
httpx.Client.send = _no_http
httpx.AsyncClient.send = _no_http_async
E.GlobalFeed = OfflineGlobalFeed


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
    cfg["risk"]["protect_gain_share"] = 0      # these checks chain trades after a win; the gain guard has its own test
    eng = E.Engine(cfg, "shadow", "test_live_paths")       # build offline, then swap in the live executor
    eng._ticks_f.close()                                    # tick rows to memory: never into runs/*/ticks.csv
    eng._ticks_f = io.StringIO()
    eng._ticks = csv.writer(eng._ticks_f)
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


passed = 0


def check(cond, msg):
    global passed
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        print(f"{passed} checks passed, then check {passed + 1} failed")
        raise SystemExit(1)
    passed += 1


TEST_TICKS = CFG.RUNS / "test_live_paths" / "ticks.csv"


def _tick_rows(path: Path) -> int:
    """Data rows (header lines excluded) in a ticks.csv; 0 if the file does not exist."""
    try:
        return sum(1 for ln in path.read_bytes().split(b"\n") if ln.strip() and not ln.startswith(b"t,"))
    except OSError:
        return 0


if __name__ == "__main__":
    rows_before = _tick_rows(TEST_TICKS)
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

    # the test itself must stay offline and must not leave fake ticks where the live AI tools read
    check(set(wallex_calls) <= {"GET /v1/markets"} and not http_attempts and _tick_rows(TEST_TICKS) == rows_before,
          f"offline: Wallex calls {sorted(set(wallex_calls))} served from the snapshot, {len(http_attempts)} HTTP sends, "
          f"no tick row added to runs/test_live_paths/ticks.csv")
    # the helper the AI tools and the engine warm start read ticks through
    header = "t,symbol,bid,ask,bid_qty,ask_qty,fair,baseline,buy_dev,sell_dev\n"
    with tempfile.TemporaryDirectory() as tmp:
        for name in ("live_main", "shadow_v1", "test_live_paths", "smoke"):
            (Path(tmp) / name).mkdir()
            (Path(tmp) / name / "ticks.csv").write_text(header, encoding="utf-8")
        listed = {p.parent.name for p in CFG.market_tick_files(Path(tmp))}
    real = {p.resolve() for p in CFG.market_tick_files()}
    check(listed == {"live_main", "shadow_v1"} and TEST_TICKS.resolve() not in real,
          f"config.market_tick_files() keeps live and shadow runs and leaves out test and smoke runs "
          f"(sample folder: {sorted(listed)}; real runs/: {len(real)} files)")

    # a restart must survive torn lines: one that a later restart appended to (a future t), a short last line
    # (t and symbol present, later fields missing -> None) and a shifted row (a symbol where a number belongs).
    # Only the one good row may seed the baseline, and only that row may reach the AI tools.
    t_now = time.time()
    row = lambda t: f"{t:.2f},BTCTMN,22000000000,22000100000,1,1,22050000000,-0.002,-0.0025,-0.0026\n"
    shifted = f"{t_now - 58:.2f},BTCTMN,22000000000,BTCTMN,1,1,22050000000,-0.002,-0.0025,-0.0026\n"
    tool_rows, tool_err = None, None
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "live_x").mkdir()
        torn = Path(tmp) / "live_x" / "ticks.csv"
        torn.write_text(
            header + row(t_now - 60) + shifted + f"{t_now - 57.5:.2f}"[:5] + row(t_now - 55)
            + f"{t_now - 52.5:.2f},BTCTMN,2200",
            encoding="utf-8")
        try:
            from wallexbot import tools as T
            tool_rows = len(T._tail_file(torn, 180, t_now - 180 * 60))
        except Exception as e:                 # noqa: BLE001 - the check reports any escape
            tool_err = f"{type(e).__name__}: {e}"
        runs_before, E.RUNS = E.RUNS, Path(tmp)
        seen = {}
        stub = type("S", (), {"fv": {"baseline_window_min": 180},
                              "trackers": {"BTCTMN": E.PremiumTracker(180, 240)},
                              "j": type("J", (), {"log": lambda self, ev, **kw: seen.update(kw)})()})()
        try:
            E.Engine._warm_start(stub)
            err = None
        except Exception as e:                 # noqa: BLE001 - the check reports any escape
            err = f"{type(e).__name__}: {e}"
        finally:
            E.RUNS = runs_before
    check(err is None and seen.get("ticks") == 1 and tool_err is None and tool_rows == 1,
          f"warm start and the AI tools' tick reader skip torn lines in a ticks.csv and keep the one good row "
          f"(warm start error: {err}, rows: {seen.get('ticks')}; tools error: {tool_err}, rows: {tool_rows})")
    print(f"final toman {fake.bal['TMN']:,.0f}")
    print(f"{passed}/{passed} checks passed")
