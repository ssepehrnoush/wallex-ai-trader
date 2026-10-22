"""Live engine for shadow (paper) and live trading. Same loop, different executor.

Every `poll_seconds`: read Wallex books + global prices -> update fair value and premium baselines
-> manage open positions (target / stop / time / end-of-window) -> look for entries within the AI
supervisor's current policy and the risk manager's limits. Every `review_minutes` the supervisor
re-reads the statistics and updates the policies. Every minute (live) the tracked positions are
reconciled with the real balances. Everything is written to decisions.jsonl.
"""
from __future__ import annotations

import csv
import json
import math
import os
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal

from .ai import AICommittee
from .client import WallexClient, new_client_id
from .config import RUNS, load_backtest, load_secrets, market_tick_files
from .fairvalue import Policy, PremiumTracker, base_of, fair_price, momentum, quote_of
from .feeds import GlobalFeed, WallexBooks
from .journal import Journal, read as read_journal
from .risk import TEHRAN, RiskManager
from .supervisor import Supervisor, min_entry
from .trader import TradeAgent


@dataclass
class Position:
    symbol: str
    qty: float               # coins we hold (net of buy fee)
    entry: float             # average buy price
    cost_q: float            # quote currency spent on what is still held
    opened: float            # unix time
    entry_dev: float
    order_id: str
    peak_bid: float = 0.0
    realized_q: float = 0.0  # P&L of chunks already sold (partial exits)


@dataclass
class Meta:
    step: int                # quantity decimals
    tick: int                # price decimals
    min_notional: float      # the exchange minimum order value, as published
    market_orders: bool


def floor_to(x: float, decimals: int) -> float:
    """Round DOWN to `decimals` without float artefacts (floor(0.29*100) is 28 in floats)."""
    return float(Decimal(repr(x)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_DOWN))


class PaperExecutor:
    """Shadow mode: fills at the observed top of book, limited to the size shown there, with real fees."""
    name = "shadow"

    def __init__(self, fees_by_quote: dict, start_cash: dict[str, float]):
        self.fees = fees_by_quote
        self.cash = dict(start_cash)

    def free(self, asset: str) -> float:
        return float("inf")

    def buy(self, sym, book, quote_amount, meta):
        f = self.fees[quote_of(sym)]["taker"]
        qty = floor_to(min(quote_amount / book.ask, book.ask_qty), meta.step)
        if qty * book.ask < meta.min_notional * 1.05:
            return None
        spent = qty * book.ask
        self.cash[quote_of(sym)] -= spent
        return {"qty": qty * (1 - f), "avg": book.ask, "spent": spent, "fee_q": spent * f, "id": new_client_id("SH")}

    def sell(self, sym, book, qty, meta, reason):
        f = self.fees[quote_of(sym)]["taker"]
        q = floor_to(min(qty, book.bid_qty if reason == "target" else qty), meta.step)
        if q <= 0:
            return None
        got = q * book.bid
        self.cash[quote_of(sym)] += got * (1 - f)
        return {"qty": q, "avg": book.bid, "received": got * (1 - f), "fee_q": got * f, "id": new_client_id("SH")}


class LiveExecutor:
    """Real orders. Taker-style LIMIT at the touch, short wait, cancel the remainder.
    Never used unless the key is explicitly marked as the hackathon account (see __main__.py)."""
    name = "live"

    def __init__(self, client: WallexClient, fees_by_quote: dict, journal: Journal):
        self.c = client
        self.fees = fees_by_quote
        self.j = journal

    def _settle(self, cid: str, waits=(0.6, 1.0, 1.5, 2.0)):
        """Wait for the order to finish; otherwise cancel it (retrying until it is really inactive, so no
        order of ours is left on the book) and return its final state. Works even when the POST itself
        timed out: we know the client id because we chose it."""
        res = None
        for w in waits:
            time.sleep(w)
            try:
                res = self.c.get_order(cid)
            except Exception as e:
                self.j.log("order_status_error", id=cid, error=str(e)[:200])
                continue
            if not res.active:
                return res
        for _ in range(3):
            try:
                self.c.cancel_order(cid)
            except Exception as e:
                self.j.log("cancel_error", id=cid, error=str(e)[:200])
            time.sleep(0.5)
            try:
                res = self.c.get_order(cid)
                if not res.active:
                    return res
            except Exception as e:
                self.j.log("order_status_error", id=cid, error=str(e)[:200])
        return res

    def _place(self, sym, side, qty, price, meta) -> str:
        cid = new_client_id()
        q, p = f"{qty:.{meta.step}f}", f"{price:.{meta.tick}f}"
        try:
            o = self.c.place_order(sym, side, "LIMIT", q, price=p, client_id=cid)
            self.j.log("order", side=side, symbol=sym, id=cid, price=p, qty=q, status=o.status)
        except Exception as e:      # a timeout may still have created the order: settle by id anyway
            self.j.log("place_error", side=side, symbol=sym, id=cid, price=p, qty=q, error=str(e)[:200])
        return cid

    def buy(self, sym, book, quote_amount, meta):
        qty = floor_to(min(quote_amount / book.ask, book.ask_qty), meta.step)
        if qty * book.ask < meta.min_notional * 1.05:
            return None
        cid = self._place(sym, "BUY", qty, book.ask, meta)
        r = self._settle(cid)
        if not r or r.executed_qty <= 0:
            return None
        fills = r.raw.get("fills") or []
        fee_coin = sum(float(fl.get("fee") or 0) for fl in fills if fl.get("feeAsset") == base_of(sym))
        net = r.executed_qty - (fee_coin or r.executed_qty * self.fees[quote_of(sym)]["taker"])
        return {"qty": floor_to(net, meta.step), "avg": r.avg_price, "spent": r.executed_sum, "fee_q": r.fee, "id": cid}

    def free(self, asset: str) -> float:
        total, locked = self.c.balances().get(asset, (0.0, 0.0))
        return max(total - locked, 0.0)

    def cancel_strays(self) -> int:
        """This engine never leaves resting orders (every order is settled or cancelled within seconds),
        so any open order found here is a leftover from a timeout or crash: cancel it."""
        n = 0
        for o in self.c.open_orders():
            try:
                self.c.cancel_order(o.client_id)
                n += 1
                self.j.log("stray_order_cancelled", id=o.client_id, symbol=o.symbol, side=o.side)
            except Exception as e:
                self.j.log("cancel_error", id=o.client_id, error=str(e)[:200])
        return n

    def sell(self, sym, book, qty, meta, reason):
        # Sell what the account really holds of this coin. Only this bot trades the hackathon account, so
        # every coin of a traded market belongs to the position, including fee-rounding leftovers: this is
        # what makes the account end fully in toman.
        q = floor_to(self.free(base_of(sym)), meta.step)
        if q <= 0 or q * book.bid < meta.min_notional:
            return None
        # Every exit is a marketable LIMIT (one order format for every market: MARKET is only enabled on a
        # few Wallex markets and is auto-cancelled on >2% moves). Stops/flatten/time cross the bid by 0.3%
        # so they fill like a market order; a target sells at the bid.
        px = book.bid * (0.997 if reason in ("stop", "flatten", "time") else 1.0)
        cid = self._place(sym, "SELL", q, px, meta)
        r = self._settle(cid)
        if not r or r.executed_qty <= 0:
            return None
        return {"qty": r.executed_qty, "avg": r.avg_price, "received": r.executed_sum - r.fee, "fee_q": r.fee, "id": cid}


class Engine:
    def __init__(self, cfg: dict, mode: str, run_name: str):
        self.cfg = cfg
        self.mode = mode
        fv = cfg["fairvalue"]
        self.fv = fv
        self.secrets = load_secrets()
        self.run_dir = RUNS / run_name
        os.environ["WALLEXBOT_RUN"] = run_name     # tells the MCP tools which run is live
        self.j = Journal(self.run_dir)
        self.public = WallexClient(min_interval=0.0)
        # The hackathon key is created ON the credit account, so it already addresses that account; Wallex's own
        # tutorial sends orders with x-api-key alone. No sub-account header: one way to address one account.
        self.priv = WallexClient(self.secrets.wallex_key) if mode == "live" else None
        # live trades only the markets the hackathon account can hold (toman markets by default)
        self.symbols: list[str] = fv.get("live_symbols", fv["symbols"]) if mode == "live" else fv["symbols"]
        books_syms = sorted(set(self.symbols) | {"USDTTMN"})
        self.books = WallexBooks(self.public, books_syms, workers=8)
        self.global_feed = GlobalFeed(sorted({base_of(s) for s in self.symbols}))
        self.fees_q = {"TMN": cfg["fees"], "USDT": cfg["fees_usdt"]}
        self.trackers = {s: PremiumTracker(fv["baseline_window_min"], fv["baseline_min_samples"]) for s in self.symbols}
        # watch markets are recorded from the first tick but start off; the supervisor's live-evidence gate
        # is the only way to switch one on
        self.watch = set(fv.get("watch_symbols", [])) & set(self.symbols)
        off = {"enabled": False, "reason": "watch: off until its live replay is profitable"}
        self.policies = {s: Policy(**{**fv["default_policy"][quote_of(s)], **(off if s in self.watch else {})})
                         for s in self.symbols}
        self.gl_hist = {b: deque(maxlen=200) for b in self.global_feed.bases}
        self.positions: dict[str, Position] = {}
        self._last_bid: dict[str, float] = {}
        self._last_gap_log = 0.0
        # closed trades survive restarts: reload them from this run's own journal
        self.trades: list[dict] = [{k: v for k, v in e.items() if k not in ("t", "event")} | {"t": e["t"]}
                                   for e in read_journal(self.run_dir / "decisions.jsonl") if e.get("event") == "exit"]
        self.meta = self._load_meta()
        self.window_end = datetime.fromisoformat(cfg["window"]["end_utc"].replace("Z", "+00:00"))
        self.risk = RiskManager(cfg["risk"], self.window_end, cfg["fees"])
        committee = AICommittee(cfg["ai"], self.secrets, RUNS / "ai_cache_live")
        self.supervisor = (Supervisor(committee, self.fees_q, self.watch, contest=cfg.get("contest"),
                                      max_spread=self.fv["max_spread"], min_depth=cfg["risk"]["min_notional"] * 1.05)
                           if cfg["ai"].get("enabled", True) else None)
        if self.supervisor:      # contest bounds hold from the first tick, not only after the first review
            self.policies = self.supervisor.bound_start(self.policies)
        # per-trade AI decision (trader.py): off unless config trade_agent.enabled; then the model decides each entry
        ta = cfg.get("trade_agent") or {}
        self.trader = TradeAgent(committee, ta) if ta.get("enabled") and cfg["ai"].get("enabled", True) else None
        self._review_lock = threading.Lock()
        self._last_review = 0.0
        self._ticks_f = open(self.run_dir / "ticks.csv", "a", newline="", encoding="utf-8")
        self._ticks = csv.writer(self._ticks_f)
        if self._ticks_f.tell() == 0:
            self._ticks.writerow(["t", "symbol", "bid", "ask", "bid_qty", "ask_qty", "fair", "baseline", "buy_dev", "sell_dev"])
        self.usdttmn = None
        self.halt_flag = self.run_dir / "STOP"          # create this file to stop new entries
        self.flatten_flag = self.run_dir / "FLATTEN"    # create this file to sell everything to toman now
        # historical evidence the supervisor may use (scripts/bt_fairvalue.py output), if present
        # (a missing or malformed file gives None and never stops the engine). Each market keeps its caveats
        # next to its numbers: the mean when entering one traded minute later, and trades on a stale USDT/TMN rate.
        self.backtest_ref = load_backtest(fv.get("backtest_reference", "fv_sweep_0.006_0.0035"), RUNS)
        if self.backtest_ref:
            keep = ("trades", "mean_ret_pct", "next_minute_mean_ret_pct", "win_pct", "stale_usdt_trades")
            self.backtest_ref["markets"] = {s: {k: d[k] for k in keep if k in d}
                                            for s, d in self.backtest_ref["markets"].items()}

        self._warm_start()
        self._last_books = {}
        if mode == "live":
            self.exec = LiveExecutor(self.priv, self.fees_q, self.j)
            self.exec.cancel_strays()
            self._reconcile(self.priv.balances(), self.books.get(), why="adopted_holding")
        else:
            cash = {"TMN": cfg["shadow"]["start_tmn"], "USDT": cfg["shadow"]["start_usdt"]}
            self.exec = PaperExecutor(self.fees_q, cash)
        self.state = None

    # -- setup ------------------------------------------------------------
    def _load_meta(self) -> dict[str, Meta]:
        m = self.public.markets()
        out = {}
        for s in self.symbols:
            x = m[s]
            out[s] = Meta(int(x["stepSize"]), int(x["tickSize"]), float(x.get("minNotional") or 0),
                          bool(x.get("isMarketTypeEnable")))
            if quote_of(s) == "USDT" and out[s].min_notional == 0:
                out[s].min_notional = 1.0
        return out

    def _warm_start(self):
        """Seed premium baselines from recent ticks (this run's own file included), so a restart does not
        need hours to relearn each market's normal premium and never trades on a cold baseline."""
        now = time.time()
        cutoff = now - self.fv["baseline_window_min"] * 60
        files = sorted(market_tick_files(RUNS), key=lambda p: p.stat().st_mtime)
        rows = []
        for p in files:
            if p.stat().st_mtime < cutoff:
                continue
            with open(p, encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    try:
                        t = float(r["t"])
                        # t in the future: a torn line that a restart appended to ("17926" + "1792617862.88,...")
                        if cutoff <= t <= now + 60 and r["symbol"] in self.trackers:
                            rows.append((t, r["symbol"], (float(r["bid"]) + float(r["ask"])) / 2 / float(r["fair"]) - 1,
                                         float(r["buy_dev"])))
                    except (KeyError, ValueError, TypeError, ZeroDivisionError):
                        continue          # TypeError: a short or torn line, whose missing fields read as None
        rows.sort()
        for t, s, prem, bd in rows:
            self.trackers[s].add(t, prem)
            self.trackers[s].note_dev(t, bd)
        self.j.log("warm_start", ticks=len(rows), baselines_pct={s: round(t.baseline * 100, 3) for s, t in self.trackers.items()})

    def _reconcile(self, bal: dict, books: dict, why: str = "reconciled"):
        """Make tracked positions match what the account really holds (live). Coins nobody tracks (a fill
        whose confirmation was lost, an earlier run) become positions so exits and flatten sell them;
        positions with nothing sellable behind them (dust, phantom) are dropped and free their slot."""
        for s in self.symbols:
            px = self._mark_price(s, books)
            if px is None:
                continue
            held = bal.get(base_of(s), (0.0, 0.0))[0]
            sellable = held * px >= self.meta[s].min_notional
            p = self.positions.get(s)
            if p and not sellable:
                self.j.log("dust_left", symbol=s, qty=held, value=round(held * px), reason=why)
                del self.positions[s]
                if self.state:
                    self.risk.on_exit(self.state, s, p.realized_q, datetime.now(timezone.utc))
            elif not p and sellable:
                self.positions[s] = Position(s, held, px, held * px, time.time(), 0.0, why, px)
                self.j.log(why, symbol=s, qty=held, mark=px)
            elif p and held > p.qty * 1.001:
                p.cost_q += (held - p.qty) * px
                p.qty = held
                self.j.log("position_resized", symbol=s, qty=held)

    # -- valuation ----------------------------------------------------------
    def _mark_price(self, s: str, books) -> float | None:
        b = books.get(s)
        if b:
            self._last_bid[s] = b.bid
            return b.bid
        if s in self._last_bid:
            return self._last_bid[s]
        p = self.positions.get(s)
        return p.entry if p else None

    def equity_tmn(self, books, bal: dict | None = None) -> float | None:
        """Account value in toman, or None when it cannot be measured right now. None means "skip this
        mark": a failed balance call must never look like a 100% loss and trip the kill switch."""
        u = books.get("USDTTMN")
        u_bid = u.bid if u else (self.usdttmn or 0)
        if self.mode != "live":
            cash = self.exec.cash
            eq = cash.get("TMN", 0) + cash.get("USDT", 0) * u_bid
            for s, p in self.positions.items():
                px = self._mark_price(s, books) or p.entry
                eq += p.qty * px * (u_bid if quote_of(s) == "USDT" else 1)
            return eq
        if bal is None:
            return None
        # value EVERY coin of the traded markets, not only tracked positions (leftovers count too)
        eq = bal.get("TMN", (0, 0))[0] + bal.get("USDT", (0, 0))[0] * u_bid
        for s in self.symbols:
            q = bal.get(base_of(s), (0, 0))[0]
            if q:
                px = self._mark_price(s, books)
                if px is None:
                    return None          # a coin we cannot price: do not guess the account value
                eq += q * px * (u_bid if quote_of(s) == "USDT" else 1)
        return eq

    # -- supervisor -----------------------------------------------------------
    def _review_async(self, now: float):
        if not self.supervisor or not self._review_lock.acquire(blocking=False):
            return
        self._last_review = now

        def work():
            try:
                # percentages only: the model never sees balances or absolute amounts (SECURITY.md, question 2)
                keep = ("symbol", "reason", "ret_pct", "held_s", "entry_dev_pct", "exit_dev_pct")
                data = {"markets": {}, "context": {},
                        "recent_trades": [{k: t[k] for k in keep if k in t} for t in self.trades[-15:]]}
                for s in self.symbols:
                    st = self.trackers[s].stats()
                    b = self._last_books.get(s)
                    if b:
                        st |= {"spread_pct": round(b.spread * 100, 3),
                               "top_depth_quote": round(min(b.bid * b.bid_qty, b.ask * b.ask_qty), 2),
                               "quote": quote_of(s)}
                    st["current_policy"] = self.policies[s].to_dict()
                    data["markets"][s] = st
                for b in ("BTC", "ETH"):
                    if b in self.gl_hist:
                        data["context"][f"{b}_global_ret_10m_pct"] = round((momentum(list(self.gl_hist[b]), now, 600) or 0) * 100, 3)
                data["context"]["fees_round_trip_pct"] = {"TMN": 0.6, "USDT": 0.26}
                if self.backtest_ref:
                    data["backtest_7d_reference"] = self.backtest_ref
                new, rec = self.supervisor.review(data, self.policies)
                self.policies = new
                self.j.log("policy_review", **rec)
            except Exception as e:
                self.j.log("policy_review_error", error=str(e)[:300], tb=traceback.format_exc()[-800:])
            finally:
                self._review_lock.release()
        threading.Thread(target=work, daemon=True).start()

    # -- main loop --------------------------------------------------------------
    def run(self, minutes: float | None = None):
        fv, poll = self.fv, self.fv["poll_seconds"]
        t_end = time.time() + minutes * 60 if minutes else None
        last_mark = 0.0
        self.j.log("start", mode=self.mode, symbols=self.symbols, watch=sorted(self.watch), policy=self.fv["default_policy"])
        warm_until = time.time() + fv["warmup_minutes"] * 60
        while True:
            loop_t = time.time()
            if t_end and loop_t > t_end:
                break
            try:
                self.step(loop_t, loop_t >= warm_until)
                # mark every minute, and a few seconds after a close so the gain guard sizes on a fresh value
                if loop_t - last_mark >= 60 or (self.state and self.state.closed_since_mark and loop_t - last_mark >= 5):
                    self.mark(loop_t)
                    last_mark = loop_t
                if self.supervisor and loop_t >= warm_until and loop_t - self._last_review >= fv["review_minutes"] * 60:
                    self._review_async(loop_t)
            except Exception as e:
                self.j.log("loop_error", error=str(e)[:300], tb=traceback.format_exc()[-1200:])
                time.sleep(3)
            if self.risk.phase(datetime.now(timezone.utc)) == "flatten" and not self.positions and datetime.now(timezone.utc) > self.window_end:
                self.j.log("window_closed")
                break
            time.sleep(max(0.0, poll - (time.time() - loop_t)))
        self.mark(time.time())
        self.j.log("stop", trades=len(self.trades))

    def step(self, now: float, trading: bool):
        books = self.books.get()
        gpx, src = self.global_feed.get()
        self._last_books = books
        u = books.get("USDTTMN")
        if u:
            self.usdttmn = u.mid
        if (not gpx or not self.usdttmn) and now - self._last_gap_log > 60:
            # keep going: without fair values there are no entries, but exits (stop / time / flatten) still run
            self.j.log("feed_gap", wallex=len(books), global_source=src, prices=len(gpx))
            self._last_gap_log = now
        for b, px in gpx.items():
            self.gl_hist[b].append((now, px))
        nowdt = datetime.now(timezone.utc)
        phase = self.risk.phase(nowdt)
        if self.flatten_flag.exists():
            phase = "flatten"          # human emergency switch: same path as the end-of-window exit
        rows = []
        for s in self.symbols:
            try:   # one market's error must never stop the others (or the minute mark) from running
                self._step_symbol(s, books.get(s), gpx, phase, now, nowdt, trading, rows)
            except Exception as e:
                self.j.log("symbol_error", symbol=s, error=str(e)[:300], tb=traceback.format_exc()[-600:])
        if rows:
            self._ticks.writerows(rows)
            self._ticks_f.flush()

    def _break_even(self, s: str, entry: float) -> float:
        t = self.fees_q[quote_of(s)]["taker"]
        return entry * (1 + 2 * t + 0.0005)

    def _step_symbol(self, s, b, gpx, phase, now, nowdt, trading, rows):
        fair = fair_price(s, gpx, self.usdttmn) if self.usdttmn or quote_of(s) == "USDT" else None
        pos = self.positions.get(s)
        pol = self.policies[s]
        if b and not fair:
            if pos:
                # no reference price this tick: no target exit, but stop / time / flatten still protect the position
                reason = ("flatten" if phase == "flatten" else "stop" if b.bid <= pos.entry * (1 - pol.stop_pct)
                          else "time" if now - pos.opened >= pol.max_hold_min * 60 else None)
                if reason:
                    self._exit(s, b, pos, reason, 0.0, now)
            return
        if not b:
            return
        tr = self.trackers[s]
        tr.add(now, b.mid / fair - 1)
        base = tr.baseline
        buy_dev = b.ask / fair - 1 - base
        sell_dev = b.bid / fair - 1 - base
        tr.note_dev(now, buy_dev)
        rows.append([round(now, 2), s, b.bid, b.ask, b.bid_qty, b.ask_qty, round(fair, 6), round(base, 6),
                     round(buy_dev, 6), round(sell_dev, 6)])

        if pos:
            pos.peak_bid = max(pos.peak_bid, b.bid)
            reason = None
            if phase == "flatten":
                reason = "flatten"
            elif b.bid <= pos.entry * (1 - pol.stop_pct):
                reason = "stop"
            elif sell_dev >= -pol.exit_slack and b.bid >= self._break_even(s, pos.entry):
                # the gap has closed AND selling now clears both fees: a "target" never locks in a fee loss
                reason = "target"
            elif now - pos.opened >= pol.max_hold_min * 60:
                reason = "time"
            if reason:
                self._exit(s, b, pos, reason, sell_dev, now)
            return

        if self.trader:
            d = self.trader.take(s)       # a trade-agent answer that came back since the last tick
            if d:
                self._act_on_decision(s, d, b, fair, base, buy_dev, pol, now, nowdt,
                                      self._no_candidate(s, b, buy_dev, pol, now, phase, trading))
                return
        if self._no_candidate(s, b, buy_dev, pol, now, phase, trading):
            return
        if self.trader:
            # the model decides this entry; ask only when the risk rules would allow a trade at all
            if self.trader.wants(s, now) and self._risk_size(s, pol, buy_dev, nowdt)[0] > 0:
                self.trader.request(s, self._dossier(s, b, base, buy_dev, pol, now), now)
            return
        self._enter(s, b, fair, base, buy_dev, pol, now, nowdt)

    def _no_candidate(self, s, b, buy_dev, pol, now, phase, trading) -> str:
        """'' when the entry rule sees a candidate in this market now, else the reason it does not."""
        if not trading or not self.trackers[s].ready or not pol.enabled or phase != "normal":
            return "not trading"
        if self.halt_flag.exists():
            return "STOP file"
        # the fee floor is enforced here too, so no policy (default, stale or AI) can buy below it
        if buy_dev > -max(pol.entry_dev, min_entry(s, self.fees_q)):
            return "no discount"
        mom = momentum(self.gl_hist[base_of(s)], now, 60) if base_of(s) in self.gl_hist else None
        if mom is not None and mom < -self.fv["max_global_drop_60s"]:
            self.j.log("skip", symbol=s, why="global price falling fast", mom_60s=round(mom, 5), buy_dev=round(buy_dev, 5))
            return "global price falling fast"
        if b.spread > self.fv["max_spread"]:
            return "spread above the cap"
        return ""

    def _dossier(self, s, b, base, buy_dev, pol, now) -> dict:
        """What the trade agent sees about one candidate: percentages and market data, never balances or keys."""
        thr = max(pol.entry_dev, min_entry(s, self.fees_q))
        devs = [(t, d) for t, d in self.trackers[s].buy_devs if t >= now - 600]
        lasted = 0.0
        for t, d in reversed(devs):           # how long the discount has been at or beyond the threshold
            if d > -thr:
                break
            lasted = now - t
        g = self.gl_hist.get(base_of(s))
        mom = {}
        for w in (60, 300):
            m = momentum(g, now, w) if g else None
            mom[f"{w}s"] = round(m * 100, 3) if m is not None else None
        u, st = self._last_books.get("USDTTMN"), self.state
        contest = self.cfg.get("contest") or {}
        lo = contest.get("size_factor_range", [0.0])[0] if contest.get("enabled") else 0.0
        return {"time_tehran": datetime.now(TEHRAN).strftime("%H:%M"),
                "market": {"symbol": s, "ask": b.ask, "bid": b.bid, "spread_pct": round(b.spread * 100, 3),
                           "ask_depth_quote": round(b.ask * b.ask_qty), "buy_dev_pct": round(buy_dev * 100, 3),
                           "threshold_pct": round(thr * 100, 3), "baseline_premium_pct": round(base * 100, 3),
                           "discount_lasted_s": round(lasted),
                           "buy_dev_last_10min_pct": [round(d * 100, 3) for _, d in devs[::12]][-20:]},
                "policy": {"stop_pct": pol.stop_pct, "max_hold_min": pol.max_hold_min, "review_reason": pol.reason[:160]},
                "global": {"coin": base_of(s), "momentum_pct": mom},
                "usdt_tmn_book": {"spread_pct": round(u.spread * 100, 3)} if u else None,
                "account": ({"return_pct": round(st.total_return * 100, 3), "drawdown_pct": round(st.drawdown * 100, 3),
                             "open_positions": len(self.positions)} if st else None),
                "recent_trades": [{k: t[k] for k in ("symbol", "reason", "ret_pct") if k in t} for t in self.trades[-5:]],
                "backtest_reference": (self.backtest_ref or {}).get("markets", {}).get(s),
                "size_min": min(lo, pol.size_factor), "size_max": pol.size_factor}

    def _act_on_decision(self, s, d, b, fair, base, buy_dev, pol, now, nowdt, gone: str):
        """A trade-agent answer is back: buy only on a fresh BUY whose candidate is still there (gone == '')."""
        cfg = self.trader.cfg
        age = now - d["asked_at"]
        act = "skip"
        if d["decision"] == "buy":
            act = "expired" if age > cfg.get("max_age_s", 20) else "candidate gone" if gone else "buy"
        self.j.log("trade_decision", symbol=s, decision=d["decision"], action=act, why_not=gone or None,
                   age_s=round(age, 1), buy_dev_pct_asked=d.get("buy_dev_pct"), buy_dev_pct_now=round(buy_dev * 100, 3),
                   **{k: d.get(k) for k in ("size_factor", "confidence", "reason_fa", "model", "latency_s", "error")})
        if d.get("error"):
            self.j.log("trade_agent_error", symbol=s, error=d["error"])     # the model did not answer: no trade
        if act == "buy":
            lo = min(d.get("size_min", 0.0), pol.size_factor)
            try:
                size = float(d.get("size_factor") or 0.0)
            except (TypeError, ValueError):
                size = lo
            size = min(max(size, lo), pol.size_factor) if math.isfinite(size) else lo
            self._enter(s, b, fair, base, buy_dev, pol, now, nowdt, size_factor=size, ai=d)
        # every answer cools the market: a long one after a real SKIP, a short one otherwise (error, expired,
        # vanished candidate, or a BUY the risk rules or the exchange then refused), so one market cannot flood the model
        long_wait = act == "skip" and not d.get("error")
        self.trader.cool(s, now, cfg.get("cooldown_after_skip_s", 120) if long_wait else cfg.get("cooldown_after_other_s", 30))

    def _risk_size(self, s, pol, buy_dev, nowdt, size_factor=None):
        """RiskManager.size for an entry in this market now: (toman notional, reasons); 0 = blocked."""
        st = self.state
        if st is None:
            return 0.0, ["no account value yet"]
        u = self.usdttmn or 0

        class C:      # adapter for RiskManager.size (expects stop/tp percentages)
            symbol = s
            stop_pct = pol.stop_pct
            tp_pct = abs(buy_dev) + pol.exit_slack
        st.open_symbols = set(self.positions)
        st.exposure = sum(p.qty * p.entry * (u if quote_of(k) == "USDT" else 1) for k, p in self.positions.items())
        return self.risk.size(st, C, pol.size_factor if size_factor is None else size_factor, nowdt, edge_checks=False)

    def _enter(self, s, b, fair, base, buy_dev, pol, now, nowdt, size_factor=None, ai=None):
        if self.state is None:
            return
        to_tmn = (self.usdttmn or 0) if quote_of(s) == "USDT" else 1.0
        notional_tmn, why = self._risk_size(s, pol, buy_dev, nowdt, size_factor)
        if notional_tmn <= 0:
            self.j.log("blocked", symbol=s, buy_dev=round(buy_dev, 5), why=why)
            return
        quote_amt = notional_tmn / to_tmn
        cash = self.exec.cash.get(quote_of(s), 0) if isinstance(self.exec, PaperExecutor) else self._live_cash(quote_of(s))
        quote_amt = min(quote_amt, cash * 0.98)
        fill = self.exec.buy(s, b, quote_amt, self.meta[s])
        if not fill:
            self.j.log("entry_unfilled", symbol=s, ask=b.ask, quote_amount=round(quote_amt, 2))
            return
        self.positions[s] = Position(s, fill["qty"], fill["avg"], fill["spent"], now, buy_dev, fill["id"], b.bid)
        self.j.log("entry", symbol=s, executor=self.exec.name, price=fill["avg"], qty=fill["qty"], spent=round(fill["spent"], 4),
                   fair=round(fair, 6), baseline_pct=round(base * 100, 3), buy_dev_pct=round(buy_dev * 100, 3),
                   policy=pol.to_dict(), risk=why, ai_decision=ai,
                   why_fa=(f"قیمت خرید والکس {abs(buy_dev) * 100:.2f}٪ ارزان‌تر از فاصلهٔ عادی‌اش با قیمت منصفانهٔ جهانی بود "
                           f"(آستانه {pol.entry_dev * 100:.2f}٪)" + (f"؛ تصمیم هوش مصنوعی: {ai.get('reason_fa', '')}" if ai else "")))

    def _live_cash(self, q):
        try:
            return self.exec.free(q)
        except Exception:
            return 0.0

    def _exit(self, s, b, pos, reason, sell_dev, now):
        fill = self.exec.sell(s, b, pos.qty, self.meta[s], reason)
        if not fill:
            # Nothing sellable left (below the exchange minimum, or coins gone): stop tracking it. Dust stays in
            # the account, still counted in equity, instead of being retried forever and blocking a slot.
            held = self._live_cash(base_of(s)) if isinstance(self.exec, LiveExecutor) else pos.qty
            if held * b.bid < self.meta[s].min_notional:
                self.j.log("dust_left", symbol=s, qty=held, value=round(held * b.bid), reason=reason)
                del self.positions[s]
                if self.state:
                    self.risk.on_exit(self.state, s, pos.realized_q, datetime.now(timezone.utc))
                return
            self.j.log("exit_unfilled", symbol=s, reason=reason, bid=b.bid)
            return
        frac = min(fill["qty"] / pos.qty, 1.0) if pos.qty else 1.0
        cost_part = pos.cost_q * frac
        pnl_q = fill["received"] - cost_part
        pos.realized_q += pnl_q
        to_tmn = self.usdttmn if quote_of(s) == "USDT" else 1.0
        rec = {"symbol": s, "reason": reason, "entry": pos.entry, "exit": fill["avg"], "qty": fill["qty"],
               "pnl_quote": round(pnl_q, 6), "pnl_tmn": round(pnl_q * (to_tmn or 0)),
               "ret_pct": round(pnl_q / cost_part * 100, 3) if cost_part else 0.0,
               "held_s": round(now - pos.opened), "entry_dev_pct": round(pos.entry_dev * 100, 3),
               "exit_dev_pct": round(sell_dev * 100, 3)}
        self.trades.append(rec)
        self.j.log("exit", executor=self.exec.name, **rec)
        remaining = max(pos.qty - fill["qty"], 0.0)
        if remaining * b.bid >= self.meta[s].min_notional:
            pos.qty = remaining
            pos.cost_q -= cost_part
        else:
            del self.positions[s]
            if self.state:
                self.risk.on_exit(self.state, s, pos.realized_q, datetime.now(timezone.utc))

    def mark(self, now: float):
        books = self._last_books or {}
        bal = None
        if self.mode == "live":
            try:
                self.exec.cancel_strays()   # runs between steps, when no order of ours is in flight
            except Exception as e:
                self.j.log("stray_check_error", error=str(e)[:200])
            try:
                bal = self.priv.balances()
                self._reconcile(bal, books)
            except Exception as e:
                self.j.log("balance_error", error=str(e)[:200])
                bal = None
        eq = self.equity_tmn(books, bal)
        if eq is None or eq <= 0:
            return          # could not measure the account this minute; keep the last state
        if self.state is None:
            # live: measure return and drawdown against the hackathon's starting capital, not against
            # whatever the account held at the last restart (a restart must not reset the kill switch)
            base = self.cfg.get("live", {}).get("start_capital") if self.mode == "live" else None
            self.state = self.risk.new_state(base or eq)
            self.state.peak_equity = max(self.state.peak_equity, eq)
        self.risk.mark(self.state, eq, datetime.now(timezone.utc))
        snap = {"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "mode": self.mode, "equity_tmn": round(eq),
                "return_pct": round(self.state.total_return * 100, 3), "drawdown_pct": round(self.state.drawdown * 100, 3),
                "halted": self.state.halted, "positions": {s: p.__dict__ for s, p in self.positions.items()},
                "trades": len(self.trades), "policies": {s: p.to_dict() for s, p in self.policies.items()},
                "baselines_pct": {s: round(t.baseline * 100, 3) for s, t in self.trackers.items()}}
        self.j.log("equity", **{k: v for k, v in snap.items() if k in ("equity_tmn", "return_pct", "drawdown_pct", "halted", "trades")})
        (self.run_dir / "state.json").write_text(json.dumps(snap, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
