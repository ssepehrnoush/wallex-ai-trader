"""Deepest buy_dev per market in live_main up to a cutoff, split by whether the market was enabled by the
policy in force at that tick (start = config defaults, then each policy_review). Read-only on MAIN."""
import json, sys, bisect
from datetime import datetime
import pandas as pd
from pathlib import Path
MAIN = str(Path(__file__).resolve().parents[3] / "wallex-bot" / "runs" / "live_main")
CUT = pd.Timestamp(sys.argv[1] if len(sys.argv) > 1 else "2026-10-21T21:20:00", tz="UTC").timestamp()
ev = []
for l in open(f"{MAIN}/decisions.jsonl", encoding="utf-8"):
    try: d = json.loads(l)
    except ValueError: continue
    ev.append(d)
starts = [d for d in ev if d["event"] == "start"]
print("start keys:", list(starts[0].keys()), json.dumps(starts[0].get("policy"))[:300])
timeline = []   # (t, {sym: (enabled, entry_dev)})
for d in ev:
    t = pd.Timestamp(d["t"]).timestamp()
    if d["event"] == "start":
        timeline.append((t, None))     # defaults: all enabled
    elif d["event"] == "policy_review":
        timeline.append((t, {s: (bool(p.get("enabled")), p.get("entry_dev")) for s, p in d["policies"].items()}))
ts = [x[0] for x in timeline]
tk = pd.read_csv(f"{MAIN}/ticks.csv", on_bad_lines="skip", dtype={"symbol": str})
for c in ["t", "bid", "ask", "buy_dev"]: tk[c] = pd.to_numeric(tk[c], errors="coerce")
tk = tk.dropna(subset=["t", "buy_dev"]); tk = tk[tk.t <= CUT]
def state(t, s):
    i = bisect.bisect_right(ts, t) - 1
    if i < 0: return True
    pol = timeline[i][1]
    return True if pol is None else pol.get(s, (True, None))[0]
tk["on"] = [state(t, s) for t, s in zip(tk.t, tk.symbol)]
for s, g in tk.groupby("symbol"):
    out = []
    for flag in (True, False):
        h = g[g.on == flag]
        if len(h):
            i = h.buy_dev.idxmin()
            out.append(f"{'ON ' if flag else 'OFF'} min {h.buy_dev.min()*100:.3f}% at {pd.Timestamp(h.loc[i,'t'], unit='s', tz='UTC'):%H:%M:%S} (n={len(h)}, <=-0.7%: {(h.buy_dev <= -0.007).sum()})")
    print(s, " | ".join(out))
print("last tick", pd.Timestamp(tk.t.max(), unit="s", tz="UTC"), "rows", len(tk))
