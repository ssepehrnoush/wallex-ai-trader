"""Extra read-only checks for the live audit.

1. Warm-start reproduction: rows per market the engine's _warm_start would load at each logged start
   (any runs/*/ticks.csv, rows in the 180 min before the start), to confirm tracker.ready (>= 240).
2. Synthetic test rows (runs/test_live_paths/ticks.csv, written by scripts/test_live_paths.py at
   17:40-17:53 UTC) inside the windows read by the warm start and by the AI's MCP tools: effect on the
   baseline median and on tools.simulate_threshold / get_premium_stats (imported from WT/wallexbot/tools.py,
   pointed at MAIN/runs read-only; these two tools only read files, they never call Wallex).
3. Forecast variants the AI could force: majors only (if it keeps PEPE / BNB / ARB disabled), plus a
   bootstrap CI of the pooled per-trade mean (live_rule trades from forecast.py).
"""
import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import csv
import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

MAIN = Path("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot")
WT = Path("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot-ultra")
HERE = Path(__file__).resolve().parent
RUNS = MAIN / "runs"
SYMS = ["BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN", "PEPETMN", "BNBTMN", "ARBTMN"]
out = {}

# ---- 1. warm-start reproduction
starts = []
for line in (RUNS / "live_main" / "decisions.jsonl").read_text(encoding="utf-8").splitlines():
    try:
        e = json.loads(line)
    except json.JSONDecodeError:
        continue
    if e["event"] == "warm_start":
        starts.append((pd.Timestamp(e["t"]).timestamp(), e["ticks"], e["baselines_pct"]))
rows_all = []
for p in RUNS.glob("*/ticks.csv"):
    with open(p, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                t = float(r["t"])
                if r["symbol"] in SYMS:
                    rows_all.append((t, r["symbol"], (float(r["bid"]) + float(r["ask"])) / 2 / float(r["fair"]) - 1, p.parent.name))
            except (KeyError, ValueError, ZeroDivisionError, TypeError):
                continue
ws = []
for t0, logged, logged_base in starts:
    sel = [x for x in rows_all if t0 - 180 * 60 <= x[0] < t0]
    per = {s: sum(1 for x in sel if x[1] == s) for s in SYMS}
    med_all = {s: round(statistics.median([x[2] for x in sel if x[1] == s]) * 100, 3) for s in SYMS if per[s]}
    med_clean = {s: round(statistics.median([x[2] for x in sel if x[1] == s and x[3] != "test_live_paths"]) * 100, 3)
                 for s in SYMS if per[s]}
    synth = sum(1 for x in sel if x[3] == "test_live_paths")
    ws.append({"start_utc": str(pd.Timestamp(t0, unit="s", tz="UTC"))[:19], "logged_ticks": logged, "recomputed_ticks": len(sel),
               "per_market": per, "min_per_market": min(per.values()), "synthetic_rows_in_window": synth,
               "baseline_logged_pct": logged_base, "baseline_recomputed_pct": med_all, "baseline_without_synthetic_pct": med_clean})
out["warm_start_check"] = ws

# ---- 2. what the AI's tools see, with and without the synthetic rows
sys.path.insert(0, str(WT))
from wallexbot import tools  # noqa: E402  (creates clients, sends nothing)

tools.RUNS = RUNS
os.environ["WALLEXBOT_RUN"] = "live_main"
orig_tail = tools._tail_ticks


def tail_clean(run_dir, minutes):
    return [r for r in orig_tail(run_dir, minutes) if not (r["symbol"] in ("BTCTMN", "ETHTMN") and float(r["bid"]) == 22050000000.0)]


sims = {}
for sym, e in (("ETHTMN", 0.004), ("ETHTMN", 0.003), ("BTCTMN", 0.001), ("ARBTMN", 0.004), ("PEPETMN", 0.004)):
    tools._tail_ticks = orig_tail
    a = tools.simulate_threshold(sym, e, minutes=240)
    tools._tail_ticks = tail_clean
    b = tools.simulate_threshold(sym, e, minutes=240)
    sims[f"{sym}@{e}"] = {"with_synthetic": {k: a.get(k) for k in ("trades", "mean_ret_pct", "sum_ret_pct", "examples")},
                          "without": {k: b.get(k) for k in ("trades", "mean_ret_pct", "sum_ret_pct", "examples")}}
tools._tail_ticks = orig_tail
ps = {}
for sym in ("BTCTMN", "ETHTMN"):
    tools._tail_ticks = orig_tail
    a = tools.get_premium_stats(sym, 120)
    tools._tail_ticks = tail_clean
    b = tools.get_premium_stats(sym, 120)
    ps[sym] = {"with_synthetic": a, "without": b}
tools._tail_ticks = orig_tail
out["ai_tools_synthetic_effect"] = {"simulate_threshold_240min": sims, "premium_stats_120min": ps,
                                    "test_file_mtime_utc": str(pd.Timestamp((RUNS / "test_live_paths" / "ticks.csv").stat().st_mtime, unit="s", tz="UTC"))[:19]}

# ---- 3. forecast if the AI keeps the thin markets disabled; CI of the pooled mean
fc = json.loads((HERE / "forecast.json").read_text(encoding="utf-8"))
tr = pd.read_csv(HERE / "bt_trades_live_rule.csv")
rem = fc["remaining_days"]
days = fc["variants"]["live_rule"]["markets"]["BTCTMN"]["days"]
rng = np.random.default_rng(7)
scen = {}
trc = pd.read_csv(HERE / "bt_trades_current_policy.csv")
share = fc["notional_share_of_equity_current"]
for lab, syms, src, w in (("0.7%_majors_only", SYMS[:5], tr, fc["notional_tmn_per_trade"] / 5_000_000),
                          ("0.7%_enabled_now", fc["enabled_now"], tr, fc["notional_tmn_per_trade"] / 5_000_000),
                          ("0.7%_all_8", SYMS, tr, fc["notional_tmn_per_trade"] / 5_000_000),
                          ("current_policy", fc["enabled_now"], trc, share["BTCTMN"]),
                          ("current_policy_majors_only", SYMS[:5], trc, share["BTCTMN"])):
    x = src[src.symbol.isin(syms)].ret_pct.to_numpy()
    if len(x) == 0:
        scen[lab] = {"bt_trades": 0}
        continue
    boots = np.array([rng.choice(x, len(x)).mean() for _ in range(20000)])
    rate = len(x) / days
    scen[lab] = {"bt_trades": int(len(x)), "rate_per_day": round(rate, 3), "expected_trades_remaining": round(rate * rem, 2),
                 "p_zero_remaining": round(float(np.exp(-rate * rem)), 4),
                 "mean_ret_pct": round(float(x.mean()), 3), "mean_ci95_pct": [round(float(np.quantile(boots, 0.025)), 3), round(float(np.quantile(boots, 0.975)), 3)],
                 "t_stat": round(float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))), 2) if len(x) > 2 else None,
                 "expected_account_return_pct": round(float(rate * rem * x.mean() * w), 3)}
out["scenarios"] = scen
(HERE / "extra_checks.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")

print("warm start check:")
for w in ws:
    print(f"  {w['start_utc']} logged={w['logged_ticks']} recomputed={w['recomputed_ticks']} min/market={w['min_per_market']} synthetic_in_window={w['synthetic_rows_in_window']}")
    print(f"     baseline logged   {w['baseline_logged_pct']}")
    print(f"     baseline recomp   {w['baseline_recomputed_pct']}")
    print(f"     without synthetic {w['baseline_without_synthetic_pct']}")
print("\nAI tools, synthetic effect:")
for k, v in sims.items():
    print(f"  simulate {k}: with={v['with_synthetic']['trades']} trades mean={v['with_synthetic']['mean_ret_pct']} ex={v['with_synthetic']['examples']}")
    print(f"  {'':9}  without={v['without']['trades']} trades mean={v['without']['mean_ret_pct']}")
for k, v in ps.items():
    a, b = v["with_synthetic"], v["without"]
    print(f"  premium_stats {k}: samples {a.get('samples')} vs {b.get('samples')}, p1 {a.get('buy_dev_p1_pct')} vs {b.get('buy_dev_p1_pct')}, "
          f"median {a.get('buy_dev_median_pct')} vs {b.get('buy_dev_median_pct')}, min<0.3 {a.get('minutes_cheaper_than')} vs {b.get('minutes_cheaper_than')}")
print("\nscenarios:")
for k, v in scen.items():
    print(f"  {k}: {v}")
