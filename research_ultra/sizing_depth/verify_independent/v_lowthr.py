"""Candle model at low thresholds (0.3%/0.4%/0.5%), live exit rule, no bootstrap: to compare with the
tick-level hypothetical buys in v_ticks_persist.json (same thresholds). Own code (uses v_bt.run)."""
import json, sys
import v_bt
out = {}
for e in [0.003, 0.004, 0.005]:
    v_bt.ENTRY = e
    tr, _ = v_bt.run({})
    out[str(e)] = {"n": int(len(tr)), "mean_pct": round(float(tr.ret.mean() * 100), 3),
                   "win": round(float((tr.ret > 0).mean() * 100), 1), "exits": tr.why.value_counts().to_dict(),
                   "stops": int((tr.why == "stop").sum())}
    print(e, out[str(e)], flush=True)
json.dump(out, open("v_lowthr.json", "w"), indent=1)
