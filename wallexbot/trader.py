"""Trade agent: the model decides every single entry (config "trade_agent"; off unless enabled).

The engine still watches every market every 2.5 s, because a dislocation can close in seconds and an LLM round trip
is slower than that. With the trade agent on, the engine no longer buys by itself: when its rule finds a candidate it
sends the model a dossier of that moment (order book, how deep and how long the discount is, the last minutes of
the discount, the global price and the USDT/TMN book, account and contest state, the bot's recent results), and the
model decides BUY or SKIP, the size within the policy, and why. The engine buys only on a fresh BUY whose candidate
is still there when the answer arrives; it then still goes through every risk rule. No answer, a late answer or an
error is a skip. Exits stay with the engine (stop, target, time, flatten).

Every decision, with its dossier and latency, is written to the decision log ("trade_decision") and to the AI
cache folder, so each trade (and each skipped candidate) can be traced to the model's reason.
"""
from __future__ import annotations

import threading
import time

TRADER = """You are the trader of a fair-value reversion engine on the Iranian exchange Wallex (toman markets).
The engine watches the real order book every 2.5 s. It just found a CANDIDATE entry: the Wallex ask is below the
global fair value (Binance price x Wallex USDT/TMN) by more than the market's threshold, after that market's normal
premium. You decide this one trade, now: BUY or SKIP.

What the bot's own research measured (30 days of 1-minute data and 16 h of the real order book):
- Round trip costs about 0.6% in fees plus the spread. A trade wins when the discount closes within the holding time.
  Filled at the signal minute the rule averaged +0.14% per trade with 61% winners; filled one minute later -0.49%.
- Discounts deeper than 1% won more often (69% filled at once, 37% a minute late), and so did tight-spread markets
  (BTC, ETH, DOGE, PEPE, BNB: 65% / 36%).
- On the real book, the 3 candidates of one night (ARB and BNB, wide spreads, stale night-time USDT/TMN rate) lost
  about 1% each: their discount did not close in 25 minutes.
- Warning signs: a falling global price, a widening spread, a thin top of book, a discount that has already lasted
  long or keeps deepening (Wallex may be repricing, not mispriced). Good signs: a fresh, sharp discount in a liquid,
  tight-spread market while the global price is steady.
Contest: only a positive final return scores profit points, and a bot that never buys can only end flat. The code
caps new risk once the account is ahead (gain guard) and stops buying after a 0.4% loss (loss budget).

Decide on the evidence in front of you: BUY when this candidate looks like a real, short-lived mispricing, SKIP when
a warning sign explains the gap. Reply with JSON only:
{"decision": "buy|skip", "size_factor": <between size_min and size_max from the dossier>,
 "confidence": <0.0-1.0>, "reason_fa": "<one short Persian sentence citing the evidence>"}"""


class TradeAgent:
    def __init__(self, committee, cfg: dict):
        self.ai, self.cfg = committee, cfg
        self._lock = threading.Lock()
        self.pending: dict[str, float] = {}          # symbol -> time asked
        self.ready: dict[str, dict] = {}             # symbol -> decision waiting for the engine
        self.cooldown_until: dict[str, float] = {}   # symbol -> no new question before this time
        self.asked: list[tuple[float, str]] = []     # (time, symbol) of recent questions (hourly caps)

    def wants(self, symbol: str, now: float) -> bool:
        """May the engine ask about this market now (nothing pending or waiting, no cooldown, under both caps)?"""
        with self._lock:
            self.asked = [(t, s) for t, s in self.asked if t > now - 3600]
            mine = sum(1 for _, s in self.asked if s == symbol)
            return (symbol not in self.pending and symbol not in self.ready
                    and self.cooldown_until.get(symbol, 0) <= now
                    and len(self.asked) < self.cfg.get("max_requests_per_hour", 30)
                    and mine < self.cfg.get("max_requests_per_symbol_hour", 8))

    def request(self, symbol: str, dossier: dict, now: float) -> None:
        with self._lock:
            self.pending[symbol] = now
            self.asked.append((now, symbol))
        try:
            threading.Thread(target=self._ask, args=(symbol, dossier, now), daemon=True).start()
        except Exception as e:      # could not even start: record a skip instead of leaving the market pending
            self._store(symbol, {"decision": "skip", "error": f"thread: {e}"[:200]}, dossier, now)

    def _ask(self, symbol: str, dossier: dict, asked_at: float) -> None:
        d = {"decision": "skip", "error": "no answer", "reason_fa": "پاسخی از مدل نرسید؛ خرید انجام نشد"}
        try:
            resp, model, latency = self.ai.ask_fast("trade_agent", TRADER, dossier, self.cfg.get("timeout_s", 8))
            d = {"decision": str(resp.get("decision", "skip")).lower(), "size_factor": resp.get("size_factor"),
                 "confidence": resp.get("confidence"), "reason_fa": str(resp.get("reason_fa", ""))[:200],
                 "model": model, "latency_s": latency}
        except Exception as e:
            d["error"] = str(e)[:200]
        finally:
            self._store(symbol, d, dossier, asked_at)

    def _store(self, symbol: str, d: dict, dossier: dict, asked_at: float) -> None:
        d |= {"asked_at": asked_at, "answered_at": time.time(), "buy_dev_pct": dossier["market"]["buy_dev_pct"],
              "size_min": dossier.get("size_min", 0.0)}
        with self._lock:
            self.pending.pop(symbol, None)
            self.ready[symbol] = d

    def take(self, symbol: str) -> dict | None:
        """The decision for this market that came back since the last tick, if any (the engine consumes it)."""
        with self._lock:
            return self.ready.pop(symbol, None)

    def cool(self, symbol: str, now: float, seconds: float) -> None:
        with self._lock:
            self.cooldown_until[symbol] = now + seconds
