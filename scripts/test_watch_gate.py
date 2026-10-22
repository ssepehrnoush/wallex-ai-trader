"""Offline check of the watch-market gate (no network, no AI, no files written).

A watch market may trade only while its own recorded live ticks replay at a profit with the policy just set.
The fake committee below always asks to switch every market on; the gate must still keep a watch market off
unless the replay evidence is profitable, and must never touch normal markets.

  python scripts/test_watch_gate.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wallexbot.fairvalue import Policy
from wallexbot.supervisor import Supervisor

FEES = {"TMN": {"maker": 0.0025, "taker": 0.003}, "USDT": {"maker": 0.001, "taker": 0.0013}}
ON = {"enabled": True, "entry_dev": 0.008, "exit_slack": 0.0005, "stop_pct": 0.009, "max_hold_min": 25,
      "size_factor": 0.5, "reason": "on"}


class FakeCommittee:
    cfg = {"agentic": False}

    def __init__(self):
        self.seen = []

    def _ask(self, role, system, payload):
        self.seen.append(payload)
        return {"risk_mode": "normal", "markets": {s: dict(ON) for s in ("BTCTMN", "POLTMN", "SANDTMN")}}


def run(evidence: dict):
    ai = FakeCommittee()
    sup = Supervisor(ai, FEES, watch={"POLTMN", "SANDTMN"})
    sup._live_evidence = lambda s, p: evidence[s]
    cur = {s: Policy(enabled=s == "BTCTMN") for s in ("BTCTMN", "POLTMN", "SANDTMN")}
    new, rec = sup.review({"markets": {}, "context": {}}, cur)
    return ai, new, rec


fails = 0


def check(name, ok):
    global fails
    fails += not ok
    print(("PASS " if ok else "FAIL ") + name)


ai, new, rec = run({"POLTMN": {"trades": 2, "sum_ret_pct": 0.4}, "SANDTMN": {"trades": 0, "sum_ret_pct": 0}})
check("profitable live replay switches the watch market on", new["POLTMN"].enabled)
check("no live trades keeps the watch market off", not new["SANDTMN"].enabled)
check("the gate's reason is written", new["SANDTMN"].reason.startswith("watch:"))
check("normal markets are not gated", new["BTCTMN"].enabled)
check("evidence is logged for each watch market the AI wanted on", set(rec["watch_evidence"]) == {"POLTMN", "SANDTMN"})
check("the AI is told which markets are on watch and the rule",
      ai.seen[0].get("watch_markets") == ["POLTMN", "SANDTMN"] and "simulate_threshold" in ai.seen[0].get("watch_rule", ""))

_, new, _ = run({"POLTMN": {"trades": 3, "sum_ret_pct": -0.2}, "SANDTMN": {"samples": 10, "note": "not enough data yet"}})
check("a losing live replay keeps the watch market off", not new["POLTMN"].enabled)
check("not enough data keeps the watch market off", not new["SANDTMN"].enabled)

_, new, _ = run({"POLTMN": {"error": "boom"}, "SANDTMN": {"error": "boom"}})
check("a failing replay keeps the watch market off", not new["POLTMN"].enabled and not new["SANDTMN"].enabled)

print("all passed" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
