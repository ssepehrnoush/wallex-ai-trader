"""Live engine for shadow (paper) and live trading. Same loop, different executor.

Every `poll_seconds`: read Wallex books + global prices -> update fair value and premium baselines
-> manage open positions (target / stop / time / end-of-window) -> look for entries within the AI
supervisor's current policy and the risk manager's limits. Every `review_minutes` the supervisor
re-reads the statistics and updates the policies. Everything is written to decisions.jsonl.
"""
from __future__ import annotations

import csv
import json
import math
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .ai import AICommittee
from .client import WallexClient, new_client_id
from .config import RUNS, load_secrets
from .fairvalue import Policy, PremiumTracker, base_of, fair_price, momentum, quote_of
from .feeds import GlobalFeed, WallexBooks
from .journal import Journal
from .risk import RiskManager
from .supervisor import Supervisor


@dataclass
class Position:
    symbol: str
    qty: float               # coins we hold (net of buy fee)
    entry: float             # average buy price
    cost_q: float            # quote currency spent incl. fee
    opened: float            # unix time
    entry_dev: float
    order_id: str
    peak_bid: float = 0.0


@dataclass
class Meta:
    step: int                # quantity decimals
    tick: int                # price decimals
    min_notional: float
    market_orders: bool


def floor_to(x: float, decimals: int) -> float:
    m = 10 ** decimals
    return math.floor(x * m) / m


class PaperExecutor:
    """Shadow mode: fills at the observed top of book, limited to the size shown there, with real fees."""
    name = "shadow"

    def __init__(self, fees_by_quote: dict, start_cash: dict[str, float]):
        self.fees = fees_by_quote
        self.cash = dict(start_cash)

    def buy(self, sym, book, quote_amount, meta):
        f = self.fees[quote_of(sym)]["taker"]
        qty = floor_to(min(quote_amount / book.ask, book.ask_qty), meta.step)
        if qty * book.ask < meta.min_notional:
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
    Never used unless the key is explicitly marked as the hackathon account (see cli.py)."""
    name = "live"

    def __init__(self, client: WallexClient, fees_by_quote: dict, journal: Journal):
        self.c = client
        self.fees = fees_by_quote
        self.j = journal

    def _settle(self, oid: str, waits=(0.7, 1.2, 1.8)):
        res = None
        for w in waits:
            time.sleep(w)
            try:
                res = self.c.get_order(oid)
            except Exception as e:
                self.j.log("order_status_error", id=oid, error=str(e)[:200])
                continue
            if not res.active or res.status.upper() in ("FILLED", "DONE", "FINISHED"):
                return res
        try:
            self.c.cancel_order(oid)
        except Exception as e:
            self.j.log("cancel_error", id=oid, error=str(e)[:200])
        try:
            res = self.c.get_order(oid)
        except Exception:
            pass
        return res

    def buy(self, sym, book, quote_amount, meta):
        qty = floor_to(min(quote_amount / book.ask, book.ask_qty), meta.step)
        if qty * book.ask < meta.min_notional:
            return None
        price = f"{book.ask:.{meta.tick}f}"
        o = self.c.place_order(sym, "BUY", "LIMIT", f"{qty:.{meta.step}f}", price=price)
        self.j.log("order", side="BUY", symbol=sym, id=o.client_id, price=price, qty=qty, status=o.status)
        r = self._settle(o.client_id)
        if not r or r.executed_qty <= 0:
            return None
        fee_coin = sum(float(fl.get("fee", 0)) for fl in r.raw.get("fills", []) if fl.get("feeAsset") == base_of(sym))
        net = r.executed_qty - (fee_coin or r.executed_qty * self.fees[quote_of(sym)]["taker"])
        return {"qty": floor_to(net, meta.step), "avg": r.avg_price, "spent": r.executed_sum, "fee_q": r.fee, "id": o.client_id}

    def sell(self, sym, book, qty, meta, reason):
        q = floor_to(qty, meta.step)
        if q <= 0:
            return None
        if reason in ("stop", "flatten") and meta.market_orders:
            o = self.c.place_order(sym, "SELL", "MARKET", f"{q:.{meta.step}f}")
        else:
            # marketable limit a little through the bid so it fills like a market order
            px = book.bid * (0.998 if reason in ("stop", "flatten", "time") else 1.0)
            o = self.c.place_order(sym, "SELL", "LIMIT", f"{q:.{meta.step}f}", price=f"{px:.{meta.tick}f}")
        self.j.log("order", side="SELL", symbol=sym, id=o.client_id, qty=q, reason=reason, status=o.status)
        r = self._settle(o.client_id)
        if not r or r.executed_qty <= 0:
            return None
        return {"qty": r.executed_qty, "avg": r.avg_price, "received": r.executed_sum - r.fee, "fee_q": r.fee, "id": o.client_id}


class Engine:
    def __init__(self, cfg: dict, mode: str, run_name: str):
        self.cfg = cfg
        self.mode = mode
        fv = cfg["fairvalue"]
        self.fv = fv
        self.secrets = load_secrets()
        self.run_dir = RUNS / run_name
        self.j = Journal(self.run_dir)
        self.public = WallexClient(min_interval=0.0)
        self.priv = WallexClient(self.secrets.wallex_key, self.secrets.wallex_subaccount or None) if mode == "live" else None
        self.symbols: list[str] = fv["symbols"]
        books_syms = sorted(set(self.symbols) | {"USDTTMN"})
        self.books = WallexBooks(self.public, books_syms, workers=8)
        self.global_feed = GlobalFeed(sorted({base_of(s) for s in self.symbols}))
        self.fees_q = {"TMN": cfg["fees"], "USDT": cfg["fees_usdt"]}
        self.trackers = {s: PremiumTracker(fv["baseline_window_min"], fv["baseline_min_samples"]) for s in self.symbols}
        self.policies = {s: Policy(**fv["default_policy"][quote_of(s)]) for s in self.symbols}
        self.gl_hist = {b: deque(maxlen=200) for b in self.global_feed.bases}
        self.positions: dict[str, Position] = {}
        self.trades: list[dict] = []
        self.meta = self._load_meta()
        self.window_end = datetime.fromisoformat(cfg["window"]["end_utc"].replace("Z", "+00:00"))
        self.risk = RiskManager(cfg["risk"], self.window_end, cfg["fees"])
        committee = AICommittee(cfg["ai"], self.secrets, RUNS / "ai_cache_live")
        self.supervisor = Supervisor(committee, self.fees_q) if cfg["ai"].get("enabled", True) else None
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
        self.backtest_ref = None
        ref = RUNS / "backtests" / fv.get("backtest_reference", "fv_sweep_0.006_0.0035") / "summary.json"
        if ref.exists():
            m = json.loads(ref.read_text(encoding="utf-8"))
            self.backtest_ref = {"note": "1-minute-candle backtest of the same rule over the last 7 days, "
                                         "entry 0.6% (TMN) / 0.35% (USDT), modelled spreads",
                                 "markets": {s: {k: v for k, v in d.items() if k in ("trades", "mean_ret_pct", "win_pct")}
                                             for s, d in m["markets"].items()}}

        self._warm_start()
        if mode == "live":
            self.exec = LiveExecutor(self.priv, self.fees_q, self.j)
            self.equity0 = self._live_equity()
        else:
            cash = {"TMN": cfg["shadow"]["start_tmn"], "USDT": cfg["shadow"]["start_usdt"]}
            self.exec = PaperExecutor(self.fees_q, cash)
            self.equity0 = None
        self.state = None

    # -- setup ------------------------------------------------------------
    def _load_meta(self) -> dict[str, Meta]:
        m = self.public.markets()
        out = {}
        for s in self.symbols:
            x = m[s]
            out[s] = Meta(int(x["stepSize"]), int(x["tickSize"]), float(x.get("minNotional") or 0) * 1.05,
                          bool(x.get("isMarketTypeEnable")))
            if quote_of(s) == "USDT" and out[s].min_notional == 0:
                out[s].min_notional = 5.5
        return out

    def _warm_start(self):
        """Seed premium baselines from recent ticks of earlier runs, so a restart does not need
        hours to relearn each market's normal premium (and never trades on a cold baseline)."""
        cutoff = time.time() - self.fv["baseline_window_min"] * 60
        files = sorted(RUNS.glob("*/ticks.csv"), key=lambda p: p.stat().st_mtime)
        seeded = 0
        for p in files:
            if p.parent == self.run_dir or p.stat().st_mtime < cutoff:
                continue
            with open(p, encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    try:
                        t = float(r["t"])
                        if t < cutoff or r["symbol"] not in self.trackers:
                            continue
                        mid = (float(r["bid"]) + float(r["ask"])) / 2
                        self.trackers[r["symbol"]].add(t, mid / float(r["fair"]) - 1)
                        self.trackers[r["symbol"]].note_dev(t, float(r["buy_dev"]))
                        seeded += 1
                    except (KeyError, ValueError, ZeroDivisionError):
                        continue
        self.j.log("warm_start", ticks=seeded, baselines_pct={s: round(t.baseline * 100, 3) for s, t in self.trackers.items()})

    def _live_equity(self) -> float:
        bal = self.priv.balances()
        return bal.get("TMN", (0, 0))[0]

    # -- valuation ----------------------------------------------------------
    def equity_tmn(self, books) -> float:
        u = books.get("USDTTMN")
        u_bid = u.bid if u else (self.usdttmn or 0)
        cash = self.exec.cash if isinstance(self.exec, PaperExecutor) else {"TMN": 0, "USDT": 0}
        eq = cash.get("TMN", 0) + cash.get("USDT", 0) * u_bid
        for s, p in self.positions.items():
            b = books.get(s)
            px = b.bid if b else p.entry
            eq += p.qty * px * (u_bid if quote_of(s) == "USDT" else 1)
        if self.mode == "live":
            try:
                bal = self.priv.balances()
                eq = bal.get("TMN", (0, 0))[0] + bal.get("USDT", (0, 0))[0] * u_bid
                for s, p in self.positions.items():
                    b = books.get(s)
                    eq += bal.get(base_of(s), (0, 0))[0] * (b.bid if b else p.entry) * (u_bid if quote_of(s) == "USDT" else 1)
            except Exception as e:
                self.j.log("balance_error", error=str(e)[:200])
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
                        data["context"][f"{b}_global_ret_10m_pct"] = round((momentum(self.gl_hist[b], now, 600) or 0) * 100, 3)
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
        self._last_books = {}
        last_mark = 0.0
        self.j.log("start", mode=self.mode, symbols=self.symbols, policy=self.fv["default_policy"])
        warm_until = time.time() + fv["warmup_minutes"] * 60
        while True:
            loop_t = time.time()
            if t_end and loop_t > t_end:
                break
            try:
                self.step(loop_t, loop_t >= warm_until)
                if loop_t - last_mark >= 60:
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
        if not gpx or not self.usdttmn:
            self.j.log("feed_gap", wallex=len(books), global_source=src)
            return
        for b, px in gpx.items():
            self.gl_hist[b].append((now, px))
        nowdt = datetime.now(timezone.utc)
        phase = self.risk.phase(nowdt)
        if self.flatten_flag.exists():
            phase = "flatten"          # human emergency switch: same path as the end-of-window exit
        rows = []
        for s in self.symbols:
            b = books.get(s)
            fair = fair_price(s, gpx, self.usdttmn)
            if not b or not fair:
                continue
            tr = self.trackers[s]
            tr.add(now, b.mid / fair - 1)
            base = tr.baseline
            buy_dev = b.ask / fair - 1 - base
            sell_dev = b.bid / fair - 1 - base
            tr.note_dev(now, buy_dev)
            rows.append([round(now, 2), s, b.bid, b.ask, b.bid_qty, b.ask_qty, round(fair, 6), round(base, 6),
                         round(buy_dev, 6), round(sell_dev, 6)])

            pos = self.positions.get(s)
            pol = self.policies[s]
            if pos:
                pos.peak_bid = max(pos.peak_bid, b.bid)
                reason = None
                if phase == "flatten":
                    reason = "flatten"
                elif b.bid <= pos.entry * (1 - pol.stop_pct):
                    reason = "stop"
                elif sell_dev >= -pol.exit_slack and b.bid > pos.entry:
                    reason = "target"
                elif now - pos.opened >= pol.max_hold_min * 60:
                    reason = "time"
                if reason:
                    self._exit(s, b, pos, reason, sell_dev, now)
                continue

            if not trading or not tr.ready or not pol.enabled or phase != "normal":
                continue
            if self.halt_flag.exists():
                continue
            if buy_dev > -pol.entry_dev:
                continue
            mom = momentum(self.gl_hist[base_of(s)], now, 60)
            if mom is not None and mom < -self.fv["max_global_drop_60s"]:
                self.j.log("skip", symbol=s, why="global price falling fast", mom_60s=round(mom, 5), buy_dev=round(buy_dev, 5))
                continue
            if b.spread > self.fv["max_spread"]:
                continue
            self._enter(s, b, fair, base, buy_dev, pol, now, nowdt)
        if rows:
            self._ticks.writerows(rows)
            self._ticks_f.flush()

    def _enter(self, s, b, fair, base, buy_dev, pol, now, nowdt):
        st = self.state
        if st is None:
            return
        u = self.usdttmn
        to_tmn = u if quote_of(s) == "USDT" else 1.0

        class C:      # adapter for RiskManager.size (expects stop/tp percentages)
            symbol = s
            stop_pct = pol.stop_pct
            tp_pct = abs(buy_dev) + pol.exit_slack
        st.open_symbols = set(self.positions)
        st.exposure = sum(p.qty * p.entry * (u if quote_of(k) == "USDT" else 1) for k, p in self.positions.items())
        notional_tmn, why = self.risk.size(st, C, pol.size_factor, nowdt, edge_checks=False)
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
                   policy=pol.to_dict(), risk=why,
                   why_fa=f"قیمت خرید والکس {abs(buy_dev) * 100:.2f}٪ زیر قیمت منصفانه جهانی بود (آستانه {pol.entry_dev * 100:.2f}٪)")

    def _live_cash(self, q):
        try:
            return self.priv.balances().get(q, (0, 0))[0] - self.priv.balances().get(q, (0, 0))[1]
        except Exception:
            return 0.0

    def _exit(self, s, b, pos, reason, sell_dev, now):
        fill = self.exec.sell(s, b, pos.qty, self.meta[s], reason)
        if not fill:
            self.j.log("exit_unfilled", symbol=s, reason=reason, bid=b.bid)
            return
        pnl_q = fill["received"] - pos.cost_q * (fill["qty"] / pos.qty if pos.qty else 1)
        to_tmn = self.usdttmn if quote_of(s) == "USDT" else 1.0
        rec = {"symbol": s, "reason": reason, "entry": pos.entry, "exit": fill["avg"], "qty": fill["qty"],
               "pnl_quote": round(pnl_q, 6), "pnl_tmn": round(pnl_q * to_tmn), "ret_pct": round(pnl_q / pos.cost_q * 100, 3),
               "held_s": round(now - pos.opened), "entry_dev_pct": round(pos.entry_dev * 100, 3),
               "exit_dev_pct": round(sell_dev * 100, 3)}
        self.trades.append(rec)
        self.j.log("exit", executor=self.exec.name, **rec)
        remaining = pos.qty - fill["qty"]
        if remaining * b.bid > self.meta[s].min_notional:
            pos.qty = remaining
            pos.cost_q *= remaining / (remaining + fill["qty"])
        else:
            del self.positions[s]
            self.risk.on_exit(self.state, s, pnl_q, datetime.now(timezone.utc))

    def mark(self, now: float):
        books = self._last_books or {}
        eq = self.equity_tmn(books)
        if self.state is None:
            self.state = self.risk.new_state(eq)
        self.risk.mark(self.state, eq, datetime.now(timezone.utc))
        snap = {"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "mode": self.mode, "equity_tmn": round(eq),
                "return_pct": round(self.state.total_return * 100, 3), "drawdown_pct": round(self.state.drawdown * 100, 3),
                "halted": self.state.halted, "positions": {s: p.__dict__ for s, p in self.positions.items()},
                "trades": len(self.trades), "policies": {s: p.to_dict() for s, p in self.policies.items()},
                "baselines_pct": {s: round(t.baseline * 100, 3) for s, t in self.trackers.items()}}
        self.j.log("equity", **{k: v for k, v in snap.items() if k in ("equity_tmn", "return_pct", "drawdown_pct", "halted", "trades")})
        (self.run_dir / "state.json").write_text(json.dumps(snap, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
