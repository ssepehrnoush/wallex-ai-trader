"""Tick-level grid for maker variants (a) passive dip-buy and (b) fair-value market making.

Three fill models (see ticksim.py):
  tick   : the specified rule (cross, or our level traded through on the book)
  trade  : same, but a book trade-through only counts if Wallex 1m candles show a real trade
           strictly through our price in that exchange minute (crosses count as fills). Only
           possible while candles exist (to 07:00 UTC 10-22), so its halves are within that window.
  strict : crosses only (ask <= P for buys, bid >= Q for sells). Pessimistic bound.
Selection: on the FIRST half, best total toman over the 8 core markets (POL/SAND excluded, they
only exist in the second half), needing >= 10 completed round trips. Report the SECOND half.
"""
import os as _os
from pathlib import Path as _P
HERE = str(_P(__file__).resolve().parent)          # this study's folder (outputs)
REPO = str(_P(__file__).resolve().parents[2])      # repo root (research/candles_30d, research/premium)
MAIN = _os.environ.get("WALLEX_MAIN", str(_P(REPO).parent / "wallex-bot"))   # live bot folder (ticks.csv, team only)
import itertools
import json
import sys
import numpy as np
import pandas as pd
from ticksim import load_ticks, sim, summarize, CANDLE_END, CLOCK_OFFSET, WIDE

OUT = rf"{HERE}"
SIZE = 700_000

df = load_ticks()
CORE = sorted(set(df.symbol) - WIDE)

GRID = []
for d1, m, T2, g in itertools.product([0.002, 0.003, 0.005], [0.001, 0.002, 0.004], [10, 30, 60], [False, True]):
    GRID.append(("a", dict(d1=d1, m=m, T2=T2, guard=g)))
for s, T2, g in itertools.product([0.002, 0.003, 0.004, 0.006], [10, 30, 60], [False, True]):
    GRID.append(("b", dict(s=s, T2=T2, guard=g)))


def label(v, p):
    if v == "a":
        return f"a d1={p['d1']*100:.1f}% m={p['m']*100:.1f}% T2={p['T2']} guard={'on' if p['guard'] else 'off'}"
    return f"b s={p['s']*100:.1f}% T2={p['T2']} guard={'on' if p['guard'] else 'off'}"


def halves(model):
    d = df
    if model == "trade":
        d = d[d.t + CLOCK_OFFSET < CANDLE_END - 60]
    mid = (d.t.min() + d.t.max()) / 2
    return {"H1": d[d.t < mid], "H2": d[d.t >= mid]}, mid


def subsum(trades, stats, syms):
    tr = [x for x in trades if x["sym"] in syms]
    return summarize(tr, stats, SIZE)


results = {"fill_models": {}, "notes": {}}
alltr = {}
for model in ["tick", "trade", "strict"]:
    hv, mid = halves(model)
    rows = []
    for v, p in GRID:
        rec = {"variant": v, "label": label(v, p), "params": p}
        for h, d in hv.items():
            tr, stt = sim(d, v, fill=model, **p)
            alltr[(model, label(v, p), h)] = tr
            rec[h + "_core"] = subsum(tr, stt, CORE)
            rec[h + "_wide"] = subsum(tr, stt, WIDE)
        rows.append(rec)
    # selection on H1 core
    for v in ["a", "b", "any"]:
        cand = [r for r in rows if (v == "any" or r["variant"] == v) and r["H1_core"].get("round_trips", 0) >= 10]
        if not cand:
            results["fill_models"].setdefault(model, {})["select_" + v] = None
            continue
        best = max(cand, key=lambda r: r["H1_core"]["toman"])
        results["fill_models"].setdefault(model, {})["select_" + v] = {
            "label": best["label"], "H1_core": best["H1_core"], "H2_core": best["H2_core"],
            "H2_wide_flagged": best["H2_wide"]}
    # compact table of every config
    tab = []
    for r in rows:
        tab.append({"label": r["label"],
                    "H1_n": r["H1_core"].get("round_trips", 0), "H1_mean": r["H1_core"].get("mean_net_pct"),
                    "H1_toman": r["H1_core"].get("toman"), "H1_ts": r["H1_core"].get("timestop_share"),
                    "H2_n": r["H2_core"].get("round_trips", 0), "H2_mean": r["H2_core"].get("mean_net_pct"),
                    "H2_toman": r["H2_core"].get("toman"), "H2_ts": r["H2_core"].get("timestop_share"),
                    "H2_wide_n": r["H2_wide"].get("round_trips", 0), "H2_wide_mean": r["H2_wide"].get("mean_net_pct")})
    results["fill_models"][model]["table"] = tab
    results["fill_models"][model]["split_utc"] = str(pd.to_datetime(mid, unit="s"))
    results["fill_models"][model]["window_utc"] = [str(pd.to_datetime(min(x.t.min() for x in hv.values()), unit="s")),
                                                   str(pd.to_datetime(max(x.t.max() for x in hv.values()), unit="s"))]
    n_pos_h1 = sum(1 for r in rows if (r["H1_core"].get("toman") or 0) > 0)
    n_pos_h2 = sum(1 for r in rows if (r["H2_core"].get("toman") or 0) > 0)
    results["fill_models"][model]["configs_positive"] = {"H1": n_pos_h1, "H2": n_pos_h2, "of": len(rows)}
    hind = max(rows, key=lambda r: r["H2_core"].get("toman", -1e18))
    results["fill_models"][model]["hindsight_best_H2_core"] = {"label": hind["label"], "H2_core": hind["H2_core"]}
    print(model, "done", results["fill_models"][model]["configs_positive"], flush=True)

pd.to_pickle(alltr, OUT + r"\tick_trades.pkl")
json.dump(results, open(OUT + r"\tick_grid.json", "w"), indent=1, default=str)

for model in ["tick", "trade", "strict"]:
    fm = results["fill_models"][model]
    print("\n=====", model, fm["window_utc"], "split", fm["split_utc"], "positive configs", fm["configs_positive"])
    for v in ["a", "b", "any"]:
        sel = fm["select_" + v]
        if sel is None:
            print(v, "no config with >=10 trades in H1")
            continue
        h1, h2 = sel["H1_core"], sel["H2_core"]
        print(f"select {v}: {sel['label']}")
        for nm, h in [("H1", h1), ("H2", h2)]:
            print(f"   {nm}: n={h.get('entries',0)} mean={h.get('mean_net_pct')}% win={h.get('win_rate')} ts={h.get('timestop_share')} worst={h.get('worst_pct')} toman={h.get('toman')} /h={h.get('per_hour')} gross?")
        w = sel["H2_wide_flagged"]
        print(f"   H2 wide (POL/SAND, flagged): n={w.get('entries',0)} mean={w.get('mean_net_pct')} toman={w.get('toman')}")
    print("hindsight best H2:", fm["hindsight_best_H2_core"]["label"], {k: fm["hindsight_best_H2_core"]["H2_core"].get(k) for k in ["entries", "mean_net_pct", "toman", "win_rate", "timestop_share"]})
