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
from .config import RUNS, load_config
from .fairvalue import base_of, quote_of

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
    runs = [d for d in RUNS.iterdir() if (d / "ticks.csv").exists()]
    return max(runs, key=lambda d: (d / "ticks.csv").stat().st_mtime)


def _tail_ticks(run_dir: Path, minutes: float) -> list[dict]:
    """Rows from the last `minutes` across the recent runs' ticks.csv files (a restart starts a new
    run, so one file alone may be too short). Each file is read from its end only."""
    cutoff = time.time() - minutes * 60
    files = sorted((p for p in RUNS.glob("*/ticks.csv") if p.stat().st_mtime >= cutoff),
                   key=lambda p: p.stat().st_mtime)
    rows = []
    for p in files:
        rows += _tail_file(p, minutes, cutoff)
    rows.sort(key=lambda r: float(r["t"]))
    return rows


def _tail_file(p: Path, minutes: float, cutoff: float) -> list[dict]:
    size = p.stat().st_size
    want = int(minutes * 60 / 2.5 * len(_cfg["fairvalue"]["symbols"]) * 120) + 4096
    with open(p, "rb") as fh:
        header = fh.readline().decode()
        fh.seek(max(0, size - want))
        chunk = fh.read().decode(errors="ignore")
    if size > want:
        chunk = chunk.split("\n", 1)[1] if "\n" in chunk else ""
    rows = []
    for r in csv.DictReader(io.StringIO(header + chunk)):
        try:
            if float(r["t"]) >= cutoff:
                rows.append(r)
        except (ValueError, TypeError, KeyError):
            continue
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
                  "round_trip_fee_pct": _pct(_FEES[quote_of(s)]["maker"] + _FEES[quote_of(s)]["taker"])}
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
    """7-day historical backtest of the same rule (1-minute data, modelled spreads), per market."""
    ref = RUNS / "backtests" / _cfg["fairvalue"].get("backtest_reference", "fv_sweep_0.006_0.0035") / "summary.json"
    m = json.loads(ref.read_text(encoding="utf-8"))["markets"]
    keep = ("trades", "mean_ret_pct", "win_pct", "half_spread_pct")
    if symbol:
        return {symbol: {k: m.get(symbol, {}).get(k) for k in keep}}
    return {s: {k: d.get(k) for k in keep} for s, d in m.items()}


def simulate_threshold(symbol: str, entry_dev: float, exit_slack: float = 0.0005,
                       stop_pct: float = 0.008, max_hold_min: float = 25, minutes: float = 240) -> dict:
    """Replay the recorded live order-book ticks of the last `minutes` for `symbol` with a candidate
    policy and report what it would have earned after real fees. Use it to test a threshold before setting it.
    entry_dev, exit_slack and stop_pct are FRACTIONS exactly as in the policy (0.005 = 0.5%)."""
    if entry_dev >= 0.05 or stop_pct >= 0.1:
        return {"error": "entry_dev / stop_pct look like percentages; pass fractions (0.005 = 0.5%)"}
    rows = [r for r in _tail_ticks(_run_dir(), minutes) if r["symbol"] == symbol]
    if len(rows) < 50:
        return {"symbol": symbol, "samples": len(rows), "note": "not enough data yet"}
    fee = _FEES[quote_of(symbol)]["taker"]
    e, sl, st, hold = entry_dev, exit_slack, stop_pct, max_hold_min * 60
    trades, i = [], 0
    while i < len(rows):
        r = rows[i]
        if float(r["buy_dev"]) <= -e:
            px, t0 = float(r["ask"]), float(r["t"])
            j, why = i + 1, "end"
            while j < len(rows):
                b, sd, t = float(rows[j]["bid"]), float(rows[j]["sell_dev"]), float(rows[j]["t"])
                if b <= px * (1 - st):
                    why = "stop"; break
                if sd >= -sl and b > px:
                    why = "target"; break
                if t - t0 >= hold:
                    why = "time"; break
                j += 1
            j = min(j, len(rows) - 1)
            ret = float(rows[j]["bid"]) * (1 - fee) / (px * (1 + fee)) - 1
            trades.append({"ret_pct": round(ret * 100, 3), "why": why, "minutes": round((float(rows[j]["t"]) - t0) / 60, 1)})
            i = j + 1
        else:
            i += 1
    rets = [t["ret_pct"] for t in trades]
    return {"symbol": symbol, "minutes": minutes, "policy": {"entry_dev": entry_dev, "exit_slack": exit_slack,
            "stop_pct": stop_pct, "max_hold_min": max_hold_min}, "trades": len(trades),
            "mean_ret_pct": round(statistics.mean(rets), 3) if rets else None,
            "win_pct": round(sum(1 for x in rets if x > 0) / len(rets) * 100, 1) if rets else None,
            "sum_ret_pct": round(sum(rets), 3), "examples": trades[-5:]}


def get_risk_state() -> dict:
    """Account-level risk state in percentages: return, drawdown, halt reason, open positions."""
    st = json.loads((_run_dir() / "state.json").read_text(encoding="utf-8"))
    return {"mode": st.get("mode"), "return_pct": st.get("return_pct"), "drawdown_pct": st.get("drawdown_pct"),
            "halted": st.get("halted"), "open_positions": list(st.get("positions", {}).keys()),
            "closed_trades": st.get("trades"), "kill_switch_dd_pct": _cfg["risk"]["kill_switch_dd"] * 100,
            "daily_loss_limit_pct": _cfg["risk"]["daily_loss_limit"] * 100}


TOOLS = [list_markets, get_premium_stats, get_orderbook, get_fair_value, get_global_momentum,
         get_recent_trades, get_backtest, simulate_threshold, get_risk_state]
