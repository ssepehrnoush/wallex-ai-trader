"""Snapshot every TMN-quoted Wallex market: volume, spread, book depth, precision, minimums.

Public endpoints only. Takes several /v2/depth/all snapshots and uses the median spread/depth,
so one odd moment in a thin book does not decide the ranking.

    python research/market_snapshot.py --snapshots 20 --gap 30   (about 10 minutes)

Writes research/markets_snapshot.csv (one row per TMN market, sorted by liquidity rank).
"""
from __future__ import annotations

import argparse
import csv
import statistics as st
import time
from datetime import datetime, timezone
from pathlib import Path

import wallex_public as wx

HERE = Path(__file__).resolve().parent


def book_metrics(book: dict) -> dict | None:
    bids = [(float(x["price"]), float(x["quantity"])) for x in book.get("bid", [])]
    asks = [(float(x["price"]), float(x["quantity"])) for x in book.get("ask", [])]
    if not bids or not asks:
        return None
    bids.sort(key=lambda x: -x[0])
    asks.sort(key=lambda x: x[0])
    bb, ba = bids[0][0], asks[0][0]
    if bb <= 0 or ba <= 0 or ba < bb:
        return None
    mid = (bb + ba) / 2
    out = {"bid": bb, "ask": ba, "mid": mid, "spread_pct": (ba - bb) / mid * 100,
           "levels_bid": len(bids), "levels_ask": len(asks)}
    for pct in (0.5, 1.0, 2.0):
        lo, hi = mid * (1 - pct / 100), mid * (1 + pct / 100)
        out[f"bid_depth_{pct}"] = sum(p * q for p, q in bids if p >= lo)
        out[f"ask_depth_{pct}"] = sum(p * q for p, q in asks if p <= hi)
    # toman needed to move price: cost to sweep 5M toman on each side (slippage vs mid, %)
    for side, levels in (("buy", asks), ("sell", bids)):
        need, cost_q, notional = 5_000_000.0, 0.0, 0.0
        for p, q in levels:
            take = min(q, (need - notional) / p)
            cost_q += take
            notional += take * p
            if notional >= need - 1e-6:
                break
        out[f"slip5m_{side}_pct"] = (abs(notional / cost_q - mid) / mid * 100) if notional >= need - 1e-6 and cost_q else float("nan")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshots", type=int, default=20)
    ap.add_argument("--gap", type=float, default=30.0, help="seconds between depth snapshots")
    ap.add_argument("--out", default=str(HERE / "markets_snapshot.csv"))
    args = ap.parse_args()

    mk = wx.markets()
    tmn = {s: v for s, v in mk.items() if v.get("quoteAsset") == "TMN"}
    print(f"{len(mk)} markets, {len(tmn)} TMN-quoted")

    snaps: dict[str, list[dict]] = {s: [] for s in tmn}
    for i in range(args.snapshots):
        books = wx.depth_all()
        for s in tmn:
            if s in books:
                m = book_metrics(books[s])
                if m:
                    snaps[s].append(m)
        print(f"depth snapshot {i + 1}/{args.snapshots} done")
        if i < args.snapshots - 1:
            time.sleep(args.gap)

    rows = []
    for s, v in tmn.items():
        stt = v.get("stats", {})
        def f(k):
            try:
                return float(stt.get(k) or 0)
            except (TypeError, ValueError):  # e.g. "-" when a market has no trades
                return 0.0
        last = f("lastPrice")
        ss = snaps[s]
        med = lambda k: st.median([x[k] for x in ss]) if ss else float("nan")
        mid = med("mid")
        row = {
            "symbol": s,
            "name": v.get("enName", "").strip(),
            "created": v.get("createdAt", "")[:10],
            "last_price": last,
            "mid": mid,
            "vol24h_tmn": f("24h_tmnVolume"),
            "vol7d_avg_daily_tmn_est": f("7d_volume") * (mid if mid == mid else last) / 7,
            "ch24h_pct": stt.get("24h_ch"),
            "ch7d_pct": stt.get("7d_ch"),
            "spread_pct_median": med("spread_pct"),
            "spread_pct_p25": st.quantiles([x["spread_pct"] for x in ss], n=4)[0] if len(ss) > 1 else float("nan"),
            "spread_pct_p75": st.quantiles([x["spread_pct"] for x in ss], n=4)[2] if len(ss) > 1 else float("nan"),
            "spread_pct_max": max((x["spread_pct"] for x in ss), default=float("nan")),
            "bid_depth_1pct_tmn": med("bid_depth_1.0"),
            "ask_depth_1pct_tmn": med("ask_depth_1.0"),
            "bid_depth_0.5pct_tmn": med("bid_depth_0.5"),
            "ask_depth_0.5pct_tmn": med("ask_depth_0.5"),
            "slip5m_buy_pct": med("slip5m_buy_pct"),
            "slip5m_sell_pct": med("slip5m_sell_pct"),
            "price_decimals": v.get("tickSize"),
            "qty_decimals": v.get("stepSize"),
            "min_qty": v.get("minQty"),
            "min_notional_tmn": v.get("minNotional"),
            "market_orders_enabled": v.get("isMarketTypeEnable"),
            "zero_fee": v.get("isZeroFee"),
            "n_snapshots": len(ss),
        }
        row["min_side_depth_1pct_tmn"] = min(row["bid_depth_1pct_tmn"], row["ask_depth_1pct_tmn"]) if ss else 0.0
        rows.append(row)

    # composite liquidity rank: mean of ranks on 24h volume, 7d avg volume, 1% depth (thinner side), spread
    def rank(key, reverse):
        order = sorted(rows, key=lambda r: (r[key] if r[key] == r[key] else (-1e30 if reverse else 1e30)), reverse=reverse)
        return {r["symbol"]: i + 1 for i, r in enumerate(order)}
    rk = [rank("vol24h_tmn", True), rank("vol7d_avg_daily_tmn_est", True),
          rank("min_side_depth_1pct_tmn", True), rank("spread_pct_median", False)]
    for r in rows:
        r["liq_score_rank_mean"] = sum(x[r["symbol"]] for x in rk) / len(rk)
    rows.sort(key=lambda r: r["liq_score_rank_mean"])
    for i, r in enumerate(rows):
        r["liq_rank"] = i + 1

    cols = ["liq_rank", "symbol"] + [c for c in rows[0] if c not in ("liq_rank", "symbol")]
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"snapshot taken {datetime.now(timezone.utc).isoformat(timespec='seconds')} -> {args.out}")
    for r in rows[:20]:
        print(f"{r['liq_rank']:>3} {r['symbol']:<10} vol24h={r['vol24h_tmn']/1e9:7.2f}B  7d/day={r['vol7d_avg_daily_tmn_est']/1e9:7.2f}B "
              f"spread={r['spread_pct_median']:.3f}%  depth1%={r['min_side_depth_1pct_tmn']/1e6:8.1f}M  slip5M={r['slip5m_buy_pct']:.3f}/{r['slip5m_sell_pct']:.3f}")


if __name__ == "__main__":
    main()
