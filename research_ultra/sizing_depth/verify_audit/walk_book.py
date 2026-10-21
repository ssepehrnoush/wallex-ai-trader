"""Part 4: "walk the book" (buy several ask levels while each is still >= threshold below fair).

The ticks record only the top level, so the gain cannot be measured offline. What CAN be measured:
  (a) the shortfall that walking could recover at most: planned size minus the top-ask fill (part 1);
  (b) how much ask depth sits right behind the top level, from depth snapshots that already exist on
      disk: the 400-character (truncated) order-book replies the AI analyst fetched during the live run
      (decisions.jsonl, random moments, NOT dislocation moments) and research/markets_snapshot.csv
      (20 full-depth snapshots, median depth within 0.5% / 1% of mid).
Upper bound of the extra 45 h return = shortfall share x expected 45 h return at that size (it assumes
every missing toman could be bought at the same edge, which deeper, pricier levels cannot give).

Writes out/walk_book.json
"""
from __future__ import annotations

import json
import re

import pandas as pd

from common import MAIN, OUT, TMN8, dump, read_jsonl

ev = read_jsonl(MAIN / "runs" / "live_main" / "decisions.jsonl")
snaps = []
for e in ev:
    if e.get("event") != "policy_review":
        continue
    for c in e.get("analyst_tool_calls") or []:
        if c.get("tool") != "wallex_basic_get_market_depth":
            continue
        txt = c.get("result", "")
        m = re.search(r'"ask":\[(.*)', txt)
        if not m:
            continue
        lv = [(float(p), float(q)) for p, q in re.findall(r'\{"price":([0-9.]+),"quantity":([0-9.]+)', m.group(1))]
        if len(lv) < 2:
            continue
        top = lv[0][0]
        row = {"t": e["t"], "symbol": c["args"]["symbol"], "levels_visible": len(lv),
               "visible_up_to_pct_above_top": round((lv[-1][0] / top - 1) * 100, 4),
               "top_ask_notional": round(lv[0][0] * lv[0][1])}
        for x in (0.001, 0.002, 0.003):
            row[f"ask_notional_within_{x * 100:.1f}pct_of_top(lower_bound)"] = round(sum(p * q for p, q in lv if p <= top * (1 + x)))
        snaps.append(row)
snap_df = pd.DataFrame(snaps)

ms = pd.read_csv(MAIN / "research" / "markets_snapshot.csv")
ms = ms[ms.symbol.isin(TMN8)][["symbol", "ask_depth_0.5pct_tmn", "ask_depth_1pct_tmn", "bid_depth_0.5pct_tmn", "n_snapshots"]]

dt = json.loads((OUT / "depth_ticks.json").read_text(encoding="utf-8"))
ai = json.loads((OUT / "ai_policy.json").read_text(encoding="utf-8"))
ub = {}
for bucket in ["0.3%", "0.4%"]:
    fill = dt["entry_episodes"][bucket]["entry_fill_pct_mean"]
    ub[bucket] = {}
    for f, pol in [("0.4", "all_on_0.4 (defensive cap)"), ("0.7", "all_on_0.7"), ("1.0", "all_on_1.0 (normal)")]:
        short = 1 - fill[f] / 100
        row = {"top_ask_fill_pct": fill[f], "shortfall_pct_of_planned": round(short * 100, 1)}
        for sc, tab in ai["expected_45h"].items():
            base = tab[pol]["ret_pct"]
            row[sc] = {"expected_45h_pct": base, "walk_upper_bound_extra_pp": round(base * short / (1 - short), 4),
                       "walk_upper_bound_extra_tmn": round(tab[pol]["tmn"] * short / (1 - short))}
        ub[bucket][f] = row

res = {"measurable_offline": False,
       "why": "ticks.csv stores only the best bid/ask and their quantities; the depth behind the top level at the moment of a "
              "dislocation was never recorded, and calling the Wallex API is not allowed in this study.",
       "analyst_depth_snapshots_random_moments": snaps, "markets_snapshot_depth": ms.to_dict("records"),
       "upper_bound": ub,
       "episodes_behind": {k: dt["entry_episodes"][k]["episodes"] for k in ["0.3%", "0.4%"]}}
dump(res, "walk_book.json")
pd.set_option("display.width", 250)
print(snap_df.to_string())
print(ms.to_string())
print(json.dumps(ub, indent=1, ensure_ascii=False))
