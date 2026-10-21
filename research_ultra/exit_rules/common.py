"""Shared data loading + portfolio simulator for the exit-rule study.

Read-only on MAIN (the live bot folder): only pandas.read_csv on recorded files. Never fetches anything.

Model (same approach as MAIN/scripts/bt_fairvalue.py, made closer to the live engine):
  * Wallex 1-minute candles, only minutes with volume > 0 (stale minutes dropped).
  * fair = Binance 1m close x Wallex USDTTMN 1m close (ffilled over the full minute grid).
  * executable prices: ask = close x (1 + hs), bid = close x (1 - hs), hs = half of the median live spread
    of that market measured in runs/*/ticks.csv (shadow_v2..v6 + live_main pooled).
  * baseline = rolling 180-min median of (close / fair - 1), shift(1)  -> known before the minute.
  * entry when buy_dev <= -0.7% (the TMN fee floor), global 1-min Binance drop not worse than -0.4%.
  * exits in the engine's order: stop (bid <= entry x (1 - stop)), target (sell_dev >= -slack AND
    bid >= entry x (1 + 2 x taker + 0.05%)), time (held >= hold). All at the bid, taker fee both legs.
  * portfolio rules of risk.py: one position per market, max 3 open, 35% / 70% caps, min 60k TMN,
    120-min cooldown on a market after a losing exit, daily loss limit 1.5% (Tehran day), kill switch 3% DD.
  * size = equity x 0.6% x size_factor(0.4) / (stop + taker + maker): a tighter stop means a bigger position.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

MAIN = Path("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot")
OUT = Path(__file__).resolve().parent
SYMS = ["BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN", "PEPETMN", "BNBTMN", "ARBTMN"]
TICK_RUNS = ["shadow_v2", "shadow_v3", "shadow_v4", "shadow_v5", "shadow_v6", "live_main"]
TAKER, MAKER = 0.003, 0.0025
ENTRY = 2 * TAKER + 0.001          # 0.7% floor
BE = 2 * TAKER + 0.0005            # break-even buffer of a "target"
TEHRAN = timezone(timedelta(hours=3, minutes=30))
RISK = dict(risk_per_trade=0.006, size_factor=0.4, max_position_pct=0.35, max_positions=3, max_exposure_pct=0.7,
            daily_loss_limit=0.015, kill_switch_dd=0.03, cooldown_min=120, min_notional=60000, start=5_000_000,
            profit_lock_at=0.03, profit_lock_factor=0.5)


def read_ticks(p: Path) -> pd.DataFrame:
    raw = p.read_bytes()
    raw = raw[: raw.rfind(b"\n") + 1]          # the live file is being appended: drop a partial last line
    return pd.read_csv(io.BytesIO(raw))


def live_spreads() -> dict:
    """Median live spread per market. Frozen to spreads_snapshot.json on first use, because the live
    ticks file keeps growing and every rerun would otherwise use slightly different spreads."""
    snap = OUT / "spreads_snapshot.json"
    if snap.exists():
        import json
        return json.loads(snap.read_text(encoding="utf-8"))["spreads"]
    parts = []
    for r in TICK_RUNS:
        t = read_ticks(MAIN / "runs" / r / "ticks.csv")
        parts.append(t[t.symbol.isin(SYMS)])
    t = pd.concat(parts)
    sp = ((t.ask / t.bid - 1).groupby(t.symbol).median()).to_dict()
    import json
    snap.write_text(json.dumps({"from_runs": TICK_RUNS, "rows": int(len(t)), "last_tick_utc": str(pd.Timestamp(t.t.max(), unit="s", tz="UTC")),
                                "spreads": sp, "spreads_pct": {k: round(v * 100, 4) for k, v in sp.items()}}, indent=1), encoding="utf-8")
    return sp


def _wallex(sym: str) -> pd.DataFrame:
    p = MAIN / "research" / "candles" / f"{sym}_1.csv"
    if not p.exists():
        p = MAIN / "research" / "premium" / f"wallex_{sym}_1m.csv"
    raw = pd.read_csv(p)
    df = pd.DataFrame({k: raw[k].astype(float).to_numpy() for k in ("open", "high", "low", "close", "volume")},
                      index=pd.to_datetime(raw.ts.astype("int64"), unit="s", utc=True))
    return df[~df.index.duplicated(keep="last")].sort_index()


def _binance(base: str) -> pd.Series:
    s = pd.read_csv(MAIN / "research" / "premium" / f"binance_{base}USDT_1m.csv", index_col=0, parse_dates=True)["close"]
    s.index = pd.to_datetime(s.index, utc=True)
    return s[~s.index.duplicated(keep="last")].sort_index()


@dataclass
class Market:
    sym: str
    hs: float
    t: np.ndarray        # int64 seconds
    ask: np.ndarray
    bid: np.ndarray
    bid_low: np.ndarray  # low x (1 - hs): for the pessimistic intra-minute stop check
    bid_open: np.ndarray
    buy_dev: np.ndarray
    sell_dev: np.ndarray
    gdrop: np.ndarray    # Binance 1-min return ending at this minute


def load_markets(spread_mult: float = 1.0, spreads: dict | None = None) -> dict[str, Market]:
    spreads = spreads or live_spreads()
    u = _wallex("USDTTMN")["close"]
    u = u.reindex(pd.date_range(u.index[0], u.index[-1], freq="1min", tz="UTC")).ffill()
    out = {}
    import os
    excl = {x for x in os.environ.get("EXIT_EXCLUDE", "").split(",") if x}
    for s in SYMS:
        if s in excl:
            continue
        w = _wallex(s)
        g = _binance(s[:-3])
        gfull = g.reindex(pd.date_range(g.index[0], g.index[-1], freq="1min", tz="UTC"))
        gret = gfull / gfull.shift(1) - 1
        fair = (g * u).dropna()
        df = w.join(fair.rename("fair"), how="inner").join(gret.rename("gret"), how="left").dropna(subset=["fair"])
        df = df[df.volume > 0]
        hs = spreads.get(s, 0.003) / 2 * spread_mult
        ask, bid = df.close * (1 + hs), df.close * (1 - hs)
        prem = df.close / df.fair - 1
        base = prem.rolling("180min", min_periods=30).median().shift(1)
        out[s] = Market(s, hs, df.index.as_unit("s").asi8.astype("int64"), ask.to_numpy(), bid.to_numpy(),
                        (df.low * (1 - hs)).to_numpy(), (df.open * (1 - hs)).to_numpy(),
                        (ask / df.fair - 1 - base).to_numpy(), (bid / df.fair - 1 - base).to_numpy(),
                        df.gret.fillna(0.0).to_numpy())
    return out


def signals(mk: dict[str, Market], entry: float = ENTRY, t0: int | None = None, t1: int | None = None):
    """All (t, sym, i) minutes where the entry rule fires, in time order."""
    ev = []
    for s, m in mk.items():
        ok = np.isfinite(m.buy_dev) & (m.buy_dev <= -entry) & (m.gdrop >= -0.004)
        idx = np.nonzero(ok)[0]
        for i in idx:
            if i >= len(m.t) - 1:
                continue
            if t0 is not None and m.t[i] < t0:
                continue
            if t1 is not None and m.t[i] >= t1:
                continue
            ev.append((int(m.t[i]), s, int(i)))
    ev.sort()
    return ev


def exit_of(m: Market, i: int, stop: float, hold_min: float, slack: float, stop_mode: str = "close",
            t_end: int | None = None):
    """Walk forward from entry minute i. Returns (j, exit_bid, why)."""
    e = m.ask[i]
    stop_px = e * (1 - stop)
    be_px = e * (1 + BE)
    n = len(m.t)
    j = i + 1
    while j < n:
        if t_end is not None and m.t[j] > t_end:
            return j - 1, m.bid[j - 1], "end"
        if stop_mode == "low" and m.bid_low[j] <= stop_px:
            px = m.bid_open[j] if m.bid_open[j] < stop_px else stop_px * (1 - 0.001)
            return j, px, "stop"
        if m.bid[j] <= stop_px:
            return j, m.bid[j], "stop"
        if m.sell_dev[j] >= -slack and m.bid[j] >= be_px:
            return j, m.bid[j], "target"
        if m.t[j] - m.t[i] >= hold_min * 60:
            return j, m.bid[j], "time"
        j += 1
    return n - 1, m.bid[n - 1], "end"


def simulate(mk: dict[str, Market], sig: list, stop: float, hold: float, slack: float, stop_mode: str = "close",
             portfolio: bool = True, risk: dict = RISK, t_end: int | None = None,
             per_sym: dict | None = None) -> pd.DataFrame:
    """Event-driven over all entry signals in time order. Returns one row per trade."""
    eq = peak = risk["start"]
    day, day_start = None, eq
    halted = ""
    busy: dict[str, int] = {}
    cooldown: dict[str, int] = {}
    open_pos: list[dict] = []          # entered, exit not yet realised
    rows = []

    def realise(upto: int):
        nonlocal eq, peak, day, day_start, halted
        open_pos.sort(key=lambda p: p["t_exit"])
        while open_pos and open_pos[0]["t_exit"] < upto:
            p = open_pos.pop(0)
            eq += p["pnl_tmn"]
            peak = max(peak, eq)
            d = pd.Timestamp(p["t_exit"], unit="s", tz="UTC").tz_convert(TEHRAN).date()
            if d != day:
                day, day_start = d, eq - p["pnl_tmn"]
                if halted.startswith("daily"):
                    halted = ""
            if p["pnl_tmn"] < 0:
                cooldown[p["symbol"]] = p["t_exit"] + risk["cooldown_min"] * 60
            if 1 - eq / peak >= risk["kill_switch_dd"]:
                halted = "kill"
            elif eq / day_start - 1 <= -risk["daily_loss_limit"]:
                halted = halted or "daily"

    for t, s, i in sig:
        if busy.get(s, -1) >= t:
            continue
        st_, ho_, sl_ = (per_sym or {}).get(s, (stop, hold, slack))
        if portfolio:
            realise(t)
            d = pd.Timestamp(t, unit="s", tz="UTC").tz_convert(TEHRAN).date()
            if d != day:
                day, day_start = d, eq
                if halted.startswith("daily"):
                    halted = ""
            if halted or cooldown.get(s, -1) > t or len(open_pos) >= risk["max_positions"]:
                continue
            rf = risk["risk_per_trade"] * (risk["profit_lock_factor"] if eq / risk["start"] - 1 >= risk["profit_lock_at"] else 1)
            notional = eq * rf * risk["size_factor"] / (st_ + TAKER + MAKER)
            notional = min(notional, eq * risk["max_position_pct"],
                           eq * risk["max_exposure_pct"] - sum(p["notional"] for p in open_pos))
            if notional < risk["min_notional"]:
                continue
        else:
            notional = 827_586.0
        m = mk[s]
        j, px, why = exit_of(m, i, st_, ho_, sl_, stop_mode, t_end)
        r = px / m.ask[i] * (1 - TAKER) ** 2 - 1
        rec = {"symbol": s, "t_entry": t, "t_exit": int(m.t[j]), "why": why, "ret_pct": r * 100,
               "minutes": (m.t[j] - m.t[i]) / 60, "entry_dev_pct": m.buy_dev[i] * 100,
               "notional": notional, "pnl_tmn": r * notional}
        rows.append(rec)
        busy[s] = int(m.t[j])
        if portfolio:
            open_pos.append(rec)
    return pd.DataFrame(rows, columns=["symbol", "t_entry", "t_exit", "why", "ret_pct", "minutes", "entry_dev_pct",
                                       "notional", "pnl_tmn"])


def stats(tr: pd.DataFrame) -> dict:
    if not len(tr):
        return {"n": 0, "mean_pct": None, "sum_pct": 0.0, "win_pct": None, "pnl_tmn": 0, "exits": {}}
    return {"n": int(len(tr)), "mean_pct": round(float(tr.ret_pct.mean()), 3), "sum_pct": round(float(tr.ret_pct.sum()), 3),
            "win_pct": round(float((tr.ret_pct > 0).mean() * 100), 1), "pnl_tmn": int(round(tr.pnl_tmn.sum())),
            "exits": {k: int(v) for k, v in tr.why.value_counts().items()}}


def per_market(tr: pd.DataFrame) -> dict:
    return {s: stats(tr[tr.symbol == s]) for s in SYMS}


def suffix() -> str:
    import os
    e = os.environ.get("EXIT_EXCLUDE", "")
    return f"_excl_{e.replace(',', '_')}" if e else ""
