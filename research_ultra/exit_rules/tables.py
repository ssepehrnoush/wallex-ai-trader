"""Print markdown tables from the saved result JSONs (no new simulation)."""
import json
from pathlib import Path

D = Path(__file__).resolve().parent


def row(name, s):
    ex = ", ".join(f"{k} {v}" for k, v in sorted(s["exits"].items()))
    return f"| {name} | {s['n']} | {s['mean_pct']} | {s['sum_pct']:.2f} | {s['win_pct']} | {s['pnl_tmn']:,} | {ex} |"


H = "| config | trades | mean net % | sum net % | win % | TMN P&L | exits |\n|---|---|---|---|---|---|---|"

for suf, label in (("", "all 8 markets"), ("_excl_BNBTMN", "7 markets, BNB excluded (BNB is disabled live)")):
    g = json.loads((D / f"grid_wf_results{suf}.json").read_text())
    a = json.loads((D / f"alternatives_results{suf}.json").read_text())
    print(f"\n### Walk-forward fold A ({label}): choose on days 1-4, evaluate on days 5-7\n")
    print("| model | chosen on days 1-4 | OOS chosen: n / mean / sum / win | OOS current: n / mean / sum / win | OOS diff (sum pts) | 90% CI | P(diff>0) | rank of chosen OOS (of 80) |")
    print("|---|---|---|---|---|---|---|---|")
    for v, r in g.items():
        f = r["A_choose_d1-4_test_d5-7"]
        c, k, b = f["chosen_eval"], f["current_eval"], f["paired_bootstrap_eval_chosen_minus_current"]
        print(f"| {v} | {f['chosen']} | {c['n']} / {c['mean_pct']} / {c['sum_pct']:.2f} / {c['win_pct']} | {k['n']} / {k['mean_pct']} / {k['sum_pct']:.2f} / {k['win_pct']} "
              f"| {b['diff_sum_pct']:+.2f} | {b['ci90']} | {b['p_gt0']} | {f['chosen_rank_on_eval_of_80']} |")
    print(f"\n### Fold B ({label}): choose on days 5-7, evaluate on days 1-4\n")
    print("| model | chosen | eval chosen sum | eval current sum | diff | 90% CI |\n|---|---|---|---|---|---|")
    for v, r in g.items():
        f = r["B_choose_d5-7_test_d1-4"]
        b = f["paired_bootstrap_eval_chosen_minus_current"]
        print(f"| {v} | {f['chosen']} | {f['chosen_eval']['sum_pct']:.2f} (n {f['chosen_eval']['n']}) | {f['current_eval']['sum_pct']:.2f} (n {f['current_eval']['n']}) | {b['diff_sum_pct']:+.2f} | {b['ci90']} |")
    r = a["close_sp1.0"]
    print(f"\n### Single changes vs current, stop on 1-min close, spread x1 ({label})\n")
    print("| change | full 7d: n / mean / sum | diff full (pts) | day-block 90% CI | diff days 1-4 | diff days 5-7 | days 5-7 trade 90% CI |\n|---|---|---|---|---|---|---|")
    for name, v in r.items():
        f = v["full"]
        if "vs_current_full" not in v:
            print(f"| **{name}** | {f['n']} / {f['mean_pct']} / {f['sum_pct']:.2f} | - | - | - | - | - |")
            continue
        bf, bi, bo = v["vs_current_full"], v["vs_current_is"], v["vs_current_oos"]
        print(f"| {name} | {f['n']} / {f['mean_pct']} / {f['sum_pct']:.2f} | {bf.get('diff_sum_pct', 0):+.2f} | {bf.get('day_boot_ci90')} | {bi.get('diff_sum_pct', 0):+.2f} | {bo.get('diff_sum_pct', 0):+.2f} | {bo.get('trade_boot_ci90')} |")
    for half in ("full", "oos"):
        print(f"\n#### {half} detail ({label})\n\n{H}")
        for name in ("current_0.9_25_0.05", "wf_choice_0.9_45_0.00", "hold45_only", "hold15", "hold10", "stop0.6", "partialX0.3", "live_now_ARB_0.8_15"):
            print(row(name, r[name][half]))
    print(f"\n#### per market, days 5-7 ({label}): current vs WF choice (0.9/45/0.00)\n")
    print("| market | current n / mean / sum / exits | WF choice n / mean / sum / exits |\n|---|---|---|")
    for s in r["current_0.9_25_0.05"]["oos_per_market"]:
        c, w = r["current_0.9_25_0.05"]["oos_per_market"][s], r["wf_choice_0.9_45_0.00"]["oos_per_market"][s]
        print(f"| {s} | {c['n']} / {c['mean_pct']} / {c['sum_pct']:.2f} / {c['exits']} | {w['n']} / {w['mean_pct']} / {w['sum_pct']:.2f} / {w['exits']} |")
    print(f"\nmean by exit reason, current, full: {r['current_0.9_25_0.05']['full'].get('mean_by_exit')}; WF choice: {r['wf_choice_0.9_45_0.00']['full'].get('mean_by_exit')}")

t = json.loads((D / "replay_ticks_results.json").read_text())
print(f"\n### Tick replay {t['span']}\n")
print("| entry | config | n | mean | sum | win | exits |\n|---|---|---|---|---|---|---|")
for E, r in t["by_entry"].items():
    for name in ("current_0.9_25_0.05", "hold45", "wf_choice_0.9_45_0.00", "hold15", "hold10", "stop0.6", "partialX0.3"):
        s = r[name]
        print(f"| {E}% (signals {r['signal_ticks']}) | {name} | {s['n']} | {s['mean_pct']} | {s['sum_pct']} | {s['win_pct']} | {s['exits']} |")
