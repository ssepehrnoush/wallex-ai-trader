"""Regenerate the data tables inside research/MARKETS.md from the CSVs the other scripts write.

    python research/market_snapshot.py && python research/fetch_candles.py && python research/analyze.py
    python research/build_tables.py

Only the text between <!-- BEGIN:name --> and <!-- END:name --> markers is replaced; the prose is kept.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
MD = HERE / "MARKETS.md"


def fnum(x, nd=2):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "n/a"
    if v != v:
        return "n/a"
    return f"{v:,.{nd}f}"


def load_snapshot():
    with open(HERE / "markets_snapshot.csv", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def credit_set():
    p = HERE / "trade_credit_markets.json"
    return set(json.loads(p.read_text(encoding="utf-8"))["available_tmn"]) if p.exists() else set()


def yn(v):
    return "yes" if str(v).lower() == "true" else "no"


def top15(rows, credit):
    out = ["| # | Market | 24h vol (B TMN) | 7d avg/day (B TMN) | Spread median % (p25-p75) | Depth within 1% of mid, thinner side (M TMN) | Cost to sweep 5M TMN buy/sell % vs mid | Price dp / qty dp | MARKET orders | Trade credit |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows[:15]:
        out.append(f"| {r['liq_rank']} | {r['symbol']} | {fnum(float(r['vol24h_tmn'])/1e9, 1)} | {fnum(float(r['vol7d_avg_daily_tmn_est'])/1e9, 1)} | "
                   f"{fnum(r['spread_pct_median'], 3)} ({fnum(r['spread_pct_p25'], 3)}-{fnum(r['spread_pct_p75'], 3)}) | "
                   f"{fnum(float(r['min_side_depth_1pct_tmn'])/1e6, 0)} | {fnum(r['slip5m_buy_pct'], 3)} / {fnum(r['slip5m_sell_pct'], 3)} | "
                   f"{r['price_decimals']} / {r['qty_decimals']} | {yn(r['market_orders_enabled'])} | {'yes' if r['symbol'] in credit else 'no'} |")
    return "\n".join(out)


def full(rows, credit):
    out = ["| # | Market | Name | 24h vol (M TMN) | Spread median % | Depth 1% thinner side (M TMN) | Price dp | Qty dp | Min qty | Min notional (TMN) | MARKET | Credit |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r['liq_rank']} | {r['symbol']} | {r['name'].replace('|', '/')} | {fnum(float(r['vol24h_tmn'])/1e6, 0)} | "
                   f"{fnum(r['spread_pct_median'], 3)} | {fnum(float(r['min_side_depth_1pct_tmn'])/1e6, 1)} | {r['price_decimals']} | {r['qty_decimals']} | "
                   f"{r['min_qty']} | {fnum(r['min_notional_tmn'], 0)} | {yn(r['market_orders_enabled'])} | {'yes' if r['symbol'] in credit else 'no'} |")
    return "\n".join(out)


def stats():
    with open(HERE / "stats.csv", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    out = ["| Market | 60d return % | 60d max DD % (buy & hold) | Median abs 15m ret % | Median abs 60m ret % | Avg daily range % | 1h move >1% (share of hours) | 1h move >2% | 4h move >1% | 4h move >2% | Corr w/ BTCTMN 1h / 1d | Corr w/ USDTTMN 1h / 1d | Corr w/ BTC in USD terms (1h) | 1m bars with zero volume % |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r['symbol']} | {fnum(r['ret_60d_pct'], 1)} | {fnum(r['maxdd_60d_pct'], 1)} | {fnum(r['med_abs_15m_pct'], 3)} | {fnum(r['med_abs_60m_pct'], 3)} | "
                   f"{fnum(r['avg_daily_range_pct'], 2)} | {fnum(r['p_1h_gt_1pct'], 1)}% | {fnum(r['p_1h_gt_2pct'], 1)}% | {fnum(r['p_4h_gt_1pct'], 1)}% | {fnum(r['p_4h_gt_2pct'], 1)}% | "
                   f"{fnum(r['corr_60m_btc'], 2)} / {fnum(r['corr_1d_btc'], 2)} | {fnum(r['corr_60m_usdt'], 2)} / {fnum(r['corr_1d_usdt'], 2)} | "
                   f"{fnum(r['corr_60m_btc_usd_terms'], 2)} | {fnum(r['zero_vol_1m_pct'], 0)} |")
    return "\n".join(out)


def candles_manifest():
    m = json.loads((HERE / "candles" / "_manifest.json").read_text(encoding="utf-8"))
    out = [f"Fetched {m['fetched_at_utc']} from `{m['endpoint']}`.", "",
           "| File | Rows / expected | Server sent | Longest gap (min) |", "|---|---|---|---|"]
    for name, f in m["files"].items():
        sent = f"{f['server_step_seconds']}s bars" + (" (resampled locally)" if f["resampled_locally"] else "")
        out.append(f"| {name} | {f['rows']} / {f['expected_rows']} | {sent} | {f['longest_gap_seconds'] // 60} |")
    return "\n".join(out)


def main():
    rows = load_snapshot()
    credit = credit_set()
    blocks = {"top15": top15(rows, credit), "full": full(rows, credit), "stats": stats(), "manifest": candles_manifest()}
    text = MD.read_text(encoding="utf-8")
    for name, body in blocks.items():
        pat = re.compile(rf"(<!-- BEGIN:{name} -->\n).*?(<!-- END:{name} -->)", re.S)
        if not pat.search(text):
            print(f"marker {name} not found, skipped")
            continue
        text = pat.sub(lambda m: m.group(1) + body + "\n" + m.group(2), text)
    MD.write_text(text, encoding="utf-8")
    print(f"updated {MD}")


if __name__ == "__main__":
    main()
