"""Hard risk limits. These are code, not prompts: the AI can shrink or veto a trade but
can never make one bigger, riskier or more frequent than these rules allow."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

TEHRAN = timezone(timedelta(hours=3, minutes=30))


@dataclass
class RiskState:
    start_equity: float
    equity: float
    peak_equity: float
    day: str = ""
    day_start_equity: float = 0.0
    halted: str = ""                                   # non-empty = no new entries, with the reason
    cooldown_until: dict = field(default_factory=dict) # symbol -> ISO time
    open_symbols: set = field(default_factory=set)
    exposure: float = 0.0                              # toman currently in positions
    closed_since_mark: bool = False                    # a trade closed after the last equity mark

    @property
    def drawdown(self) -> float:
        return 1 - self.equity / self.peak_equity if self.peak_equity else 0.0

    @property
    def total_return(self) -> float:
        return self.equity / self.start_equity - 1


class RiskManager:
    def __init__(self, p: dict, window_end: datetime, fees: dict):
        self.p = p
        self.window_end = window_end
        self.fees = fees

    def new_state(self, equity: float) -> RiskState:
        return RiskState(start_equity=equity, equity=equity, peak_equity=equity)

    def mark(self, st: RiskState, equity: float, now: datetime) -> None:
        st.equity = equity
        st.closed_since_mark = False
        st.peak_equity = max(st.peak_equity, equity)
        day = now.astimezone(TEHRAN).date().isoformat()
        if day != st.day:
            st.day, st.day_start_equity = day, equity
            if st.halted.startswith("daily"):
                st.halted = ""
        if st.drawdown >= self.p["kill_switch_dd"]:
            st.halted = f"kill switch: drawdown {st.drawdown:.2%} >= {self.p['kill_switch_dd']:.0%}"
        elif st.day_start_equity and equity / st.day_start_equity - 1 <= -self.p["daily_loss_limit"]:
            st.halted = st.halted or f"daily loss limit hit ({equity / st.day_start_equity - 1:.2%})"

    def phase(self, now: datetime) -> str:
        left = (self.window_end - now).total_seconds() / 60
        if left <= self.p["flatten_min_before_end"]:
            return "flatten"
        if left <= self.p["no_new_entries_min_before_end"]:
            return "no_new_entries"
        return "normal"

    def risk_fraction(self, st: RiskState) -> float:
        """Per-trade risk as a fraction of equity, reduced once we are in profit to protect the rank."""
        r = self.p["risk_per_trade"]
        if st.total_return >= self.p["profit_lock_at"]:
            r *= self.p["profit_lock_factor"]
        return r

    def size(self, st: RiskState, cand, ai_size_factor: float, now: datetime, edge_checks: bool = True) -> tuple[float, list[str]]:
        """Return (toman notional to buy, reasons). Notional 0 means blocked.
        edge_checks=False skips the target-vs-fee and reward/risk tests, for strategies whose
        entry threshold already guarantees the fee is covered (fair-value reversion)."""
        why: list[str] = []
        if st.halted:
            return 0.0, [st.halted]
        ph = self.phase(now)
        if ph != "normal":
            return 0.0, [f"window phase {ph}: no new entries"]
        cd = st.cooldown_until.get(cand.symbol)
        if cd and datetime.fromisoformat(cd) > now:
            return 0.0, [f"{cand.symbol} cooling down until {cd}"]
        if cand.symbol in st.open_symbols:
            return 0.0, [f"already holding {cand.symbol}"]
        if len(st.open_symbols) >= self.p["max_positions"]:
            return 0.0, [f"max {self.p['max_positions']} positions open"]
        round_trip = self.fees["maker"] * 2
        if edge_checks and cand.tp_pct < round_trip + self.p["min_edge_after_fees"]:
            return 0.0, [f"target {cand.tp_pct:.2%} does not clear fees {round_trip:.2%} + edge"]
        if edge_checks and cand.tp_pct / max(cand.stop_pct, 1e-9) < self.p["min_reward_risk"]:
            return 0.0, [f"reward/risk {cand.tp_pct / cand.stop_pct:.2f} < {self.p['min_reward_risk']}"]

        factor = min(max(ai_size_factor, 0.0), 1.0)
        risk_toman = st.equity * self.risk_fraction(st) * factor
        notional = risk_toman / (cand.stop_pct + self.fees["taker"] + self.fees["maker"])
        cap_pos = st.equity * self.p["max_position_pct"]
        cap_exp = st.equity * self.p["max_exposure_pct"] - st.exposure
        if notional > cap_pos:
            notional, _ = cap_pos, why.append(f"capped at {self.p['max_position_pct']:.0%} of equity per position")
        if notional > cap_exp:
            notional, _ = max(cap_exp, 0.0), why.append("capped by total exposure limit")
        # Gain guard: once the account is ahead of its start capital, a new trade may only risk a share of the
        # gain, so the worst case of everything open cannot turn the contest result negative. The gain is the
        # marked equity, so it is contest-wide and survives a restart; after a close the bot waits for the next
        # mark (the engine marks within seconds) instead of sizing on a stale value. Open positions count at
        # their marked value and their worst case is worst x entry exposure, which holds while none is up by
        # more than that (target exits take profit near +0.65%).
        share = self.p.get("protect_gain_share", 0.0)
        if share:
            if st.closed_since_mark:
                return 0.0, why + ["a trade just closed: waiting for a fresh account value"]
            gain = st.equity - st.start_equity
            if gain >= self.p["protect_gain_min_toman"]:
                worst = self.p["protect_gain_worst_loss"]
                room = (gain - worst * st.exposure) * share / worst
                if room < self.p["min_notional"] * 1.05:      # LiveExecutor.buy skips orders under 1.05 x minimum
                    return 0.0, why + [f"protecting a {gain:,.0f} toman gain: no new risk"]
                if notional > room:
                    notional, _ = room, why.append(f"capped so a {worst:.0%} loss uses at most {share:.0%} of the {gain:,.0f} toman gain")
        if notional < self.p["min_notional"]:
            return 0.0, why + [f"size {notional:,.0f} below exchange minimum {self.p['min_notional']:,}"]
        why.append(f"risking {risk_toman:,.0f} toman ({self.risk_fraction(st) * factor:.2%} of equity) over a {cand.stop_pct:.2%} stop")
        return notional, why

    def on_exit(self, st: RiskState, symbol: str, pnl: float, now: datetime) -> None:
        st.open_symbols.discard(symbol)
        st.closed_since_mark = True
        if pnl < 0:
            st.cooldown_until[symbol] = (now + timedelta(minutes=self.p["cooldown_min_after_loss"])).isoformat()
