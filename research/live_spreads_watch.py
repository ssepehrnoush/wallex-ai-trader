"""Build research/live_spreads_watch.json: the median live spread (ask / bid - 1, a fraction) of the watch markets
POLTMN and SANDTMN, the --spreads-json of their 30-day backtest (RESULTS.md 2.6). They are missing from
research/live_spreads_shadow_v1.json because the live bot records them only since its 2026-10-22 06:12 UTC start.
Reads the live bot's ticks.csv only (never writes there; ticks.csv is not in git).

  python research/live_spreads_watch.py --main ../wallex-bot --cutoff 2026-10-22T08:00:00Z
"""
import argparse
import io
import json
from pathlib import Path

import pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("--main", default=str(Path(__file__).resolve().parents[2] / "wallex-bot"))
ap.add_argument("--cutoff", default="2026-10-22T08:00:00Z")
ap.add_argument("--symbols", default="POLTMN,SANDTMN")
ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "live_spreads_watch.json"))
a = ap.parse_args()
iso = lambda t: pd.Timestamp(t, unit="s", tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")

txt = (Path(a.main) / "runs" / "live_main" / "ticks.csv").read_text(encoding="utf-8")
txt = txt[:txt.rfind("\n") + 1]                       # the file is still being appended: drop a partial last line
t = pd.read_csv(io.StringIO(txt), on_bad_lines="skip")
for c in ("t", "bid", "ask"):
    t[c] = pd.to_numeric(t[c], errors="coerce")
t = t.dropna(subset=["t", "bid", "ask"])
t = t[(t.bid > 0) & (t.ask >= t.bid) & (t.t < pd.Timestamp(a.cutoff).timestamp()) & t.symbol.isin(a.symbols.split(","))]
sp = t.ask / t.bid - 1
m = sp.groupby(t.symbol).agg(["median", "count"])
cap = json.loads((Path(__file__).resolve().parent.parent / "config.json").read_text(encoding="utf-8"))["fairvalue"]["max_spread"]
res = {"note": "Median live spread (ask / bid - 1, fraction) of the watch markets from the live run's ticks before "
               "the cutoff. The live bot records them from its first tick (watch markets start disabled). "
               "share_at_or_below_max_spread = fraction of those ticks the engine's spread cap would let it buy in.",
       "spreads": {s: round(float(m.loc[s, "median"]), 6) for s in m.index},
       "samples": {s: int(m.loc[s, "count"]) for s in m.index},
       "max_spread": cap,
       "share_at_or_below_max_spread": {s: round(float((sp[t.symbol == s] <= cap).mean()), 4) for s in m.index},
       "first_utc": {s: iso(t[t.symbol == s].t.min()) for s in m.index},
       "source": {"file": "MAIN/runs/live_main/ticks.csv", "cutoff_utc": a.cutoff},
       "how": "python research/live_spreads_watch.py --main ../wallex-bot --cutoff " + a.cutoff}
Path(a.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
print(json.dumps(res, indent=1))
