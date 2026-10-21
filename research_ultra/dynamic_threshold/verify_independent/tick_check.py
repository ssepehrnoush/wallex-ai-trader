"""Live-tick check: deepest live buy_dev (engine-computed) per TMN market, and how many ticks pass each threshold."""
import json
from pathlib import Path
import pandas as pd
MAIN = Path("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot")
fr = []
for r in ["shadow_v2", "shadow_v3", "shadow_v4", "shadow_v5", "shadow_v6", "live_main"]:
    t = pd.read_csv(MAIN / "runs" / r / "ticks.csv", on_bad_lines="skip")
    for c in ["t", "bid", "ask", "buy_dev"]:
        t[c] = pd.to_numeric(t[c], errors="coerce")
    t = t.dropna(subset=["t", "bid", "ask", "buy_dev"])
    t["run"] = r
    fr.append(t)
T = pd.concat(fr)
T = T[T.symbol.str.endswith("TMN") & (T.t <= 1792606800)]
g = T.groupby("symbol")
out = pd.DataFrame({"ticks": g.size(), "hours": g.size() * 2.5 / 3600, "min_buy_dev_pct": g.buy_dev.min() * 100,
                    "p1_pct": g.buy_dev.quantile(0.01) * 100,
                    "n<=-0.5%": g.buy_dev.apply(lambda x: (x <= -0.005).sum()),
                    "n<=-0.7%": g.buy_dev.apply(lambda x: (x <= -0.007).sum()),
                    "half_spread_pct": g.apply(lambda d: ((d.ask / d.bid - 1).median()) / 2 * 100)}).round(3)
print(out.to_string())
# live decisions log
n_entry = n_exit = n_lines = 0
reasons = {}
with open(MAIN / "runs" / "live_main" / "decisions.jsonl", encoding="utf-8") as f:
    for line in f:
        try:
            d = json.loads(line)
        except Exception:
            continue
        n_lines += 1
        ev = d.get("event") or d.get("type") or d.get("kind")
        reasons[ev] = reasons.get(ev, 0) + 1
print("decisions lines", n_lines, "events", reasons)
