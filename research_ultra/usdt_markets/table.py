"""Print compact per-market / per-period tables from out/candle_backtest.json (for REPORT.md)."""
import json
from common import OUT
cb = json.loads((OUT / "candle_backtest.json").read_text(encoding="utf-8"))
for m, o in cb["models"].items():
    print("##", m)
    for thr, v in o["by_threshold"].items():
        for per in ("train", "test"):
            a = v[per]
            print(f"{thr} {per:5s} ALL n={a.get('n')} mean={a.get('mean_pct')} sum={a.get('sum_pct')} win={a.get('win_pct')} exits={a.get('exits')}")
            for s, pm in v[per].get("per_market", {}).items():
                print(f"      {s} n={pm['n']} mean={pm.get('mean_pct')} sum={pm.get('sum_pct')} win={pm.get('win_pct')} exits={pm.get('exits')}")
