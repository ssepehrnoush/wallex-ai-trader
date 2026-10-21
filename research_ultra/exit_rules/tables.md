
### Walk-forward fold A (all 8 markets): choose on days 1-4, evaluate on days 5-7

| model | chosen on days 1-4 | OOS chosen: n / mean / sum / win | OOS current: n / mean / sum / win | OOS diff (sum pts) | 90% CI | P(diff>0) | rank of chosen OOS (of 80) |
|---|---|---|---|---|---|---|---|
| close_sp1.0 | stop0.9_hold45_x0.00 | 35 / 0.375 / 13.14 / 82.9 | 35 / 0.311 / 10.88 / 74.3 | +2.25 | [-2.269, 6.559] | 0.803 | 9 |
| low_sp1.0 | stop0.9_hold45_x0.00 | 35 / 0.381 / 13.33 / 82.9 | 35 / 0.311 / 10.88 / 74.3 | +2.45 | [-1.834, 6.672] | 0.833 | 8 |
| close_sp1.5 | stop0.9_hold45_x0.00 | 28 / 0.247 / 6.91 / 75.0 | 27 / 0.211 / 5.70 / 70.4 | +1.21 | [-3.029, 5.014] | 0.708 | 2 |

### Fold B (all 8 markets): choose on days 5-7, evaluate on days 1-4

| model | chosen | eval chosen sum | eval current sum | diff | 90% CI |
|---|---|---|---|---|---|
| close_sp1.0 | stop1.2_hold45_x0.10 | 7.43 (n 26) | 6.69 (n 26) | +0.74 | [-1.273, 2.662] |
| low_sp1.0 | stop1.2_hold45_x0.10 | 7.43 (n 26) | 6.69 (n 26) | +0.74 | [-1.253, 2.715] |
| close_sp1.5 | stop1.5_hold45_x0.00 | 7.54 (n 20) | 5.32 (n 20) | +2.22 | [-1.145, 6.055] |

### Single changes vs current, stop on 1-min close, spread x1 (all 8 markets)

| change | full 7d: n / mean / sum | diff full (pts) | day-block 90% CI | diff days 1-4 | diff days 5-7 | days 5-7 trade 90% CI |
|---|---|---|---|---|---|---|
| **current_0.9_25_0.05** | 61 / 0.288 / 17.57 | - | - | - | - | - |
| hold45_only | 61 / 0.326 / 19.91 | +2.34 | [-1.557, 6.971] | +0.71 | +1.62 | [-2.919, 5.904] |
| wf_choice_0.9_45_0.00 | 60 / 0.358 / 21.47 | +3.90 | [-0.086, 8.518] | +1.64 | +2.25 | [-2.327, 6.492] |
| hold15 | 61 / 0.271 / 16.55 | -1.02 | [-4.1, 1.86] | -0.42 | -0.60 | [-3.511, 1.876] |
| hold10 | 56 / 0.079 / 4.41 | -13.16 | [-19.52, -6.848] | -7.25 | -5.92 | [-10.59, -1.659] |
| stop0.6 | 60 / 0.279 / 16.77 | -0.81 | [-2.416, 0.0] | +0.00 | -0.81 | [-3.358, 0.942] |
| stop1.2 | 61 / 0.288 / 17.57 | +0.00 | [0.0, 0.0] | +0.00 | +0.00 | [0.0, 0.0] |
| partialX0.1 | 63 / 0.283 / 17.82 | +0.24 | [-1.111, 1.57] | +0.03 | +0.22 | [-3.829, 4.621] |
| partialX0.2 | 64 / 0.257 / 16.46 | -1.11 | [-2.416, 0.196] | -0.32 | -0.80 | [-6.566, 5.068] |
| partialX0.3 | 64 / 0.238 / 15.23 | -2.35 | [-4.293, -0.456] | -1.31 | -1.03 | [-7.033, 4.745] |
| slack0 | 60 / 0.294 / 17.61 | +0.04 | [-2.866, 3.042] | -0.25 | +0.29 | [0.044, 0.598] |
| live_now_ARB_0.8_15 | 61 / 0.272 / 16.60 | -0.98 | [-3.237, 1.283] | -0.49 | -0.49 | [-3.276, 1.853] |

#### full detail (all 8 markets)

| config | trades | mean net % | sum net % | win % | TMN P&L | exits |
|---|---|---|---|---|---|---|
| current_0.9_25_0.05 | 61 | 0.288 | 17.57 | 73.8 | 147,348 | target 42, time 19 |
| wf_choice_0.9_45_0.00 | 60 | 0.358 | 21.47 | 80.0 | 165,375 | stop 1, target 46, time 13 |
| hold45_only | 61 | 0.326 | 19.91 | 80.3 | 160,438 | stop 1, target 47, time 13 |
| hold15 | 61 | 0.271 | 16.55 | 75.4 | 138,639 | target 38, time 23 |
| hold10 | 56 | 0.079 | 4.41 | 62.5 | 36,416 | target 28, time 28 |
| stop0.6 | 60 | 0.279 | 16.77 | 73.3 | 169,133 | stop 1, target 42, time 17 |
| partialX0.3 | 64 | 0.238 | 15.23 | 76.6 | 127,414 | stop 1, target 48, time 15 |
| live_now_ARB_0.8_15 | 61 | 0.272 | 16.60 | 75.4 | 141,769 | target 40, time 21 |

#### oos detail (all 8 markets)

| config | trades | mean net % | sum net % | win % | TMN P&L | exits |
|---|---|---|---|---|---|---|
| current_0.9_25_0.05 | 35 | 0.311 | 10.88 | 74.3 | 90,738 | target 23, time 12 |
| wf_choice_0.9_45_0.00 | 35 | 0.375 | 13.14 | 82.9 | 109,689 | stop 1, target 28, time 6 |
| hold45_only | 35 | 0.357 | 12.51 | 82.9 | 104,358 | stop 1, target 28, time 6 |
| hold15 | 35 | 0.294 | 10.28 | 74.3 | 85,626 | target 21, time 14 |
| hold10 | 35 | 0.142 | 4.97 | 65.7 | 41,109 | target 18, time 17 |
| stop0.6 | 34 | 0.296 | 10.08 | 73.5 | 106,025 | stop 1, target 23, time 10 |
| partialX0.3 | 38 | 0.259 | 9.85 | 78.9 | 82,040 | stop 1, target 29, time 8 |
| live_now_ARB_0.8_15 | 35 | 0.297 | 10.40 | 77.1 | 89,121 | target 22, time 13 |

#### per market, days 5-7 (all 8 markets): current vs WF choice (0.9/45/0.00)

| market | current n / mean / sum / exits | WF choice n / mean / sum / exits |
|---|---|---|
| BTCTMN | 3 / 0.368 / 1.10 / {'target': 3} | 3 / 0.434 / 1.30 / {'target': 3} |
| ETHTMN | 0 / None / 0.00 / {} | 0 / None / 0.00 / {} |
| SOLTMN | 2 / 0.168 / 0.34 / {'target': 2} | 2 / 0.168 / 0.34 / {'target': 2} |
| XRPTMN | 2 / 0.351 / 0.70 / {'target': 2} | 2 / 0.351 / 0.70 / {'target': 2} |
| DOGETMN | 1 / 0.355 / 0.35 / {'target': 1} | 1 / 0.355 / 0.35 / {'target': 1} |
| PEPETMN | 11 / 0.361 / 3.97 / {'target': 7, 'time': 4} | 11 / 0.385 / 4.23 / {'target': 9, 'time': 2} |
| BNBTMN | 4 / -0.027 / -0.11 / {'time': 3, 'target': 1} | 5 / 0.538 / 2.69 / {'target': 4, 'time': 1} |
| ARBTMN | 12 / 0.377 / 4.52 / {'target': 7, 'time': 5} | 11 / 0.32 / 3.52 / {'target': 7, 'time': 3, 'stop': 1} |

mean by exit reason, current, full: {'target': 0.561, 'time': -0.316}; WF choice: {'stop': -1.787, 'target': 0.615, 'time': -0.387}

### Walk-forward fold A (7 markets, BNB excluded (BNB is disabled live)): choose on days 1-4, evaluate on days 5-7

| model | chosen on days 1-4 | OOS chosen: n / mean / sum / win | OOS current: n / mean / sum / win | OOS diff (sum pts) | 90% CI | P(diff>0) | rank of chosen OOS (of 80) |
|---|---|---|---|---|---|---|---|
| close_sp1.0 | stop0.9_hold45_x0.00 | 30 / 0.348 / 10.45 / 83.3 | 31 / 0.355 / 10.99 / 80.6 | -0.54 | [-4.329, 2.679] | 0.431 | 19 |
| low_sp1.0 | stop0.9_hold45_x0.00 | 30 / 0.355 / 10.64 / 83.3 | 31 / 0.355 / 10.99 / 80.6 | -0.35 | [-3.82, 2.754] | 0.459 | 17 |
| close_sp1.5 | stop0.9_hold45_x0.00 | 24 / 0.215 / 5.16 / 75.0 | 24 / 0.236 / 5.67 / 75.0 | -0.51 | [-4.305, 2.573] | 0.429 | 18 |

### Fold B (7 markets, BNB excluded (BNB is disabled live)): choose on days 5-7, evaluate on days 1-4

| model | chosen | eval chosen sum | eval current sum | diff | 90% CI |
|---|---|---|---|---|---|
| close_sp1.0 | stop1.2_hold45_x0.10 | 3.83 (n 20) | 2.70 (n 20) | +1.13 | [-0.434, 2.813] |
| low_sp1.0 | stop1.2_hold45_x0.10 | 3.83 (n 20) | 2.70 (n 20) | +1.13 | [-0.45, 2.831] |
| close_sp1.5 | stop0.9_hold25_x0.00 | 0.76 (n 14) | 1.53 (n 14) | -0.77 | [-2.638, 0.333] |

### Single changes vs current, stop on 1-min close, spread x1 (7 markets, BNB excluded (BNB is disabled live))

| change | full 7d: n / mean / sum | diff full (pts) | day-block 90% CI | diff days 1-4 | diff days 5-7 | days 5-7 trade 90% CI |
|---|---|---|---|---|---|---|
| **current_0.9_25_0.05** | 51 / 0.268 / 13.69 | - | - | - | - | - |
| hold45_only | 50 / 0.304 / 15.18 | +1.49 | [-2.931, 5.591] | +2.67 | -1.18 | [-4.866, 2.057] |
| wf_choice_0.9_45_0.00 | 49 / 0.342 / 16.74 | +3.05 | [-1.834, 7.647] | +3.59 | -0.54 | [-4.425, 2.845] |
| hold15 | 50 / 0.244 / 12.21 | -1.48 | [-4.706, 1.615] | -0.64 | -0.84 | [-3.656, 1.591] |
| hold10 | 49 / 0.091 / 4.47 | -9.22 | [-15.183, -3.326] | -3.06 | -6.15 | [-10.805, -1.945] |
| stop0.6 | 50 / 0.258 / 12.88 | -0.81 | [-2.416, 0.0] | +0.00 | -0.81 | [-3.358, 0.942] |
| stop1.2 | 51 / 0.268 / 13.69 | +0.00 | [0.0, 0.0] | +0.00 | +0.00 | [0.0, 0.0] |
| partialX0.1 | 53 / 0.255 / 13.52 | -0.17 | [-1.665, 1.339] | -0.38 | +0.22 | [-3.805, 4.342] |
| partialX0.2 | 54 / 0.225 / 12.17 | -1.52 | [-2.761, -0.172] | -0.73 | -0.80 | [-6.711, 4.929] |
| partialX0.3 | 54 / 0.203 / 10.94 | -2.75 | [-4.795, -0.703] | -1.71 | -1.03 | [-6.952, 4.677] |
| slack0 | 50 / 0.275 / 13.73 | +0.04 | [-2.936, 2.985] | -0.25 | +0.29 | [0.044, 0.598] |
| live_now_ARB_0.8_15 | 50 / 0.234 / 11.69 | -2.00 | [-4.57, 0.528] | -1.51 | -0.49 | [-3.208, 1.813] |

#### full detail (7 markets, BNB excluded (BNB is disabled live))

| config | trades | mean net % | sum net % | win % | TMN P&L | exits |
|---|---|---|---|---|---|---|
| current_0.9_25_0.05 | 51 | 0.268 | 13.69 | 76.5 | 114,382 | target 35, time 16 |
| wf_choice_0.9_45_0.00 | 49 | 0.342 | 16.74 | 79.6 | 140,138 | stop 1, target 37, time 11 |
| hold45_only | 50 | 0.304 | 15.18 | 80.0 | 126,967 | stop 1, target 38, time 11 |
| hold15 | 50 | 0.244 | 12.21 | 76.0 | 101,878 | target 30, time 20 |
| hold10 | 49 | 0.091 | 4.47 | 65.3 | 36,935 | target 25, time 24 |
| stop0.6 | 50 | 0.258 | 12.88 | 76.0 | 135,929 | stop 1, target 35, time 14 |
| partialX0.3 | 54 | 0.203 | 10.94 | 79.6 | 91,205 | stop 1, target 42, time 11 |
| live_now_ARB_0.8_15 | 50 | 0.234 | 11.69 | 76.0 | 99,874 | target 32, time 18 |

#### oos detail (7 markets, BNB excluded (BNB is disabled live))

| config | trades | mean net % | sum net % | win % | TMN P&L | exits |
|---|---|---|---|---|---|---|
| current_0.9_25_0.05 | 31 | 0.355 | 10.99 | 80.6 | 91,636 | target 22, time 9 |
| wf_choice_0.9_45_0.00 | 30 | 0.348 | 10.45 | 83.3 | 87,024 | stop 1, target 24, time 5 |
| hold45_only | 30 | 0.327 | 9.81 | 83.3 | 81,715 | stop 1, target 24, time 5 |
| hold15 | 31 | 0.327 | 10.15 | 80.6 | 84,534 | target 20, time 11 |
| hold10 | 31 | 0.156 | 4.84 | 71.0 | 40,027 | target 17, time 14 |
| stop0.6 | 30 | 0.339 | 10.18 | 80.0 | 107,162 | stop 1, target 22, time 7 |
| partialX0.3 | 34 | 0.293 | 9.95 | 85.3 | 82,937 | stop 1, target 28, time 5 |
| live_now_ARB_0.8_15 | 31 | 0.339 | 10.50 | 83.9 | 90,020 | target 21, time 10 |

#### per market, days 5-7 (7 markets, BNB excluded (BNB is disabled live)): current vs WF choice (0.9/45/0.00)

| market | current n / mean / sum / exits | WF choice n / mean / sum / exits |
|---|---|---|
| BTCTMN | 3 / 0.368 / 1.10 / {'target': 3} | 3 / 0.434 / 1.30 / {'target': 3} |
| ETHTMN | 0 / None / 0.00 / {} | 0 / None / 0.00 / {} |
| SOLTMN | 2 / 0.168 / 0.34 / {'target': 2} | 2 / 0.168 / 0.34 / {'target': 2} |
| XRPTMN | 2 / 0.351 / 0.70 / {'target': 2} | 2 / 0.351 / 0.70 / {'target': 2} |
| DOGETMN | 1 / 0.355 / 0.35 / {'target': 1} | 1 / 0.355 / 0.35 / {'target': 1} |
| PEPETMN | 11 / 0.361 / 3.97 / {'target': 7, 'time': 4} | 11 / 0.385 / 4.23 / {'target': 9, 'time': 2} |
| BNBTMN | 0 / None / 0.00 / {} | 0 / None / 0.00 / {} |
| ARBTMN | 12 / 0.377 / 4.52 / {'target': 7, 'time': 5} | 11 / 0.32 / 3.52 / {'target': 7, 'time': 3, 'stop': 1} |

mean by exit reason, current, full: {'target': 0.527, 'time': -0.297}; WF choice: {'stop': -1.787, 'target': 0.605, 'time': -0.35}

### Tick replay {'from': '2026-10-21 14:29:52.549999952+00:00', 'to': '2026-10-21 18:18:53.599999905+00:00', 'rows': 29661, 'hours_with_data': 3.13}

| entry | config | n | mean | sum | win | exits |
|---|---|---|---|---|---|---|
| 0.7000000000000001% (signals 0) | current_0.9_25_0.05 | 0 | None | 0.0 | None | {} |
| 0.7000000000000001% (signals 0) | hold45 | 0 | None | 0.0 | None | {} |
| 0.7000000000000001% (signals 0) | wf_choice_0.9_45_0.00 | 0 | None | 0.0 | None | {} |
| 0.7000000000000001% (signals 0) | hold15 | 0 | None | 0.0 | None | {} |
| 0.7000000000000001% (signals 0) | hold10 | 0 | None | 0.0 | None | {} |
| 0.7000000000000001% (signals 0) | stop0.6 | 0 | None | 0.0 | None | {} |
| 0.7000000000000001% (signals 0) | partialX0.3 | 0 | None | 0.0 | None | {} |
| 0.5% (signals 1) | current_0.9_25_0.05 | 1 | 0.433 | 0.433 | 100.0 | {'target': 1} |
| 0.5% (signals 1) | hold45 | 1 | 0.433 | 0.433 | 100.0 | {'target': 1} |
| 0.5% (signals 1) | wf_choice_0.9_45_0.00 | 1 | 0.433 | 0.433 | 100.0 | {'target': 1} |
| 0.5% (signals 1) | hold15 | 1 | 0.433 | 0.433 | 100.0 | {'target': 1} |
| 0.5% (signals 1) | hold10 | 1 | 0.433 | 0.433 | 100.0 | {'target': 1} |
| 0.5% (signals 1) | stop0.6 | 1 | 0.433 | 0.433 | 100.0 | {'target': 1} |
| 0.5% (signals 1) | partialX0.3 | 1 | 0.433 | 0.433 | 100.0 | {'target': 1} |
| 0.4% (signals 15) | current_0.9_25_0.05 | 5 | 0.035 | 0.177 | 60.0 | {'target': 3, 'time': 2} |
| 0.4% (signals 15) | hold45 | 5 | -0.06 | -0.3 | 60.0 | {'target': 3, 'time': 1, 'end': 1} |
| 0.4% (signals 15) | wf_choice_0.9_45_0.00 | 5 | -0.06 | -0.3 | 60.0 | {'target': 3, 'time': 1, 'end': 1} |
| 0.4% (signals 15) | hold15 | 5 | -0.108 | -0.538 | 40.0 | {'time': 3, 'target': 2} |
| 0.4% (signals 15) | hold10 | 5 | -0.123 | -0.617 | 20.0 | {'time': 4, 'target': 1} |
| 0.4% (signals 15) | stop0.6 | 5 | 0.035 | 0.177 | 60.0 | {'target': 3, 'time': 2} |
| 0.4% (signals 15) | partialX0.3 | 5 | 0.035 | 0.177 | 60.0 | {'target': 3, 'time': 2} |
| 0.3% (signals 111) | current_0.9_25_0.05 | 11 | -0.09 | -0.985 | 36.4 | {'target': 4, 'time': 4, 'end': 3} |
| 0.3% (signals 111) | hold45 | 11 | -0.018 | -0.197 | 54.5 | {'target': 6, 'end': 3, 'time': 2} |
| 0.3% (signals 111) | wf_choice_0.9_45_0.00 | 11 | -0.013 | -0.144 | 54.5 | {'target': 6, 'end': 3, 'time': 2} |
| 0.3% (signals 111) | hold15 | 10 | -0.338 | -3.38 | 20.0 | {'time': 6, 'target': 2, 'end': 2} |
| 0.3% (signals 111) | hold10 | 10 | -0.304 | -3.038 | 20.0 | {'time': 8, 'target': 2} |
| 0.3% (signals 111) | stop0.6 | 11 | -0.164 | -1.8 | 36.4 | {'target': 4, 'time': 3, 'end': 3, 'stop': 1} |
| 0.3% (signals 111) | partialX0.3 | 11 | -0.111 | -1.217 | 36.4 | {'target': 4, 'time': 4, 'end': 3} |
