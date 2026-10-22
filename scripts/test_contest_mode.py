"""Offline check of contest mode in the supervisor and the contest loss budget in RiskManager.size
(no network, no AI, no files written).

Contest mode: the AI may switch a live market off only for a structural problem the code can see in the review's
own market data (no fresh book, spread above the cap, top of book below the minimum order); entry_dev is capped
and size_factor kept in range; watch markets keep their own gate; a "halt" from the AI is respected.

  python scripts/test_contest_mode.py
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wallexbot.config import load_config
from wallexbot.fairvalue import Policy
from wallexbot.risk import RiskManager
from wallexbot.supervisor import Supervisor

cfg = load_config().raw
FEES = {"TMN": cfg["fees"], "USDT": cfg["fees_usdt"]}
CONTEST = cfg["contest"]
OK_BOOK = {"samples": 500, "spread_pct": 0.1, "top_depth_quote": 2_000_000, "quote": "TMN"}


class FakeCommittee:
    cfg = {"agentic": False}

    def __init__(self, markets, mode="defensive"):
        self.markets, self.mode, self.seen = markets, mode, []

    def _ask(self, role, system, payload):
        self.seen.append(payload)
        return {"risk_mode": self.mode, "markets": self.markets}


def supervisor(ai=None, contest=CONTEST, watch=()):
    return Supervisor(ai, FEES, watch=watch, contest=contest, max_spread=cfg["fairvalue"]["max_spread"],
                      min_depth=cfg["risk"]["min_notional"] * 1.05)


def review(ai_markets, stats, mode="defensive", contest=CONTEST, watch=(), evidence=None, current=None):
    ai = FakeCommittee(ai_markets, mode)
    sup = supervisor(ai, contest, watch)
    sup._live_evidence = lambda s, p: evidence or {"trades": 0, "sum_ret_pct": 0}
    cur = current or {s: Policy() for s in ai_markets}
    new, rec = sup.review({"markets": stats, "context": {}}, cur)
    return ai, new, rec


fails = 0


def check(cond, msg):
    global fails
    print(("PASS " if cond else "FAIL ") + msg)
    fails += not cond


off = {"enabled": False, "entry_dev": 0.009, "size_factor": 0.05, "reason": "quiet"}
ai, new, rec = review({"BTCTMN": off}, {"BTCTMN": OK_BOOK})
p = new["BTCTMN"]
check(p.enabled, "AI switched a healthy market off: contest mode keeps it on")
check(p.size_factor == 0.2, f"size_factor raised into the range (got {p.size_factor})")
check(p.entry_dev == 0.009, f"a threshold inside the cap is kept (got {p.entry_dev})")
check(p.reason.startswith("حالت مسابقه"), "the change is explained in the policy reason")
check(rec["contest_adjustments"]["BTCTMN"]["ai"]["enabled"] is False, "the AI's own decision is logged next to the override")
check(ai.seen[0].get("contest_rule") and ai.seen[0].get("contest_bounds", {}).get("entry_dev_max") == 0.01,
      "the model is told the contest rule and bounds")

_, new, _ = review({"ETHTMN": {"enabled": True, "entry_dev": 0.015, "size_factor": 0.9}}, {"ETHTMN": OK_BOOK})
check(new["ETHTMN"].entry_dev == 0.01 and new["ETHTMN"].size_factor == 0.3,
      f"entry_dev capped at 1% and size at 0.3 (got {new['ETHTMN'].entry_dev}, {new['ETHTMN'].size_factor})")

for name, st in (("spread above the cap", {**OK_BOOK, "spread_pct": 0.9}),
                 ("top of book below the minimum order", {**OK_BOOK, "top_depth_quote": 30_000}),
                 ("no fresh book", {"samples": 3})):
    _, new, rec = review({"ARBTMN": off}, {"ARBTMN": st})
    check(not new["ARBTMN"].enabled and rec["contest_adjustments"]["ARBTMN"]["structural_problem"],
          f"{name}: the AI may keep it off")

_, new, rec = review({"BTCTMN": off}, {"BTCTMN": OK_BOOK}, mode="halt")
check(not new["BTCTMN"].enabled and "halt" in rec["contest_adjustments"], "an AI 'halt' is respected and logged")

_, new, _ = review({"POLTMN": off}, {"POLTMN": OK_BOOK}, watch={"POLTMN"})
check(not new["POLTMN"].enabled, "watch markets keep their own gate")
_, new, _ = review({"POLTMN": {"enabled": True, "entry_dev": 0.015, "size_factor": 0.9}}, {"POLTMN": OK_BOOK}, watch={"POLTMN"},
                  evidence={"trades": 2, "sum_ret_pct": 0.5})
check(new["POLTMN"].enabled and new["POLTMN"].size_factor == 0.3 and new["POLTMN"].entry_dev == 0.015,
      "a watch market the replay switches on is size-clamped, with the AI's own threshold")
_, new, _ = review({"BTCTMN": off}, {"BTCTMN": OK_BOOK, "ETHTMN": OK_BOOK},
                  current={"BTCTMN": Policy(), "ETHTMN": Policy(size_factor=0.5)})
check(new["ETHTMN"].enabled and new["ETHTMN"].size_factor == 0.3, "a market missing from the AI answer is bounded too")
_, new, _ = review({"BTCTMN": {"enabled": True, "entry_dev": 0.009, "size_factor": 0.25}}, {"BTCTMN": OK_BOOK},
                  contest={**CONTEST, "max_entry_dev": 0.005})
check(new["BTCTMN"].entry_dev >= 0.007, f"a contest cap below the fee floor never pushes entry under the floor ({new['BTCTMN'].entry_dev})")
start = supervisor(watch={"POLTMN"}).bound_start({"BTCTMN": Policy(size_factor=0.5), "POLTMN": Policy(enabled=False, size_factor=0.5)})
check(start["BTCTMN"].size_factor == 0.3 and start["BTCTMN"].enabled and not start["POLTMN"].enabled and start["POLTMN"].size_factor == 0.3,
      "start-up policies are bounded before the first review (watch markets stay off)")
check(supervisor(contest={"enabled": True}).contest["size_factor_range"] == [0.2, 0.3], "an incomplete contest block gets the defaults")

_, new, rec = review({"BTCTMN": off}, {"BTCTMN": OK_BOOK}, contest={**CONTEST, "enabled": False})
check(not new["BTCTMN"].enabled and new["BTCTMN"].size_factor == 0.05 and not rec["contest_adjustments"],
      "contest disabled: the AI's policy is applied unchanged")

_, new, _ = review({"BTCTMN": {"enabled": True, "entry_dev": 0.0071, "size_factor": 0.25}}, {"BTCTMN": OK_BOOK})
check(new["BTCTMN"].entry_dev == 0.0071 and new["BTCTMN"].size_factor == 0.25, "policies already inside the bounds are untouched")

# loss budget in RiskManager.size
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


class C:
    symbol, stop_pct, tp_pct = "BTCTMN", 0.009, 0.0


budget = cfg["risk"]["contest_loss_budget"]
rm = RiskManager(cfg["risk"], NOW + timedelta(days=1), cfg["fees"])
st = rm.new_state(5_000_000)
rm.mark(st, 5_000_000 * (1 - budget) + 1, NOW)
n1, _ = rm.size(st, C, 0.25, NOW, edge_checks=False)
rm.mark(st, 5_000_000 * (1 - budget), NOW)
n2, why = rm.size(st, C, 0.25, NOW, edge_checks=False)
check(n1 > 0 and n2 == 0 and "loss budget" in why[-1], f"loss budget: trades at -{budget - 0.0000002:.4%}, stops at -{budget:.1%} ({why[-1]})")
rm.on_exit(st, "ETHTMN", -1_000, NOW)
st.equity = 4_999_000
n3, why3 = rm.size(st, C, 0.25, NOW, edge_checks=False)
check(n3 == 0 and "waiting" in why3[-1], "after a close it waits for a fresh account value before checking the budget")
rm.mark(st, 5_000_000 * (1 - budget / 2), NOW)
n4, _ = rm.size(st, C, 0.25, NOW, edge_checks=False)
check(n4 > 0, "the budget releases when the marked account recovers above it")
rm.mark(st, 5_010_000, NOW)
n5, why5 = rm.size(st, C, 0.25, NOW, edge_checks=False)
check(round(n5) == 125_000 and "capped" in why5[0], f"ahead by 10,000: the gain guard caps the size, the budget is not involved ({n5:,.0f})")

print("all passed" if not fails else f"{fails} failed")
sys.exit(1 if fails else 0)
