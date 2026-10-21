"""Independent re-check of the live run (pure Python, low memory; reads MAIN read-only).

1. Per market stats of the live ticks (recorded buy_dev, spread, baseline).
2. My own trailing 180-min median baseline from shadow_v2..v6 + live_main ticks -> my own buy_dev.
3. Policy timeline from decisions.jsonl (start = default policy + 3 min warmup; policy_review replaces)
   and a gate walk: how many ticks crossed the threshold that was actually in force.
"""
import bisect
import json
import sys
from collections import defaultdict
from datetime import datetime

MAIN = "C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot"
RUNS = MAIN + "/runs"
SYMS = ["BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN", "PEPETMN", "BNBTMN", "ARBTMN"]


def read_ticks(path):
    with open(path, encoding="utf-8") as fh:
        txt = fh.read()
    lines = txt.split("\n")
    if not txt.endswith("\n"):
        lines = lines[:-1]          # partial last line
    hdr = lines[0].split(",")
    out = []
    for ln in lines[1:]:
        p = ln.split(",")
        if len(p) != len(hdr):
            continue
        try:
            r = dict(zip(hdr, p))
            out.append((float(r["t"]), r["symbol"], float(r["bid"]), float(r["ask"]), float(r["bid_qty"]),
                        float(r["ask_qty"]), float(r["fair"]), float(r["baseline"]), float(r["buy_dev"])))
        except ValueError:
            continue
    return out


def ts(s):
    return datetime.fromisoformat(s).timestamp()


def q(xs, p):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    k = (len(xs) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


live = read_ticks(RUNS + "/live_main/ticks.csv")
hist = []
for r in ["shadow_v2", "shadow_v3", "shadow_v4", "shadow_v5", "shadow_v6"]:
    try:
        hist += read_ticks(f"{RUNS}/{r}/ticks.csv")
    except FileNotFoundError:
        pass
t0, t1 = live[0][0], live[-1][0]
print(f"live ticks rows={len(live)} from {datetime.utcfromtimestamp(t0)} to {datetime.utcfromtimestamp(t1)} UTC "
      f"= {(t1 - t0) / 3600:.3f} h")

# ---- decisions: policy timeline -------------------------------------------------------------
events = []
with open(RUNS + "/live_main/decisions.jsonl", encoding="utf-8") as fh:
    for ln in fh:
        try:
            events.append(json.loads(ln))
        except Exception:
            pass
timeline = []   # (t, kind, payload)
for e in events:
    if e["event"] == "start":
        d = e["policy"]["TMN"]
        timeline.append((ts(e["t"]), "start", {s: (d["enabled"], d["entry_dev"]) for s in SYMS}))
    elif e["event"] == "policy_review" and e.get("policies"):
        timeline.append((ts(e["t"]), "review", {s: (p["enabled"], p["entry_dev"]) for s, p in e["policies"].items()}))
timeline.sort()
WARM = 3 * 60


def policy_at(t):
    pol, warm_until = None, None
    for te, kind, p in timeline:
        if te > t:
            break
        if kind == "start":
            pol, warm_until = dict(p), te + WARM
        else:
            pol = dict(p)
    return pol, warm_until


# ---- my own baseline ---------------------------------------------------------------------------
def my_buy_devs(sym):
    rows = sorted([r for r in hist + live if r[1] == sym], key=lambda r: r[0])
    win = []      # sorted premiums
    dq = []       # (t, prem) in time order
    head = 0
    out = {}
    for r in rows:
        t, _, bid, ask, _, _, fair, _, _ = r
        prem = (bid + ask) / 2 / fair - 1
        dq.append((t, prem))
        bisect.insort(win, prem)
        while dq[head][0] < t - 180 * 60:
            old = dq[head][1]
            win.pop(bisect.bisect_left(win, old))
            head += 1
        n = len(win)
        med = win[n // 2] if n % 2 else (win[n // 2 - 1] + win[n // 2]) / 2
        if t0 <= t <= t1:
            out[t] = (ask / fair - 1 - med, n)
    return out


res = {}
THR = [0.004, 0.005, 0.006, 0.007]
print("\nmarket | n | min_bd% (rec) | p1% | p50% | min-at | min/<= -0.4/-0.5/-0.6/-0.7 | spread med/p99 % | %spread>0.6 "
      "| base first->last % | my min_bd% | max|rec-my| bp | gate: warm/disabled/thr_fail/thr_pass")
for s in SYMS:
    R = [r for r in live if r[1] == s]
    bd = [r[8] for r in R]
    sp = [r[3] / r[2] - 1 for r in R]
    mins = [sum(1 for x in bd if x <= -th) * 2.5 / 60 for th in THR]
    imin = min(range(len(bd)), key=lambda i: bd[i])
    mine = my_buy_devs(s)
    diffs = [abs(r[8] - mine[r[0]][0]) for r in R if r[0] in mine]
    my_min = min(v[0] for v in mine.values())
    # gate walk
    warm = dis = fail = ok = 0
    cross06 = cross05 = 0
    passes = []
    for r in R:
        pol, wu = policy_at(r[0])
        if pol is None or r[0] < wu:
            warm += 1
            continue
        en, thr = pol[s]
        if not en:
            dis += 1
            continue
        if r[8] <= -thr:
            ok += 1
            passes.append((r[0], r[8], thr, r[3] / r[2] - 1))
        else:
            fail += 1
    res[s] = {"n": len(R), "min_buy_dev_pct": round(bd[imin] * 100, 3),
              "min_at_utc": str(datetime.utcfromtimestamp(R[imin][0])),
              "p1_pct": round(q(bd, 0.01) * 100, 3), "p50_pct": round(q(bd, 0.5) * 100, 3),
              "minutes_below": dict(zip([f"-{th*100:.1f}%" for th in THR], [round(m, 2) for m in mins])),
              "spread_med_pct": round(q(sp, 0.5) * 100, 3), "spread_p99_pct": round(q(sp, 0.99) * 100, 3),
              "pct_spread_gt_0.6": round(100 * sum(1 for x in sp if x > 0.006) / len(sp), 2),
              "baseline_first_last_pct": [round(R[0][7] * 100, 3), round(R[-1][7] * 100, 3)],
              "my_min_buy_dev_pct": round(my_min * 100, 3),
              "max_abs_diff_rec_vs_my_bp": round(max(diffs) * 1e4, 2) if diffs else None,
              "median_abs_diff_bp": round(q(diffs, 0.5) * 1e4, 2) if diffs else None,
              "gate": {"warmup_or_no_policy": warm, "disabled": dis, "threshold_fail": fail, "threshold_pass": ok},
              "passes": passes[:10]}
    x = res[s]
    print(f"{s} | {x['n']} | {x['min_buy_dev_pct']} | {x['p1_pct']} | {x['p50_pct']} | {x['min_at_utc'][11:19]} | "
          f"{'/'.join(str(v) for v in x['minutes_below'].values())} | {x['spread_med_pct']}/{x['spread_p99_pct']} | "
          f"{x['pct_spread_gt_0.6']} | {x['baseline_first_last_pct']} | {x['my_min_buy_dev_pct']} | "
          f"{x['max_abs_diff_rec_vs_my_bp']} (med {x['median_abs_diff_bp']}) | "
          f"{x['gate']['warmup_or_no_policy']}/{x['gate']['disabled']}/{x['gate']['threshold_fail']}/{x['gate']['threshold_pass']}")

# time gaps
tt = sorted(set(r[0] for r in live))
gaps = sorted(((b - a), a) for a, b in zip(tt, tt[1:]))[-5:]
print("\nlargest tick gaps (s, at):", [(round(g, 1), str(datetime.utcfromtimestamp(a))[11:19]) for g, a in gaps])
print("timeline:", [(str(datetime.utcfromtimestamp(t))[11:19], k, {s: v for s, v in p.items()}) for t, k, p in timeline])

# shadow majors: min buy_dev (recorded) per market
print("\nshadow_v2..v6 recorded min buy_dev % per market (n ticks):")
for s in SYMS:
    b = [r[8] for r in hist if r[1] == s]
    if b:
        print(f"  {s}: {min(b) * 100:.3f} (n={len(b)}, {len(b) * 2.5 / 3600:.2f} h)")
json.dump({"live_span_h": round((t1 - t0) / 3600, 3), "markets": res}, open(sys.argv[1] if len(sys.argv) > 1 else
          "live_check.json", "w"), indent=1, default=str)
