"""Replay the live_main ticks with this repo's simulate_threshold (the engine's buy and sell rule), frozen at a
cutoff, read-only on ../wallex-bot/runs, network blocked. Main case: ARBTMN with the policy it had at its
20:35 UTC crossing of -0.7% (20:18 UTC review: entry 0.8%, stop 0.8%, hold 15 min; the market was disabled).
    python research_ultra/review_docs/arb_replay.py [--cutoff 2026-10-21T21:20:00]"""
import argparse, json, os, socket, sys
from pathlib import Path
from datetime import datetime, timezone
def _blk(*a, **k): raise OSError("network blocked")
socket.socket.connect = _blk; socket.create_connection = _blk; socket.getaddrinfo = _blk
WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT))
os.environ["WALLEXBOT_RUN"] = "live_main"
from wallexbot import tools as T
ap = argparse.ArgumentParser(); ap.add_argument("--main", default=str(WT.parent / "wallex-bot"))
ap.add_argument("--cutoff", default="2026-10-21T21:20:00"); a = ap.parse_args()
T.RUNS = Path(a.main) / "runs"
CUT = datetime.fromisoformat(a.cutoff).replace(tzinfo=timezone.utc).timestamp()
class _Clock:                       # tools reads time.time(): freeze "now" at the cutoff
    @staticmethod
    def time(): return CUT
T.time = _Clock
_orig = T._tail_ticks
T._tail_ticks = lambda run_dir, minutes: [r for r in _orig(run_dir, minutes) if float(r["t"]) <= CUT]
for sym, e, st, hold in [("ARBTMN", 0.008, 0.008, 15), ("ARBTMN", 0.007, 0.009, 25)]:
    r = T.simulate_threshold(sym, e, 0.0005, st, hold, minutes=300)
    print(sym, f"entry {e} stop {st} hold {hold}:", json.dumps(r, ensure_ascii=False))
for sym in ["BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN", "PEPETMN", "BNBTMN"]:
    r = T.simulate_threshold(sym, 0.007, 0.0005, 0.008, 20, minutes=300)
    print(sym, "entry 0.007:", r.get("trades"), "trades", r.get("mean_ret_pct"))
