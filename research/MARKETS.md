# Wallex TMN markets: liquidity, costs, and what the data says

Research snapshot taken 2026-10-21 (about 17:30-17:50 Tehran time), public endpoints only. No API key was
used, nothing was signed in to, and no orders were placed.

How to regenerate everything in this file:

```
.venv\Scripts\python research\market_snapshot.py     # ~10 min, 20 depth snapshots 30 s apart -> markets_snapshot.csv
.venv\Scripts\python research\fetch_candles.py       # ~1 min -> candles\<SYMBOL>_<RES>.csv + candles\_manifest.json
.venv\Scripts\python research\analyze.py             # -> stats.csv (+ USDT drift and hour-of-day profile on stdout)
.venv\Scripts\python research\build_tables.py        # rewrites the tables between the BEGIN/END markers below
```

Files: `markets_snapshot.csv` (all 187 TMN markets), `stats.csv`, `trade_credit_markets.json`,
`candles/`, `analyze_output.txt` (last run of `analyze.py`), `wallex_public.py` (rate-limited public client, ~3 req/s).

---

## 1. Market universe

`GET /v1/markets` lists 373 markets, 187 of them quoted in TMN. Every TMN market has the same
`minNotional` of **50,000 toman** (1% of a 5M account). `tickSize` and `stepSize` are **numbers of decimal
places**, not increments: almost all TMN prices are whole toman (`tickSize` 0); only sub-1-toman
coins such as PEPE and SHIB have 4 price decimals. `minQty` is 10^-stepSize.

`isMarketTypeEnable` is true only for **BTCTMN, ETHTMN, USDTTMN and XAUTTMN** (plus BTC/ETH/XAUT-USDT).
On every other TMN market expect MARKET orders to be rejected and use LIMIT orders. Wallex docs also say
MARKET orders are auto-cancelled if they would move price more than 2% from the last trade
(https://developers.wallex.ir/docs/spot-types).

### Top 15 by liquidity

The rank is the mean of four ranks: 24h toman volume, 7-day average daily toman volume, depth within
1% of mid on the thinner side, and median spread. Spread and depth are medians of 20 `/v2/depth/all`
snapshots taken 30 s apart (top 50 levels per side). "Cost to sweep 5M" is the volume-weighted fill
price for a 5,000,000 toman market order versus mid, so it includes half the spread.

<!-- BEGIN:top15 -->
| # | Market | 24h vol (B TMN) | 7d avg/day (B TMN) | Spread median % (p25-p75) | Depth within 1% of mid, thinner side (M TMN) | Cost to sweep 5M TMN buy/sell % vs mid | Price dp / qty dp | MARKET orders | Trade credit |
|---|---|---|---|---|---|---|---|---|---|
| 1 | USDTTMN | 224.7 | 150.8 | 0.001 (0.000-0.004) | 4,632 | 0.001 / 0.001 | 0 / 2 | yes | yes |
| 2 | BTCTMN | 78.2 | 63.5 | 0.175 (0.061-0.179) | 629 | 0.088 / 0.088 | 0 / 8 | yes | yes |
| 3 | PEPETMN | 110.4 | 54.1 | 0.218 (0.025-0.447) | 648 | 0.135 / 0.122 | 4 / 0 | no | yes |
| 4 | XAUTTMN | 29.1 | 20.1 | 0.024 (0.003-0.028) | 1,139 | 0.012 / 0.022 | 0 / 5 | yes | yes |
| 5 | ETHTMN | 37.3 | 41.8 | 0.191 (0.128-0.280) | 808 | 0.095 / 0.102 | 0 / 5 | yes | yes |
| 6 | XRPTMN | 35.2 | 18.8 | 0.171 (0.132-0.273) | 326 | 0.086 / 0.101 | 0 / 1 | no | yes |
| 7 | PUMPTMN | 85.6 | 76.3 | 0.160 (0.080-0.239) | 171 | 0.099 / 0.120 | 0 / 1 | no | yes |
| 8 | ARBTMN | 30.9 | 19.4 | 0.181 (0.087-0.302) | 329 | 0.093 / 0.168 | 0 / 4 | no | yes |
| 9 | SOLTMN | 31.8 | 14.1 | 0.269 (0.194-0.359) | 449 | 0.135 / 0.146 | 0 / 5 | no | yes |
| 10 | STMN | 10.2 | 12.0 | 0.145 (0.087-0.279) | 240 | 0.252 / 0.074 | 0 / 2 | no | yes |
| 11 | DOGETMN | 21.4 | 14.5 | 0.259 (0.214-0.303) | 230 | 0.138 / 0.129 | 0 / 1 | no | yes |
| 12 | USOONTMN | 64.8 | 46.0 | 0.023 (0.006-0.163) | 42 | 0.011 / 0.022 | 0 / 4 | no | yes |
| 13 | BNBTMN | 26.6 | 31.4 | 0.275 (0.167-0.331) | 84 | 0.138 / 0.138 | 0 / 5 | no | yes |
| 14 | LTCTMN | 7.8 | 4.3 | 0.295 (0.277-0.364) | 270 | 0.147 / 0.147 | 0 / 5 | no | yes |
| 15 | GRAMTMN | 4.4 | 3.4 | 0.184 (0.080-0.367) | 229 | 0.092 / 0.138 | 0 / 2 | no | no |
<!-- END:top15 -->

Notes on the ranking:

- **Volume is not liquidity here.** PEPETMN, PUMPTMN and USOONTMN show huge 24h volumes, but their books
  within 1% of mid are 7-110x thinner than USDTTMN's. USOONTMN (tokenised US Oil Fund, listed 2026-03)
  trades every few seconds in small clips at one price with a thin book, which looks like incentive or
  market-maker volume. Treat its volume number with suspicion.
- **Spreads move a lot within minutes.** An earlier 5-snapshot run (17:34 Tehran) gave quite different
  medians from the 20-snapshot run in the table (17:40-17:50):

  | Market | 5-snapshot run | 20-snapshot run |
  |---|---|---|
  | USDTTMN | 0.085% | 0.001% |
  | XAUTTMN | 0.108% | 0.024% |
  | BTCTMN | 0.339% | 0.175% |
  | ETHTMN | 0.267% | 0.191% |
  | TRXTMN | 0.001% | 0.624% |

  Use the p25-p75 range, not one reading. The bot should read the live book before every order.
- Across both runs, BTCTMN's spread ranged from 0.06% to 0.34%, against 0.00-0.09% for USDTTMN. Even the
  second most important market can cost more to cross than a whole maker fee.

The full table of all 187 TMN markets is in the appendix at the end of this file.

---

## 2. Fees (source: Wallex's own fee table)

Wallex's commission page https://wallex.ir/commission is fed by the public JSON endpoint
`GET https://api.wallex.ir/v2/structured-fee` (fetched and checked 2026-10-21). Levels are based on the
account's trailing 30-day volume (`recent_days: 30`):

| TMN markets | 30-day volume from | Maker | Taker |
|---|---|---|---|
| Level 1 | 0 | 0.25% | 0.30% |
| Level 2 | 100M toman | 0.20% | 0.25% |
| Level 3 | 1B | 0.17% | 0.20% |
| Level 4 | 3B | 0.14% | 0.17% |
| Level 5 | 10B | 0.12% | 0.15% |
| Level 6 | 20B | 0.10% | 0.13% |

USDT-quoted markets: Level 1 0.10% maker / 0.13% taker; Level 2 (from 500k USDT) 0.05% / 0.08%.

- A fresh 5M account starts at Level 1. It would need about 100M toman of traded volume (about 10 full
  round trips of the whole account) to reach Level 2.
- The account's actual rates are returned by the authenticated `GET /v1/account/fee`
  (https://developers.wallex.ir/docs/basic-services-account-fee). This was not called here (it needs a key).
  Check it on the trade-credit sub-account on day 1, because nothing public says whether credit
  accounts pay the same rates.
- Zero-fee markets: `isZeroFee` is true only for USDSUSDT today; no TMN market is zero-fee.
- The fee is charged in the asset you receive (`feeAsset` in fills). For example, a BUY on BTCTMN is
  charged in BTC (example in https://api-docs.wallex.ir; fields `fee`, `feeAsset`, `makerFeeCoefficient`,
  `takerFeeCoefficient` in https://developers.wallex.ir/docs/spot-create-order). Size the SELL quantity
  from the actual post-fee balance, not from the ordered quantity.

**Round-trip cost at Level 1**, including the spread range seen in the two snapshot runs:

| Market | Maker in, maker out | Taker in, taker out (fees + full spread) |
|---|---|---|
| USDTTMN | 0.50% | 0.60-0.69% |
| XAUTTMN | 0.50% | 0.60-0.71% |
| BTCTMN | 0.50% | 0.66-0.94% |
| ETHTMN | 0.50% | 0.73-1.0% |

The final forced conversion back to toman costs one more exit (0.25-0.30% plus half a spread) on
whatever is still held.

---

## 3. Trade credit (اعتبار معاملاتی): what is public

Confirmed from official sources:

- **What it is.** It is a credit line paid in toman or USDT on top of your own collateral. You can use
  it for spot, instant trade and convert. Each credit account is a separate account with its own wallet,
  its own API key and its own order history (https://developers.wallex.ir/docs/trade-credit-intro).
- **API access.** Send the credit account's API key plus a `sub-account-client-id` header
  (https://developers.wallex.ir/docs/spot-intro). Orders go to `POST /v1/account/orders`
  (`symbol`, `type` LIMIT/MARKET/STOP_LIMIT/STOP_MARKET, `side`, `price`, `quantity`, optional unique
  `client_id`), rate limited to **20 order requests per 10 seconds**
  (https://developers.wallex.ir/docs/spot-create-order).
- **Allowed markets.** The public package detail endpoint `GET /v1/prop/packages/{id}` returns
  `unavailableMarkets`. The list is identical for packages 165, 166 and 181. Subtracting it from
  `/v1/markets` leaves **62 TMN markets** and 58 USDT markets that credit accounts can trade (saved in
  `trade_credit_markets.json`). All ten markets downloaded below are allowed. Notable exclusions include
  **ZECTMN, XMRTMN, DASHTMN, GRAMTMN and ASTERTMN**. The hackathon's own package may have a different
  list, so check it once credentials arrive.
- **Liquidation mechanics.** These come from the public `GET /v1/prop/packages` and
  https://developers.wallex.ir/docs/trade-credit-types.
  - Every 30-day TMN package has collateral equal to 10% of the loan.
  - The warning limit is loan × 1.07 and the liquidation limit is loan × 1.04.
    - Example: the 10M package has 1M collateral, warning at 10.7M and liquidation at 10.4M.
  - Measured against total equity (collateral + loan), that is only **2.7% to the warning level and 5.45%
    to forced liquidation**.
  - The 7-day packages have different buffers, from 4.5% to 16%.
  - There is also a daily fee, 12,000 toman per day on the 10M package (0.12% of the loan per day).
  - The Wallex blog says nothing is returned after liquidation
    (https://wallex.ir/blog/wallex-trading-platform-tutorial/).
  - **If the hackathon's 5M account follows the 10%-collateral pattern, a 5-6% drawdown on a fully
    invested book ends the run.** That makes the "low max drawdown" criterion a survival constraint too.
    Ask the organisers what the warning and liquidation limits are for the challenge account.

Not found anywhere public: whether trading fees differ on credit accounts, and the exact package or limits
used for the hackathon.

---

## 4. Wallex MCP server: what exists

Searched: the developers.wallex.ir docs (113 pages in its sitemap), api-docs.wallex.ir, the wallex.ir
routes (`/mcp` and `/hackathon` return 404), the GitHub user `wallex` (no public repos), npm, PyPI, mcp.so,
and Persian and English web searches.

- **No official Wallex MCP server is published anywhere public.** The organisers must supply the URL or
  package themselves.
- Two unofficial community servers exist. Both are small and unmaintained-looking, and both need an API key:
  - https://github.com/SajjadKiani/wallex-mcp-server
    - Python FastMCP, stdio transport, configuration from environment variables.
    - Run with `fastmcp run wallex_mcp/server.py`, or with `fastmcp dev ...` during development.
    - It can optionally sit behind the `mcpo` proxy: `mcpo --port 8001 --api-key "wallex-local" -- fastmcp run wallex_mcp/server.py`.
      That `--api-key` is the proxy's own key, not a Wallex key.
    - Tools: `get_balance`, `place_order`, `cancel_order`. About 2 stars, last push May 2025.
    - Listed on https://mcp.so/zh/servers/wallex-mcp-server.
  - https://github.com/mhsenpc/wallex_mcp_server
    - Go, built from source, stdio transport, `WALLEX_API_KEY` environment variable.
    - Tools: `get_account_balances`, `get_historical_data_ohlc`.
- Unofficial SDKs: PyPI `wallex` 0.5.2 (https://github.com/amiwrpremium/wallex), `wallex-python`,
  `pywallex`; npm `wallex`, `ts-wallex`. The only official SDKs are the Centrifugo websocket clients
  (https://developers.wallex.ir/docs/socket-intro).

The bot should therefore talk to the REST API directly. Wrap our own calls as tools if the "AI
involvement" story needs MCP, rather than depending on an unofficial server that can place orders.

---

## 5. Candle data notes (API quirks found while downloading)

These behaviours are not documented; they were probed on 2026-10-21.

| Requested `resolution` | What the server actually returns | Per-call cap seen |
|---|---|---|
| `1` | 1-minute bars | about 1.25 days; a 2-day window returns `no_data` |
| `15` | **1-minute bars** (not 15m) | about 20 days (28,800 bars) |
| `60` | 1-hour bars stamped on UTC hours (Tehran XX:30) | 720 bars (30 days) |
| `240` | **1-hour bars** | 720 bars |
| `1D` | daily bars | 800 bars |

`fetch_candles.py` measures the step the server returned and resamples locally to the requested size.
15m files are built from 1-minute bars. A native-vs-rebuilt cross-check found 99.8-99.9% of hourly closes
identical; the rest are a few server-side revisions (2026-09-26 13:30 and 2026-10-01 03:30-04:30 Tehran).

- Candle `v` is base-asset volume and is **about 2x** `/v1/markets` `24h_volume` (both sides seem to be
  counted). `analyze.py` halves it when it reports toman volume.
- No forward-filling: hours or minutes without data are absent from the CSVs.

<!-- BEGIN:manifest -->
Fetched 2026-10-21T14:05:47+00:00 from `https://api.wallex.ir/v1/udf/history`.

| File | Rows / expected | Server sent | Longest gap (min) |
|---|---|---|---|
| USDTTMN_1.csv | 9461 / 10080 | 60s bars | 620 |
| USDTTMN_15.csv | 5688 / 5760 | 60s bars (resampled locally) | 615 |
| USDTTMN_60.csv | 1423 / 1440 | 3600s bars | 660 |
| BTCTMN_1.csv | 10067 / 10080 | 60s bars | 14 |
| BTCTMN_15.csv | 5755 / 5760 | 60s bars (resampled locally) | 90 |
| BTCTMN_60.csv | 1439 / 1440 | 3600s bars | 120 |
| ETHTMN_1.csv | 10041 / 10080 | 60s bars | 40 |
| ETHTMN_15.csv | 5753 / 5760 | 60s bars (resampled locally) | 90 |
| ETHTMN_60.csv | 1439 / 1440 | 3600s bars | 120 |
| XAUTTMN_1.csv | 10055 / 10080 | 60s bars | 14 |
| XAUTTMN_15.csv | 5755 / 5760 | 60s bars (resampled locally) | 90 |
| XAUTTMN_60.csv | 1439 / 1440 | 3600s bars | 120 |
| PEPETMN_1.csv | 10066 / 10080 | 60s bars | 15 |
| PEPETMN_15.csv | 5755 / 5760 | 60s bars (resampled locally) | 90 |
| PEPETMN_60.csv | 1439 / 1440 | 3600s bars | 120 |
| PUMPTMN_1.csv | 10067 / 10080 | 60s bars | 14 |
| PUMPTMN_15.csv | 5755 / 5760 | 60s bars (resampled locally) | 90 |
| PUMPTMN_60.csv | 1439 / 1440 | 3600s bars | 120 |
| DOGETMN_1.csv | 10064 / 10080 | 60s bars | 16 |
| DOGETMN_15.csv | 5755 / 5760 | 60s bars (resampled locally) | 90 |
| DOGETMN_60.csv | 1439 / 1440 | 3600s bars | 120 |
| SOLTMN_1.csv | 10058 / 10080 | 60s bars | 23 |
| SOLTMN_15.csv | 5754 / 5760 | 60s bars (resampled locally) | 90 |
| SOLTMN_60.csv | 1439 / 1440 | 3600s bars | 120 |
| XRPTMN_1.csv | 10060 / 10080 | 60s bars | 19 |
| XRPTMN_15.csv | 5755 / 5760 | 60s bars (resampled locally) | 90 |
| XRPTMN_60.csv | 1439 / 1440 | 3600s bars | 120 |
| BNBTMN_1.csv | 10064 / 10080 | 60s bars | 14 |
| BNBTMN_15.csv | 5755 / 5760 | 60s bars (resampled locally) | 90 |
| BNBTMN_60.csv | 1439 / 1440 | 3600s bars | 120 |
<!-- END:manifest -->

---

## 6. What the data says

Ten markets: USDTTMN, BTCTMN, ETHTMN, XAUTTMN, PEPETMN, PUMPTMN, DOGETMN, SOLTMN, XRPTMN, BNBTMN.

- Returns are in toman, using 60 days of 1h bars (2026-08-22 to 2026-10-21) and 60 days of 15m bars.
- "Daily" means a Tehran calendar day.
- Gaps are forward-filled for return calculations, so a gap shows up as one move.
- The 1-minute column covers the last 7 days.

<!-- BEGIN:stats -->
| Market | 60d return % | 60d max DD % (buy & hold) | Median abs 15m ret % | Median abs 60m ret % | Avg daily range % | 1h move >1% (share of hours) | 1h move >2% | 4h move >1% | 4h move >2% | Corr w/ BTCTMN 1h / 1d | Corr w/ USDTTMN 1h / 1d | Corr w/ BTC in USD terms (1h) | 1m bars with zero volume % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| USDTTMN | 37.9 | -6.6 | 0.051 | 0.118 | 2.40 | 1.5% | 0.2% | 11.1% | 0.6% | 0.59 / 0.55 | 1.00 / 1.00 | n/a | 48 |
| BTCTMN | 51.9 | -10.1 | 0.115 | 0.234 | 3.70 | 4.9% | 0.6% | 20.6% | 3.3% | 1.00 / 1.00 | 0.59 / 0.55 | n/a | 42 |
| ETHTMN | 41.9 | -17.8 | 0.159 | 0.297 | 4.43 | 7.9% | 1.1% | 28.6% | 7.2% | 0.79 / 0.92 | 0.48 / 0.49 | 0.72 | 63 |
| BNBTMN | 49.2 | -15.1 | 0.152 | 0.295 | 4.18 | 6.7% | 0.7% | 28.6% | 6.4% | 0.70 / 0.80 | 0.53 / 0.57 | 0.57 | 75 |
| DOGETMN | 26.4 | -24.1 | 0.203 | 0.388 | 5.80 | 15.8% | 2.6% | 40.6% | 13.6% | 0.69 / 0.80 | 0.34 / 0.25 | 0.64 | 74 |
| PEPETMN | 59.0 | -19.7 | 0.262 | 0.510 | 7.84 | 22.9% | 6.7% | 47.5% | 19.4% | 0.60 / 0.67 | 0.22 / 0.09 | 0.59 | 51 |
| PUMPTMN | 39.9 | -44.4 | 0.411 | 0.777 | 11.43 | 39.7% | 15.8% | 65.6% | 42.2% | 0.42 / 0.41 | 0.16 / 0.17 | 0.41 | 38 |
| SOLTMN | 58.4 | -20.6 | 0.203 | 0.396 | 5.24 | 14.0% | 2.2% | 36.4% | 11.9% | 0.70 / 0.82 | 0.37 / 0.30 | 0.64 | 77 |
| XAUTTMN | 24.3 | -6.7 | 0.082 | 0.194 | 2.61 | 3.1% | 0.3% | 12.5% | 1.1% | 0.55 / 0.63 | 0.69 / 0.79 | 0.27 | 76 |
| XRPTMN | 29.0 | -20.6 | 0.205 | 0.385 | 5.57 | 14.9% | 2.4% | 38.9% | 13.6% | 0.71 / 0.83 | 0.35 / 0.25 | 0.67 | 67 |
<!-- END:stats -->

### The toman/dollar leg dominates

- **USDTTMN rose 37.9% in 60 days** (191,460 to 263,941 toman). That is +0.54% a day compounded, and 63%
  of days were up. Its own worst peak-to-trough was -6.6%.
- The move was not steady:
  - It came mostly from 2026-08-22 to 2026-10-04 (+15.7% over the last 30 days alone).
  - The **last 14 days are flat to slightly down (-0.18%)**.
  - The last 7 days are +0.15%, in a range of about 257k-269k.
  - Do not assume the 60-day drift of about 1.2% per 54 h will continue during the challenge.
- **BTCTMN rose 51.9% in toman, but only +10.2% in dollar terms** (BTCTMN / USDTTMN). In log terms the
  falling toman explains most of every market's toman return:
  - about 70% for PEPE and SOL, 77% for BTC, 80% for BNB, and 92-96% for ETH and PUMP;
  - more than all of it for DOGE, XRP and XAUT, which fell in dollar terms (-8.3%, -6.4% and -9.8%).
- Correlation with USDTTMN:
  - Hourly: 0.59 for BTC, 0.48 for ETH, 0.69 for XAUT.
  - Daily: 0.55 for BTC and 0.79 for XAUT.
  - Meme coins are barely linked to it (PEPE 0.09 daily).
- After stripping out the USDT leg, XAUT is nearly independent of BTC (0.27). It is the one real
  diversifier.

### Size of moves versus costs

- At Level 1 a round trip costs 0.50% (maker both ways) to about 0.9% (taker on BTC/ETH).
- For BTCTMN:
  - The median absolute move is 0.23% over 1 h, 0.48% over 4 h and 1.5% over a Tehran day.
  - Only 4.9% of hours move more than 1%, and 21% of 4-hour windows do.
- For ETHTMN: median absolute move 0.63% over 4 h and 1.96% a day, and 29% of 4-hour windows move more
  than 1%.
- For USDTTMN: median 0.25% over 4 h and 0.93% a day.
- Signals on 15-minute or 1-hour horizons cannot beat fees. Holding periods need to be **4 h to a day or
  more**, with targets of at least 1.2-1.5%.
- Lag-1 autocorrelation of returns is slightly negative at 1 h and 4 h for every market (-0.02 to -0.15).
  - Part of this is bid-ask bounce in last-trade closes.
  - There is no evidence of short-horizon momentum.
  - USDTTMN daily returns have +0.15 autocorrelation (trend persistence), but with 59 days that is not
    statistically significant.

### When the market is active (Tehran time, 60 days)

Hourly bars start at XX:30 Tehran.

- **BTCTMN**
  - Volume is lowest from 02:30 to 06:30, at about 0.6-0.8B toman per hour (median).
  - It is highest from 16:30 to 20:30, at about 3.5-4.3B per hour.
  - Volatility peaks in the 10:30 and 17:30 bars.
- **USDTTMN**
  - Volume peaks from 09:30 to 13:30, at 20-25B per hour; the 10:30 bar has the largest median move.
  - Overnight it is very quiet.
- **Weekdays**
  - Saturday is BTC's quietest day.
  - **Friday is USDTTMN's quietest day (about half of Monday's volume).**
  - The challenge ends Friday 23:50, so do the final conversion to toman earlier on Friday, in staged
    maker orders, rather than in the last minutes.

### Recommended markets for a low-churn, fee-aware swing strategy

1. **USDTTMN: the core, and the "cash" alternative to toman.**
   - It has the tightest spread (0.00-0.09%) and the deepest book (about 4.6B toman within 1%).
   - It has the lowest volatility and the smallest drawdown of anything here, and MARKET orders work.
   - Toman depreciation gives it a positive long-run drift, but that drift has stalled for two weeks.
   - Use a regime filter, for example a daily trend or a breakout above the 2-week range. Do not hold
     it blindly.
2. **XAUTTMN (tokenised gold): the diversifier.**
   - Spread 0.003-0.11%, about 1.1B toman depth within 1%, max drawdown -6.7%, and MARKET orders enabled.
   - It tracks USDTTMN (daily correlation 0.79) plus gold.
   - Its USD-terms correlation with BTC is only 0.27.
   - Its moves are small (median 0.34% over 4 h), so it needs day-scale holds.
3. **BTCTMN: the main crypto leg.**
   - It has a deep crypto book (0.6-0.7B within 1%) and MARKET orders enabled.
   - Its main cost is the 0.06-0.34% spread, so enter and exit with post-only-style LIMIT orders.
4. **ETHTMN: the higher-beta alternative to BTC.**
   - About 30% more movement per day than BTC.
   - Daily correlation with BTC is 0.92, so treat BTC and ETH as one risk bucket, not two.
5. Optional: **SOLTMN or XRPTMN**, only with small size and LIMIT orders.
   - Their 1h moves exceed 1% in about 14-15% of hours.
   - But their spreads run 0.13-0.45% and their 60-day buy-and-hold drawdowns are about -21%.

Avoid for this challenge:

- **PUMPTMN**: 60-day max drawdown -44%, and 4h moves exceed 2% 42% of the time.
- **PEPETMN**: drawdown -20%, a spread that swung between 0.03% and 0.55%, and LIMIT-only.
- **USOONTMN**: volume of suspect quality, and it is an oil-ETF token rather than crypto.
- **TRXTMN**: the spread jumps between about 0 and 0.76%.
- **Anything not on the trade-credit list**, such as ZECTMN.

### Red flags

- **USDTTMN has gaps while every other market keeps trading.** There are no USDTTMN candles from about
  02:30 to 08:30 on 2026-10-13, or from 01:20 to 11:40 on 2026-10-20 (Tehran). The 1-minute and 1-hour
  series agree on both gaps.
  - These look like overnight trading halts on the USDT/TMN book. BTC, ETH and the rest printed normally.
  - The bot must tolerate no fills and a stale USDTTMN price overnight.
  - It must never derive a "toman value" from a USDTTMN last price that is hours old.
- **A shared outage hit every market** on 2026-09-22 from 15:30 to 17:30 Tehran (1-2 bars missing).
  Plan for at least one exchange-wide stall during the 54 h.
- **Trading is very bursty.**
  - Even on USDTTMN, 48% of 1-minute bars in the last 7 days had zero volume (BTC 42%, ETH 63%, SOL 77%).
  - Minute-level signals are mostly noise, and LIMIT orders may sit unfilled for minutes.
- **Spreads are wide and unstable outside USDT and XAUT** (see the p25-p75 range in the top-15 table).
- **The candle API has undocumented caps and mislabels resolutions** (section 5).
  - The volume field is double-counted relative to the ticker.
- **Trade-credit liquidation risk** (section 3).
  - With public package terms, the warning comes at about -2.7% of equity and liquidation at about -5.45%.
  - Cap gross exposure and use hard stops well inside that.

---

## Appendix: all 187 TMN markets (snapshot 2026-10-21)

"MARKET" means MARKET orders are enabled (`isMarketTypeEnable`). "Credit" means the market is not in
`unavailableMarkets` of the public trade-credit packages. "dp" means decimal places.

<!-- BEGIN:full -->
| # | Market | Name | 24h vol (M TMN) | Spread median % | Depth 1% thinner side (M TMN) | Price dp | Qty dp | Min qty | Min notional (TMN) | MARKET | Credit |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | USDTTMN | Tether - Toman | 224,743 | 0.001 | 4,631.9 | 0 | 2 | 0.01 | 50,000 | yes | yes |
| 2 | BTCTMN | Bitcoin - Toman | 78,242 | 0.175 | 629.2 | 0 | 8 | 1e-08 | 50,000 | yes | yes |
| 3 | PEPETMN | Pepe - Toman | 110,440 | 0.218 | 648.5 | 4 | 0 | 1 | 50,000 | no | yes |
| 4 | XAUTTMN | - Toman | 29,112 | 0.024 | 1,138.7 | 0 | 5 | 1e-05 | 50,000 | yes | yes |
| 5 | ETHTMN | Ethereum - Toman | 37,265 | 0.191 | 808.2 | 0 | 5 | 1e-05 | 50,000 | yes | yes |
| 6 | XRPTMN | Ripple - Toman | 35,236 | 0.171 | 326.0 | 0 | 1 | 0.1 | 50,000 | no | yes |
| 7 | PUMPTMN | Pump fun - Toman | 85,616 | 0.160 | 171.0 | 0 | 1 | 0.1 | 50,000 | no | yes |
| 8 | ARBTMN | Arbitrum - Toman | 30,943 | 0.181 | 328.7 | 0 | 4 | 0.0001 | 50,000 | no | yes |
| 9 | SOLTMN | Solana - Toman | 31,824 | 0.269 | 449.4 | 0 | 5 | 1e-05 | 50,000 | no | yes |
| 10 | STMN | SONIC - Toman | 10,249 | 0.145 | 240.1 | 0 | 2 | 0.01 | 50,000 | no | yes |
| 11 | DOGETMN | Dogecoin - Toman | 21,368 | 0.259 | 230.3 | 0 | 1 | 0.1 | 50,000 | no | yes |
| 12 | USOONTMN | United States Oil Fund (Ondo Tokenized) - TOMAN | 64,786 | 0.023 | 41.6 | 0 | 4 | 0.0001 | 50,000 | no | yes |
| 13 | BNBTMN | Binance Coin - Toman | 26,571 | 0.275 | 84.5 | 0 | 5 | 1e-05 | 50,000 | no | yes |
| 14 | LTCTMN | Litcoin - Toman | 7,776 | 0.295 | 269.8 | 0 | 5 | 1e-05 | 50,000 | no | yes |
| 15 | GRAMTMN | Gram - TOMAN | 4,440 | 0.184 | 229.4 | 0 | 2 | 0.01 | 50,000 | no | no |
| 16 | TRXTMN | TRON - Toman | 12,177 | 0.624 | 258.4 | 0 | 1 | 0.1 | 50,000 | no | yes |
| 17 | PAXGTMN | Paxos Gold - Toman | 1,579 | 0.187 | 499.9 | 0 | 6 | 1e-06 | 50,000 | no | no |
| 18 | HYPETMN | Hyperliquid - Toman | 6,368 | 0.535 | 332.7 | 0 | 3 | 0.001 | 50,000 | no | yes |
| 19 | APETMN | ApeCoin - Toman | 10,993 | 0.536 | 136.3 | 0 | 2 | 0.01 | 50,000 | no | yes |
| 20 | ADATMN | Cardano - Toman | 6,951 | 0.285 | 100.4 | 0 | 1 | 0.1 | 50,000 | no | yes |
| 21 | LINKTMN | Chainlink - Toman | 3,709 | 0.571 | 493.3 | 0 | 2 | 0.01 | 50,000 | no | yes |
| 22 | UNITMN | Uniswap - Toman | 6,478 | 0.570 | 206.9 | 0 | 2 | 0.01 | 50,000 | no | yes |
| 23 | SHIBTMN | Shiba Inu - Toman | 3,773 | 0.499 | 242.1 | 4 | 0 | 1 | 50,000 | no | yes |
| 24 | DASHTMN | Dash - Toman | 6,093 | 0.545 | 113.7 | 0 | 5 | 1e-05 | 50,000 | no | no |
| 25 | ZECTMN | Zcash - Toman | 29,294 | 0.834 | 158.4 | 0 | 4 | 0.0001 | 50,000 | no | no |
| 26 | ASTERTMN | ASTER - Toman | 5,026 | 0.570 | 88.1 | 0 | 2 | 0.01 | 50,000 | no | no |
| 27 | XMRTMN | Monero - Toman | 6,499 | 0.404 | 182.3 | 0 | 4 | 0.0001 | 50,000 | no | no |
| 28 | SANDTMN | Sandbox - Toman | 6,384 | 0.581 | 77.4 | 0 | 1 | 0.1 | 50,000 | no | yes |
| 29 | COPXONTMN | Global X Copper Miners Tokenized ETF (Ondo) - TOMAN | 2,116 | 0.177 | 61.9 | 0 | 4 | 0.0001 | 50,000 | no | yes |
| 30 | QNTTMN | Quant - Toman | 2,240 | 0.567 | 167.0 | 0 | 3 | 0.001 | 50,000 | no | no |
| 31 | WLDTMN | Worldcoin - Toman | 3,122 | 0.478 | 86.4 | 0 | 2 | 0.01 | 50,000 | no | yes |
| 32 | SUITMN | Sui - Toman | 8,974 | 0.877 | 136.9 | 0 | 2 | 0.01 | 50,000 | no | yes |
| 33 | AVAXTMN | Avalanche - Toman | 3,220 | 0.571 | 65.4 | 0 | 5 | 1e-05 | 50,000 | no | yes |
| 34 | CAKETMN | PancakeSwap - Toman | 1,702 | 0.626 | 340.4 | 0 | 2 | 0.01 | 50,000 | no | yes |
| 35 | AAVETMN | Aave - Toman | 3,490 | 0.578 | 91.8 | 0 | 5 | 1e-05 | 50,000 | no | yes |
| 36 | CRVTMN | Curve DAO Token - Toman | 2,924 | 0.820 | 349.9 | 0 | 2 | 0.01 | 50,000 | no | no |
| 37 | ONDOTMN | Ondo - Toman | 952 | 0.583 | 292.9 | 0 | 2 | 0.01 | 50,000 | no | no |
| 38 | UNGONTMN | US Natural Gas Fund Tokenized Stock (Ondo) - TOMAN | 531 | 0.334 | 299.2 | 0 | 3 | 0.001 | 50,000 | no | no |
| 39 | FILTMN | Filecoin - Toman | 3,909 | 0.721 | 30.8 | 0 | 2 | 0.01 | 50,000 | no | yes |
| 40 | BCHTMN | Bitcoin Cash - Toman | 2,770 | 1.104 | 196.4 | 0 | 5 | 1e-05 | 50,000 | no | yes |
| 41 | FLOKITMN | FLOKI - Toman | 2,046 | 0.668 | 79.6 | 4 | 0 | 1 | 50,000 | no | yes |
| 42 | BATTMN | Basic Attention Token - Toman | 1,866 | 0.488 | 19.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 43 | ENATMN | Ethena - Toman | 2,268 | 0.986 | 84.1 | 0 | 2 | 0.01 | 50,000 | no | yes |
| 44 | ATOMTMN | Cosmos - Toman | 2,163 | 0.619 | 64.6 | 0 | 2 | 0.01 | 50,000 | no | yes |
| 45 | NEARTMN | Near - Toman | 3,200 | 1.877 | 131.6 | 0 | 2 | 0.01 | 50,000 | no | no |
| 46 | GALATMN | Gala - Toman | 3,099 | 0.581 | 0.2 | 0 | 1 | 0.1 | 50,000 | no | yes |
| 47 | HMSTRTMN | Hamster Kombat - Toman | 1,925 | 0.513 | 20.5 | 1 | 2 | 0.01 | 50,000 | no | no |
| 48 | USDSTMN | USDS - TOMAN | 602 | 0.653 | 1,502.1 | 0 | 2 | 0.01 | 50,000 | no | no |
| 49 | ATMN | VAULTA - Toman | 2,857 | 1.264 | 40.2 | 0 | 2 | 0.01 | 50,000 | no | no |
| 50 | HBARTMN | Hedera - Toman | 1,868 | 1.165 | 110.1 | 0 | 2 | 0.01 | 50,000 | no | yes |
| 51 | POLTMN | POL (MATIC) - Toman | 1,164 | 0.604 | 65.5 | 0 | 1 | 0.1 | 50,000 | no | yes |
| 52 | SKYTMN | Sky - Toman | 464 | 0.585 | 93.6 | 0 | 1 | 0.1 | 50,000 | no | no |
| 53 | DYDXTMN | dYdX - Toman | 59 | 0.302 | 211.5 | 0 | 2 | 0.01 | 50,000 | no | yes |
| 54 | DOTTMN | Polkadot - Toman | 1,248 | 0.775 | 32.1 | 0 | 2 | 0.01 | 50,000 | no | yes |
| 55 | PENGUTMN | Pudgy Penguins - Toman | 1,646 | 0.704 | 48.8 | 0 | 1 | 0.1 | 50,000 | no | yes |
| 56 | DOGSTMN | - Toman | 1,751 | 0.709 | 39.9 | 2 | 2 | 0.01 | 50,000 | no | no |
| 57 | FORMTMN | Four - Toman | 712 | 0.751 | 97.5 | 1 | 2 | 0.01 | 50,000 | no | yes |
| 58 | SLVONTMN | iShares Silver Trust Tokenized ETF (Ondo) - Toman | 2,078 | 0.935 | 2.2 | 0 | 3 | 0.001 | 50,000 | no | yes |
| 59 | NOTTMN | NOTCOIN - Toman | 2,159 | 1.028 | 16.0 | 1 | 0 | 1 | 50,000 | no | no |
| 60 | ONETMN | Harmony - Toman | 1,222 | 1.143 | 48.1 | 1 | 2 | 0.01 | 50,000 | no | no |
| 61 | RAYTMN | RAYDIUM - Toman | 570 | 0.513 | 14.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 62 | WTMN | Wormhole - Toman | 2,645 | 0.995 | 0.1 | 0 | 2 | 0.01 | 50,000 | no | no |
| 63 | SEITMN | Sei - Toman | 3,817 | 1.852 | 0.5 | 0 | 2 | 0.01 | 50,000 | no | yes |
| 64 | ETCTMN | Ethereum Classic - Toman | 536 | 1.361 | 474.9 | 0 | 5 | 1e-05 | 50,000 | no | yes |
| 65 | BOMETMN | Bome - Toman | 1,631 | 1.786 | 26.6 | 1 | 2 | 0.01 | 50,000 | no | no |
| 66 | BANKTMN | Lorenzo Protocol - Toman | 425 | 0.989 | 31.9 | 0 | 2 | 0.01 | 50,000 | no | no |
| 67 | SUSHITMN | SushiSwap - Toman | 158 | 0.646 | 81.5 | 1 | 3 | 0.001 | 50,000 | no | yes |
| 68 | USDCTMN | USDC - Toman | 470 | 0.764 | 51.3 | 0 | 2 | 0.01 | 50,000 | no | yes |
| 69 | MAGICTMN | Magic - Toman | 1,306 | 1.790 | 0.5 | 0 | 2 | 0.01 | 50,000 | no | no |
| 70 | GRTTMN | Graph - Toman | 234 | 0.637 | 11.6 | 0 | 2 | 0.01 | 50,000 | no | no |
| 71 | ICPTMN | Internet Computer - Toman | 963 | 1.625 | 1.5 | 0 | 2 | 0.01 | 50,000 | no | no |
| 72 | EIGENTMN | EigenLayer - Toman | 4 | 0.553 | 320.7 | 0 | 4 | 0.0001 | 50,000 | no | yes |
| 73 | SKLTMN | Skale Network - Toman | 29 | 1.735 | 40.1 | 0 | 2 | 0.01 | 50,000 | no | no |
| 74 | BONKTMN | Bonk - Toman | 317 | 0.886 | 3.7 | 4 | 0 | 1 | 50,000 | no | no |
| 75 | TAOTMN | Bittensor - Toman | 478 | 0.858 | 0.1 | 0 | 3 | 0.001 | 50,000 | no | no |
| 76 | ORCATMN | Orca - Toman | 1,051 | 1.415 | 0.1 | 0 | 2 | 0.01 | 50,000 | no | no |
| 77 | WIFTMN | dogwifhat - Toman | 376 | 0.781 | 26.0 | 1 | 2 | 0.01 | 50,000 | no | no |
| 78 | RUNETMN | THORChain - Toman | 1,735 | 2.236 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 79 | SNXTMN | Synthetix - Toman | 129 | 0.624 | 12.8 | 0 | 2 | 0.01 | 50,000 | no | no |
| 80 | ENJTMN | Enjin Coin - Toman | 225 | 1.057 | 1.2 | 0 | 3 | 0.001 | 50,000 | no | yes |
| 81 | FLOWTMN | Flow - Toman | 565 | 1.518 | 1.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 82 | ALICETMN | MyNeighborAlice - Toman | 85 | 0.693 | 26.8 | 0 | 2 | 0.01 | 50,000 | no | yes |
| 83 | TTMN | Threshold - Toman | 3 | 0.583 | 10.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 84 | FETTMN | Fetch.ai - Toman | 456 | 2.506 | 0.0 | 0 | 4 | 0.0001 | 50,000 | no | no |
| 85 | MOGTMN | Mog Coin - Toman | 117 | 1.757 | 23.8 | 4 | 1 | 0.1 | 50,000 | no | yes |
| 86 | MANATMN | Decentraland - Toman | 119 | 1.143 | 36.6 | 0 | 1 | 0.1 | 50,000 | no | yes |
| 87 | RENDERTMN | Render - Toman | 201 | 1.185 | 18.2 | 0 | 2 | 0.01 | 50,000 | no | no |
| 88 | CVXTMN | Convex Finance - Toman | 591 | 1.439 | 0.4 | 0 | 2 | 0.01 | 50,000 | no | no |
| 89 | IMXTMN | Immutable - Toman | 19 | 1.068 | 45.0 | 0 | 2 | 0.01 | 50,000 | no | yes |
| 90 | STRKTMN | Starknet - Toman | 1,165 | 2.257 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 91 | DEXETMN | DeXe - Toman | 551 | 2.150 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 92 | ALGOTMN | Algorand - Toman | 60 | 0.995 | 0.7 | 0 | 2 | 0.01 | 50,000 | no | no |
| 93 | TIATMN | Celestia - Toman | 241 | 2.832 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 94 | MMTTMN | Momentum - Toman | 336 | 3.285 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 95 | ETHFITMN | ether.fi - Toman | 1,458 | 2.725 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 96 | 1BBABYDOGETMN | 1B Baby Doge Coin - Toman | 34 | 1.304 | 6.1 | 0 | 8 | 1e-08 | 50,000 | no | no |
| 97 | TNSRTMN | Tensor - Toman | 275 | 2.966 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | yes |
| 98 | 1INCHTMN | 1inch Network - Toman | 68 | 0.923 | 4.3 | 0 | 2 | 0.01 | 50,000 | no | yes |
| 99 | API3TMN | API3 - Toman | 10 | 0.632 | 8.9 | 0 | 2 | 0.01 | 50,000 | no | no |
| 100 | CHZTMN | Chiliz - Toman | 186 | 3.354 | 0.0 | 0 | 1 | 0.1 | 50,000 | no | yes |
| 101 | ELONTMN | Dogelon - Toman | 587 | 6.188 | 0.0 | 5 | 0 | 1 | 50,000 | no | yes |
| 102 | EGLDTMN | Elrond - Toman | 10 | 0.641 | 4.2 | 0 | 5 | 1e-05 | 50,000 | no | yes |
| 103 | NMRTMN | Numeraire - Toman | 2 | 1.120 | 0.2 | 0 | 3 | 0.001 | 50,000 | no | no |
| 104 | MNTTMN | Mantle - Toman | 21 | 0.689 | 3.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 105 | GMTTMN | STEPN - Toman | 30 | 1.461 | 0.6 | 0 | 1 | 0.1 | 50,000 | no | no |
| 106 | PYTHTMN | Pyth Network - Toman | 463 | 2.962 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 107 | LRCTMN | Loopring - Toman | 293 | 3.741 | 0.0 | 1 | 2 | 0.01 | 50,000 | no | no |
| 108 | APTTMN | Apt - Toman | 839 | 2.160 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 109 | TOSHITMN | TOSHI - Toman | 463 | 12.971 | 0.0 | 3 | 1 | 0.1 | 50,000 | no | no |
| 110 | YFITMN | yearn.finance - Toman | 0 | 0.641 | 22.9 | 0 | 5 | 1e-05 | 50,000 | no | yes |
| 111 | SOONTMN | SOON - Toman | 211 | 4.373 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 112 | XLMTMN | Stellar - Toman | 118 | 4.819 | 0.0 | 0 | 1 | 0.1 | 50,000 | no | yes |
| 113 | 2ZTMN | DoubleZero - Toman | 9 | 1.206 | 0.2 | 0 | 1 | 0.1 | 50,000 | no | no |
| 114 | METTMN | Meteora - Toman | 179 | 4.150 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 115 | KMNOTMN | Kamino Finance - Toman | 5 | 2.172 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 116 | JUPTMN | Jupiter - Toman | 652 | 5.798 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 117 | LPTTMN | Livepeer - Toman | 0 | 0.846 | 0.4 | 0 | 2 | 0.01 | 50,000 | no | no |
| 118 | MEMETMN | Memecoin - Toman | 15 | 2.230 | 0.0 | 0 | 4 | 0.0001 | 50,000 | no | no |
| 119 | OSMOTMN | Osmosis - Toman | 610 | 6.459 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 120 | EDUTMN | Open Campus - Toman | 220 | 4.807 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 121 | TURBOTMN | Turbo - Toman | 138 | 7.111 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 122 | AXSTMN | Axie Infinity - Toman | 245 | 4.631 | 0.0 | 0 | 5 | 1e-05 | 50,000 | no | yes |
| 123 | BTTCTMN | BitTorrent - Toman | 204 | 4.351 | 0.0 | 6 | 0 | 1 | 50,000 | no | no |
| 124 | FLRTMN | Flare - Toman | 89 | 3.658 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 125 | BICOTMN | Biconomy - Toman | 316 | 6.397 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 126 | KASTMN | Kaspa - Toman | 266 | 2.388 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 127 | KAITOTMN | KAITO - Toman | 10 | 1.846 | 0.1 | 0 | 2 | 0.01 | 50,000 | no | no |
| 128 | XPLTMN | Plasma - Toman | 206 | 9.927 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 129 | CELRTMN | Celer - Toman | 101 | 3.631 | 0.0 | 0 | 1 | 0.1 | 50,000 | no | no |
| 130 | ATHTMN | Aethir - Toman | 476 | 4.311 | 0.0 | 1 | 2 | 0.01 | 50,000 | no | no |
| 131 | TRBTMN | Tellor - Toman | 19 | 2.320 | 0.0 | 0 | 3 | 0.001 | 50,000 | no | no |
| 132 | SYRUPTMN | Maple Finance - Toman | 222 | 3.166 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 133 | MAJORTMN | MAJOR - Toman | 15 | 4.218 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 134 | JSTTMN | JUST - Toman | 0 | 2.312 | 0.0 | 0 | 1 | 0.1 | 50,000 | no | no |
| 135 | ZENTMN | Horizen - Toman | 25 | 3.089 | 0.0 | 0 | 3 | 0.001 | 50,000 | no | no |
| 136 | PEOPLETMN | ConstitutionDAO - Toman | 17 | 2.561 | 0.0 | 0 | 4 | 0.0001 | 50,000 | no | yes |
| 137 | MASKTMN | Mask Network - Toman | 0 | 7.506 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 138 | CATTMN | Cat - Toman | 282 | 7.778 | 0.0 | 4 | 1 | 0.1 | 50,000 | no | no |
| 139 | POLSTMN | PolkaStarter - Toman | 60 | 4.950 | 0.0 | 0 | 1 | 0.1 | 50,000 | no | no |
| 140 | SAHARATMN | Sahara AI - Toman | 2 | 2.290 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 141 | MORPHOTMN | Morpho - Toman | 3 | 3.853 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 142 | ZORATMN | ZORA - Toman | 0 | 2.308 | 0.0 | 2 | 1 | 0.1 | 50,000 | no | no |
| 143 | COOKIETMN | Cookie - Toman | 139 | 8.251 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | yes |
| 144 | LINEATMN | Linea - Toman | 26 | 6.906 | 0.0 | 0 | 1 | 0.1 | 50,000 | no | no |
| 145 | UMATMN | UMA - Toman | 62 | 8.256 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 146 | BIOTMN | Bio Protocol - Toman | 70 | 2.864 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 147 | AIXBTTMN | Aixbt - Toman | 12 | 2.259 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 148 | SUPERTMN | SuperVerse - Toman | 96 | 10.788 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 149 | NEIROTMN | Neiro - Toman | 3 | 3.512 | 0.0 | 2 | 1 | 0.1 | 50,000 | no | no |
| 150 | CATITMN | Catizen - Toman | 2 | 2.732 | 0.0 | 0 | 1 | 0.1 | 50,000 | no | no |
| 151 | PROVETMN | Succinct - Toman | 0 | 5.081 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 152 | AEROTMN | Aerodrome Finance - Toman | 4 | 3.805 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 153 | SLPTMN | Smooth Love Potion - Toman | 7 | 2.462 | 0.0 | 2 | 0 | 1 | 50,000 | no | no |
| 154 | IOTMN | Io - Toman | 3 | 6.799 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 155 | AEVOTMN | Aevo - Toman | 0 | 2.595 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 156 | BANDTMN | Band - Toman | 0 | 5.870 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 157 | VIRTUALTMN | Virtuals Protocol - Toman | 19 | 10.636 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 158 | CGPTTMN | ChainGPT - Toman | 10 | 7.552 | 0.0 | 2 | 2 | 0.01 | 50,000 | no | no |
| 159 | HOTTMN | Hot - Toman | 5 | 4.841 | 0.0 | 2 | 1 | 0.1 | 50,000 | no | no |
| 160 | MOVETMN | Movement - Toman | 0 | 4.023 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 161 | KAIATMN | Kaia - Toman | 21 | 4.603 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 162 | GIGGLETMN | Giggle Fund - Toman | 0 | 3.709 | 0.0 | 0 | 3 | 0.001 | 50,000 | no | no |
| 163 | WBTCTMN | Wrapped Bitcoin - Toman | 2 | 3.073 | 0.0 | 0 | 6 | 1e-06 | 50,000 | no | no |
| 164 | TSLAXTMN | Tesla tokenized stock - Toman | 0 | n/a | 0.0 | 0 | 3 | 0.001 | 50,000 | no | no |
| 165 | AGLDTMN | Adventure Gold - Toman | 32 | 6.393 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | yes |
| 166 | XTZTMN | Tezos - Toman | 6 | 19.104 | 0.0 | 0 | 1 | 0.1 | 50,000 | no | no |
| 167 | AIOZTMN | AIOZ Network - Toman | 0 | 5.517 | 0.0 | 0 | 1 | 0.1 | 50,000 | no | no |
| 168 | ZRXTMN | 0x Protocol - Toman | 0 | 11.541 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 169 | KITETMN | Kite - Toman | 0 | n/a | 0.0 | 0 | 1 | 0.1 | 50,000 | no | no |
| 170 | AMZNXTMN | Amazon tokenized stock - Toman | 0 | n/a | 0.0 | 0 | 3 | 0.001 | 50,000 | no | no |
| 171 | ZILTMN | Zilliqa - Toman | 0 | 16.296 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 172 | AAPLXTMN | Apple tokenized stock - Toman | 0 | n/a | 0.0 | 0 | 3 | 0.001 | 50,000 | no | no |
| 173 | LAYERTMN | Solayer - Toman | 0 | 10.263 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 174 | JASMYTMN | JasmyCoin - Toman | 0 | n/a | 0.0 | 0 | 0 | 1 | 50,000 | no | no |
| 175 | SAFETMN | Safe - Toman | 0 | 10.384 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 176 | NVDAXTMN | NVIDIA tokenized stock - Toman | 0 | n/a | 0.0 | 0 | 3 | 0.001 | 50,000 | no | no |
| 177 | CVCTMN | Civic - Toman | 0 | 4.400 | 0.0 | 2 | 2 | 0.01 | 50,000 | no | no |
| 178 | AVNTTMN | Avantis - Toman | 0 | 6.829 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 179 | METAXTMN | Meta tokenized stock - Toman | 0 | n/a | 0.0 | 0 | 4 | 0.0001 | 50,000 | no | no |
| 180 | TONTMN | Ton - Toman | 0 | n/a | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 181 | NEXOTMN | Nexo - Toman | 0 | 15.516 | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 182 | MEMEFITMN | MemeFi - Toman | 0 | n/a | 0.0 | 0 | 2 | 0.01 | 50,000 | no | no |
| 183 | MDTTMN | Measurable Data Token - Toman | 0 | n/a | 0.0 | 3 | 0 | 1 | 50,000 | no | no |
| 184 | HOODXTMN | Robinhood tokenized stock - Toman | 0 | n/a | 0.0 | 0 | 3 | 0.001 | 50,000 | no | no |
| 185 | COINXTMN | Coinbase tokenized stock - Toman | 0 | n/a | 0.0 | 0 | 3 | 0.001 | 50,000 | no | no |
| 186 | XTMN | X - Toman | 0 | n/a | 0.0 | 4 | 2 | 0.01 | 50,000 | no | no |
| 187 | PPLTONTMN | abrdn Physical Platinum Shares Tokenized ETF (Ondo) - TOMAN | 0 | n/a | 0.0 | 0 | 4 | 0.0001 | 50,000 | no | no |
<!-- END:full -->
