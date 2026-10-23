"""Read-only snapshot of one run folder (runs/<run>) for the control-room UI: KPIs, the equity curve, the risk
brakes, every market's distance to its buy threshold, the AI reviews with their tool calls, the trade agent's
decisions and the restarts. Nothing here writes anywhere; the live bot is never touched.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from wallexbot.config import load_config
from wallexbot.report import _events, _state, _ticks

FA_NAMES = {"BTC": "بیت‌کوین", "ETH": "اتریوم", "SOL": "سولانا", "XRP": "ریپل", "DOGE": "دوج‌کوین", "PEPE": "پپه",
            "BNB": "بی‌ان‌بی", "ARB": "آربیتروم", "POL": "پالیگان", "SAND": "سندباکس"}


_FA = str.maketrans("0123456789.", "۰۱۲۳۴۵۶۷۸۹٫")


def _t(x) -> datetime:
    return datetime.fromisoformat(str(x).replace("Z", "+00:00"))


def _thin(pts: list, n: int = 400) -> list:
    if len(pts) <= n:
        return pts
    step = len(pts) / n
    return [pts[int(i * step)] for i in range(n)] + [pts[-1]]


def kind_of(run_dir: Path, state: dict) -> str:
    """'live' for a real-money run (by name or by the mode the engine wrote), 'shadow' for paper runs."""
    if run_dir.name.startswith("live") or state.get("mode") == "live":
        return "live"
    if run_dir.name.startswith("shadow") or state.get("mode") == "shadow":
        return "shadow"
    return "other"


def snapshot(run_dir: Path, tick_hours: float = 6.0) -> dict:
    cfg = load_config().raw
    ev = _events(run_dir / "decisions.jsonl")
    st = _state(run_dir / "state.json")
    now = datetime.now(timezone.utc)
    end = _t(cfg["window"]["end_utc"])
    risk = cfg["risk"]
    starts = [e for e in ev if e.get("event") == "start"]
    reviews = [e for e in ev if e.get("event") == "policy_review"]
    equity = [(e["t"], e.get("equity_tmn")) for e in ev if e.get("event") == "equity" and e.get("equity_tmn")]
    decisions = [e for e in ev if e.get("event") == "trade_decision"]
    exits = [e for e in ev if e.get("event") == "exit"]     # closed trades; state.json only keeps their count
    errors = [e for e in ev if "error" in str(e.get("event", ""))]
    calls = [c for r in reviews for c in (r.get("analyst_tool_calls") or [])]
    official = sum(1 for c in calls if c.get("server") == "wallex-official")

    start_eq = 5_000_000.0 if kind_of(run_dir, st) == "live" else (equity[0][1] if equity else st.get("equity_tmn") or 0)
    eq_now = float(st.get("equity_tmn") or (equity[-1][1] if equity else start_eq))
    ret = (eq_now / start_eq - 1) * 100 if start_eq else 0.0
    peak = max([v for _, v in equity] + [start_eq]) if equity else start_eq
    dd_now = (1 - eq_now / peak) * 100 if peak else 0.0
    max_dd = 0.0
    run_peak = start_eq
    for _, v in equity:
        run_peak = max(run_peak, v)
        max_dd = max(max_dd, (1 - v / run_peak) * 100)

    # markets: the latest policy, plus the last hours of real order-book ticks (buy_dev = how far the ask is
    # below the market's usual premium; the bot buys when it is at or below -threshold)
    pol = (reviews[-1].get("policies") if reviews else None) or st.get("policies") or {}
    ticks = _ticks(run_dir / "ticks.csv", hours=tick_hours) if (run_dir / "ticks.csv").exists() else {}
    watch = set(starts[-1].get("watch") or []) if starts else set()
    markets = []
    for sym, p in pol.items():
        tk = ticks.get(sym) or []
        devs = [d for (_, _, _, d) in tk if d is not None and not math.isnan(d)]
        last = tk[-1] if tk else None
        spread = ((last[2] / last[1] - 1) * 100) if last and last[1] else None
        coin = sym.replace("TMN", "").replace("USDT", "")
        markets.append({
            "symbol": sym, "name": FA_NAMES.get(coin, coin), "enabled": bool(p.get("enabled")), "watch": sym in watch,
            "threshold_pct": round(float(p.get("entry_dev", 0)) * 100, 2), "stop_pct": round(float(p.get("stop_pct", 0)) * 100, 2),
            "hold_min": p.get("max_hold_min"), "size_factor": p.get("size_factor"), "reason_fa": p.get("reason", ""),
            "dev_now_pct": round(devs[-1] * 100, 3) if devs else None,
            "dev_min_pct": round(min(devs) * 100, 3) if devs else None,
            "spread_pct": round(spread, 3) if spread is not None else None,
            "spark": [round(d * 100, 3) for d in _thin(devs, 120)],
        })
    markets.sort(key=lambda m: (not m["enabled"], (m["dev_min_pct"] or 0) + m["threshold_pct"]))

    def review_out(r):
        cs = r.get("analyst_tool_calls") or []
        tools = {}
        for c in cs:
            k = (c.get("server") or "engine", c.get("tool") or "?")
            tools[k] = tools.get(k, 0) + 1
        return {"t": r["t"], "risk_mode": r.get("risk_mode"), "model": r.get("analyst_model"), "summary_fa": r.get("summary_fa"),
                "calls": len(cs), "official": sum(1 for c in cs if c.get("server") == "wallex-official"),
                "tools": [{"server": s, "tool": t, "n": n} for (s, t), n in sorted(tools.items(), key=lambda x: -x[1])],
                "on": [s for s, p in (r.get("policies") or {}).items() if p.get("enabled")],
                "off": [s for s, p in (r.get("policies") or {}).items() if not p.get("enabled")],
                "contest_adjustments": r.get("contest_adjustments") or {}}

    day_start = None
    tehran_day = (now + timedelta(hours=3, minutes=30)).date()
    for t, v in equity:
        if (_t(t) + timedelta(hours=3, minutes=30)).date() == tehran_day:
            day_start = v
            break
    day_ret = (eq_now / day_start - 1) * 100 if day_start else 0.0
    no_new_at = end - timedelta(minutes=risk["no_new_entries_min_before_end"])
    flatten_at = end - timedelta(minutes=risk["flatten_min_before_end"])
    brakes = [
        {"key": "kill", "name": "کلید قطع", "rule": f"افت {risk['kill_switch_dd'] * 100:.0f}٪ از اوج: خرید تازه کامل قطع".translate(_FA),
         "value": round(dd_now, 2), "limit": risk["kill_switch_dd"] * 100, "tripped": "kill" in (st.get("halted") or "")},
        {"key": "daily", "name": "سقف ضرر روزانه", "rule": f"{risk['daily_loss_limit'] * 100:.1f}٪ ضرر در یک روز: تا فردا خرید نمی‌کند".translate(_FA),
         "value": round(-day_ret, 2), "limit": risk["daily_loss_limit"] * 100, "tripped": "daily" in (st.get("halted") or "")},
        {"key": "contest", "name": "سقف ضرر مسابقه", "rule": f"{risk.get('contest_loss_budget', 0) * 100:.1f}٪ زیر سرمایهٔ اول: خرید تازه قطع".translate(_FA),
         "value": round(-ret, 2), "limit": risk.get("contest_loss_budget", 0) * 100, "tripped": ret <= -risk.get("contest_loss_budget", 0) * 100},
        {"key": "gain", "name": "محافظ سود", "rule": "وقتی جلوییم، معاملهٔ بعدی نمی‌تواند حساب را منفی کند",
         "value": round(ret, 2), "limit": 0, "tripped": False, "active": ret > 0},
    ]
    return {
        "run": run_dir.name, "kind": kind_of(run_dir, st), "now": now.isoformat(), "updated": st.get("t") or (ev[-1]["t"] if ev else None),
        "contest": {"end": end.isoformat(), "no_new_entries": no_new_at.isoformat(), "flatten": flatten_at.isoformat()},
        "kpis": {"equity": round(eq_now), "start_equity": round(start_eq), "return_pct": round(ret, 3), "max_drawdown_pct": round(max_dd, 3),
                 "trades": st["trades"] if isinstance(st.get("trades"), int) else len(exits), "open_positions": len(st.get("positions") or {}), "reviews": len(reviews),
                 "tool_calls": len(calls), "tool_calls_official": official, "errors": len(errors), "decisions": len(decisions),
                 "halted": st.get("halted") or ""},
        "equity": [[t, v] for t, v in _thin(equity)],
        "brakes": brakes,
        "markets": markets,
        "reviews": [review_out(r) for r in reviews[-8:]][::-1],
        "decisions": [{k: d.get(k) for k in ("t", "symbol", "decision", "action", "buy_dev_pct_asked", "latency_s", "reason_fa", "size_factor")}
                      for d in decisions[-20:]][::-1],
        "trades": [{k: x.get(k) for k in ("t", "symbol", "reason", "ret_pct", "pnl_tmn", "held_s")} for x in exits[-20:]][::-1],
        "positions": st.get("positions") or {},
        "starts": [{"t": s["t"], "mode": s.get("mode"), "trade_agent": s.get("trade_agent")} for s in starts],
    }
