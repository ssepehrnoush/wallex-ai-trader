"""Turn a bt_fairvalue run into tomans with the live bot's own risk rules (wallexbot/risk.py + config.json "risk").

bt_fairvalue reports net percent per trade, one market at a time. This replays those trades in time order on one
account: RiskManager.size sets each order (risk_per_trade x AI size factor over stop + both fees, capped per
position and in total, minimum order), at most max_positions at once, a cooldown after each loss, the Tehran-day
loss limit, the drawdown kill switch and the profit lock. The window-end phases are off (a month has no contest end).
Writes <run>/toman.json; no network, no files read outside the run folder and config.json.

Simplifications, all stated in the output note: equity moves only when a trade closes (the engine marks open
positions every tick, so its kill switch can fire earlier), every order fills in full (history has no order book),
and a trade the account skips leaves that market's later trades unchanged (bt_fairvalue already chose them).

  python scripts/bt_toman.py runs/backtests/fv_live_rule_30d
"""
import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from wallexbot.config import ROOT, load_config
from wallexbot.risk import TEHRAN, RiskManager

ap = argparse.ArgumentParser()
ap.add_argument("run", help="a bt_fairvalue --out folder (trades.csv, trades_next_minute.csv, summary.json)")
ap.add_argument("--equity", type=float, default=5_000_000)
ap.add_argument("--size-factors", default="0.5,0.3", help="AI size factors to replay (0.5 = default policy)")
a = ap.parse_args()
run = Path(a.run) if Path(a.run).is_absolute() else ROOT / a.run
cfg = load_config().raw
summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
stop = summary["params"]["stop"]
window = [pd.Timestamp(x) for x in summary["params"]["window_utc"]]


def replay(trades: pd.DataFrame, factor: float, kill: bool = True) -> dict:
    p = {**cfg["risk"], **({} if kill else {"kill_switch_dd": 1.0})}
    rm = RiskManager(p, (window[1] + pd.Timedelta(days=30)).to_pydatetime(), cfg["fees"])
    st = rm.new_state(a.equity)
    open_, taken, skipped, by_sym, by_day = [], [], {}, {}, {}
    max_dd, halted_at = 0.0, None

    def settle(now):
        nonlocal max_dd, halted_at
        for t_exit, sym, notional, ret in sorted(o for o in open_ if o[0] <= now):
            open_.remove((t_exit, sym, notional, ret))
            pnl = notional * ret / 100
            st.exposure -= notional
            rm.on_exit(st, sym, pnl, t_exit.to_pydatetime())
            rm.mark(st, st.equity + pnl, t_exit.to_pydatetime())
            max_dd = max(max_dd, st.drawdown)
            by_sym[sym] = by_sym.get(sym, 0) + pnl
            day = t_exit.to_pydatetime().astimezone(TEHRAN).date().isoformat()
            by_day[day] = by_day.get(day, 0) + pnl
            if st.halted.startswith("kill") and not halted_at:
                halted_at = t_exit.isoformat()

    for r in trades.itertuples():
        settle(r.te)
        rm.mark(st, st.equity, r.te.to_pydatetime())          # a new Tehran day lifts the daily loss limit
        cand = SimpleNamespace(symbol=r.symbol, stop_pct=stop, tp_pct=0.0)
        notional, why = rm.size(st, cand, factor, r.te.to_pydatetime(), edge_checks=False)
        if not notional:
            key = why[-1].split(":")[0].split(" until")[0].replace(r.symbol, "<market>")
            skipped[key] = skipped.get(key, 0) + 1
            continue
        st.open_symbols.add(r.symbol)
        st.exposure += notional
        open_.append((r.tx, r.symbol, notional, r.ret_pct))
        taken.append(notional)
    settle(pd.Timestamp.max.tz_localize("UTC"))
    days = pd.Series(by_day, dtype=float)
    weeks = {}
    for d, v in days.items():
        w = (window[0].tz_convert(TEHRAN).normalize() + pd.Timedelta(days=7 * ((pd.Timestamp(d).tz_localize(TEHRAN)
             - window[0].tz_convert(TEHRAN).normalize()).days // 7))).date().isoformat()
        weeks[w] = weeks.get(w, 0) + v
    return {"trades_taken": len(taken), "trades_skipped": skipped, "mean_order_toman": round(sum(taken) / max(len(taken), 1)),
            "final_equity_toman": round(st.equity), "pnl_toman": round(st.equity - a.equity),
            "return_pct": round((st.equity / a.equity - 1) * 100, 2), "max_drawdown_pct": round(max_dd * 100, 2),
            "kill_switch_at_utc": halted_at, "days_up": int((days > 0).sum()), "days_down": int((days < 0).sum()),
            "best_day_toman": round(days.max()) if len(days) else 0, "worst_day_toman": round(days.min()) if len(days) else 0,
            "by_market_toman": {k: round(v) for k, v in sorted(by_sym.items())},
            "by_week_toman": {k: round(v) for k, v in sorted(weeks.items())}}


def load(name: str) -> pd.DataFrame:
    t = pd.read_csv(run / name) if (run / name).stat().st_size > 1 else pd.DataFrame()    # a run with 0 trades
    if not len(t):
        return pd.DataFrame(columns=["symbol", "te", "tx", "ret_pct"])
    t["te"], t["tx"] = pd.to_datetime(t.t_entry, utc=True), pd.to_datetime(t.t_exit, utc=True)
    return t.sort_values("te").reset_index(drop=True)


fills = {"instant": load("trades.csv"), "next_minute": load("trades_next_minute.csv")}
out = {}
for f in (float(x) for x in a.size_factors.split(",")):
    for name, t in fills.items():
        out[f"{name}_size{f}"] = replay(t, f)
    out[f"next_minute_size{f}_no_kill_switch"] = replay(fills["next_minute"], f, kill=False)
res = {"note": (f"bt_fairvalue trades from {run.name} replayed on one {a.equity:,.0f} toman account with "
                f"wallexbot/risk.py and config.json risk (stop {stop:.2%}). 'instant' = entry at the signal minute "
                "(trades.csv), 'next_minute' = entry at the next traded minute (trades_next_minute.csv); the live engine "
                "polls the real order book every few seconds, so it sits between them. Equity moves only when a trade "
                "closes, every order fills in full, the window-end phases are off. '_no_kill_switch' raises the "
                "drawdown kill switch to 100% to show what it saved. Weeks start on the window's first Tehran day."),
       "params": {"run": run.name, "equity_toman": a.equity, "stop": stop, "window_utc": summary["params"]["window_utc"],
                  "risk": cfg["risk"], "fees": cfg["fees"]},
       "scenarios": out}
(run / "toman.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
for k, v in out.items():
    print(k, {x: v[x] for x in ("trades_taken", "pnl_toman", "return_pct", "max_drawdown_pct", "kill_switch_at_utc")})
