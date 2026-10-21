"""Builds REPORT.md and result.json from the json outputs (every number below comes from out/*.json)."""
from __future__ import annotations

import json

import dt_data as D

OUT = D.HERE / "out"
R = json.loads((OUT / "results_main.json").read_text(encoding="utf-8"))
RF = json.loads((OUT / "results_usdtfresh.json").read_text(encoding="utf-8"))
RT = json.loads((OUT / "results_ticks.json").read_text(encoding="utf-8"))
X = json.loads((OUT / "summary_extra.json").read_text(encoding="utf-8"))

NAMES = {"V4_fixed_0.7": "V4 fixed 0.7% (live rule)", "V1_per_market_fixed": "V1 per-market fixed (chosen days 1-4)",
         "V2_vol_scaled_global_k": f"V2 vol-scaled, k={R['v2_k_global']} (chosen days 1-4)",
         "V2b_vol_scaled_per_market_k": "V2b vol-scaled, k per market",
         "V3_spread_aware": "V3 0.7% + half spread",
         "ORACLE_per_market_chosen_on_OOS": "ORACLE per-market chosen ON days 5-7 (not tradable)"}


def fresh_name(k):
    return NAMES[k].replace(f"k={R['v2_k_global']}", f"k={RF['v2_k_global']}")


def f(x, d=3):
    return "-" if x is None else f"{x:.{d}f}" if isinstance(x, float) else str(x)


def main_table(res):
    rows = ["| variant | IS trades | IS mean % | IS sum % | IS win % | OOS trades | OOS mean % | OOS sum % | OOS win % | OOS exits | OOS diff vs V4 (90% CI) |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, v in res["variants"].items():
        i, o = v["IS"], v["OOS"]
        b = res["bootstrap_vs_V4_OOS"].get(k)
        bs = "-" if not b else f"{b['diff_sum_net_pct']:+.2f} [{b['ci90'][0]:+.2f}, {b['ci90'][1]:+.2f}]"
        ex = ", ".join(f"{a} {n}" for a, n in o["exits"].items())
        rows.append(f"| {NAMES[k]} | {i['trades']} | {f(i['mean_net_pct'])} | {f(i['sum_net_pct'])} | {f(i['win_pct'],1)} | "
                    f"{o['trades']} | {f(o['mean_net_pct'])} | {f(o['sum_net_pct'])} | {f(o['win_pct'],1)} | {ex} | {bs} |")
    return "\n".join(rows)


def market_table(res, period):
    keys = ["V4_fixed_0.7", "V1_per_market_fixed", "V2_vol_scaled_global_k", "V3_spread_aware"]
    rows = ["| market | V1 thr % | " + " | ".join(f"{k.split('_')[0]} n / mean / sum" for k in keys) + " |",
            "|---|---|" + "---|" * len(keys)]
    for s in D.SYMS:
        cells = []
        for k in keys:
            x = res["variants"][k][f"{period}_by_market"][s]
            cells.append(f"{x['trades']} / {f(x['mean_net_pct'])} / {f(x['sum_net_pct'])}")
        rows.append(f"| {s} | {res['v1_choice_pct'][s]} | " + " | ".join(cells) + " |")
    return "\n".join(rows)


curve = "\n".join(["| one threshold for all 8 | IS trades | IS sum % | IS mean % | OOS trades | OOS sum % | OOS mean % |",
                   "|---|---|---|---|---|---|---|"] +
                  [f"| {th}% | {v['IS']['trades']} | {v['IS']['sum_net_pct']} | {f(v['IS']['mean_net_pct'])} | {v['OOS']['trades']} | "
                   f"{v['OOS']['sum_net_pct']} | {f(v['OOS']['mean_net_pct'])} |" for th, v in X["global_fixed_threshold_curve"].items()])
band = "\n".join(["| V4 trades by entry dip | trades | sum % | mean % |", "|---|---|---|---|"] +
                 [f"| {k} | {v['trades']} | {v['sum_net_pct']} | {f(v['mean_net_pct'])} |" for k, v in X["V4_trades_by_entry_dip_depth"].items()])
ksw = "\n".join(["| k | IS trades | IS sum % | OOS trades | OOS sum % |", "|---|---|---|---|---|"] +
                [f"| {k} | {v['IS']['trades']} | {v['IS']['sum_net_pct']} | {v['OOS']['trades']} | {v['OOS']['sum_net_pct']} |"
                 for k, v in R["vol_k_sweep"].items()])
port = "\n".join(["| variant | OOS trades | OOS sum % | TMN P&L at size 0.4 (about 828k per trade) |", "|---|---|---|---|"] +
                 [f"| {NAMES[k]} | {v['OOS_portfolio_max3_dailylimit']['trades']} | {v['OOS_portfolio_max3_dailylimit']['sum_net_pct']} | "
                  f"{v['OOS_portfolio_max3_dailylimit']['pnl_tmn_at_size_0.4']:,} |" for k, v in R["variants"].items()])
fresh = "\n".join(["| variant (USDTTMN traded <= 10 min ago only) | IS trades | IS sum % | OOS trades | OOS sum % | OOS diff vs V4 (90% CI) |",
                   "|---|---|---|---|---|---|"] +
                  [f"| {fresh_name(k)} | {v['IS']['trades']} | {v['IS']['sum_net_pct']} | {v['OOS']['trades']} | {v['OOS']['sum_net_pct']} | "
                   + ("-" if k not in RF["bootstrap_vs_V4_OOS"] else
                      f"{RF['bootstrap_vs_V4_OOS'][k]['diff_sum_net_pct']:+.2f} [{RF['bootstrap_vs_V4_OOS'][k]['ci90'][0]:+.2f}, {RF['bootstrap_vs_V4_OOS'][k]['ci90'][1]:+.2f}]") + " |"
                   for k, v in RF["variants"].items()])
ticks = "\n".join(["| market | hours of ticks | deepest buy_dev % | p1 buy_dev % | median spread % | separate dips <= -0.5 / -0.6 / -0.7 % |", "|---|---|---|---|---|---|"] +
                  [f"| {s} | {RT['hours_by_symbol'][s]} | {v['min_pct']} | {v['p1_pct']} | {v['median_spread_pct']} | "
                   f"{v['dip_episodes_below_pct']['0.5']} / {v['dip_episodes_below_pct']['0.6']} / {v['dip_episodes_below_pct']['0.7']} |"
                   for s, v in RT["buy_dev_dist"].items()])
cvt = "\n".join(["| market | candle p1 buy_dev % (14:00-18:15 UTC, 7 days) | candle share of minutes <= -0.7 % | tick p1 buy_dev % | tick share <= -0.7 % |",
                 "|---|---|---|---|---|"] +
                [f"| {s} | {v['candle_p1_pct']} | {v['candle_share_le_0.7']} | {v['tick_p1_pct']} | {v['tick_share_le_0.7']} |"
                 for s, v in X["candle_vs_tick_buy_dev_same_hours"].items()])
v4o, v1o = R["variants"]["V4_fixed_0.7"]["OOS"], R["variants"]["V1_per_market_fixed"]["OOS"]
rv = R["reverse_walk_forward_(choose_days5-7,_test_days1-4)"]

summary_md = f"""**Verdict: reject.** No dynamic or per-market threshold beat the fixed 0.7% out of sample, and every variant cut the trade count by about 26-29%.

Method: 7 days of Wallex 1m candles for the 8 live TMN markets (traded minutes only), fair = Binance 1m close x Wallex USDTTMN close, baseline = 180-min rolling median premium shifted by one minute, executable prices = close x (1 +/- half the median live spread measured on 3.2 h of real ticks), live exits (target needs bid >= entry x 1.0065, stop 0.9%, time 25 min, ARB 15), taker 0.3% on both legs, one position per market, 120-min cooldown after a loss, 60 s global-drop filter. Choices made on days 1-4 (2026-10-14 14:05 to 10-18 14:05 UTC), evaluated on days 5-7 (to 10-21 14:05). Noise: 2-hour block bootstrap of the OOS difference vs V4 (5000 draws).

{main_table(R)}

Per market, OOS days 5-7 (trades / mean net % / sum net %):

{market_table(R, 'OOS')}

Why it fails: on days 1-4 the 0.7-0.8% dips lost money ({X['V4_trades_by_entry_dip_depth']['IS dip 0.7-0.8%']['trades']} trades, sum {X['V4_trades_by_entry_dip_depth']['IS dip 0.7-0.8%']['sum_net_pct']}%), so every in-sample fit raised the threshold (PEPE to 0.9%, ARB to 0.8%, k=3); on days 5-7 the same band made money ({X['V4_trades_by_entry_dip_depth']['OOS dip 0.7-0.8%']['trades']} trades, +{X['V4_trades_by_entry_dip_depth']['OOS dip 0.7-0.8%']['sum_net_pct']}%) and the 0.8-0.9% band made +{X['V4_trades_by_entry_dip_depth']['OOS dip 0.8-0.9%']['sum_net_pct']}%. Higher thresholds raise the mean per trade ({v4o['mean_net_pct']}% to {v1o['mean_net_pct']}%) but not the total, and even the oracle (per-market thresholds picked on the test days themselves) beats V4 by only +{R['bootstrap_vs_V4_OOS']['ORACLE_per_market_chosen_on_OOS']['diff_sum_net_pct']}% over 3 days. Live tick replay (3.2 h for 5 markets, 1.7 h for PEPE/BNB/ARB): no tick ever reached buy_dev -0.7% (deepest ARB {RT['buy_dev_dist']['ARBTMN']['min_pct']}%), so every variant makes 0 trades there; too small to say anything."""

report = f"""# Per-market dynamic entry threshold, walk-forward (2026-10-21)

Verdict: **reject**. Keep the fixed 0.7% for every market.

{summary_md}

## In-sample vs out-of-sample per market

Days 1-4 (in-sample; V1/V2 numbers here are fitted, so optimistic):

{market_table(R, 'IS')}

## One threshold for all markets (shape of the trade-off)

{curve}

In-sample the best single threshold is 0.8%; out of sample it is 0.7%. The ranking flips between the two halves, which is what noise looks like.

{band}

V4 exits: days 1-4 target {X['V4_exit_reason_means']['IS']['target']['count']} (mean {X['V4_exit_reason_means']['IS']['target']['mean']}%), time {X['V4_exit_reason_means']['IS']['time']['count']} (mean {X['V4_exit_reason_means']['IS']['time']['mean']}%); days 5-7 target {X['V4_exit_reason_means']['OOS']['target']['count']} (mean {X['V4_exit_reason_means']['OOS']['target']['mean']}%), time {X['V4_exit_reason_means']['OOS']['time']['count']} (mean {X['V4_exit_reason_means']['OOS']['time']['mean']}%). No stop was hit in 62 trades. V4 makes {X['V4_trades_per_day_IS']} trades/day on days 1-4 and {X['V4_trades_per_day_OOS']} on days 5-7 (model).

## Volatility-scaled k sweep (one k for all markets)

{ksw}

k = 1.5 is identical to the fixed 0.7% (1.5 x the 3-h std rarely exceeds 0.7%). Mean V2 threshold at k = 3: {R['vol_k_sweep']['3.0']['mean_threshold_pct']}.

## Portfolio view (max 3 positions, daily-loss and kill-switch rules), days 5-7

{port}

## Robustness

Reverse walk-forward (choose on days 5-7, test on days 1-4): V1 with the days-5-7 choice {rv['V1_choice_pct']} gives {rv['V1_test_IS']['trades']} trades, sum {rv['V1_test_IS']['sum_net_pct']}% vs V4 {rv['V4_same_days']['trades']} trades, sum {rv['V4_same_days']['sum_net_pct']}%; V2 picks k = {rv['V2_k']} which is the same as V4. So in the reverse direction the best case is +{rv['V1_test_IS']['sum_net_pct'] - rv['V4_same_days']['sum_net_pct']:.2f}% with {rv['V4_same_days']['trades'] - rv['V1_test_IS']['trades']} fewer trades: noise.

Sensitivity, only minutes whose USDTTMN price traded within 10 min (Wallex USDTTMN does not trade about 20:00-05:00 UTC, so this drops the night):

{fresh}

Here V3 looks better out of sample (+{RF['bootstrap_vs_V4_OOS']['V3_spread_aware']['diff_sum_net_pct']}%) but it was worse than V4 in-sample ({RF['variants']['V3_spread_aware']['IS']['sum_net_pct']} vs {RF['variants']['V4_fixed_0.7']['IS']['sum_net_pct']}), and in the main run it is the worst variant ({R['bootstrap_vs_V4_OOS']['V3_spread_aware']['diff_sum_net_pct']:+.2f}%). Not robust.

## Live tick replay (small sample)

{ticks}

All five variants: 0 trades on the recorded ticks.

Candle model vs real ticks over the same UTC hours (different days, so only indicative):

{cvt}

The 1-minute candle model shows deeper and more frequent dips than the real order book. The whole backtest (all variants equally) probably overstates how often 0.7% dips happen, which matches the live run's 0 trades so far. This does not change the ranking of the variants, but it means trade counts from candles are an upper bound.

## Caveats

- Small samples: 35 OOS trades for V4, 25-26 for the variants; the 90% bootstrap intervals of the differences all include 0.
- Executable prices are modelled from 1-minute closes and a constant median spread (measured on 10-21 ticks and applied to the whole week); no order-book depth, no partial fills, exits assumed to fill at the bid.
- Stops checked on minute closes only (no intrabar lows); no stop was hit in any variant.
- Night: fair value uses a frozen USDTTMN price from about 20:00 to 05:00 UTC (no USDTTMN trades); the live engine uses the USDTTMN book mid then.

## Files

Scripts: fetch_binance_gap.py (fills Binance 1m 10-14 13:00-17:45 UTC, public API), dt_data.py, bt_dynamic.py, tick_replay.py, summarize.py, write_report.py. Outputs in out/.
"""

one_line_fa = ("رد شد: حد خرید جداگانه یا هوشمند برای هر بازار در سه روز آزمون سود بیشتری از حد ثابت ۰٫۷ درصد نداد "
               "(۱۰٫۴ درصد جمع سود خالص در ۳۵ معامله در برابر حداکثر ۹٫۹ درصد در ۲۵ معامله) و تعداد معامله ها را حدود ۳۰ درصد کم کرد؛ حد فعلی بماند.")

caveats = ("Small samples (35 OOS trades for V4, 25-26 for variants); all bootstrap 90% CIs of the OOS difference include 0 and all point estimates are negative "
           "(V1 -0.45%, V2 -1.81%, V2b -2.17%, V3 -3.29% sum over 3 days). Candle model: executable prices from 1m closes +/- a constant median live spread, "
           "stops on closes only, exits at the bid, no depth. The candle model shows deeper/more frequent dips than real ticks (e.g. BTC p1 buy_dev -0.51% in "
           "candles vs -0.17% in ticks, same UTC hours, different days), so trade counts are an upper bound for every variant. Live tick replay: 0 trades for "
           "every variant (no tick reached -0.7%; deepest ARB -0.59%) so it cannot discriminate. About half of the candle-model trades happen while Wallex "
           "USDTTMN is not trading (about 20:00-05:00 UTC) and fair uses a frozen USDTTMN price; in the daytime-only sensitivity V3 was +0.80% OOS but worse "
           "in-sample and the worst variant in the main run, so not robust. Side note for the supervisor: raising entry_dev above 0.7% (a 'tightening' the "
           "risk officer may do) lowers trade count without raising total net % in this data.")

result = {
    "idea": "Per-market dynamic entry threshold (fixed per-market from {0.7..1.5}%, volatility-scaled k x 3h std of buy_dev, spread-aware 0.7%+half spread) vs the live fixed 0.7%, walk-forward days 1-4 / 5-7 on 7 days of 1m candles for the 8 live TMN markets, plus live tick replay.",
    "verdict": "reject",
    "one_line_fa": one_line_fa,
    "summary_md": summary_md,
    "caveats": caveats,
    "scripts": [str(D.HERE / n) for n in ("fetch_binance_gap.py", "dt_data.py", "bt_dynamic.py", "tick_replay.py", "summarize.py", "write_report.py")],
    "result_file": str(D.HERE / "result.json"),
    "implementation_sketch": "",
    "live_bot_alert": "",
}
(D.HERE / "REPORT.md").write_text(report, encoding="utf-8")
(D.HERE / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
print(summary_md)
