"""Audit 4: 45 h gain of size 0.7 / 1.0 over the 0.4 defensive cap under alternative edge/rate assumptions
(linear model as in ai_policy.py: trades x mean x filled notional; pooled fill from the author's 0.3% episodes)."""
import json
AUD = "C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot-ultra/research_ultra/sizing_depth/verify_audit/out"
a2 = json.load(open(f"{AUD}/a2_robust.json", encoding="utf-8"))
DAYS, H, EQ = 6.849, 45.0, 5_000_000
FILL = {0.4: 0.938, 0.7: 0.914, 1.0: 0.899}
PLAN = {0.4: 827586, 0.7: 1448276, 1.0: 1750000}
scen = {
 "author base (69 tr, +0.230%)": (69, 0.230),
 "base rate, mean without top-5 trades (+0.106%)": (69, a2["base_stats"]["mean_without_top5"]),
 "full-spread entry (51 tr, +0.159%)": (51, 0.159),
 "full-spread entry + no USDT gap (33 tr, +0.256%)": (a2["fullentry_nogap"]["trades"], a2["fullentry_nogap"]["mean"]),
 "1-min delay, signal must persist (14 tr, +0.077%)": (a2["delay1_recheck"]["trades"], a2["delay1_recheck"]["mean"]),
 "author rate x0.29 (base mean)": (69 * 0.2896, 0.230),
 "edge -0.20%/trade (downside if live edge is negative), base rate": (69, -0.20),
}
out = {}
for k, (n, m) in scen.items():
    tr45 = n / DAYS * H / 24
    r = {f: tr45 * m / 100 * PLAN[f] * FILL[f] / EQ * 100 for f in (0.4, 0.7, 1.0)}
    out[k] = {"trades_45h": round(tr45, 1), "ret_0.4_pct": round(r[0.4], 3), "gain_0.7_vs_0.4_pp": round(r[0.7] - r[0.4], 3),
              "gain_1.0_vs_0.4_pp": round(r[1.0] - r[0.4], 3)}
    print(k, out[k])
json.dump(out, open(f"{AUD}/a4_cost_range.json", "w", encoding="utf-8"), indent=1)
