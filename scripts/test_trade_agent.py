"""Offline test of the per-trade AI decision (config "trade_agent", wallexbot/trader.py) through the engine's own
entry path, with the fake exchange of scripts/test_live_paths.py and a fake model (no network, no keys, no orders).

Checks: with the agent on, the engine asks instead of buying and buys only on a fresh BUY whose candidate is still
there; SKIP, a late answer, a vanished candidate and a model error all mean no trade; a SKIP cools the market down;
no question is asked when the risk rules would block the trade anyway; the model's size is kept inside
[size_min, policy size]; every answer is logged; and with the agent off the engine enters by itself as before.

  python scripts/test_trade_agent.py
"""
import importlib.util
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# reuse the offline guards, the fake exchange and make_engine of the live-path test (its checks run only as __main__)
spec = importlib.util.spec_from_file_location("live_paths", ROOT / "scripts" / "test_live_paths.py")
LP = importlib.util.module_from_spec(spec)
spec.loader.exec_module(LP)

import tempfile

from wallexbot.ai import AICommittee
from wallexbot.feeds import Book
from wallexbot.trader import TradeAgent

RUN = ROOT / "runs" / "test_live_paths"
saved = {f: (RUN / f).read_bytes() for f in ("decisions.jsonl", "state.json") if (RUN / f).exists()}


class FakeAI:
    def __init__(self, decision="buy", size=0.25, fail=False):
        self.decision, self.size, self.fail, self.calls = decision, size, fail, []

    def ask_fast(self, role, system, payload, timeout_s):
        self.calls.append(payload)
        if self.fail:
            raise RuntimeError("model down")
        return ({"decision": self.decision, "size_factor": self.size, "confidence": 0.7, "reason_fa": "آزمون"},
                "fake-model", 0.01)


class Log:
    def __init__(self):
        self.events = []

    def log(self, event, **kw):
        self.events.append((event, kw))

    def last(self, event):
        return next((kw for e, kw in reversed(self.events) if e == event), None)


GLOBAL = {"BTC": 83800.0}                       # fair = 83,800 x 264,000 = 22,123,200,000 toman
CHEAP = Book(21_900_000_000, 1.0, 21_900_100_000, 1.0, 0)     # ask about 1.0% under fair: a candidate
NORMAL = Book(22_120_000_000, 1.0, 22_121_000_000, 1.0, 0)    # at fair: no candidate


def engine(ai=None, cfg=None):
    fake = LP.FakeClient()
    eng = LP.make_engine(fake)
    eng.j = Log()
    tr = eng.trackers["BTCTMN"]
    t0 = time.time() - 3600
    for i in range(tr.min_samples + 10):           # a ready baseline at the fair price
        tr.add(t0 + i, 0.0)
    eng.policies["BTCTMN"].size_factor = 0.3
    if ai is not None:
        eng.trader = TradeAgent(ai, {"enabled": True, "timeout_s": 1, "max_age_s": 20, "cooldown_after_skip_s": 120,
                                     "cooldown_after_other_s": 30, "max_requests_per_hour": 30,
                                     "max_requests_per_symbol_hour": 8, **(cfg or {})})
    return eng


def tick(eng, book, now):
    eng._step_symbol("BTCTMN", book, GLOBAL, "normal", now, datetime.now(timezone.utc), True, [])


def answered(eng, timeout=3.0):
    t = time.time()
    while time.time() - t < timeout:
        with eng.trader._lock:
            if eng.trader.ready:
                return True
        time.sleep(0.01)
    return False


fails = 0


def check(cond, msg):
    global fails
    print(("PASS " if cond else "FAIL ") + msg)
    fails += not cond


class _Provider:
    """Stands in for an OpenAI client: records with_options(max_retries=...) and counts create() calls."""
    def __init__(self, fail):
        self.fail, self.n, self.retries = fail, 0, []
        self.chat = self.completions = self

    def with_options(self, **kw):
        self.retries.append(kw.get("max_retries"))
        return self

    def create(self, **kw):
        self.n += 1
        if self.fail:
            raise TimeoutError("slow gateway")
        msg = type("M", (), {"content": '{"decision": "skip"}'})()
        return type("R", (), {"choices": [type("C", (), {"message": msg})()]})()


try:
    c = AICommittee.__new__(AICommittee)
    c.cfg, c.offline, c.cache_dir, c.calls = {}, False, Path(tempfile.mkdtemp()), 0
    primary, fallback = _Provider(True), _Provider(False)
    c.client, c.model, c.fallback = primary, "primary-model", (fallback, "fallback-model")
    _, used, _ = c.ask_fast("trade_agent", "system", {"x": 1}, 1)
    check(primary.n == 1 and primary.retries == [0] and fallback.n == 1 and used == "fallback-model",
          "ask_fast: one attempt per provider with the SDK's own retries off, then the fallback")

    now = time.time()
    ai = FakeAI("buy", size=0.25)
    eng = engine(ai)
    tick(eng, CHEAP, now)
    check(len(ai.calls) == 1 and "BTCTMN" not in eng.positions, "agent on: a candidate is asked about, not bought")
    m = ai.calls[0]["market"]
    check(m["symbol"] == "BTCTMN" and m["buy_dev_pct"] < -0.9 and "buy_dev_last_10min_pct" in m and ai.calls[0]["size_max"] == 0.3,
          "the dossier has the market, its discount history and the size range")
    check("balance" not in str(ai.calls[0]).lower(), "the dossier holds no balances")
    check(answered(eng), "the answer comes back on its own thread")
    tick(eng, CHEAP, now + 2)
    check("BTCTMN" in eng.positions, "a fresh BUY with the candidate still there opens the position")
    entry = eng.j.last("entry")
    check(entry and entry["ai_decision"]["decision"] == "buy" and "تصمیم هوش مصنوعی" in entry["why_fa"],
          "the entry log carries the model's decision and reason")
    check(eng.j.last("trade_decision")["action"] == "buy", "the decision itself is logged")

    ai = FakeAI("skip")
    eng = engine(ai)
    tick(eng, CHEAP, now)
    answered(eng)
    tick(eng, CHEAP, now + 2)
    for k in range(3, 30, 5):
        tick(eng, CHEAP, now + k)
    check("BTCTMN" not in eng.positions and len(ai.calls) == 1, "SKIP: no trade, and the market cools down (no new question)")
    tick(eng, CHEAP, now + 200)
    check(len(ai.calls) == 2, "after the cooldown the next candidate is asked about again")

    ai = FakeAI("buy")
    eng = engine(ai)
    tick(eng, CHEAP, now)
    answered(eng)
    tick(eng, CHEAP, now + 30)
    check("BTCTMN" not in eng.positions and eng.j.last("trade_decision")["action"] == "expired", "a BUY older than max_age_s expires")
    tick(eng, CHEAP, now + 35)
    n_after = len(ai.calls)
    tick(eng, CHEAP, now + 65)
    check(n_after == 1 and len(ai.calls) == 2, "after an expired answer the market waits the short cooldown, then is asked again")

    ai = FakeAI("buy")
    eng = engine(ai)
    tick(eng, CHEAP, now)
    answered(eng)
    tick(eng, NORMAL, now + 2)
    d = eng.j.last("trade_decision")
    check("BTCTMN" not in eng.positions and d["action"] == "candidate gone" and d["why_not"] == "no discount",
          "a BUY whose discount closed before the answer arrived buys nothing")

    ai = FakeAI(fail=True)
    eng = engine(ai)
    tick(eng, CHEAP, now)
    answered(eng)
    tick(eng, CHEAP, now + 2)
    d = eng.j.last("trade_decision")
    check("BTCTMN" not in eng.positions and d["decision"] == "skip" and d["error"], "a model error is a skip, logged with the error")
    check(eng.j.last("trade_agent_error") is not None, "a model error is also logged as trade_agent_error")
    tick(eng, CHEAP, now + 40)
    check(len(ai.calls) == 2, "after an error the market is asked again after the short cooldown, not the long one")

    ai = FakeAI("expired-never")
    eng = engine(ai, {"cooldown_after_skip_s": 0, "cooldown_after_other_s": 0, "max_requests_per_symbol_hour": 2})
    for k in range(0, 40, 4):
        tick(eng, CHEAP, now + k)
        answered(eng, 0.5)
    check(len(ai.calls) == 2, f"the per-market hourly cap holds ({len(ai.calls)} questions)")

    ai = FakeAI("buy")
    eng = engine(ai)
    eng.state.halted = "test halt"
    tick(eng, CHEAP, now)
    check(not ai.calls, "no question is asked when the risk rules would block the trade")

    for size, want in ((5.0, 0.3), (0.0, 0.2), ("abc", 0.2), (float("nan"), 0.2), (float("inf"), 0.2)):
        ai = FakeAI("buy", size=size)
        eng = engine(ai)
        tick(eng, CHEAP, now)
        answered(eng)
        tick(eng, CHEAP, now + 2)
        check("BTCTMN" in eng.positions, f"model size {size!r}: the BUY still opens a trade")
        risk = " ".join(eng.j.last("entry")["risk"])
        expected = 5_000_000 * 0.006 * want
        check(f"{expected:,.0f}" in risk, f"model size {size!r} -> size factor {want} used ({risk[:60]})")

    eng = engine(None)
    tick(eng, CHEAP, now)
    check("BTCTMN" in eng.positions and eng.trader is None, "agent off: the engine enters by itself, as before")
finally:
    for f, data in saved.items():
        (RUN / f).write_bytes(data)

print("all passed" if not fails else f"{fails} failed")
sys.exit(1 if fails else 0)
