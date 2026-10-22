"""Step 1: event study. For every pre-declared entry config, de-duplicated events (60-min cooldown
per market), forward fixed-hold outcomes 5..120 min: gross Wallex continuation, global (fair) move,
premium change, and net-of-fees-and-spread return for instant and next-minute entry. IS/OOS split."""
import os as _os
from pathlib import Path as _P
HERE = str(_P(__file__).resolve().parent)          # this study's folder (outputs)
REPO = str(_P(__file__).resolve().parents[2])      # repo root (research/candles_30d, research/premium)
MAIN = _os.environ.get("WALLEX_MAIN", str(_P(REPO).parent / "wallex-bot"))   # live bot folder (ticks.csv, team only)
import json
import os
import numpy as np
import pandas as pd
import common as c
import engine as E

D = c.load_all()
grid = next(iter(D.values())).index
split_i = int(np.searchsorted(grid, c.SPLIT))

rows, ev_rows = [], []
for cfg in E.configs():
    v, k, thr, Z = cfg
    for m, df in D.items():
        sig = E.signal(df, v, k, thr, Z)
        idx = E.dedupe(np.flatnonzero(sig), E.COOLDOWN_ES)
        a = E.arrays(df)
        n = len(df)
        for i in idx:
            rec = {"variant": v, "k": k, "thr": thr, "Z": Z, "market": m, "i": int(i),
                   "oos": bool(i >= split_i), "prem": a["prem"][i], "dp": df[f"dp{k}"].values[i],
                   "r": df[f"r{k}"].values[i], "g": df[f"g{k}"].values[i], "vr": df[f"vr{k}"].values[i]}
            for H in E.HOLDS:
                for lag in (0, 1):
                    e = i + lag
                    if e + H >= n:
                        rec[f"net{H}_l{lag}"] = np.nan
                        continue
                    rec[f"net{H}_l{lag}"] = c.net_ret(a["buy"][e], a["sell"][e + H])
                if i + H < n:
                    rec[f"gross{H}"] = a["close"][i + H] / a["close"][i] - 1
                    rec[f"glob{H}"] = a["fair"][i + H] / a["fair"][i] - 1
                    rec[f"dprem{H}"] = a["prem"][i + H] - a["prem"][i]
            ev_rows.append(rec)

ev = pd.DataFrame(ev_rows)
ev.to_csv(os.path.join(c.OUT, "events_all.csv"), index=False)

key = ["variant", "k", "thr", "Z"]
summ = []
for cfg, g in ev.groupby(key):
    for part, gg in (("IS", g[~g.oos]), ("OOS", g[g.oos])):
        r = dict(zip(key, cfg))
        r["part"] = part
        r["n"] = len(gg)
        for H in E.HOLDS:
            r[f"gross{H}"] = gg[f"gross{H}"].mean() * 100
            r[f"glob{H}"] = gg[f"glob{H}"].mean() * 100
            r[f"dprem{H}"] = gg[f"dprem{H}"].mean() * 100
            r[f"net{H}_l0"] = gg[f"net{H}_l0"].mean() * 100
            r[f"net{H}_l1"] = gg[f"net{H}_l1"].mean() * 100
            r[f"win{H}_l0"] = (gg[f"net{H}_l0"] > 0).mean() * 100
        summ.append(r)
summ = pd.DataFrame(summ)
summ.to_csv(os.path.join(c.OUT, "event_study_summary.csv"), index=False)

# Unconditional baseline: enter at every traded minute (no signal)
base = {}
for m, df in D.items():
    a = E.arrays(df)
    tr = np.flatnonzero(df.traded.values)
    b = {}
    for H in E.HOLDS:
        ii = tr[tr + H < len(df)]
        b[f"net{H}"] = float(np.mean(c.FEE_MULT * a["sell"][ii + H] / a["buy"][ii] - 1) * 100)
        b[f"gross{H}"] = float(np.mean(a["close"][ii + H] / a["close"][ii] - 1) * 100)
    base[m] = b
json.dump(base, open(os.path.join(c.OUT, "baseline_unconditional.json"), "w"), indent=1)

pd.set_option("display.width", 250)
cols = ["variant", "k", "thr", "Z", "part", "n"] + [f"gross{H}" for H in (5, 15, 30, 60, 120)] + \
       [f"net{H}_l0" for H in (5, 15, 30, 60, 120)] + [f"net{H}_l1" for H in (15, 30, 60)]
print(summ[cols].round(2).to_string())
