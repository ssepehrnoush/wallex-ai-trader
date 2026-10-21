"""Print the markdown tables used in REPORT.md from the saved summaries (no new computation)."""
import json
from pathlib import Path
OUT = Path(__file__).resolve().parent
SYMS = ["BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN", "PEPETMN", "BNBTMN", "ARBTMN"]
ARMS = ["cur", "cur_thr", "cur_cont", "a", "b", "c", "a_raw", "aT", "bT"]


def ex(d):
    return " ".join(f"{k}:{v}" for k, v in sorted(d.get("exits", {}).items()))


for tag in ("", "_wide"):
    r = json.loads((OUT / f"candles_summary{tag}.json").read_text(encoding="utf-8"))
    print(f"\n### candles, spread set = {r['spread_set']}  spreads% {r['spreads_pct_used']}")
    for mode in ("pessimistic", "optimistic"):
        print(f"\n#### paired, same-minute ordering = {mode}")
        print("| arm | period | n | maker fill % | mean net % | sum net % | median % | win % | avg hold min | exits |")
        print("|---|---|---|---|---|---|---|---|---|---|")
        for arm in ARMS:
            for per in ("all", "d1-4", "d5-7"):
                x = r["paired"][mode][arm][per]
                if x["n"] == 0:
                    continue
                print(f"| {arm} | {per} | {x['n']} | {x['maker_fill_pct']} | {x['mean_net_pct']} | {x['sum_net_pct']} | {x['median_net_pct']} | {x['win_pct']} | {x['avg_hold_min']} | {ex(x)} |")
    print("\n#### per market (paired, pessimistic): n / mean net % / exits")
    print("| market | " + " | ".join(ARMS) + " |")
    print("|---|" + "---|" * len(ARMS))
    for s in SYMS:
        cells = []
        for arm in ARMS:
            x = r["paired"]["pessimistic"][arm]["per_market"][s]
            cells.append("0" if x["n"] == 0 else f"{x['n']} / {x['mean_net_pct']} / {ex(x)}")
        print(f"| {s} | " + " | ".join(cells) + " |")
    print("\n#### sequential (each arm its own entries)")
    print("| arm | n | maker fill % | mean net % | sum net % | win % | exits |")
    print("|---|---|---|---|---|---|---|")
    for arm in ARMS:
        x = r["sequential"][arm]["all"]
        print(f"| {arm} | {x['n']} | {x['maker_fill_pct']} | {x['mean_net_pct']} | {x['sum_net_pct']} | {x['win_pct']} | {ex(x)} |")
    print("\n#### paired difference per trade (maker arm minus taker reference), pessimistic")
    print("| reference | arm | mean diff % | median | t | bootstrap 95% CI | mean w/o 3 largest | better/worse/same | d1-4 | d5-7 |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for ref, d in r["paired_diff"].items():
        for arm in ("a", "b", "c", "aT", "bT"):
            v = d[arm]
            print(f"| {ref} | {arm} | {v['mean_diff_pct']} | {v['median_diff_pct']} | {v['t_stat']} | {v['boot95_ci_pct']} | {v['mean_diff_without_3_largest_abs_pct']} | {v['better']}/{v['worse']}/{v['same']} | {v['d1-4_mean_diff']} | {v['d5-7_mean_diff']} |")

t = json.loads((OUT / "ticks_summary.json").read_text(encoding="utf-8"))
print("\n### ticks")
print("hours", t["hours_total"], "runs", t["runs"])
print("min buy_dev % per market", t["min_buy_dev_pct_per_symbol"])
print("median spread %", t["median_spread_pct"])
print("calibration", t["calibration_target_crossings"])
print("| threshold | arm|rule | n | maker fill % | mean net % | sum % | win % | exits |")
print("|---|---|---|---|---|---|---|---|")
for th, d in t["by_threshold"].items():
    for k, v in d.items():
        x = v["all"]
        if x["n"] == 0:
            print(f"| {th} | {k} | 0 | | | | | |")
        else:
            print(f"| {th} | {k} | {x['n']} | {x['maker_fill_pct']} | {x['mean_net_pct']} | {x['sum_net_pct']} | {x['win_pct']} | {ex(x)} |")
