"""Assemble result.json (the structured answer) from the numbers printed by the other scripts in out/*.log."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

SUMMARY = r"""
**Verdict: reject.** On the walk-forward evidence, the expected gain from trading the 5 Wallex USDT markets over the
remaining ~45 h is smaller than the USDT/TMN conversion cost plus a 1-sigma USDT/TMN move. This holds for every
allocation tested. Ignoring the FX risk, the best case is about +6k toman (+0.12% of equity) on 827k converted.

### 1. The rule on 7 days of 1-minute candles (bt_candles.py)

Method:
- Same approach as scripts/bt_fairvalue.py: fair = Binance 1m close; only minutes where Wallex traded; spreads are
  the median of the shadow v2..v6 ticks.
- Made closer to the live engine:
  - baseline = rolling 180-min median, shift(1);
  - floor 0.36%, spread <= 0.6%, 1-min Binance drop filter of -0.4%;
  - engine exits: stop 0.9%; target needs sell_dev >= -0.05% AND bid >= entry x (1 + 0.31%); time 25 min;
  - taker 0.13% on both legs; 120-min cooldown after a loss.
- Two fill models:
  - **mid**: ask = close x (1 + half spread), as in the existing backtest.
  - **cons**: ask = close x (1 + full spread), with 0.1% extra on stop and time exits.
- Data: 2026-10-14 17:41 to 10-21 14:09 UTC. Train = 4 days. Test = 2.85 days.

| model | thr | train n | train mean % | train sum % | test n | test mean % | test sum % | test win % | test exits |
|---|---|---|---|---|---|---|---|---|---|
| mid | 0.36 | 12 | +0.185 | +2.22 | 8 | +0.278 | +2.23 | 100 | 8 target |
| mid | 0.45 | 5 | -0.375 | -1.88 | 4 | +0.277 | +1.11 | 100 | 4 target |
| mid | 0.60 | 2 | -0.782 | -1.57 | 0 | n/a | 0 | n/a | n/a |
| cons | 0.36 | 6 | -0.318 | -1.91 | 5 | -0.045 | -0.23 | 80 | 4 target, 1 stop |
| cons | 0.45 | 4 | -0.227 | -0.91 | 1 | +0.481 | +0.48 | 100 | 1 target |
| cons | 0.60 | 2 | +0.503 | +1.01 | 0 | n/a | 0 | n/a | n/a |

Walk-forward (choose on train, report test):
- **mid, one threshold for all markets:** 0.36% is chosen. Test: 8 trades, +0.278% mean (SE 0.10), 2.8 trades/day.
  - BTCUSDT: 4 trades, +0.375%.
  - ETHUSDT: 4 trades, +0.181%.
- **cons, one threshold for all markets:** 0.60% is chosen. Test: 0 trades.
- **cons, per-market threshold:** test 3 trades, +0.452%, all BTCUSDT, 1.05 trades/day.

Per market, full 7 days at 0.36%:

| market | mid | cons |
|---|---|---|
| BTCUSDT | 9 trades, +0.377% | 7 trades, +0.41% |
| ETHUSDT | 7 trades, +0.424% | 2 trades, -0.79% |
| SOLUSDT | 3 trades, -0.23% | 1 trade, -2.13% |
| XRPUSDT | 1 stop, -1.22% | 1 stop, -1.29% |
| DOGEUSDT | 0 trades | 0 trades |

BTCUSDT is the only market that is positive in every model, threshold and period.

Share of minutes in which Wallex traded: BTC 24%, ETH 13%, SOL 10%, XRP 11%, DOGE 4.7%.

### 1b. Live 2.5 s ticks (bt_ticks.py, shadow v2..v6, 104.6 min, 2026-10-21 14:19-16:05 UTC)

- **Median spread:** BTC 0.028%, ETH 0.141%, SOL 0.24%, XRP 0.226%, DOGE 0.438% (spread > 0.6% in 29% of DOGE ticks).
- **Median top ask:** 77-355 USDT. At the moment of a signal it was only 1.2-11.5 USDT (one case: 771).
- **Replay at 0.36%:** 4 trades, mean **-0.002%**, win 75%, exits 3 target and 1 time.
  - BTC +0.15%, ETH +0.38%, SOL +0.23%, XRP -0.77%.
- **Replay at 0.45%:** 2 trades, +0.27%.
- **Replay at 0.60%:** 1 trade, +0.23%.
- **Real paper engine:** 1 USDT trade, ETHUSDT +0.301%. It spent 3.34 USDT and made 2,645 toman.
- **Signal frequency:** the ticks show 16.5 episodes per market-day at 0.36%, against 0.58 in the candles (about
  28x). That is only 6 episodes in 8.7 market-hours, clustered around 14:51 and 15:03.

### 2. USDT/TMN over 45 h (fx_usdttmn.py)

| source | daily drift | 45 h mean | 45 h 1 sigma | p05 / p95 |
|---|---|---|---|---|
| 7 d 1-min | +0.012%/day | -0.61% | 0.78% (3.7 independent windows) | -1.78 / +0.58 |
| 7 d realised hourly vol x sqrt(45) | n/a | n/a | 1.71% | n/a |
| 60 d hourly | +0.537%/day (+37.9% total) | +0.95% | 2.12% | -1.74 / +4.83 |
| last 14 d | -0.013%/day | -0.31% | 1.68% | -2.54 / +3.51 |

### 3. Economics for 45 h (economics.py)

Inputs:
- Equity 5,000,000 TMN = about 18.9 USDT at 264,602.
- Engine size at size_factor 0.4 = 827,586 TMN = 3.13 USDT per trade.
- The depth cap is 5.25 USDT (1.39M TMN), the median top ask at tick signals.
- Conversion costs 0.714% of X (2 x 0.3% taker + 0.114% USDTTMN spread).

Net = gain - conversion - 1-sigma FX. The two FX figures are 1.71% and, in brackets, 0.78%:

| X | per trade | scenario | trades in 45 h | mean % | gain | conversion | net after 1 sigma | net without FX |
|---|---|---|---|---|---|---|---|---|
| 827k | 811k | A candles mid, walk-forward | 5.2 | +0.278 | +11.8k | -5.9k | -8.2k (-0.6k) | +5.9k |
| 827k | 811k | B candles cons, walk-forward | 2.0 | +0.452 | +7.2k | -5.9k | -12.9k (-5.2k) | +1.3k |
| 827k | 811k | C ticks 0.36% | 103 | -0.002 | -1.7k | -5.9k | -21.7k (-14.1k) | -7.6k |
| 1.75M | 1.39M | A | 5.2 | +0.278 | +20.3k | -12.5k | -22.2k (-5.9k) | +7.8k |
| 1.75M | 1.39M | B | 2.0 | +0.452 | +12.4k | -12.5k | -30.1k (-13.9k) | -0.1k |
| 2.5M | 1.39M | A | 5.2 | +0.278 | +20.3k | -17.9k | -40.4k (-17.2k) | +2.4k |

Break-even:
- Scenario A needs 8.7 trades in 45 h (5.4 with the 0.78% sigma). It expects 5.2.
- Scenario B needs 5.4 (3.3). It expects 2.0.

Idle toman = 0. **No allocation beats idle after a 1-sigma FX move.** Even with no FX risk, the best case is +7.8k
toman (0.16% of equity).

### 4. What the engine would need (check_engine.py, offline)

- **Accounting breaks on shared coins.** All 5 USDT bases are also live TMN markets.
  - equity_tmn() counts the shared coin twice: one 827k BTCTMN position shows as **+16.5% equity**. After the
    sale, the drop from that inflated peak trips the 3% kill switch.
  - _reconcile() adopts a phantom BTCUSDT position from the BTCTMN coins.
  - LiveExecutor.sell() sells free(base), so one market's exit would sell the other market's coins.
- **No conversion logic exists.** It would need TMN to USDT at the start, and USDT to TMN before the 75-min flatten.
  The flatten path only sells coins of traded markets, so USDT would stay USDT.
- **Already handled:** USDT is valued in equity, and the supervisor floor already gives USDT markets 0.36%.

### Samples

- Candle test: 8 trades (mid) or 3-5 (cons).
- Tick replay: 4 trades in 104.6 min.
- Paper engine: 1 USDT trade.
- FX 7-day: 3.7 independent 45 h windows.

All of these are small.
""".strip()

res = {
    "idea": "Convert part of the toman capital to USDT (USDTTMN, ~0.71% round trip) and run the same fair-value reversion rule on the Wallex USDT markets BTCUSDT ETHUSDT SOLUSDT XRPUSDT DOGEUSDT (fees 0.10/0.13%, floor 0.36%) for the remaining ~45 h.",
    "verdict": "reject",
    "one_line_fa": "رد شد: سود قابل انتظار از معامله در بازارهای تتری در ۴۵ ساعت باقی مانده (حدود ۷ تا ۱۲ هزار تومان روی ۸۲۷ هزار تومان) از هزینه تبدیل تومان به تتر و برگشت آن (حدود ۰٫۷ درصد) به اضافه نوسان معمول قیمت تتر (۰٫۸ تا ۱٫۷ درصد) کمتر است، پس نتیجه خالص منفی است.",
    "summary_md": SUMMARY,
    "caveats": "",
    "scripts": [],
    "result_file": str(HERE / "result.json").replace("\\", "/"),
    "implementation_sketch": "",
    "live_bot_alert": "",
}
CAVEATS = [
    "Small samples everywhere: the candle test has 8 trades (mid) or 3-5 (cons); the tick replay has 4 trades in 104.6 minutes; the paper engine made 1 USDT trade; the 7-day FX data has 3.7 independent 45 h windows.",
    "The candle model only sees minutes in which Wallex traded. The live ticks show about 28x more signal episodes at 0.36% (16.5 against 0.58 per market-day), but only 6 episodes in 8.7 market-hours, clustered. At that frequency a per-trade mean of about +0.025% would already pay the conversion and 1 sigma, but the 4 tick trades averaged -0.002%. This is the one open question. Paper-shadowing BTCUSDT and ETHUSDT for several hours (15 or more trades) would answer it; that needs the orchestrator, because shadow runs are not allowed here.",
    "The 1-sigma FX range is wide: 0.78% (7 d window std, few windows), 1.71% (7 d hourly realised vol, which includes some bid-ask bounce) or 2.1% (60 d). The verdict is the same at all three.",
    "The 60-day USDTTMN drift (+0.54%/day, toman weakening) would have favoured holding USDT. The last 7-14 days were flat to negative. That is a directional FX bet that needs no USDT-market trading, so it is not counted as an edge.",
    "The USDTTMN spread (median 0.114%, p75 0.37%) comes from the earlier 20-snapshot market study, not a fresh book, because Wallex is not called.",
    "Exits are modelled at the bid. Live stop and time exits cross 0.3% below the bid on books with 1-11 USDT at the top, and the cons model adds only 0.1%. In the candle backtest, the 60 s global-drop filter is approximated by the 1-minute Binance return.",
    "Per-trade stats ignore the 3-position limit that USDT trades would share with the TMN markets.",
    "The account is only about 18.9 USDT, so a trade is 3.13 USDT. The exchange minimum for USDT markets was not checked directly, but the paper engine's 3.34 USDT ETHUSDT order passed the min-notional check.",
    "This answer was not also written as REPORT.md: the harness blocked report .md files for this subagent. The same content is in summary_md here.",
]
res["caveats"] = " ".join(f"({i + 1}) {c}" for i, c in enumerate(CAVEATS))
res["scripts"] = [str(HERE / s).replace("\\", "/") for s in (
    "common.py", "bt_candles.py", "bt_ticks.py", "fx_usdttmn.py", "economics.py", "check_engine.py",
    "freq_calibration.py", "table.py", "make_result.py")]
(HERE / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print("wrote", HERE / "result.json", len(json.dumps(res)))
