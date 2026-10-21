"""Writes result.json (the structured answer of this study). All numbers come from out/*.json."""
import json
from pathlib import Path

D = "C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot-ultra/research_ultra/sizing_depth/"
summary = r"""
**Short answer.** The order book is not what keeps trades small; the AI's "defensive" mode is. At cheap moments the top ask covered about 94% of a 0.4-size order and about 90% of a 1.0-size order (18 entry episodes). In PEPE, BNB, ARB and XRP, which make most of the trades, it covered 100%. So size_factor matters almost one-for-one.

### Method
- **Ticks:** shadow_v2..v6 plus live_main, frozen at 18:15 UTC. 30,094 TMN rows: 3.2 h for BTC/ETH/SOL/XRP/DOGE, 1.63 h for PEPE/BNB/ARB.
- **Entry fill:** the engine buys `min(planned/ask, ask_qty)` once per signal, so fill = min(1, ask x ask_qty / planned).
- **Planned notional:** 0.4 = 828k TMN, 0.7 = 1.448M, 1.0 = 2.07M capped to 1.75M.
- **Backtest:** 7 days of 1-minute data (6.85 d, 2026-10-14 17:41 to 10-21 14:04 UTC) at the 0.7% entry, all 8 TMN markets. Two exit rules: the original one (it reproduces fv_sweep_0.007_0.004 exactly for the 5 core markets) and the live one (break-even target, ARB hold 15, 60 s drop filter). Taker fees 0.3% on both legs.
- **Walk-forward split:** first 4 days vs last 2.85 days. Since size is not tuned, this only checks that the edge holds in both halves.
- **Account replay:** chronological, with the risk-manager caps, cooldown, daily limit, kill switch, profit lock and the measured depth fill. Also 40 overlapping historical 45 h windows, a Monte Carlo (20k paths), and a stress test that injects -1.8% stop-outs.

### 1. Depth vs planned size
| bucket (pooled) | ticks | fill 0.4 | fill 1.0 | binds 0.4 / 1.0 |
|---|---|---|---|---|
| all ticks | 30,094 | 95.1% | 92.5% | 7% / 12% |
| buy_dev <= -0.3% | 111 | 94.8% | 91.9% | 10% / 13% |
| buy_dev <= -0.4% | 15 | 86.6% | 76.1% | 33% / 33% |
| buy_dev <= -0.5% / -0.7% | 1 / **0** | 100% / n.a. | 100% / n.a. | |
| entry episodes at -0.3% (engine-like) | 18 | **93.8%** | **89.9%** | 12% / 18% |
| entry episodes at -0.4% | 6 | 87.4% | 76.5% | 33% / 33% |

Per market cheap-moment fill (0.4 / 1.0):

| market | fill |
|---|---|
| PEPE, BNB, ARB, XRP | 100% / 100% |
| ETH | 96% / 93% |
| BTC | 94% / 84% |
| SOL | 30% / 21% (8 ticks) |
| DOGE | 21% / 10% (59 ticks) |

Weighted by trade rate the fill is 95.6% at 0.4 and 94.6% at 1.0. BNB is thin at ordinary moments: 23% of its ticks have a top ask below 828k.

**Exit side.**
- In simulated exits the top bid covered 100% (0.4) and 94% (1.0) of the position.
- In the target zone the top bid was smaller than a 1.0 position in 36% of DOGE ticks, 29% of PEPE and 22% of ARB; the rest sells on the next tick, so this costs time, not price.
- In falling markets (the stop-like moment) the top bid was smaller than 1.75M in at most 3.8% of ticks.

### 2. Expected return over ~45 h, and the bad case
| variant | trades (per day) | mean net/trade | win | exits | WF first 4 d | WF last 2.85 d |
|---|---|---|---|---|---|---|
| original rule and spreads (= existing backtest) | 65 (9.5) | +0.325% | 69% | 59 target / 6 time / 0 stop | +0.31% (28) | +0.33% (37) |
| **live rule, tick spreads (base)** | 69 (10.1) | **+0.230%** | 71% | 43 / 26 / 0 | +0.22% (29) | +0.24% (40) |
| without the 10 h USDTTMN candle gap | 43 (6.3) | +0.289% | 79% | 31 / 12 / 0 | +0.31% (22) | +0.27% (21) |
| pessimistic: full spread paid on entry | 51 (7.4) | +0.159% | 67% | 31 / 20 / 0 | +0.20% (22) | +0.13% (29) |

Per market, base (trades, mean): PEPE 22, +0.20% · BNB 13, +0.42% · ARB 23, +0.10% (-0.21% in the pessimistic variant) · XRP 4, +0.43% · BTC 3, +0.37% · SOL 3, +0.17% · DOGE 1, +0.36% · ETH 0. PEPE, BNB and ARB make 84% of the trades.

**Expected account return over 45 h** (linear model; replay, windows and Monte Carlo agree within about 0.1 pp):
| size | base | pessimistic entry | rate x0.29 (what live ticks showed) |
|---|---|---|---|
| 0.4 (defensive cap) | +0.67% (34k TMN) | +0.34% | +0.20% |
| 0.7 | +1.15% (58k) | +0.59% | +0.33% |
| 1.0 | +1.37% (68k) | +0.70% | +0.40% |
| AI policy now (0.4, BNB off) | +0.44% (22k) | +0.13% | +0.13% |

Forty historical 45 h windows (base): mean +0.61% / +1.04% / +1.24% at 0.4 / 0.7 / 1.0. **No window was negative**, and the worst maximum drawdown was 0.33% / 0.57% / 0.67%.

**Opportunity-rate check.**
| threshold | cheap minutes the candle model expected in the tick period | cheap minutes the ticks showed |
|---|---|---|
| -0.3% | 16.7 | 4.6 |
| -0.5% | 4.1 | 0.04 |
| -0.7% | 1.4 | 0 |

The candle backtest probably overstates how often the bot gets a chance, by about 3.5x. A lower rate scales every size option down equally.

**Bad case** (a stop costs -0.9% plus 0.6% fees = -1.5% of notional; -1.8% with the full 0.3% crossing):
| size | loss per stop (% equity) | 3 stops in a row | stops to 1.5% daily limit | stops to 3% kill switch | all open positions stop at once |
|---|---|---|---|---|---|
| 0.4 | 0.25% | 0.75% | 7 | 13 | 0.89% |
| 0.7 | 0.43% | 1.30% | 4 | 7 | 1.26% (70% exposure cap) |
| 1.0 | 0.53% | **1.58%** (trips the daily limit) | 3 | 6 | 1.26% |

The history has 0 stops in 69 trades; the worst trade was -1.28% (a time exit).

**Stress test**, with a 5% share of trades turned into stop-outs (chance that drawdown reaches 1.5% / chance the kill switch fires, within 45 h):

| size | outcome |
|---|---|
| 0.4 | 0.08% / 0% |
| 0.7 | 3.5% / 0.01% |
| 1.0 | 7.3% / 0.1% |
| rule (1.0, then 0.4 at 1% drawdown) | 4.3% / 0% |

At a 10% stop share the edge is about 0. The kill-switch chance is then 1.1% at 1.0 and 0.015% with the rule.

### 3. The AI risk mode (policy choice for Daii)
- **5 of 5 live reviews chose "defensive"**, with 0 trades and 0 drawdown. In review 1 the analyst wrote "Risk is normal" and the officer overrode it. The officer also cut some markets to 0.25 to 0.35.
- **Markets switched off.** Each review sees a single spread reading:

| market | share of live time switched on | spread when the AI cited a wide spread | 2-hour median spread |
|---|---|---|---|
| PEPE | 38% | 0.585% | 0.153% |
| ARB | 38% | | |
| ETH | 38% | 0.07%, 0.18%, 0.014% | 0.09% to 0.14% |
| BNB | 74%, and off now | 0.317% at 17:55 (top ask 13.3M) | 0.146% |

  Engine spread gate: 0.6%.
- **Cost over 45 h, base:**

| change | cost |
|---|---|
| defensive cap vs 0.7 | 0.48 pp (24k TMN) |
| defensive cap vs 1.0 | 0.69 pp (35k TMN) |
| BNB switched off | another 0.23 pp |
| current AI policy vs 0.7 with all markets on | 0.71 pp |

  In the cautious scenarios the cap costs 0.14 to 0.35 pp.
- **A code rule beats the blanket LLM mode.** "Size X normally, 0.4 once drawdown >= 1% or the day is <= -0.75%" never triggered in 7 days, so it keeps the upside. It also cuts tail risk (see the stress test).
- **Recommendation:** use 0.7 with the rule. Use 1.0 with the rule if Daii wants maximum rank: about +0.2 pp more, but three stops then hit the daily limit.

### 4. Walk the book
This cannot be measured offline: the ticks hold only the top level. At random moments the book behind the top is deep:
- the analyst's truncated depth reads show 64M to 15B TMN within 0.1% of the best ask;
- markets_snapshot.csv shows 81M to 23B within 0.5% of mid.

Upper bound of the gain (all missing size bought at the same edge):

| size | base | thinnest-book proxy |
|---|---|---|
| 0.4 | +0.04 pp | +0.10 pp |
| 0.7 | +0.11 pp | +0.32 pp |
| 1.0 | +0.15 pp | +0.42 pp |

The real gain is lower. Not worth a live change now.
"""
caveats = r"""
- **Depth sample.** The tick sample is one afternoon: 3.2 h (1.63 h for PEPE/BNB/ARB). It has no tick at the 0.7% entry level, so entry depth comes from proxies (-0.3% and -0.4%: 18 and 6 episodes). These are small samples.
- **The backtest may be optimistic.** It uses 1-minute closes plus a modelled half-spread. The tick-vs-candle check suggests it overstates how often cheap moments happen, by roughly 3.5x. The per-trade mean rests on 69 trades, 84% of them in PEPE/BNB/ARB. ARB's mean turns negative if the full spread is paid on entry.
- **The edge is unconfirmed live.** Live has produced 0 trades so far. Size scales gains and losses alike. If the live edge is negative, 0.7 loses about 1.75x what 0.4 would.
- **Stops are untested.** The backtest has no stop exits, so the bad case is analytic plus an injected-stop stress test.
- **Data gap.** The USDTTMN candles have a 10.3 h hole that the original backtest forward-fills. Removing it gives fewer trades with a higher mean.
- **Exits below the top level are unmeasured.** Depth below the top bid at stop moments was not recorded.
- **REPORT.md was not written.** The subagent harness blocks writing .md report files. The human summary is in summary_md and in result.json, and all numbers are in out/*.json and out/*.log.
"""
impl = r"""
**Goal.** Code decides the risk mode; the AI keeps thresholds and per-market judgement.

1. **engine._review_async.** Add percentage-only account context. The model already never sees balances, so this stays within SECURITY.md:
   `st = self.state`
   `data["context"]["account"] = {"drawdown_pct": round(st.drawdown*100,3), "day_ret_pct": round((st.equity/st.day_start_equity-1)*100,3) if st and st.day_start_equity else 0.0}`
   If `self.state` is None, use 0 for both.
2. **supervisor.review.** After `mode = final.get(...)`:
   `acct = data.get("context",{}).get("account",{})`
   `stressed = acct.get("drawdown_pct",0) >= 1.0 or acct.get("day_ret_pct",0) <= -0.75`
   `ai_mode = mode`
   `if mode == "defensive" and not stressed: mode = "normal"`
   `if mode == "normal" and stressed: mode = "defensive"`
   Keep "halt" as it is. In the per-market loop:
   `if mode == "normal" and enabled: size = max(size, SIZE_FLOOR)` with `SIZE_FLOOR` from config, `fairvalue.size_floor_normal = 0.7`
   then the existing `if mode == "defensive": size = min(size, 0.4)`.
   Log `ai_risk_mode = ai_mode` next to the final `risk_mode` in rec, so the decision trail stays honest.
3. **Optional.** Ignore an AI "disable" unless the market's 2-hour median spread is > 0.5% or its median top depth is below the planned size. This needs a spread deque in PremiumTracker, since today the supervisor sees a single spread snapshot. Only BNB is affected now, and it is the backtest's best market.
4. **Config.** `default_policy.TMN.size_factor` could go from 0.5 to 0.7, so a restart does not briefly run below the floor.

**Edge cases**
- On AI failure, keep the existing fail-safe (size <= 0.3 regardless of the floor). Make sure the floor is applied only on the success path.
- Profit lock (risk halves at +3%), the daily limit, the kill switch, the 35% and 70% caps and the 0.7% entry floor are all unchanged.
- The floor must not re-enable a disabled market.
- The 70% exposure cap means 0.7 runs 2 full positions plus one ~0.6M position.

**Risks**
- Gains and losses scale by about 1.75x. Three stops at 0.7 cost about 1.30% of equity, still under the 1.5% daily limit. The kill switch needs 7 stops.
- If the edge is not real live, the bot loses faster.
- Editing supervisor.py or engine.py in MAIN changes the live bot only after an engine restart. Ship it via the orchestrator with a quick shadow check.

**Walk the book.** Do not build it now. If it is ever built: one buy LIMIT at `fair*(1+baseline-entry_threshold)` with qty = planned/price, so it sweeps only levels still below the threshold.
"""
scripts = [D + s for s in ["common.py", "depth_ticks.py", "calib_freq.py", "bt_sizing.py", "ai_policy.py", "walk_book.py",
                           "stress.py", "write_result.py"]]
res = {
    "idea": "Position size vs order-book depth: is the top of the book what limits trade size, and should the AI's unconditional "
            "'defensive' mode (size_factor capped at 0.4) be replaced by a code rule (0.7 normally, 0.4 only after a 1% drawdown "
            "or a -0.75% day)? Also, can walking several ask levels add anything?",
    "verdict": "accept",
    "one_line_fa": "پذیرفته شد، ولی تصمیم نهایی با دایی است: عمق بازار جلوی معامله را نمی‌گیرد و حدود ۹۰ تا ۹۴ درصد سفارش پر می‌شود. "
                   "اگر اندازه معامله از ۰٫۴ به ۰٫۷ برسد، سود مورد انتظار ۴۵ ساعت باقی‌مانده در حالت پایه از حدود ۰٫۶۷ به ۱٫۱۵ درصد سرمایه "
                   "می‌رسد و در حالت محتاطانه از ۰٫۲ به ۰٫۳۳ درصد.",
    "summary_md": summary.strip(),
    "caveats": caveats.strip(),
    "scripts": scripts,
    "result_file": D + "result.json",
    "implementation_sketch": impl.strip(),
    "live_bot_alert": "",
    "outputs": [D + "out/" + f for f in ["depth_ticks.json", "depth_entry_episodes.csv", "calib_freq.json", "bt_sizing.json",
                                         "ai_policy.json", "walk_book.json", "stress.json"]],
}
Path(D + "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print("ok", len(json.dumps(res)))
