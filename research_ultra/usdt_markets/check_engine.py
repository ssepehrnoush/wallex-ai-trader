"""Part 3b: what would the engine do with a USDT market whose base coin is also traded in toman?

Offline only: builds an Engine object WITHOUT __init__ (no Wallex call, no keys) from the WT copy of the code
and feeds made-up balances/books to equity_tmn() and _reconcile(), to show the accounting effect of putting
BTCTMN and BTCUSDT in live_symbols together.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))

from wallexbot.engine import Engine, Meta, Position  # noqa: E402


class J:
    def __init__(self):
        self.events = []

    def log(self, ev, **kw):
        self.events.append((ev, kw))


def make(symbols):
    e = Engine.__new__(Engine)
    e.mode = "live"
    e.symbols = symbols
    e.usdttmn = 264_000
    e._last_bid = {}
    e.positions = {}
    e.state = None
    e.j = J()
    e.meta = {s: Meta(8, 2, 1.0 if s.endswith("USDT") else 50_000, True) for s in symbols}
    return e


R = 264_000
books = {"USDTTMN": SimpleNamespace(bid=R, ask=R + 100, mid=R + 50),
         "BTCTMN": SimpleNamespace(bid=22_000_000_000, ask=22_010_000_000),
         "BTCUSDT": SimpleNamespace(bid=83_300, ask=83_310)}
# account after a BTCTMN buy of ~827k toman (BTCTMN = 22e9 toman per BTC): 4,173,000 TMN cash + 0.0000376 BTC, no USDT
bal = {"TMN": (4_173_000, 0), "BTC": (0.0000376, 0), "USDT": (0, 0)}

e1 = make(["BTCTMN"])
e2 = make(["BTCTMN", "BTCUSDT"])
eq1 = e1.equity_tmn(books, bal)
eq2 = e2.equity_tmn(books, bal)
print(f"equity, TMN markets only        : {eq1:,.0f}")
print(f"equity, BTCTMN + BTCUSDT listed : {eq2:,.0f}   (BTC counted twice: +{eq2 - eq1:,.0f} = {100 * (eq2 / eq1 - 1):.1f}%)")

# reconcile: engine tracks the BTCTMN position; does BTCUSDT adopt the same coins?
e2.positions["BTCTMN"] = Position("BTCTMN", 0.0000376, 22_010_000_000, 827_000, 0, -0.008, "x", 22_000_000_000)
e2._reconcile(bal, books, why="adopted_holding")
print("positions after reconcile:", sorted(e2.positions))
print("journal:", e2.j.events)
