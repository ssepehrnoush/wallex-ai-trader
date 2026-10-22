"""Check a candle backtest against the live order book where the two overlap in time.

The 30-day backtest window (RESULTS.md 2.6) ends 2026-10-22 07:00 UTC, after the live bot started recording the
real order book every 2.5 s (MAIN/runs/live_main/ticks.csv, not in git, read only). For every backtest entry
inside the recorded span this prints what the live book showed around that candle minute: the deepest buy_dev
(the engine's own baseline), the spread, whether any tick met the entry rule (buy_dev <= -entry and spread <= the
cap), and the AI policy in force (config defaults at each start, then each policy_review).
The window is [minute - 60 s, minute + 120 s] of local tick time: the laptop clock runs about 40 s behind the
exchange (RESULTS.md 6), so this covers the candle minute with margin.

  python scripts/bt_live_overlap.py --run runs/backtests/fv_live_rule_30d --main ../wallex-bot --out docs/bt30_live_overlap.json
"""
import argparse
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from wallexbot.config import ROOT, load_config

ap = argparse.ArgumentParser()
ap.add_argument("--run", default="runs/backtests/fv_live_rule_30d")
ap.add_argument("--main", default=str(ROOT.parent / "wallex-bot"))
ap.add_argument("--out", default="docs/bt30_live_overlap.json")
a = ap.parse_args()
at_root = lambda p: Path(p) if Path(p).is_absolute() else ROOT / p
cfg = load_config().raw
cap = cfg["fairvalue"]["max_spread"]
live = Path(a.main) / "runs" / "live_main"

txt = (live / "ticks.csv").read_text(encoding="utf-8")
tk = pd.read_csv(io.StringIO(txt[:txt.rfind("\n") + 1]), on_bad_lines="skip")
for c in ("t", "bid", "ask", "buy_dev"):
    tk[c] = pd.to_numeric(tk[c], errors="coerce")
tk = tk.dropna(subset=["t", "bid", "ask"])
timeline = []                                         # (unix time, {symbol: (enabled, entry_dev)} or None = defaults)
for line in (live / "decisions.jsonl").read_text(encoding="utf-8").splitlines():
    d = json.loads(line)
    t = pd.Timestamp(d["t"]).timestamp()
    if d.get("event") == "start":
        timeline.append((t, None))
    elif d.get("event") == "policy_review":
        timeline.append((t, {s: (bool(p.get("enabled")), p.get("entry_dev")) for s, p in d["policies"].items()}))


def policy_at(sym: str, t: float):
    last = [p for ts, p in timeline if ts <= t]
    if not last:
        return None
    if last[-1] is None:
        return {"enabled": True, "entry_dev": cfg["fairvalue"]["default_policy"]["TMN"]["entry_dev"], "from": "defaults"}
    on, dev = last[-1].get(sym, (False, None))
    return {"enabled": on, "entry_dev": dev, "from": "policy_review"}


tr = pd.read_csv(at_root(a.run) / "trades.csv")
tr["ts"] = pd.to_datetime(tr.t, utc=True).map(pd.Timestamp.timestamp)
first, last = tk.t.min(), tk.t.max()
rows = []
for r in tr[(tr.ts >= first) & (tr.ts <= last)].itertuples():
    w = tk[(tk.symbol == r.symbol) & (tk.t >= r.ts - 60) & (tk.t < r.ts + 120)]
    sp = w.ask / w.bid - 1
    pol = policy_at(r.symbol, r.ts)
    thr = max(pol["entry_dev"] or 0, 2 * cfg["fees"]["taker"] + 0.001) if pol else 2 * cfg["fees"]["taker"] + 0.001
    rows.append({"symbol": r.symbol, "minute_utc": r.t, "backtest_ret_pct": round(r.ret_pct, 3), "backtest_exit": r.why,
                 "backtest_entry_dev_pct": round(r.entry_dev_pct, 3), "ticks": len(w),
                 "live_min_buy_dev_pct": round(w.buy_dev.min() * 100, 3) if len(w) else None,
                 "live_median_spread_pct": round(sp.median() * 100, 3) if len(w) else None,
                 "live_ticks_meeting_rule": int(((w.buy_dev <= -thr) & (sp <= cap)).sum()),
                 "policy": pol})
res = {"note": ("Backtest entries inside the live recording, against the live order book around the same minute. "
                "live_ticks_meeting_rule counts ticks with buy_dev <= -max(policy entry_dev, fee floor) and spread <= "
                f"max_spread {cap:.1%}; the engine also needs the market enabled by the policy in force."),
       "run": at_root(a.run).name, "live_ticks_utc": [pd.Timestamp(first, unit="s", tz="UTC").isoformat(),
                                                       pd.Timestamp(last, unit="s", tz="UTC").isoformat()],
       "entries": rows}
at_root(a.out).write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
print(json.dumps(res, indent=1, ensure_ascii=False))
