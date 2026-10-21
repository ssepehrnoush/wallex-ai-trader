"""Fair-value reversion: the core trading logic.

Idea in one line: Wallex is a local market that follows global prices with a delay. When a
Wallex coin can be BOUGHT (at the ask) clearly below its global fair value, it usually
catches up within minutes; we buy then and sell when the gap has closed.

    fair(TMN market)  = global USDT price (Binance mid) x Wallex USDT/TMN mid
    fair(USDT market) = global USDT price
    premium           = Wallex mid / fair - 1        (each market has its own normal premium)
    buy_dev           = ask / fair - 1 - baseline    (negative = cheap to buy right now)
    sell_dev          = bid / fair - 1 - baseline    (back near 0 = gap closed)

`baseline` is the rolling median premium of that market, so a market that always trades a bit
above or below global prices is judged against its own normal, not against zero.
"""
from __future__ import annotations

import bisect
import statistics
from collections import deque
from dataclasses import asdict, dataclass


@dataclass
class Policy:
    """Per-market trading policy. Set by the AI supervisor within hard bounds (supervisor.py)."""
    enabled: bool = True
    entry_dev: float = 0.005       # buy when ask is this far below fair (after baseline)
    exit_slack: float = 0.0005     # sell when bid is within this of fair (after baseline)
    stop_pct: float = 0.008        # hard stop from entry
    max_hold_min: float = 25.0
    size_factor: float = 0.5       # fraction of the standard risk budget
    reason: str = "default"

    def to_dict(self):
        return asdict(self)


def quote_of(symbol: str) -> str:
    return "TMN" if symbol.endswith("TMN") else "USDT"


def base_of(symbol: str) -> str:
    return symbol[:-3] if symbol.endswith("TMN") else symbol[:-4]


def fair_price(symbol: str, global_px: dict[str, float], usdttmn_mid: float | None) -> float | None:
    g = global_px.get(base_of(symbol))
    if not g:
        return None
    if quote_of(symbol) == "TMN":
        return g * usdttmn_mid if usdttmn_mid else None
    return g


class PremiumTracker:
    """Rolling premium samples for one market (time-bounded window)."""
    def __init__(self, window_min: float = 180, min_samples: int = 40):
        self.window = window_min * 60
        self.min_samples = min_samples
        self.samples: deque[tuple[float, float]] = deque()
        self.sorted: list[float] = []
        self.buy_devs: deque[tuple[float, float]] = deque()   # for supervisor statistics

    def add(self, t: float, premium: float) -> None:
        self.samples.append((t, premium))
        bisect.insort(self.sorted, premium)
        while self.samples and self.samples[0][0] < t - self.window:
            _, old = self.samples.popleft()
            i = bisect.bisect_left(self.sorted, old)
            if i < len(self.sorted) and self.sorted[i] == old:
                self.sorted.pop(i)

    def note_dev(self, t: float, buy_dev: float) -> None:
        self.buy_devs.append((t, buy_dev))
        while self.buy_devs and self.buy_devs[0][0] < t - 7200:
            self.buy_devs.popleft()

    @property
    def ready(self) -> bool:
        return len(self.sorted) >= self.min_samples

    @property
    def baseline(self) -> float:
        n = len(self.sorted)
        if not n:
            return 0.0
        return self.sorted[n // 2] if n % 2 else (self.sorted[n // 2 - 1] + self.sorted[n // 2]) / 2

    def stats(self) -> dict:
        # called from the supervisor thread while the main loop appends: work on a snapshot
        devs = [d for _, d in list(self.buy_devs)]
        if len(devs) < 10:
            return {"samples": len(devs)}
        q = statistics.quantiles(devs, n=100)
        snap = list(self.sorted)
        base = statistics.median(snap) if snap else 0.0
        return {"samples": len(devs), "baseline_premium_pct": round(base * 100, 3),
                "buy_dev_p1_pct": round(q[0] * 100, 3), "buy_dev_p5_pct": round(q[4] * 100, 3),
                "buy_dev_p50_pct": round(q[49] * 100, 3),
                "minutes_below_0.3pct": round(sum(1 for d in devs if d < -0.003) * 2.5 / 60, 1),
                "minutes_below_0.5pct": round(sum(1 for d in devs if d < -0.005) * 2.5 / 60, 1),
                "minutes_below_0.8pct": round(sum(1 for d in devs if d < -0.008) * 2.5 / 60, 1)}


def momentum(history: deque, t: float, seconds: float) -> float | None:
    """Return of the global price over the last `seconds` (history of (t, price))."""
    if not history:
        return None
    target = t - seconds
    old = None
    for ts, px in history:
        if ts >= target:
            old = px
            break
    if old is None:
        return None
    return history[-1][1] / old - 1
