# Per-market dynamic entry threshold, walk-forward (2026-10-21)

Verdict: **reject**. Keep the fixed 0.7% for every market.

**Verdict: reject.** No dynamic or per-market threshold beat the fixed 0.7% out of sample, and every variant cut the trade count by about 26-29%.

Method: 7 days of Wallex 1m candles for the 8 live TMN markets (traded minutes only), fair = Binance 1m close x Wallex USDTTMN close, baseline = 180-min rolling median premium shifted by one minute, executable prices = close x (1 +/- half the median live spread measured on 3.2 h of real ticks), live exits (target needs bid >= entry x 1.0065, stop 0.9%, time 25 min, ARB 15), taker 0.3% on both legs, one position per market, 120-min cooldown after a loss, 60 s global-drop filter. Choices made on days 1-4 (2026-10-14 14:05 to 10-18 14:05 UTC), evaluated on days 5-7 (to 10-21 14:05). Noise: 2-hour block bootstrap of the OOS difference vs V4 (5000 draws).

| variant | IS trades | IS mean % | IS sum % | IS win % | OOS trades | OOS mean % | OOS sum % | OOS win % | OOS exits | OOS diff vs V4 (90% CI) |
|---|---|---|---|---|---|---|---|---|---|---|
| V4 fixed 0.7% (live rule) | 27 | 0.218 | 5.878 | 70.4 | 35 | 0.297 | 10.397 | 77.1 | target 22, time 13 | - |
| V1 per-market fixed (chosen days 1-4) | 16 | 0.529 | 8.472 | 87.5 | 25 | 0.398 | 9.944 | 76.0 | target 17, time 8 | -0.45 [-3.07, +2.48] |
| V2 vol-scaled, k=3.0 (chosen days 1-4) | 18 | 0.453 | 8.153 | 83.3 | 25 | 0.343 | 8.585 | 76.0 | target 17, time 8 | -1.81 [-5.52, +1.63] |
| V2b vol-scaled, k per market | 19 | 0.455 | 8.649 | 84.2 | 26 | 0.316 | 8.228 | 73.1 | target 17, time 9 | -2.17 [-6.06, +1.36] |
| V3 0.7% + half spread | 20 | 0.298 | 5.962 | 75.0 | 26 | 0.274 | 7.111 | 80.8 | target 18, time 8 | -3.29 [-8.50, +1.24] |
| ORACLE per-market chosen ON days 5-7 (not tradable) | 20 | 0.301 | 6.013 | 80.0 | 31 | 0.344 | 10.657 | 83.9 | target 21, time 10 | +0.26 [-0.68, +1.40] |

Per market, OOS days 5-7 (trades / mean net % / sum net %):

| market | V1 thr % | V4 n / mean / sum | V1 n / mean / sum | V2 n / mean / sum | V3 n / mean / sum |
|---|---|---|---|---|---|
| BTCTMN | 0.7 | 3 / 0.368 / 1.103 | 3 / 0.368 / 1.103 | 3 / 0.368 / 1.103 | 3 / 0.368 / 1.103 |
| ETHTMN | 0.7 | 0 / - / 0.000 | 0 / - / 0.000 | 0 / - / 0.000 | 0 / - / 0.000 |
| SOLTMN | 0.7 | 2 / 0.168 / 0.336 | 2 / 0.168 / 0.336 | 2 / 0.168 / 0.336 | 2 / 0.168 / 0.336 |
| XRPTMN | 0.7 | 2 / 0.352 / 0.703 | 2 / 0.352 / 0.703 | 2 / 0.352 / 0.703 | 0 / - / 0.000 |
| DOGETMN | 0.7 | 1 / 0.355 / 0.355 | 1 / 0.355 / 0.355 | 1 / 0.355 / 0.355 | 1 / 0.355 / 0.355 |
| PEPETMN | 0.9 | 11 / 0.361 / 3.973 | 5 / 0.720 / 3.602 | 8 / 0.380 / 3.042 | 11 / 0.370 / 4.066 |
| BNBTMN | 0.7 | 4 / -0.027 / -0.106 | 4 / -0.027 / -0.106 | 3 / 0.084 / 0.251 | 3 / 0.084 / 0.251 |
| ARBTMN | 0.8 | 12 / 0.336 / 4.034 | 8 / 0.494 / 3.952 | 6 / 0.466 / 2.796 | 6 / 0.167 / 1.001 |

Why it fails: on days 1-4 the 0.7-0.8% dips lost money (11 trades, sum -1.522%), so every in-sample fit raised the threshold (PEPE to 0.9%, ARB to 0.8%, k=3); on days 5-7 the same band made money (9 trades, +0.795%) and the 0.8-0.9% band made +3.014%. Higher thresholds raise the mean per trade (0.297% to 0.398%) but not the total, and even the oracle (per-market thresholds picked on the test days themselves) beats V4 by only +0.26% over 3 days. Live tick replay (3.2 h for 5 markets, 1.7 h for PEPE/BNB/ARB): no tick ever reached buy_dev -0.7% (deepest ARB -0.591%), so every variant makes 0 trades there; too small to say anything.

## In-sample vs out-of-sample per market

Days 1-4 (in-sample; V1/V2 numbers here are fitted, so optimistic):

| market | V1 thr % | V4 n / mean / sum | V1 n / mean / sum | V2 n / mean / sum | V3 n / mean / sum |
|---|---|---|---|---|---|
| BTCTMN | 0.7 | 0 / - / 0.000 | 0 / - / 0.000 | 0 / - / 0.000 | 0 / - / 0.000 |
| ETHTMN | 0.7 | 0 / - / 0.000 | 0 / - / 0.000 | 0 / - / 0.000 | 0 / - / 0.000 |
| SOLTMN | 0.7 | 1 / 0.180 / 0.180 | 1 / 0.180 / 0.180 | 1 / 0.180 / 0.180 | 0 / - / 0.000 |
| XRPTMN | 0.7 | 2 / 0.514 / 1.029 | 2 / 0.514 / 1.029 | 2 / 0.514 / 1.029 | 2 / 0.514 / 1.029 |
| DOGETMN | 0.7 | 0 / - / 0.000 | 0 / - / 0.000 | 0 / - / 0.000 | 0 / - / 0.000 |
| PEPETMN | 0.9 | 11 / 0.039 / 0.433 | 3 / 0.668 / 2.004 | 7 / 0.366 / 2.561 | 9 / 0.028 / 0.249 |
| BNBTMN | 0.7 | 7 / 0.635 / 4.448 | 7 / 0.635 / 4.448 | 6 / 0.659 / 3.952 | 7 / 0.635 / 4.448 |
| ARBTMN | 0.8 | 6 / -0.035 / -0.211 | 3 / 0.270 / 0.811 | 2 / 0.215 / 0.431 | 2 / 0.118 / 0.236 |

## One threshold for all markets (shape of the trade-off)

| one threshold for all 8 | IS trades | IS sum % | IS mean % | OOS trades | OOS sum % | OOS mean % |
|---|---|---|---|---|---|---|
| 0.7% | 27 | 5.879 | 0.218 | 35 | 10.398 | 0.297 |
| 0.8% | 17 | 7.466 | 0.439 | 27 | 9.812 | 0.363 |
| 0.9% | 12 | 7.139 | 0.595 | 15 | 7.587 | 0.506 |
| 1.0% | 8 | 5.26 | 0.657 | 9 | 4.89 | 0.543 |
| 1.2% | 6 | 4.826 | 0.804 | 2 | 1.343 | 0.671 |
| 1.5% | 0 | 0.0 | - | 0 | 0.0 | - |

In-sample the best single threshold is 0.8%; out of sample it is 0.7%. The ranking flips between the two halves, which is what noise looks like.

| V4 trades by entry dip | trades | sum % | mean % |
|---|---|---|---|
| IS dip 0.7-0.8% | 11 | -1.522 | -0.138 |
| IS dip 0.8-0.9% | 5 | 0.64 | 0.128 |
| IS dip 0.9-1.0% | 6 | 1.992 | 0.332 |
| IS dip 1.0-99% | 5 | 4.768 | 0.954 |
| OOS dip 0.7-0.8% | 9 | 0.795 | 0.088 |
| OOS dip 0.8-0.9% | 14 | 3.014 | 0.215 |
| OOS dip 0.9-1.0% | 7 | 2.888 | 0.413 |
| OOS dip 1.0-99% | 5 | 3.699 | 0.740 |

V4 exits: days 1-4 target 18 (mean 0.524%), time 9 (mean -0.395%); days 5-7 target 22 (mean 0.501%), time 13 (mean -0.047%). No stop was hit in 62 trades. V4 makes 6.8 trades/day on days 1-4 and 11.7 on days 5-7 (model).

## Volatility-scaled k sweep (one k for all markets)

| k | IS trades | IS sum % | OOS trades | OOS sum % |
|---|---|---|---|---|
| 1.5 | 27 | 5.878 | 35 | 10.397 |
| 2.0 | 24 | 6.087 | 31 | 9.398 |
| 2.5 | 20 | 6.862 | 28 | 8.655 |
| 3.0 | 18 | 8.153 | 25 | 8.585 |
| 4.0 | 8 | 4.981 | 16 | 5.158 |

k = 1.5 is identical to the fixed 0.7% (1.5 x the 3-h std rarely exceeds 0.7%). Mean V2 threshold at k = 3: {'BTCTMN': 0.702, 'ETHTMN': 0.7, 'SOLTMN': 0.725, 'XRPTMN': 0.714, 'DOGETMN': 0.707, 'PEPETMN': 0.793, 'BNBTMN': 0.816, 'ARBTMN': 0.943}.

## Portfolio view (max 3 positions, daily-loss and kill-switch rules), days 5-7

| variant | OOS trades | OOS sum % | TMN P&L at size 0.4 (about 828k per trade) |
|---|---|---|---|
| V4 fixed 0.7% (live rule) | 35 | 10.397 | 86,044 |
| V1 per-market fixed (chosen days 1-4) | 25 | 9.944 | 82,296 |
| V2 vol-scaled, k=3.0 (chosen days 1-4) | 25 | 8.585 | 71,048 |
| V2b vol-scaled, k per market | 26 | 8.228 | 68,091 |
| V3 0.7% + half spread | 26 | 7.111 | 58,852 |
| ORACLE per-market chosen ON days 5-7 (not tradable) | 31 | 10.657 | 88,192 |

## Robustness

Reverse walk-forward (choose on days 5-7, test on days 1-4): V1 with the days-5-7 choice {'BTCTMN': 0.7, 'ETHTMN': 0.7, 'SOLTMN': 0.7, 'XRPTMN': 0.7, 'DOGETMN': 0.7, 'PEPETMN': 0.8, 'BNBTMN': 1.0, 'ARBTMN': 0.7} gives 20 trades, sum 6.013% vs V4 27 trades, sum 5.878%; V2 picks k = 1.5 which is the same as V4. So in the reverse direction the best case is +0.13% with 7 fewer trades: noise.

Sensitivity, only minutes whose USDTTMN price traded within 10 min (Wallex USDTTMN does not trade about 20:00-05:00 UTC, so this drops the night):

| variant (USDTTMN traded <= 10 min ago only) | IS trades | IS sum % | OOS trades | OOS sum % | OOS diff vs V4 (90% CI) |
|---|---|---|---|---|---|
| V4 fixed 0.7% (live rule) | 14 | 1.683 | 17 | 5.29 | - |
| V1 per-market fixed (chosen days 1-4) | 9 | 2.778 | 13 | 4.623 | -0.67 [-1.70, +0.16] |
| V2 vol-scaled, k=2.5 (chosen days 1-4) | 11 | 2.893 | 15 | 5.262 | -0.03 [-0.27, +0.19] |
| V2b vol-scaled, k per market | 11 | 2.893 | 16 | 4.905 | -0.39 [-1.34, +0.19] |
| V3 0.7% + half spread | 10 | 1.152 | 14 | 6.085 | +0.80 [-0.08, +1.86] |
| ORACLE per-market chosen ON days 5-7 (not tradable) | 10 | 1.676 | 15 | 6.275 | +0.98 [-0.06, +2.17] |

Here V3 looks better out of sample (+0.796%) but it was worse than V4 in-sample (1.152 vs 1.683), and in the main run it is the worst variant (-3.29%). Not robust.

## Live tick replay (small sample)

| market | hours of ticks | deepest buy_dev % | p1 buy_dev % | median spread % | separate dips <= -0.5 / -0.6 / -0.7 % |
|---|---|---|---|---|---|
| ARBTMN | 1.67 | -0.591 | -0.194 | 0.44 | 1 / 0 / 0 |
| BNBTMN | 1.67 | -0.335 | -0.287 | 0.146 | 0 / 0 / 0 |
| BTCTMN | 3.24 | -0.424 | -0.173 | 0.03 | 0 / 0 / 0 |
| DOGETMN | 3.24 | -0.308 | -0.219 | 0.168 | 0 / 0 / 0 |
| ETHTMN | 3.24 | -0.488 | -0.267 | 0.136 | 0 / 0 / 0 |
| PEPETMN | 1.67 | -0.444 | -0.298 | 0.113 | 0 / 0 / 0 |
| SOLTMN | 3.24 | -0.285 | -0.129 | 0.156 | 0 / 0 / 0 |
| XRPTMN | 3.24 | -0.384 | -0.264 | 0.194 | 0 / 0 / 0 |

All five variants: 0 trades on the recorded ticks.

Candle model vs real ticks over the same UTC hours (different days, so only indicative):

| market | candle p1 buy_dev % (14:00-18:15 UTC, 7 days) | candle share of minutes <= -0.7 % | tick p1 buy_dev % | tick share <= -0.7 % |
|---|---|---|---|---|
| BTCTMN | -0.514 | 0.17 | -0.172 | 0.0 |
| ETHTMN | -0.387 | 0.0 | -0.267 | 0.0 |
| SOLTMN | -0.43 | 0.173 | -0.129 | 0.0 |
| XRPTMN | -0.376 | 0.122 | -0.264 | 0.0 |
| DOGETMN | -0.314 | 0.0 | -0.219 | 0.0 |
| PEPETMN | -0.804 | 1.593 | -0.298 | 0.0 |
| BNBTMN | -0.635 | 0.733 | -0.287 | 0.0 |
| ARBTMN | -0.735 | 1.138 | -0.194 | 0.0 |

The 1-minute candle model shows deeper and more frequent dips than the real order book. The whole backtest (all variants equally) probably overstates how often 0.7% dips happen, which matches the live run's 0 trades so far. This does not change the ranking of the variants, but it means trade counts from candles are an upper bound.

## Caveats

- Small samples: 35 OOS trades for V4, 25-26 for the variants; the 90% bootstrap intervals of the differences all include 0.
- Executable prices are modelled from 1-minute closes and a constant median spread (measured on 10-21 ticks and applied to the whole week); no order-book depth, no partial fills, exits assumed to fill at the bid.
- Stops checked on minute closes only (no intrabar lows); no stop was hit in any variant.
- Night: fair value uses a frozen USDTTMN price from about 20:00 to 05:00 UTC (no USDTTMN trades); the live engine uses the USDTTMN book mid then.

## Files

Scripts: fetch_binance_gap.py (fills Binance 1m 10-14 13:00-17:45 UTC, public API), dt_data.py, bt_dynamic.py, tick_replay.py, summarize.py, write_report.py. Outputs in out/.
