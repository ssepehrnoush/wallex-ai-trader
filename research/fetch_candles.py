"""Download Wallex OHLCV candles (public UDF endpoint) and save one CSV per symbol/resolution.

    python research/fetch_candles.py                       # defaults below
    python research/fetch_candles.py --symbols BTCTMN,ETHTMN --days-1m 3
    python research/fetch_candles.py --days-1m 30 --days-15m 0 --days-60m 0 --end 2026-10-22T07:00:00Z \
        --symbols USDTTMN,BTCTMN,ETHTMN,SOLTMN,XRPTMN,DOGETMN,PEPETMN,BNBTMN,ARBTMN,POLTMN,SANDTMN \
        --out-dir research/candles_30d                    # the 30-day backtest data (RESULTS.md 2.6)

Output: research/candles/<SYMBOL>_<RES>.csv  with columns ts,open,high,low,close,volume
        (ts = bar OPEN time, unix seconds UTC; volume = base-asset volume as the API reports it)
        research/candles/_manifest.json   (what was fetched, when, row counts, gaps)

Observed API behaviour (probed 2026-10-21, not documented anywhere we could find):
  * resolution=1  -> 1-minute bars, but a window longer than ~1.25 days returns few/no bars,
                     so we request 1 day per call.
  * resolution=15 -> the server actually returns 1-MINUTE bars (up to ~20 days per call).
                     We request 10-day windows and resample to 15m locally.
  * resolution=60 and 240 -> 1-hour bars, at most ~720 bars (30 days) per call.
  * resolution=1D -> daily bars, at most ~800 per call.
  * Minutes/hours with no trades (or exchange downtime) are simply absent; nothing is forward-filled.
The script never trusts the requested resolution: it measures the step the server returned and
resamples down to the requested bar size when the server sent finer bars.
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import wallex_public as wx

HERE = Path(__file__).resolve().parent
DEFAULT_SYMBOLS = "USDTTMN,BTCTMN,ETHTMN,XAUTTMN,PEPETMN,PUMPTMN,DOGETMN,SOLTMN,XRPTMN,BNBTMN"
RES_SECONDS = {"1": 60, "15": 900, "60": 3600, "240": 14400, "1D": 86400}
# window per request, chosen to stay under the observed per-call caps
CHUNK_SECONDS = {"1": 86400, "15": 10 * 86400, "60": 25 * 86400, "240": 25 * 86400, "1D": 700 * 86400}
DAY = 86400


def fetch_window(symbol: str, res: str, start: int, end: int, depth: int = 0) -> list[tuple]:
    """Fetch [start, end) and, if the answer looks truncated at the head, re-fetch the head in halves."""
    d = wx.udf_history(symbol, res, start, end)
    if d.get("s") != "ok" or not d.get("t"):
        if d.get("s") not in ("ok", "no_data"):
            print(f"  ! {symbol} {res} {start}-{end}: unexpected status {d.get('s')} {str(d)[:200]}")
        return []
    bars = list(zip(d["t"], d["o"], d["h"], d["l"], d["c"], d["v"]))
    first = int(d["t"][0])
    # Missing head bigger than 10% of the window: maybe a per-call cap, maybe real downtime. Split and retry.
    if depth < 3 and first - start > 0.1 * (end - start) and first - start > 2 * 3600:
        mid = start + (first - start) // 2
        bars = fetch_window(symbol, res, start, mid, depth + 1) + fetch_window(symbol, res, mid, first, depth + 1) + bars
    return bars


def fetch(symbol: str, res: str, start: int, end: int) -> list[tuple]:
    out: dict[int, tuple] = {}
    step = CHUNK_SECONDS[res]
    a = start
    while a < end:
        b = min(a + step, end)
        for bar in fetch_window(symbol, res, a, b):
            ts = int(bar[0])
            if start <= ts < end:
                out[ts] = (ts, *map(float, bar[1:]))
        a = b
    return [out[k] for k in sorted(out)]


def native_step(bars: list[tuple]) -> int | None:
    if len(bars) < 3:
        return None
    diffs = Counter(b[0] - a[0] for a, b in zip(bars, bars[1:]))
    return diffs.most_common(1)[0][0]


def resample(bars: list[tuple], period: int) -> list[tuple]:
    buckets: dict[int, list] = {}
    for ts, o, h, l, c, v in bars:  # bars are sorted by ts
        k = ts // period * period
        cur = buckets.get(k)
        if cur is None:
            buckets[k] = [k, o, h, l, c, v]
        else:
            cur[2] = max(cur[2], h)
            cur[3] = min(cur[3], l)
            cur[4] = c
            cur[5] += v
    return [tuple(buckets[k]) for k in sorted(buckets)]


def write_csv(path: Path, bars: list[tuple]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ts", "open", "high", "low", "close", "volume"])
        for ts, o, h, l, c, v in bars:
            w.writerow([ts, repr(o), repr(h), repr(l), repr(c), repr(v)])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    ap.add_argument("--days-1m", type=float, default=7)
    ap.add_argument("--days-15m", type=float, default=60)
    ap.add_argument("--days-60m", type=float, default=60)
    ap.add_argument("--out-dir", default=str(HERE / "candles"))
    ap.add_argument("--end", default="", help="fixed window end, ISO UTC (default: now)")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    now = int(datetime.fromisoformat(args.end.replace("Z", "+00:00")).timestamp()) if args.end else int(time.time())
    jobs = [("1", args.days_1m), ("15", args.days_15m), ("60", args.days_60m)]
    manifest = {"fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "endpoint": wx.BASE + "/v1/udf/history", "end_utc": args.end or None, "files": {}}

    for sym in [s.strip().upper() for s in args.symbols.split(",") if s.strip()]:
        for res, days in jobs:
            if days <= 0:
                continue
            period = RES_SECONDS[res]
            end = now // period * period          # drop the still-forming bar
            start = end - int(days * DAY)
            t0 = time.time()
            raw = fetch(sym, res, start, end)
            nstep = native_step(raw)
            bars = resample(raw, period) if nstep and nstep < period else raw
            bars = [b for b in bars if b[0] + period <= end]
            expected = (end - start) // period
            path = out_dir / f"{sym}_{res}.csv"
            write_csv(path, bars)
            gaps = [(a[0], b[0]) for a, b in zip(bars, bars[1:]) if b[0] - a[0] > period]
            longest = max(gaps, key=lambda g: g[1] - g[0], default=None)
            manifest["files"][path.name] = {
                "symbol": sym, "resolution": res, "bar_seconds": period, "server_step_seconds": nstep,
                "resampled_locally": bool(nstep and nstep < period), "rows": len(bars),
                "expected_rows": expected, "missing_rows": expected - len(bars),
                "first_ts": bars[0][0] if bars else None, "last_ts": bars[-1][0] if bars else None,
                "longest_gap_seconds": (longest[1] - longest[0]) if longest else 0,
                "longest_gap_from_ts": longest[0] if longest else None,
            }
            print(f"{sym:<9} res={res:<3} rows={len(bars):>6}/{expected:<6} server_step={nstep} "
                  f"longest_gap={(longest[1]-longest[0])/60 if longest else 0:.0f}min  ({time.time()-t0:.1f}s)")

    (out_dir / "_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"done -> {out_dir}")


if __name__ == "__main__":
    main()
