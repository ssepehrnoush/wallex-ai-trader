"""Did fake test ticks reach the live AI? Every simulate_threshold / get_premium_stats output of every live
review, and the warm-start baselines of every start. Read-only on MAIN."""
import json
from pathlib import Path
MAIN = Path(__file__).resolve().parents[3] / "wallex-bot" / "runs" / "live_main" / "decisions.jsonl"
import sys
from datetime import datetime
CUT = sys.argv[1] if len(sys.argv) > 1 else "2026-10-21T21:20:00"   # UTC; rows after it are ignored
rows = [r for r in (json.loads(l) for l in open(MAIN, encoding="utf-8")) if r["t"][:19] <= CUT]
n_rev = n_sim = 0; mx_tr = 0; mx_mean = 0.0
for r in rows:
    if r["event"] == "policy_review":
        n_rev += 1
        for c in r.get("analyst_tool_calls") or []:
            if c.get("tool") == "simulate_threshold":
                n_sim += 1
                import re; res = str(c.get("result")); m1 = re.search(r"\"trades\": ([0-9]+)", res); m2 = re.search(r"\"mean_ret_pct\": (-?[0-9.]+)", res); d = {"trades": int(m1.group(1)) if m1 else 0, "mean_ret_pct": float(m2.group(1)) if m2 else None}
                mx_tr = max(mx_tr, d.get("trades") or 0)
                if d.get("mean_ret_pct") is not None: mx_mean = max(mx_mean, abs(d["mean_ret_pct"]))
    if r["event"] == "warm_start":
        b = r.get("baselines_pct") or {}
        print(r["t"], "warm_start ticks", r.get("ticks"), {k: v for k, v in b.items()})
print("reviews", n_rev, "simulate_threshold calls", n_sim, "max trades", mx_tr, "max |mean| %", mx_mean)
