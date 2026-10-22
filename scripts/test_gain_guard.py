"""Offline check of the gain guard in RiskManager.size (no network, no AI, no files written).

Once the marked equity is ahead of the start capital, a new trade may only risk a share of the gain: with the
config values (share 0.5, worst-case loss 4%) a new order is capped at gain x 0.5 / 0.04, minus what the open
exposure could lose, and blocked when that is below the smallest order the live executor sends (1.05 x the
exchange minimum). Right after a close it waits for a fresh mark. Behind or flat, sizing is unchanged.

  python scripts/test_gain_guard.py
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wallexbot.config import load_config
from wallexbot.risk import RiskManager

cfg = load_config().raw
P, FEES = cfg["risk"], cfg["fees"]
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
END = NOW + timedelta(days=1)
START = 5_000_000


class C:
    symbol = "BTCTMN"
    stop_pct = 0.009
    tp_pct = 0.0


fails = 0


def check(cond, msg):
    global fails
    print(("PASS " if cond else "FAIL ") + msg)
    fails += not cond


def fresh(p=P):
    rm = RiskManager(p, END, FEES)
    return rm, rm.new_state(START)


def closed(rm, st, pnl, equity=None):
    """A trade closes with this P&L, then the engine marks the account."""
    rm.on_exit(st, "ETHTMN", pnl, NOW)
    rm.mark(st, (st.equity + pnl) if equity is None else equity, NOW)


size = lambda rm, st: rm.size(st, C, 0.5, NOW, edge_checks=False)

rm, st = fresh()
base, _ = size(rm, st)
check(round(base) == 1_034_483, f"flat account: normal size {base:,.0f}")

rm, st = fresh()
rm.on_exit(st, "ETHTMN", 2_400, NOW)
n, why = size(rm, st)
check(n == 0 and "waiting" in why[-1], f"right after a close, before the next mark: wait ({why[-1]})")

rm, st = fresh()
closed(rm, st, 2_400)
n, why = size(rm, st)
check(n == 0 and "protecting" in why[-1], f"+2,400 marked gain: no new risk ({why[-1]})")

rm, st = fresh()
closed(rm, st, 4_900)
n, why = size(rm, st)
check(n == 0, f"+4,900: room 61,250 is under the 63,000 the executor would send, so blocked ({why[-1]})")

rm, st = fresh()
closed(rm, st, 10_000)
n, _ = size(rm, st)
check(round(n) == 125_000, f"+10,000: capped at 10,000 x 0.5 / 0.04 = 125,000 (got {n:,.0f})")

rm, st = fresh()
closed(rm, st, 10_000)
st.open_symbols, st.exposure = {"SOLTMN"}, 100_000
n, _ = size(rm, st)
check(round(n) == 75_000, f"+10,000 with 100,000 open: (10,000 - 4,000) x 0.5 / 0.04 = 75,000 (got {n:,.0f})")

rm, st = fresh()
closed(rm, st, -5_000)
n, _ = size(rm, st)
check(n > 900_000, f"behind by 5,000: guard off, normal size ({n:,.0f})")

rm, st = fresh()                                     # restart while behind: the state starts from the start capital
rm.mark(st, 4_980_000, NOW)
closed(rm, st, 1_500)
n, _ = size(rm, st)
check(n > 900_000, f"restart at -20,000, then a +1,500 win: still behind, normal size ({n:,.0f})")

rm, st = fresh()
rm.mark(st, 5_006_000, NOW)                          # restart while ahead: the gain is in the marked equity
n, _ = size(rm, st)
check(round(n) == 75_000, f"restart at +6,000: capped at 75,000 (got {n:,.0f})")

rm, st = fresh()                                     # marked high with a position up, then it stops out
st.open_symbols, st.exposure = {"SOLTMN"}, 300_000
rm.mark(st, 5_030_000, NOW)
st.exposure = 0
rm.on_exit(st, "SOLTMN", -12_000, NOW)
n1, why1 = size(rm, st)
rm.mark(st, 4_988_000, NOW)
n2, _ = size(rm, st)
check(n1 == 0 and "waiting" in why1[-1] and n2 > 900_000,
      f"stale +30,000 mark, then a -12,000 stop: waits, then normal size at -12,000 ({n2:,.0f})")

rm, st = fresh()
rm.mark(st, START + 0.4, NOW)                        # rounding noise is not a gain
n, _ = size(rm, st)
check(round(n) == round(base), f"equity +0.4 toman: guard off ({n:,.0f})")

rm, st = fresh({**P, "protect_gain_share": 0, "contest_loss_budget": 0})
rm.on_exit(st, "ETHTMN", 10_000, NOW)
n, _ = size(rm, st)
check(round(n) == round(base), f"share 0 and no loss budget: no guard and no wait after a close ({n:,.0f})")

worst, ok, cases = P["protect_gain_worst_loss"], True, 0
for gain in range(-50_000, 400_001, 2_500):
    for exposure in (0, 50_000, 300_000, 900_000):
        rm, st = fresh()
        rm.mark(st, START + gain, NOW)
        st.exposure = exposure
        st.open_symbols = {"SOLTMN"} if exposure else set()
        g, _ = size(rm, st)
        rm0, st0 = fresh({**P, "protect_gain_share": 0})
        rm0.mark(st0, START + gain, NOW)
        st0.exposure, st0.open_symbols = st.exposure, set(st.open_symbols)
        n0, _ = size(rm0, st0)
        cases += 1
        if g > n0 + 1e-6 or (g > 0 and gain >= P["protect_gain_min_toman"] and gain - worst * (exposure + g) < 0):
            ok = False
            print(f"   gain {gain:,} exposure {exposure:,}: guard {g:,.0f}, no guard {n0:,.0f}")
check(ok, f"sweep of {cases} cases: never larger than without the guard, and a {worst:.0%} loss on everything open never erases the gain")

print("all passed" if not fails else f"{fails} failed")
sys.exit(1 if fails else 0)
