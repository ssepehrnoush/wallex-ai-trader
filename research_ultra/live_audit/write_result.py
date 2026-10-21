"""Assemble result.json (the structured answer of the live audit) from the numbers printed by
audit_live.py, forecast.py and extra_checks.py (snapshot 2026-10-21 18:27 UTC)."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
WT = "C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot-ultra"

SUMMARY = r"""## Live bot audit (runs/live_main), snapshot 2026-10-21 18:27 UTC

**Verdict: ACCEPT (healthy).** Nothing is blocking entries. There have been zero trades because no market's buy_dev came close to the threshold. The deepest reading in 1.70 h of live ticks was **ARBTMN at -0.591%** (17:31), and ARB was disabled by the AI at that moment anyway. The threshold is -0.7%, or -0.8% on ETH/PEPE/ARB since the 18:25 review. In the 7-day backtest, **64% of 1.7-hour windows also had zero entries**, so the current silence is normal.

### 0. Code identity
- WT is on commit 7229c75. `diff -r --strip-trailing-cr` of WT/wallexbot against MAIN/wallexbot shows no differences, and config.json is identical too. A plain diff flags every line only because of line endings.
- **The running process is older than 7229c75.** The last start line still shows the old default:
  `{"t": "2026-10-21T17:51:56+00:00", "event": "start", ... "TMN": {"enabled": true, "entry_dev": 0.006, ...`
  The committed config says 0.007, and MAIN engine.py and config.json have mtime 17:52:32 UTC, 36 s after the process loaded them.
  - Effect: the engine-level fee floor added in 7229c75 is not active in the running bot.
  - This is harmless for now. The loaded supervisor clamps every AI policy to at least 0.7%, and a failed review keeps the current policy.
  - Past exposure: 51 s on the default 0.6% (17:54:56 to 17:55:47), and BTC at 0.6% from 17:36 to 17:51 under the previous process. No trade happened in either window.

### 1. Why zero trades (live 16:45:30 to 18:27:33 UTC, 1.70 h, 2,418 ticks per market, 2.5 s cadence, max gap 22 s)
| market | min buy_dev % | p1 % | p50 % | minutes <= -0.4/-0.5/-0.6/-0.7% | spread med/p99 % | ticks spread>0.6% | baseline first->last % | prem-baseline med % |
|---|---|---|---|---|---|---|---|---|
| BTCTMN | -0.210 | -0.145 | +0.084 | 0/0/0/0 | 0.032/0.186 | 0% | -0.233 -> -0.147 | +0.058 |
| ETHTMN | -0.406 (AI-off) | -0.267 | +0.031 | 0.12/0/0/0 | 0.097/0.285 | 0% | -0.116 -> -0.111 | -0.014 |
| SOLTMN | -0.161 | -0.120 | +0.063 | 0/0/0/0 | 0.095/0.379 | 0% | -0.055 -> -0.042 | +0.002 |
| XRPTMN | -0.352 | -0.273 | +0.077 | 0/0/0/0 | 0.124/0.398 | 0% | -0.058 -> -0.030 | +0.011 |
| DOGETMN | -0.308 | -0.219 | +0.048 | 0/0/0/0 | 0.113/0.343 | 0.04% | -0.009 -> -0.048 | -0.026 |
| PEPETMN | -0.444 (AI-off) | -0.380 | +0.074 | 0.21/0/0/0 | 0.089/0.906 | 7.7% | -0.076 -> -0.133 | -0.008 |
| BNBTMN (off) | -0.335 | -0.287 | -0.048 | 0/0/0/0 | 0.146/0.356 | 0% | +0.395 -> +0.187 | -0.116 |
| ARBTMN | -0.591 (AI-off) | -0.390 | +0.148 | 0.96/0.42/0/0 | 0.430/1.056 | 23.0% | -0.083 -> -0.128 | -0.047 |

The shadow ticks from earlier today tell the same story. For the majors (14:19 to 16:05, 1.75 h) the deepest reading was ETH at -0.488%. PEPE, BNB and ARB had only 10 minutes of shadow data. No market reached -0.6% in about 3.4 h of tick data for the majors or about 1.9 h for the thin markets.

Baseline drift is small. In BTC the premium is rising, which biases buy_dev up by about 0.06%. In BNB the bias is -0.12%, but BNB is disabled.

**Gate walk (ticks out of 2,418 per market):**
- Warmup: 216 ticks (3 starts).
- Disabled by the AI: ETH, PEPE and ARB 1,310 ticks each, XRP 90, BNB 852.
- **Every remaining tick failed the threshold check**: BTC, SOL and DOGE 2,202 each, XRP 2,112, ETH, PEPE and ARB 892 each, BNB 1,350. They would also fail at the running engine's own 0.6% threshold, since no tick was ever at or below -0.6%.
- The momentum, spread and risk gates were never reached, and there are no `skip` events.

The other gates check out:
- **tracker.ready:** the 17:51 warm start loaded 22,112 rows, at least 1,828 per market, and my recount matches exactly. At the 16:45 start PEPE, BNB and ARB had 232 rows (below 240), so they were not ready for about 20 s. That is trivial.
- **Phase and brakes:** phase is normal until 2026-10-23 17:50 UTC. There is no STOP or FLATTEN file.
- **State:** halted "", no cooldowns, no positions, equity 5,000,000 all in cash.
- **Order size:** 666,667 TMN at the current policy (size 0.3, stop 0.8%), or 827,586 at size 0.4 and stop 0.9%. That is above the 60,000 minimum and below the 35% cap.
- **Order book:** the top ask is smaller than the order in 0.5% (ARB) to 29% (BNB) of ticks; in those cases the order shrinks but is not blocked. The top ask is below 105k TMN in 0 to 5.1% of ticks (DOGE 5.1%). That is the only case that could cause `entry_unfilled`. The exchange minNotional is not logged.

### 2. Errors and anomalies in decisions.jsonl
112 lines, 0 unparseable: equity 103, policy_review 6, warm_start 3, start 3.

There are **none** of the following: `*_error`, `feed_gap`, `blocked`, `skip`, `entry_unfilled`, `exit_unfilled`, `dust_left`, `stray_order_cancelled`. The 111 AI tool calls returned 0 errors. The equity mark arrives every minute, with no gap over 90 s.

Latest line: `{"t": "2026-10-21T18:26:59+00:00", "event": "equity", "equity_tmn": 5000000, "return_pct": 0.0, "drawdown_pct": 0.0, "halted": "", "trades": 0}`.

Anomalies, none of them blocking:
- The running code is older than the commit (section 0).
- The 16:52:50 warm start logged `"ticks": 13456` with baselines identical to 16:45, which means that run's own ticks were not loaded (my recount is 14,832). The current code has fixed this.
- The AI keeps switching markets on and off. It called XRP's spread high at 16:48 and low at 17:55. The three deepest dislocations all happened while those markets were off.
- The 18:25:41 review set ETH, PEPE and ARB to 0.8% and size to 0.3.
- Synthetic test rows are being read (section 4).

### 3. Forecast (entries allowed until 2026-10-23 17:50 UTC, 1.974 days away)
- **Method:** the bt_fairvalue.py approach on the local 7-day 1-minute files (6.85 days), with a shift(1) 180-min median baseline, volume>0 minutes only, and a 0.3% taker fee on both legs.
- **Reproduction is exact:** majors at 0.7% match the stored fv_sweep_0.007_0.004 (BTC 3, ETH 0, SOL 2, XRP 2, DOGE 1).
- **"Live rule"** means the engine's own exits (the target needs bid >= entry x 1.0065), using live_main median spreads.

| market | 0.7% bt rule, orig spreads | 0.7% live rule | **current AI policy** |
|---|---|---|---|
| BTCTMN | 3, +0.340% | 3, +0.367% | 3, +0.367% |
| ETHTMN | 0 | 2, -0.086% | 0 (0.8%) |
| SOLTMN | 2, +0.057% | 5, +0.250% | 5, +0.250% |
| XRPTMN | 2, +0.969% | 4, +0.482% | 4, +0.482% |
| DOGETMN | 1, +0.362% | 1, +0.411% | 1, +0.411% |
| PEPETMN | 27, +0.320% | 23, +0.349% | 18, +0.311% (0.8%) |
| BNBTMN | 9, +0.289% | 13, +0.420% | off |
| ARBTMN | 21, +0.306% | 23, +0.115% (14 time) | 16, +0.345% (0.8%) |
| pooled | 65, +0.325%, win 69% | 74, +0.279%, win 72% | **47, +0.336%/trade, win 79%, mean 95% CI +0.17..+0.53, t=3.7; exits 33 target / 14 time** |

**Forecast under the current policy:**
- Rate: 6.86 trades/day, so **E[N] = 13.5**. The 90% range is 8 to 20 with a fixed rate, or 8 to 22 when rate uncertainty is included.
- **Expected account return: +0.65% (about +32k TMN)**, with a 5-95% range of +0.12% to +1.32% and a 1.6% chance of a loss.

**Other scenarios:**
- At 0.7% on all 7 enabled markets: 17.6 trades (11 to 27) and +0.77% (-0.11% to +2.0%).
- If the AI leaves only the majors on: 3.75 to 4.3 trades and +0.18% to +0.22%.
- PEPE and ARB together supply 34 of the 47 backtest trades behind the forecast.
- Stability: the first 4 days had 31 trades (7.75/day, +0.23%), the last 2.85 days had 43 (15.1/day, +0.31%). Daily counts range from 6 to 16.

**Is today unusually calm?** Mildly calm, but not abnormal.
- Share of backtest windows with zero entries: 1.7 h 64%, 3.8 h 38%, 6 h 22%, 12 h 3.7%, 24 h 0%. If there are still zero trades by about 18:00 UTC tomorrow, that would be a red flag.
- Binance 1-minute volatility during the live hours, as a percentile of the 7-day hours: BTC 35, ETH 40, SOL 46, XRP 61, DOGE 33, PEPE 61, BNB 25, ARB 47.
- Backtest entries cluster on big global moves. Median |15-min move| at entry vs all minutes: ETH 1.22 vs 0.13%, DOGE 1.16 vs 0.15%, ARB 0.76 vs 0.32%, PEPE 0.50 vs 0.25%.
- The live buy_dev tail is shallower than the 7-day candle tail. p1: BTC -0.15 vs -0.39, PEPE -0.38 vs -0.69, ARB -0.39 vs -0.75. Share of minutes at or below -0.5%: 0 live vs 0.3 to 3.2% over the 7 days.
- Part of that gap comes from the method (candle close plus a modelled spread, versus the real ask). Without overlapping data the two causes cannot be separated, so treat the forecast as an upper bound, especially for PEPE and ARB.

### 4. Risks before the deadline
1. **Synthetic test rows** in MAIN/runs/test_live_paths/ticks.csv feed the AI's MCP tools until about 21:53 UTC.
   - simulate_threshold for ETHTMN at 0.004 returns a mean of **+1591.99%**, including one +3184% "target". Without the fake rows it is -0.15%.
   - ETH at 0.003 returns +637.6% (real: -0.145%). BTC at 0.001 returns +0.38% (real: -0.51%).
   - The bot still cannot trade below 0.7% because of the supervisor clamp, but these numbers could push the analyst toward "normal" mode or a bigger size.
   - Effect on a restart's warm start is negligible (BTC baseline -0.135 vs -0.134).
2. **The AI's simulator does not match the engine.** simulate_threshold counts a "target" at bid > entry before fees, while the engine needs +0.65%. It also ignores the spread filter, and get_backtest still serves the 0.6% sweep. All of this makes the AI more conservative, not more dangerous.
3. **The AI switching off or tightening the thin markets** is the biggest driver of trade count: 13.5 expected trades drop to about 4 if they stay off.
4. **Restarts:**
   - 3 minutes with no entries, then about 1 minute on the default policy (all 8 markets including BNB, size 0.5).
   - Risk state is not persisted: cooldowns, peak equity and the daily start are lost.
   - Open positions are re-adopted at the current bid with a fresh clock.
   - journal.read() would fail on a partial last line after a hard kill.
5. **75-minute flatten:** low risk. Entries stop at 17:50 UTC and the max hold is 20 to 25 min, so positions close by about 18:15, before the 19:05 flatten.
6. **Host memory:** about 1.4 GB free of 16 GB. A small pandas job hit `OpenBLAS error: Memory allocation still failed`. The engine does not use numpy, but research jobs on this machine should run single-threaded.

**Sample sizes are small.** Live: 1.70 h. Shadow: 1.75 h for the majors, 10 min for the thin markets. Backtest: 47 trades under the current policy, 74 at 0.7% on all 8, and only 0 to 5 trades per major."""

CAVEATS = (
    "Every number is a snapshot (live data up to 18:27 UTC, policy from the 18:25:41 review), and the AI can change "
    "policies every 30 min, which moves the forecast. "
    "The forecast is a candle-based backtest with modelled spreads. It ignores the 0.6% spread filter (which blocks ARB 23% and PEPE 7.7% of the time) "
    "and top-of-book capacity. The live tick tail is shallower than the candle tail, so treat the forecast as an upper bound. "
    "Live sample is 1.70 h (the thin markets have only about 1.9 h of ticks in total). Per-market backtest samples are tiny (0 to 5 trades for the majors). "
    "The claim that the running process is older than 7229c75 rests on the start log line (entry_dev 0.006) plus file mtimes. "
    "I could not read the process command line (WMI timed out). "
    "The exchange minNotional per market is not logged anywhere, so the entry_unfilled risk is estimated from top-ask depth only. "
    "REPORT.md could not be written: the harness refuses report .md files from subagents. The full report is in summary_md and in result.json. "
    "I made no writes to MAIN and no Wallex calls; Binance public klines only (8 requests)."
)

IMPL = (
    "No engine change is needed for the bot to trade. It is healthy, and every entry gate except the threshold passes on live data. "
    "Hygiene items, all the team's call and none done here:\n"
    "(1) Move MAIN/runs/test_live_paths/ticks.csv out of runs/ (e.g. to old junk). This needs no restart: tools._tail_ticks re-globs runs/*/ticks.csv on every MCP call, "
    "and the engine reads other runs only in _warm_start at restart. Also avoid running scripts/test_live_paths.py against MAIN/runs while live. "
    "A longer-term fix is to make _tail_ticks and _warm_start read only live_* or shadow_* runs.\n"
    "(2) The next planned restart will load 7229c75 automatically, which activates the engine-level fee floor and the 0.7% default. "
    "Do not restart only for this: the supervisor clamp already enforces 0.7%, and a restart costs 3 min of no entries plus about 1 min on the default policy.\n"
    "(3) Optional, in tools.py (MCP is relaunched on each review, so no engine restart): make simulate_threshold use the engine's exit rule "
    "(target only if bid >= entry*(1+2*taker+0.0005)) and the 0.6% spread filter, and point get_backtest to the 0.7% sweep. "
    "Right now the AI's evidence is biased conservative.\n"
    "(4) Optional robustness for any restart: let journal.read skip a partial last line, and persist RiskState (cooldowns, peak, day start) in state.json. "
    "Risk: any edit in MAIN changes the live bot immediately (tools.py on the next review). Edit in WT, review, then copy."
)

ALERT = (
    "Low to medium, not blocking. Synthetic test rows from scripts/test_live_paths.py sit in MAIN/runs/test_live_paths/ticks.csv "
    "(17 rows, 17:39:56 to 17:53:01 UTC). Example line: "
    "'1792604877.57,ETHTMN,22050000000,22050100000,1.0,1.0,664178704.75,-0.001494,32.200541,32.20039'. "
    "wallexbot/tools.py _tail_ticks globs every runs/*/ticks.csv, so until about 21:53 UTC the live AI's simulate_threshold sees fake data. "
    "ETHTMN at entry_dev 0.004 returns mean_ret_pct 1591.99 (one 'target' at +3184%; -0.15% without the fake rows), and ETH at 0.003 returns +637.6%. "
    "The bot still cannot buy below 0.7% (supervisor clamp), but the analyst could be pushed toward normal mode or a larger size_factor. "
    "Remedy if the team agrees: move that one file out of runs/, which needs no restart. "
    "FYI, also not blocking: the running engine predates 7229c75. Start line: "
    "'{\"t\": \"2026-10-21T17:51:56+00:00\", \"event\": \"start\", ... \"TMN\": {\"enabled\": true, \"entry_dev\": 0.006, ...' "
    "while MAIN engine.py and config.json were rewritten at 17:52:32 UTC. The engine-level fee floor is therefore not loaded, "
    "but the AI policies are clamped to 0.007 or more by the loaded supervisor."
)

ONE_LINE_FA = ("پذیرفته: ربات سالم است و هیچ چیزی جلوی خرید را نگرفته؛ فقط در این ۱.۷ ساعت هیچ بازاری به ۰.۷ درصد ارزانی نرسید "
               "(عمیق‌ترین ۰.۵۹ درصد در ARB). تا پایان مسابقه حدود ۱۴ معامله (بین ۸ تا ۲۲) و سودی حدود ۰.۶۵ درصد سرمایه پیش‌بینی می‌شود.")

result = {
    "idea": "Live bot audit: why zero trades, is anything blocking, and a forecast",
    "verdict": "accept",
    "one_line_fa": ONE_LINE_FA,
    "summary_md": SUMMARY,
    "caveats": CAVEATS,
    "scripts": [f"{WT}/research_ultra/live_audit/{n}" for n in
                ("audit_live.py", "fetch_binance.py", "forecast.py", "extra_checks.py", "write_result.py")],
    "result_file": f"{WT}/research_ultra/live_audit/result.json",
    "implementation_sketch": IMPL,
    "live_bot_alert": ALERT,
    "outputs": [f"{WT}/research_ultra/live_audit/{n}" for n in
                ("live_stats.json", "forecast.json", "extra_checks.json", "audit_live.out.txt", "forecast.out.txt",
                 "extra_checks.out.txt", "bt_trades_current_policy.csv", "bt_trades_live_rule.csv", "bt_trades_bt_orig.csv",
                 "bt_trades_repro.csv", "binance_today/")],
}
assert "\u2014" not in ONE_LINE_FA
(HERE / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
print("wrote", HERE / "result.json", len(json.dumps(result)))
