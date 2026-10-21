"""Event-driven backtest on Wallex history, using the SAME strategy, AI committee and risk
manager code as the live engine.

Fill model (deliberately conservative):
- entry: limit buy at the signal bar's close, valid for `entry_valid_bars` next bars, filled only
  if a later bar trades BELOW it (strictly), at the limit price, maker fee;
- stop: if a bar's low touches the stop, exit at min(stop, bar open) minus slippage, taker fee;
  when stop and target fall inside the same bar the stop is assumed first;
- target: resting limit sell, maker fee;
- time stop and end-of-test: exit at the bar close minus slippage, taker fee.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import strategy
from .ai import AICommittee, snapshot
from .data import load_csv
from .indicators import add_features, resample
from .risk import RiskManager


@dataclass
class Pos:
    symbol: str
    qty: float
    entry: float
    stop: float
    tp: float
    opened_i: int
    opened_ts: str
    setup: str
    key: str
    max_hold: int
    be_done: bool = False


def build_frames(symbols, start=None, end=None) -> dict[str, pd.DataFrame]:
    frames = {}
    for s in symbols:
        d15 = load_csv(s, "15", start, end)
        frames[s] = add_features(d15, resample(d15, 60))
    return frames


def context_frame(frames: dict) -> pd.DataFrame:
    """BTC and USDT/TMN context known at each 15m close."""
    out = {}
    if "BTCTMN" in frames:
        b = frames["BTCTMN"]
        out["btc_ret_4h"] = b["ret_4h"]
        out["btc_ret_24h"] = b["close"].pct_change(96)
        out["btc_h_trend"] = np.sign(b["h_ema20"] - b["h_ema50"])
    if "USDTTMN" in frames:
        u = frames["USDTTMN"]
        out["usdt_ret_4h"] = u["ret_4h"]
        out["usdt_ret_24h"] = u["close"].pct_change(96)
    return pd.DataFrame(out)


def ctx_at(ctx: pd.DataFrame, ts) -> dict:
    if ts not in ctx.index:
        return {}
    r = ctx.loc[ts]
    return {k: (round(float(v), 5) if pd.notna(v) else None) for k, v in r.items()}


def collect_candidates(frames, p) -> list[tuple[str, int, strategy.Candidate]]:
    out = []
    for s, f in frames.items():
        if s not in p["trade_symbols"]:
            continue
        for i in range(len(f)):
            for c in strategy.propose(s, f, i, p):
                out.append((s, i, c))
    return out


def judge(cands, frames, ctx, ai: AICommittee | None, workers: int = 8) -> dict[str, dict]:
    """Verdict per candidate key. Without AI every candidate is approved at full size (baseline)."""
    def key(s, c):
        return f"{s}|{c.ts}|{c.setup}"
    if ai is None:
        return {key(s, c): {"approved": True, "size_factor": 1.0, "stop_pct": c.stop_pct, "tp_pct": c.tp_pct,
                            "reason_fa": "rules only"} for s, i, c in cands}

    def one(item):
        s, i, c = item
        return key(s, c), ai.decide(c, snapshot(frames[s], i, ctx_at(ctx, frames[s].index[i])))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return dict(ex.map(one, cands))


def run(cfg, frames, cands, verdicts, start_equity: float, label: str, out_dir: Path | None = None) -> dict:
    p, fees = cfg["strategy"], cfg["fees"]
    rp = dict(cfg["risk"])
    rm = RiskManager(rp, datetime(2100, 1, 1, tzinfo=timezone.utc), fees)
    st = rm.new_state(start_equity)
    slip = cfg["slippage"]
    arrays = {s: (f.index, f["open"].to_numpy(), f["high"].to_numpy(), f["low"].to_numpy(), f["close"].to_numpy())
              for s, f in frames.items() if s in p["trade_symbols"]}
    pos_map = {s: {ts: i for i, ts in enumerate(a[0])} for s, a in arrays.items()}
    by_ts: dict = {}
    for s, i, c in cands:
        by_ts.setdefault(arrays[s][0][i], []).append((s, i, c))
    timeline = sorted(set().union(*[set(a[0]) for a in arrays.values()]))

    cash = start_equity
    positions: dict[str, Pos] = {}
    pending: dict[str, dict] = {}
    trades, curve, log = [], [], []
    fees_paid = 0.0

    def close_pos(s, ps: Pos, px: float, fee_rate: float, reason: str, i: int, ts):
        nonlocal cash, fees_paid
        gross = ps.qty * px
        fee = gross * fee_rate
        cash += gross - fee
        fees_paid += fee
        cost = ps.qty * ps.entry * (1 + fees["maker"])
        pnl = gross - fee - cost
        trades.append({"symbol": s, "setup": ps.setup, "opened": ps.opened_ts, "closed": ts.isoformat(),
                       "entry": ps.entry, "exit": px, "qty": ps.qty, "pnl": pnl, "ret": pnl / cost,
                       "bars": i - ps.opened_i, "reason": reason, "key": ps.key})
        rm.on_exit(st, s, pnl, ts.to_pydatetime())
        del positions[s]

    for ts in timeline:
        now = ts.to_pydatetime()
        for s, (idx, o, h, l, c) in arrays.items():
            i = pos_map[s].get(ts)
            if i is None:
                continue
            ps = positions.get(s)
            if ps and i > ps.opened_i:
                if l[i] <= ps.stop:
                    close_pos(s, ps, min(ps.stop, o[i]) * (1 - slip), fees["taker"], "stop", i, ts)
                elif h[i] >= ps.tp:
                    close_pos(s, ps, ps.tp, fees["maker"], "target", i, ts)
                else:
                    if not ps.be_done and h[i] >= ps.entry * (1 + rp["breakeven_trigger_pct"]):
                        ps.stop = max(ps.stop, ps.entry * (1 + rp["breakeven_lock_pct"]))
                        ps.be_done = True
                    if i - ps.opened_i >= ps.max_hold:
                        close_pos(s, ps, c[i] * (1 - slip), fees["taker"], "time", i, ts)
            pd_ = pending.get(s)
            if pd_ and s not in positions:
                if i > pd_["i"] and l[i] < pd_["price"]:
                    qty = pd_["notional"] / pd_["price"]
                    cost = qty * pd_["price"] * (1 + fees["maker"])
                    if cost <= cash:
                        cash -= cost
                        fees_paid += qty * pd_["price"] * fees["maker"]
                        positions[s] = Pos(s, qty, pd_["price"], pd_["price"] * (1 - pd_["stop_pct"]),
                                           pd_["price"] * (1 + pd_["tp_pct"]), i, ts.isoformat(), pd_["setup"],
                                           pd_["key"], pd_["max_hold"])
                        st.open_symbols.add(s)
                    del pending[s]
                elif i - pd_["i"] >= p["entry_valid_bars"]:
                    st.open_symbols.discard(s)
                    del pending[s]

        mtm = sum(ps.qty * arrays[s][4][pos_map[s][ts]] for s, ps in positions.items() if ts in pos_map[s])
        equity = cash + mtm
        st.exposure = mtm
        rm.mark(st, equity, now)
        curve.append((ts.isoformat(), equity))

        for s, i, cnd in by_ts.get(ts, []):
            k = f"{s}|{cnd.ts}|{cnd.setup}"
            v = verdicts.get(k, {"approved": False, "reason_fa": "no verdict"})
            rec = {"t": ts.isoformat(), "key": k, "approved_ai": v.get("approved"), "why_rules": cnd.why}
            if not v.get("approved"):
                rec["blocked"] = "ai_reject"
                log.append(rec)
                continue
            if s in pending:
                rec["blocked"] = "order already pending"
                log.append(rec)
                continue
            cnd2 = strategy.Candidate(**{**cnd.__dict__})
            cnd2.stop = cnd.entry * (1 - v["stop_pct"])
            cnd2.tp = cnd.entry * (1 + v["tp_pct"])
            notional, why = rm.size(st, cnd2, v.get("size_factor", 1.0), now)
            notional = min(notional, cash / (1 + fees["maker"]))
            rec["risk"] = why
            if notional < rp["min_notional"]:
                rec["blocked"] = "risk"
                log.append(rec)
                continue
            pending[s] = {"i": i, "price": cnd.entry, "notional": notional, "stop_pct": v["stop_pct"],
                          "tp_pct": v["tp_pct"], "setup": cnd.setup, "key": k, "max_hold": cnd.max_hold_bars}
            st.open_symbols.add(s)
            rec["order"] = {"notional": round(notional), "stop_pct": v["stop_pct"], "tp_pct": v["tp_pct"]}
            log.append(rec)

    last_ts = timeline[-1]
    for s, ps in list(positions.items()):
        i = pos_map[s][last_ts]
        close_pos(s, ps, arrays[s][4][i] * (1 - slip), fees["taker"], "end", i, last_ts)
    curve.append((last_ts.isoformat(), cash))

    res = metrics(curve, trades, start_equity, fees_paid, label)
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{label}_summary.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
        pd.DataFrame(trades).to_csv(out_dir / f"{label}_trades.csv", index=False)
        pd.DataFrame(curve, columns=["t", "equity"]).to_csv(out_dir / f"{label}_equity.csv", index=False)
        with open(out_dir / f"{label}_decisions.jsonl", "w", encoding="utf-8") as fh:
            for r in log:
                fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    return res


def metrics(curve, trades, start_equity, fees_paid, label) -> dict:
    eq = pd.Series([e for _, e in curve], index=pd.to_datetime([t for t, _ in curve]))
    dd = (1 - eq / eq.cummax()).max()
    days = max((eq.index[-1] - eq.index[0]).total_seconds() / 86400, 1e-9)
    tr = pd.DataFrame(trades)
    res = {"label": label, "start": str(eq.index[0]), "end": str(eq.index[-1]), "days": round(days, 1),
           "return_pct": round((eq.iloc[-1] / start_equity - 1) * 100, 3),
           "max_drawdown_pct": round(dd * 100, 3), "trades": len(tr), "trades_per_day": round(len(tr) / days, 2),
           "fees_paid": round(fees_paid)}
    if len(tr):
        wins = tr[tr.pnl > 0]
        res |= {"win_rate_pct": round(len(wins) / len(tr) * 100, 1),
                "avg_trade_ret_pct": round(tr.ret.mean() * 100, 3),
                "profit_factor": round(wins.pnl.sum() / max(-tr[tr.pnl <= 0].pnl.sum(), 1e-9), 2),
                "by_setup": tr.groupby("setup").agg(n=("pnl", "size"), pnl=("pnl", "sum")).round(0).to_dict("index"),
                "by_symbol": tr.groupby("symbol").agg(n=("pnl", "size"), pnl=("pnl", "sum")).round(0).to_dict("index"),
                "by_reason": tr.groupby("reason").size().to_dict()}
        # worst 3-day window: the length of the real competition
        r3 = eq.resample("1h").last().ffill()
        w = r3.pct_change(72).dropna()
        if len(w):
            res |= {"worst_3d_pct": round(w.min() * 100, 2), "best_3d_pct": round(w.max() * 100, 2),
                    "share_3d_windows_positive_pct": round((w > 0).mean() * 100, 1)}
    return res
