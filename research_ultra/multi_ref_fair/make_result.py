"""Assemble result.json (the structured answer) from the run outputs in ./out plus the written conclusions."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
bt = json.loads((OUT / "bt_multiref_summary.json").read_text(encoding="utf-8"))
tk = json.loads((OUT / "ticks_usdt_leg_summary.json").read_text(encoding="utf-8"))
lm = json.loads((OUT / "live_mid_check.json").read_text(encoding="utf-8"))

SUMMARY = r"""
**Verdict: reject (do not change the live engine).**
- **Part A, median of Binance, OKX and Bybit: inconclusive, harmless.** It changes nothing on 6 of 8 markets and only reshuffles about 10 threshold-edge trades on PEPE and ARB. Without one PEPE pump event, the net is +0.08% (bt exits) or -1.47% (live exits) over 7 days.
- **Part B, 5-min median of USDTTMN: reject.** Entries made during USDTTMN spikes were profitable (9 trades, mean +0.19%). Smoothing removes them and adds losers: -2.07% (bt) or -4.50% (live exits) without the outlier. The live USDTTMN mid never jumped more than 0.06% between ticks.

### Method
- **Data:** Wallex 1m candles from 2026-10-14 14:05 to 10-21 14:04 UTC (10,080 min), 8 TMN markets, minutes with volume > 0 only. Binance, OKX and Bybit 1m closes were fetched fresh (all reachable, 10,393 rows per base, no gaps). Binance 1s klines cover the tick period.
- **Backtest:** same logic as bt_fairvalue.py: entry at buy_dev <= -0.7%; exits target (sell_dev >= -0.05% and bid > entry), stop 0.9%, time 25 min (ARB 15); taker 0.3% on each leg; half the median live spread (frozen snapshot); baseline = rolling 180-min median, shift(1). Every run is repeated with the live break-even target guard (bid >= entry x 1.0065). No tuning; the split is the first 4 days vs the last 3 days.
- **Variants:** A0 = Binance x USDTTMN (current); A1 = median3 x USDTTMN; B1 = Binance x causal 5-min median USDTTMN; AB = both.
- **Ticks:** shadow_v2..v6 plus live_main, 3.4 h (13,166 + 18,944 TMN rows). The USDTTMN mid is exact on the shadow runs (fair XTMN / fair XUSDT). On live it is estimated from Binance 1s closes, with error p95 0.015% when validated on the shadow runs.

### Part A: Binance vs the 3-exchange median
| market | 1m close >0.05% | >0.1% | >0.2% | live MID >0.1% (n) | Binance mid step |
|---|---|---|---|---|---|
| BTC/ETH/SOL | 0 | 0 | 0 | 0 (377) | 0.003-0.010% |
| XRP | 0.04% | 0 | 0 | 0.27% | 0.008% |
| DOGE | 0.38% | 0.02% | 0 | 0.27% | 0.012% |
| BNB | 0.11% | 0 | 0 | 0 (189) | 0.007% |
| ARB | 6.6% | 0.26% | 0 | 0.53% (189) | 0.051% |
| PEPE | 65.1% | 42.5% | 8.5% | 10.6% (189), 0 >0.2% | 0.213% |

Binance's PEPE tick (1e-8) is 0.21% of the price, against 1e-9 on OKX and Bybit, so the live PEPE fair moves in 0.21% steps.

### Backtest, 7 days
| variant | n | mean % | sum % | win % | exits (target/time) | first 4d sum (n) | last 3d sum (n) | sum with live exits |
|---|---|---|---|---|---|---|---|---|
| A0 current | 69 | +0.229 | +15.80 | 62.3 | 54/15 | +6.37 (29) | +9.44 (40) | +15.88 |
| A1 median3 | 68 | +0.282 | +19.16 | 61.8 | 55/13 | +6.58 (26) | +12.58 (42) | +17.47 |
| B1 robust u | 65 | +0.262 | +17.04 | 60.0 | 50/15 | +6.07 (26) | +10.96 (39) | +14.48 |
| AB both | 63 | +0.297 | +18.69 | 60.3 | 50/13 | +6.97 (22) | +11.73 (41) | +16.17 |

A0 per market (n, mean %, sum %): BTC 3, +0.37, +1.11; ETH 0; SOL 3, +0.04, +0.11; XRP 4, +0.25, +1.00; DOGE 1, +0.36, +0.36; PEPE 22, +0.21, +4.53; BNB 13, +0.44, +5.69; ARB 23, +0.13, +3.00.

### Changed sets
**A0 to A1 (median3).** Only PEPE (net +1.58) and ARB (+1.14) change.
- Gone: 11 trades, sum +2.99, win 54.5%. New: 10 trades, sum +5.71, win 40%.
- Net: +3.35 (bt) and +1.60 (live exits). Most of it is one PEPE pump on 10-20 near 14:00: +5.41 at 13:59 appears and +2.78 at 14:02 disappears.
- **Without |ret| > 2%: +0.08 (bt) and -1.47 (live).** First 4 days: -0.21 (bt) and -0.31 (live).
- The removed entries where Binance was > 0.1% above the median were winners (6 trades, mean +0.25%), so "Binance-only creates false signals" is not supported.

**A0 to B1 (robust u).**
- Removed: 10 trades, sum +4.02, including 8 u-spike entries (sum +1.36, 62.5% win).
- Added: 6 trades, sum +4.63, win 16.7%: one +5.45 PEPE pump and 5 small losers.
- Net: +1.23 (bt) and -1.40 (live). **Without |ret| > 2%: -2.07 (bt) and -4.50 (live).** First 4 days: -0.72 (bt) and -2.89 (live).

### Part B on candles and ticks
- **1m candles.** USDTTMN traded in only 4,895 of 10,080 minutes. The raw close vs the 5-min median differs by > 0.1% in 2.64% of minutes and > 0.2% in 0.54%; max 1.21%.
- **A0 entries on a u up-spike:** > 0.1%: 9 trades, mean +0.19%, sum +1.74, 66.7% win (8 target, 1 time). > 0.2%: 8 trades, mean +0.17%.
- **Simultaneous entries:** 3 minutes had 2 or more markets entering together (7 trades, mean +0.11%, mean u spike 0.78%). They are spike-driven but not losers (small sample).
- **Live ticks.** The largest tick-to-tick USDTTMN mid jump was 0.059% (shadow, exact) and 0.053% (live, estimated). The largest deviation from the 5-min median was 0.23%, a slow drift, so a median would only add lag.
- **Signals on ticks.** 0 ticks reached buy_dev <= -0.7% with either fair, so the tick replay has 0 trades in both versions. No timestamp had 2 or more markets <= -0.4% at once.
- **One wider episode.** The only time 3 or more markets were <= -0.25% (BTC, ETH, DOGE, shadow_v2, 14:51 local) was a real global move: u dev +0.002%, Binance = median within 0.005%. The gap closed because the global price fell back (fair -0.37% in 60 s), not because Wallex rose.
"""

CAVEATS = r"""Small samples: 69 trades in 7 days and only 10-20 changed trades per comparison. A 0.01% change in one modelled spread moved Part A's net excluding the outlier from -0.63% to +0.08%, so the differences are noise-level.
The 1m backtest uses last-trade closes for Binance and USDTTMN. The live bot uses mids, which agree with the multi-exchange median far more often and have no USDTTMN spikes. The backtest therefore overstates both the problem and the fix.
Spreads are constant medians, there is no depth, and the 60 s global-drop filter is not modelled (same as bt_fairvalue.py).
The tick period is only 3.4 h and had no 0.7% signals at all.
The PC clock is about 40 s behind exchange time (local minus Binance server time = -40.15 s, minus OKX = -39.5 s; the tick-to-1s-kline alignment is best at +39 s). For the live bot this only shifts the no-new-entries and flatten moments about 40 s later, still about 74 min before the deadline, so it is harmless. Anyone aligning ticks.csv or decisions.jsonl with exchange data must add about 39 s.
REPORT.md could not be written: the harness refused report-file writes for this subagent. Its content is in summary_md."""

base = "C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot-ultra/research_ultra/multi_ref_fair/"
res = {
    "idea": "Fair value from the median of Binance, OKX and Bybit instead of Binance alone (Part A), and a robust USDT/TMN leg via a causal 5-minute median of the Wallex USDTTMN price (Part B)",
    "verdict": "reject",
    "verdict_per_part": {"A_multi_reference_median": "inconclusive (harmless, no measurable benefit)", "B_robust_usdttmn_leg": "reject"},
    "one_line_fa": "رد شد: میانهٔ قیمت سه صرافی فقط چند معاملهٔ PEPE و ARB را عوض کرد و بدون یک معاملهٔ استثنایی سودی نداشت (+۰٫۰۸٪ در ۷ روز)، و صاف کردن نرخ تتر به تومان ۲٫۰۷٪ هم بدتر شد.",
    "summary_md": SUMMARY.strip(),
    "caveats": CAVEATS.strip(),
    "scripts": [base + "fetch_refs.py", base + "bt_multiref.py", base + "ticks_usdt_leg.py", base + "live_mid_check.py", base + "make_result.py"],
    "result_file": base + "result.json",
    "implementation_sketch": "",
    "live_bot_alert": "",
    "key_numbers": {
        "backtest_bt_exits": {v: bt["backtest_bt_exits"][v]["all"] for v in ("A0", "A1", "B1", "AB")},
        "backtest_live_exits": {v: bt["backtest_live_breakeven_exits"][v]["all"] for v in ("A0", "A1", "B1", "AB")},
        "changes_bt_exits": {k: {kk: vv for kk, vv in x.items() if not kk.endswith("_list")} for k, x in bt["changes_bt_exits"].items()},
        "changes_live_exits": bt["changes_live_exits"],
        "partA_reference_divergence_1m": bt["partA_reference_divergence"],
        "partA_live_mid_check": lm,
        "partB_usdttmn_leg_1m": bt["partB_usdttmn_leg"],
        "partB_A0_entries_on_usdttmn_up_spike": bt["A0_entries_on_usdttmn_up_spike"],
        "partB_A0_simultaneous_entries": bt["A0_simultaneous_entries"],
        "partB_ticks_leg_per_run": tk["usdttmn_leg_per_run"],
        "partB_tick_signal_counts": tk["tick_signal_counts"],
        "partB_tick_simultaneous_episodes": {k: {kk: vv for kk, vv in x.items() if kk != "rows"} for k, x in tk["simultaneous_episodes"].items()},
        "u_estimator_lag_s": tk["chosen_lag_s"],
        "spreads_used_pct": bt["spreads_used_pct"],
    },
}
(HERE / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
print("ok", len(json.dumps(res, ensure_ascii=False, default=str)))
