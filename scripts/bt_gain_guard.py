"""What the gain guard (config risk.protect_gain_*) does to a contest-length stretch, measured on a backtest run.

Every window of --hours (stepped by one hour) of the run's trades is replayed on a fresh 5,000,000 toman account
with wallexbot/risk.py, once with the guard and once without, for both fills in the run: entry at the signal
minute (trades.csv) and one minute later (trades_next_minute.csv). The contest pays the profit score only to
an account that ends in profit, so the number that matters is the share of windows that end above zero.
Same simplifications as scripts/bt_toman.py (equity moves on closes, full fills). No network.

  python scripts/bt_gain_guard.py runs/backtests/fv_ai_policy_30d --size-factor 0.35 --hours 32.5 --out docs/gain_guard_windows.json
Contest mode (RESULTS.md 8): every market on at the 0.7% floor, size 0.25, gain guard and the 0.4% loss budget:
  python scripts/bt_gain_guard.py runs/backtests/fv_live_rule_30d --size-factor 0.25 --hours 28 --loss-budget 0.004 --out docs/contest_mode_windows.json
"""
import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from wallexbot.config import ROOT, load_config
from wallexbot.risk import RiskManager

ap = argparse.ArgumentParser()
ap.add_argument("run")
ap.add_argument("--size-factor", type=float, default=0.35, help="AI size factor (live reviews used 0.25 to 0.4)")
ap.add_argument("--hours", type=float, default=32.5, help="window length (2026-10-22 09:20 UTC to the last entry)")
ap.add_argument("--equity", type=float, default=5_000_000)
ap.add_argument("--loss-budget", type=float, default=0.0, help="contest loss budget in both replays (config: 0.004)")
ap.add_argument("--out", default="docs/gain_guard_windows.json")
a = ap.parse_args()
at_root = lambda p: Path(p) if Path(p).is_absolute() else ROOT / p
run = at_root(a.run)
cfg = load_config().raw
stop = json.loads((run / "summary.json").read_text(encoding="utf-8"))["params"]["stop"]
far = pd.Timestamp("2100-01-01", tz="UTC").to_pydatetime()          # no contest-end phases inside a window


def replay(tr: pd.DataFrame, guard: bool) -> tuple[float, int]:
    p = {**cfg["risk"], "contest_loss_budget": a.loss_budget, **({} if guard else {"protect_gain_share": 0})}
    rm = RiskManager(p, far, cfg["fees"])
    st, open_, taken = rm.new_state(a.equity), [], 0

    def settle(now):
        for t_exit, sym, notional, ret in sorted(o for o in open_ if o[0] <= now):
            open_.remove((t_exit, sym, notional, ret))
            pnl = notional * ret / 100
            st.exposure -= notional
            rm.on_exit(st, sym, pnl, t_exit.to_pydatetime())
            rm.mark(st, st.equity + pnl, t_exit.to_pydatetime())

    for r in tr.itertuples():
        settle(r.te)
        rm.mark(st, st.equity, r.te.to_pydatetime())
        notional, _ = rm.size(st, SimpleNamespace(symbol=r.symbol, stop_pct=stop, tp_pct=0.0), a.size_factor,
                              r.te.to_pydatetime(), edge_checks=False)
        if notional <= 0:
            continue
        st.open_symbols.add(r.symbol)
        st.exposure += notional
        open_.append((r.tx, r.symbol, notional, r.ret_pct))
        taken += 1
    settle(pd.Timestamp.max.tz_localize("UTC"))
    return st.equity - a.equity, taken


res = {}
for name, f in (("instant", "trades.csv"), ("next_minute", "trades_next_minute.csv")):
    t = pd.read_csv(run / f)
    t["te"], t["tx"] = pd.to_datetime(t.t_entry, utc=True), pd.to_datetime(t.t_exit, utc=True)
    t = t.sort_values("te").reset_index(drop=True)
    starts = pd.date_range(t.te.min().floor("h"), t.te.max() - pd.Timedelta(hours=a.hours), freq="1h")
    for guard in (False, True):
        out = [replay(t[(t.te >= s) & (t.te < s + pd.Timedelta(hours=a.hours))], guard) for s in starts]
        pnl, n = np.array([x[0] for x in out]), np.array([x[1] for x in out])
        res[f"{name}_{'guard' if guard else 'no_guard'}"] = {
            "windows": len(out), "share_positive": round(float((pnl > 0).mean()), 3),
            "share_zero": round(float((pnl == 0).mean()), 3), "share_negative": round(float((pnl < 0).mean()), 3),
            "mean_pnl_toman": round(float(pnl.mean())), "median_pnl_toman": round(float(np.median(pnl))),
            "worst_pnl_toman": round(float(pnl.min())), "best_pnl_toman": round(float(pnl.max())),
            "mean_trades": round(float(n.mean()), 2)}
doc = {"note": (f"Every {a.hours:g}-hour window (stepped 1 h) of {run.name} replayed on a fresh {a.equity:,.0f} toman "
                f"account with wallexbot/risk.py, AI size factor {a.size_factor}, stop {stop:.2%}; '_guard' uses "
                "config risk.protect_gain_*, '_no_guard' sets protect_gain_share to 0. Candle trades: the live order "
                "book offers fewer signals (RESULTS.md 2.6, 2.7), so read the shares, not the trade counts."),
       "params": {"run": run.name, "size_factor": a.size_factor, "hours": a.hours, "equity_toman": a.equity,
                  "loss_budget": a.loss_budget,
                  "guard": {k: v for k, v in cfg["risk"].items() if k.startswith("protect_gain")}},
       "results": res}
at_root(a.out).write_text(json.dumps(doc, indent=1), encoding="utf-8")
print(json.dumps(res, indent=1))
