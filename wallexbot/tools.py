"""Wallex tools: what the AI supervisor (and any MCP client) can look at.

All tools are READ-ONLY. None can place, change or cancel an order and none returns keys,
balances or toman amounts: the agent investigates, the engine and the risk manager act.
The same functions are served over MCP by mcp_server.py.
"""
from __future__ import annotations

import csv
import io
import json
import os
import statistics
import time
from pathlib import Path

import httpx

from .client import WallexClient
from .config import RUNS, load_backtest, load_config, market_tick_files
from .fairvalue import base_of, quote_of
from .supervisor import min_entry

_cfg = load_config().raw
_FEES = {"TMN": _cfg["fees"], "USDT": _cfg["fees_usdt"]}
_wallex = WallexClient(min_interval=0.1)
_http = httpx.Client(timeout=8)


def _run_dir(run: str | None = None) -> Path:
    """The engine run these tools describe: the engine exports WALLEXBOT_RUN to its MCP server, so
    the agent always reads the LIVE run, never a test or an old shadow run."""
    run = run or os.environ.get("WALLEXBOT_RUN")
    if run and (RUNS / run).exists():
        return RUNS / run
    return max(market_tick_files(RUNS), key=lambda p: p.stat().st_mtime).parent


def _tail_ticks(run_dir: Path, minutes: float) -> list[dict]:
    """Rows from the last `minutes` across the recent live/shadow runs' ticks.csv files (a restart starts a
    new run, so one file alone may be too short; test runs are never read). Each file is read from its end only."""
    cutoff = time.time() - minutes * 60
    files = sorted((p for p in market_tick_files(RUNS) if p.stat().st_mtime >= cutoff),
                   key=lambda p: p.stat().st_mtime)
    rows = []
    for p in files:
        rows += _tail_file(p, minutes, cutoff)
    rows.sort(key=lambda r: float(r["t"]))
    return rows


_TICK_FIELDS = ("t", "symbol", "bid", "ask", "bid_qty", "ask_qty", "fair", "baseline", "buy_dev", "sell_dev")
_TICK_NUMS = ("bid", "ask", "fair", "buy_dev", "sell_dev")          # the numbers the tools parse from a tick


def _tail_file(p: Path, minutes: float, cutoff: float) -> list[dict]:
    size = p.stat().st_size
    want = int(minutes * 60 / 2.5 * len(_cfg["fairvalue"]["symbols"]) * 120) + 4096
    with open(p, "rb") as fh:
        header = fh.readline().decode()
        fh.seek(max(0, size - want))
        chunk = fh.read().decode(errors="ignore")
    if size > want:
        chunk = chunk.split("\n", 1)[1] if "\n" in chunk else ""
    rows, now = [], time.time()
    for r in csv.DictReader(io.StringIO(header + chunk)):
        # A real tick has all ten fields, and the numbers the tools read parse as floats. A torn line (the engine
        # may be mid-write) reads its missing fields as None; a torn line that a restart appended to reads its
        # extra fields under the key None, or shifts them so a number field holds a symbol. Neither is a real tick,
        # and keeping one would make get_premium_stats and simulate_threshold fail for that market.
        if None in r or any(r.get(k) is None for k in _TICK_FIELDS):
            continue
        try:
            t = float(r["t"])
            for k in _TICK_NUMS:
                float(r[k])
        except (ValueError, TypeError, KeyError):
            continue
        if cutoff <= t <= now + 60:          # a future t is a torn line merged with the next row
            rows.append(r)
    return rows


def _pct(x, nd=3):
    return None if x is None else round(x * 100, nd)


# ---------------------------------------------------------------------------- tools
def list_markets() -> dict:
    """Markets the bot watches, with their current policy, normal premium (baseline), spread and depth."""
    st = json.loads((_run_dir() / "state.json").read_text(encoding="utf-8"))
    out = {}
    for s in _cfg["fairvalue"]["symbols"]:
        pol = st.get("policies", {}).get(s, {})
        out[s] = {"quote": quote_of(s), "bot_policy_enabled": pol.get("enabled"), "entry_dev_pct": _pct(pol.get("entry_dev")),
                  "size_factor": pol.get("size_factor"), "baseline_premium_pct": st.get("baselines_pct", {}).get(s),
                  "round_trip_fee_pct": _pct(2 * _FEES[quote_of(s)]["taker"])}
    return {"updated": st.get("t"), "markets": out}


def get_premium_stats(symbol: str, minutes: float = 120) -> dict:
    """How often and how deep Wallex was cheaper than global fair value for `symbol` in the last `minutes`
    (buy_dev = ask vs fair, after the market's normal premium; negative = cheap)."""
    rows = [r for r in _tail_ticks(_run_dir(), minutes) if r["symbol"] == symbol]
    if len(rows) < 20:
        return {"symbol": symbol, "samples": len(rows), "note": "not enough data yet"}
    bd = [float(r["buy_dev"]) for r in rows]
    spreads = [float(r["ask"]) / float(r["bid"]) - 1 for r in rows if float(r["bid"]) > 0]
    q = statistics.quantiles(bd, n=100)
    sec = 2.5
    return {"symbol": symbol, "minutes": minutes, "samples": len(rows),
            "buy_dev_now_pct": _pct(bd[-1]), "buy_dev_p1_pct": _pct(q[0]), "buy_dev_p5_pct": _pct(q[4]),
            "buy_dev_median_pct": _pct(q[49]),
            "minutes_cheaper_than": {f"{t}%": round(sum(1 for d in bd if d <= -t / 100) * sec / 60, 1) for t in (0.3, 0.5, 0.8, 1.2)},
            "spread_median_pct": _pct(statistics.median(spreads)), "spread_max_pct": _pct(max(spreads))}


def get_orderbook(symbol: str, levels: int = 5) -> dict:
    """Live top of the Wallex order book: best `levels` bids and asks with size in quote currency."""
    bids, asks = _wallex.depth(symbol)
    f = lambda side: [{"price": p, "qty": q, "value_quote": round(p * q, 2)} for p, q in side[:levels]]
    return {"symbol": symbol, "bids": f(bids), "asks": f(asks),
            "spread_pct": _pct(asks[0][0] / bids[0][0] - 1) if bids and asks else None}


def _global_mid(base: str) -> float:
    r = _http.get("https://api.binance.com/api/v3/ticker/bookTicker", params={"symbol": f"{base}USDT"}).json()
    return (float(r["bidPrice"]) + float(r["askPrice"])) / 2


def get_fair_value(symbol: str) -> dict:
    """Global fair value of a Wallex market right now and how far Wallex's bid/ask are from it."""
    bids, asks = _wallex.depth(symbol)
    g = _global_mid(base_of(symbol))
    u = None
    if quote_of(symbol) == "TMN":
        ub, ua = _wallex.depth("USDTTMN")
        u = (ub[0][0] + ua[0][0]) / 2
    fair = g * u if u else g
    return {"symbol": symbol, "global_usdt": g, "usdttmn_mid": u, "fair": round(fair, 6),
            "wallex_bid": bids[0][0], "wallex_ask": asks[0][0],
            "ask_vs_fair_pct": _pct(asks[0][0] / fair - 1), "bid_vs_fair_pct": _pct(bids[0][0] / fair - 1)}


def get_global_momentum() -> dict:
    """BTC and ETH global price change over the last 5, 15 and 60 minutes, and the 1-hour range."""
    out = {}
    for b in ("BTC", "ETH"):
        k = _http.get("https://api.binance.com/api/v3/klines", params={"symbol": f"{b}USDT", "interval": "1m", "limit": 61}).json()
        c = [float(x[4]) for x in k]
        hi, lo = max(float(x[2]) for x in k), min(float(x[3]) for x in k)
        out[b] = {"chg_5m_pct": _pct(c[-1] / c[-6] - 1), "chg_15m_pct": _pct(c[-1] / c[-16] - 1),
                  "chg_60m_pct": _pct(c[-1] / c[0] - 1), "range_60m_pct": _pct(hi / lo - 1)}
    return out


def get_recent_trades(n: int = 15) -> list[dict]:
    """The bot's last `n` closed trades (percentages only)."""
    keep = ("t", "symbol", "reason", "ret_pct", "held_s", "entry_dev_pct", "exit_dev_pct", "executor")
    p = _run_dir() / "decisions.jsonl"
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if '"event": "exit"' in l]
    return [{k: r[k] for k in keep if k in r} for r in rows[-n:]]


def get_backtest(symbol: str | None = None) -> dict:
    """Historical backtest of the fair-value rule, per market: trades, mean/total return after fees, win rate,
    exit reasons, and that market's robustness checks: next_minute_mean_ret_pct (the mean when entering one traded
    minute later) and stale_usdt_trades / stale_usdt_mean_ret_pct (trades priced on an old USDT/TMN rate).
    Read mean_ret_pct together with next_minute_mean_ret_pct. `note` says what was tested and how far to trust it.
    A market missing from it was not tested."""
    name = _cfg["fairvalue"].get("backtest_reference", "fv_sweep_0.006_0.0035")
    d = load_backtest(name, RUNS)
    if d is None:
        return {"note": f"backtest '{name}' is not available", "markets": {}}
    m, note = d["markets"], d["note"]
    keep = ("trades", "mean_ret_pct", "next_minute_mean_ret_pct", "next_minute_trades", "win_pct", "sum_ret_pct",
            "stale_usdt_trades", "stale_usdt_mean_ret_pct", "half_spread_pct", "exits")
    pick = lambda x: {k: x[k] for k in keep if k in x}
    if symbol:
        if symbol not in m:
            return {"note": note, "markets": {}, "not_tested": f"{symbol} is not in this backtest"}
        return {"note": note, "markets": {symbol: pick(m[symbol])}}
    return {"note": note, "markets": {s: pick(x) for s, x in m.items()}}


def simulate_threshold(symbol: str, entry_dev: float, exit_slack: float = 0.0005,
                       stop_pct: float = 0.008, max_hold_min: float = 25, minutes: float = 240) -> dict:
    """Replay the recorded live order-book ticks of the last `minutes` for `symbol` with a candidate policy,
    using the engine's own rule, and report what it would have earned after fees.
    Entry at the ask when buy_dev <= -entry_dev and the spread is within the engine's max_spread; exit at the bid on
    stop, on target (sell_dev >= -exit_slack AND bid clears both taker fees + 0.05%) or at max_hold_min.
    Net return = (1 - taker)^2 x exit_bid / entry_ask - 1. An entry_dev below the engine's fee floor
    (2 x taker + 0.1%) is replayed at the floor, as the engine would trade it. Not modelled: the 60 s global-drop
    filter, risk cooldowns and partial fills. entry_dev, exit_slack and stop_pct are FRACTIONS as in the policy
    (0.007 = 0.7%)."""
    if entry_dev >= 0.05 or stop_pct >= 0.1:
        return {"error": "entry_dev / stop_pct look like percentages; pass fractions (0.005 = 0.5%)"}
    rows = [r for r in _tail_ticks(_run_dir(), minutes) if r["symbol"] == symbol]
    if len(rows) < 50:
        return {"symbol": symbol, "samples": len(rows), "note": "not enough data yet"}
    fee = _FEES[quote_of(symbol)]["taker"]
    floor = min_entry(symbol, _FEES)
    e, sl, st, hold = max(entry_dev, floor), exit_slack, stop_pct, max_hold_min * 60
    max_spread = _cfg["fairvalue"]["max_spread"]
    trades, wide, i = [], 0, 0
    while i < len(rows):
        r = rows[i]
        ask, bid = float(r["ask"]), float(r["bid"])
        if float(r["buy_dev"]) <= -e:
            if bid <= 0 or ask / bid - 1 > max_spread:
                wide += 1; i += 1
                continue
            px, t0 = ask, float(r["t"])
            target_px = px * (1 + 2 * fee + 0.0005)          # engine._break_even
            j, why = i + 1, "end"
            while j < len(rows):
                b, sd, t = float(rows[j]["bid"]), float(rows[j]["sell_dev"]), float(rows[j]["t"])
                if b <= px * (1 - st):
                    why = "stop"; break
                if sd >= -sl and b >= target_px:
                    why = "target"; break
                if t - t0 >= hold:
                    why = "time"; break
                j += 1
            j = min(j, len(rows) - 1)
            # buy fee is paid in coin, sell fee in quote (as the live executor fills)
            ret = (1 - fee) ** 2 * float(rows[j]["bid"]) / px - 1
            trades.append({"ret_pct": round(ret * 100, 3), "why": why, "minutes": round((float(rows[j]["t"]) - t0) / 60, 1)})
            i = j + 1
        else:
            i += 1
    rets = [t["ret_pct"] for t in trades]
    out = {"symbol": symbol, "minutes": minutes, "policy": {"entry_dev": e, "exit_slack": exit_slack,
           "stop_pct": stop_pct, "max_hold_min": max_hold_min}, "trades": len(trades),
           "mean_ret_pct": round(statistics.mean(rets), 3) if rets else None,
           "win_pct": round(sum(1 for x in rets if x > 0) / len(rets) * 100, 1) if rets else None,
           "sum_ret_pct": round(sum(rets), 3), "signal_ticks_skipped_wide_spread": wide, "examples": trades[-5:]}
    if entry_dev < floor:
        out["clamped_to_floor"] = (f"entry_dev {entry_dev} is below the engine's fee floor {round(floor, 6)}; "
                                   "the engine would clamp it to the floor, so this replay used the floor")
    return out


def get_risk_state() -> dict:
    """Account-level risk state in percentages: return, drawdown, halt reason, open positions."""
    st = json.loads((_run_dir() / "state.json").read_text(encoding="utf-8"))
    return {"mode": st.get("mode"), "return_pct": st.get("return_pct"), "drawdown_pct": st.get("drawdown_pct"),
            "halted": st.get("halted"), "open_positions": list(st.get("positions", {}).keys()),
            "closed_trades": st.get("trades"), "kill_switch_dd_pct": _cfg["risk"]["kill_switch_dd"] * 100,
            "daily_loss_limit_pct": _cfg["risk"]["daily_loss_limit"] * 100}


TOOLS = [list_markets, get_premium_stats, get_orderbook, get_fair_value, get_global_momentum,
         get_recent_trades, get_backtest, simulate_threshold, get_risk_state]
