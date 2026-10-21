"""Build research/live_spreads_shadow_v1.json: the median live spread (ask / bid - 1, a fraction) per market,
the input of scripts/bt_fairvalue.py --spreads-json. Reads the live bot's tick files only (never writes there).

Primary source: runs/shadow_v1/ticks.csv (the spreads the original 7-day backtests used). Markets missing
there (PEPETMN, BNBTMN, ARBTMN) take the median of runs/live_main/ticks.csv up to --cutoff.

  python research/live_spreads.py --main ../wallex-bot --cutoff 2026-10-21T19:00:00Z
"""
import argparse
import io
import json
from pathlib import Path

import pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("--main", default=str(Path(__file__).resolve().parents[2] / "wallex-bot"))
ap.add_argument("--cutoff", default="2026-10-21T19:00:00Z")
ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "live_spreads_shadow_v1.json"))
a = ap.parse_args()
iso = lambda t: pd.Timestamp(t, unit="s", tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def ticks(run: str, cutoff: float | None = None) -> pd.DataFrame:
    txt = (Path(a.main) / "runs" / run / "ticks.csv").read_text(encoding="utf-8")
    if not txt.endswith("\n"):            # live_main is still being appended: drop a partial last line
        txt = txt[:txt.rfind("\n") + 1]
    t = pd.read_csv(io.StringIO(txt), on_bad_lines="skip")
    for c in ("t", "bid", "ask"):
        t[c] = pd.to_numeric(t[c], errors="coerce")
    t = t.dropna(subset=["t", "bid", "ask"])
    t = t[(t.bid > 0) & (t.ask >= t.bid)]
    return t[t.t < cutoff] if cutoff else t


def medians(t: pd.DataFrame) -> pd.DataFrame:
    return (t.ask / t.bid - 1).groupby(t.symbol).agg(["median", "count"])


cut = pd.Timestamp(a.cutoff).timestamp()
sv1, live = ticks("shadow_v1"), ticks("live_main", cut)
m1, m2 = medians(sv1), medians(live)
spreads, src, n = {}, {}, {}
for s in sorted(set(m1.index) | set(m2.index)):
    m, name = (m1, "shadow_v1") if s in m1.index else (m2, "live_main")
    spreads[s], src[s], n[s] = round(float(m.loc[s, "median"]), 6), name, int(m.loc[s, "count"])
res = {"note": "Median live spread (ask / bid - 1, fraction) per market. shadow_v1 is a 70-second sample "
               "(about 29 order-book snapshots per market) taken shortly after the candles were fetched; markets "
               "missing there use the median of the live run's ticks before the cutoff.",
       "spreads": spreads, "source": src, "samples": n,
       "sources": {"shadow_v1": {"file": "MAIN/runs/shadow_v1/ticks.csv", "rows": len(sv1),
                                 "first_utc": iso(sv1.t.min()), "last_utc": iso(sv1.t.max())},
                   "live_main": {"file": "MAIN/runs/live_main/ticks.csv", "cutoff_utc": a.cutoff, "rows": len(live),
                                 "first_utc": iso(live.t.min()), "last_utc": iso(live.t.max())}},
       "live_main_median_all_markets": {s: round(float(v), 6) for s, v in m2["median"].items()},
       "how": "python research/live_spreads.py --main ../wallex-bot --cutoff " + a.cutoff}
Path(a.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
print(json.dumps(res, indent=1))
