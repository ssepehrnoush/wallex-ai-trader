"""Spread and top-of-book depth of live_main markets in the half hour before the 20:48 and 21:19 UTC reviews,
and at the ARB crossing of -0.7% (20:35 UTC). Read-only on MAIN/runs/live_main/ticks.csv."""
import pandas as pd
from pathlib import Path
t = pd.read_csv(Path(__file__).resolve().parents[3] / "wallex-bot" / "runs" / "live_main" / "ticks.csv", on_bad_lines="skip", dtype={"symbol": str})
for c in ["t", "bid", "ask", "bid_qty", "ask_qty", "buy_dev"]:
    t[c] = pd.to_numeric(t[c], errors="coerce")
t = t.dropna(subset=["t", "bid", "ask"])
t["spread_pct"] = (t.ask / t.bid - 1) * 100
t["top_ask_m_toman"] = t.ask * t.ask_qty / 1e6
ts = lambda s: pd.Timestamp(s, tz="UTC").timestamp()
for a, b in [("2026-10-21 20:18", "2026-10-21 20:48"), ("2026-10-21 20:48", "2026-10-21 21:19")]:
    w = t[(t.t >= ts(a)) & (t.t < ts(b))]
    for s in ["PEPETMN", "BNBTMN", "DOGETMN", "ARBTMN"]:
        g = w[w.symbol == s]
        print(f"{a[11:]}-{b[11:]} {s}: spread median {g.spread_pct.median():.3f}% p90 {g.spread_pct.quantile(.9):.3f}% "
              f"max {g.spread_pct.max():.3f}% | top ask median {g.top_ask_m_toman.median():.1f} M toman")
x = t[(t.symbol == "ARBTMN") & (t.buy_dev <= -0.007) & (t.t <= ts("2026-10-21 21:20"))]   # up to the docs cutoff
print("ARB ticks <= -0.7%:", len(x), "from", pd.Timestamp(x.t.min(), unit="s"), "to", pd.Timestamp(x.t.max(), unit="s"),
      f"| spread {x.spread_pct.min():.4f}%..{x.spread_pct.max():.4f}% | top ask {x.top_ask_m_toman.max():.1f} M toman at the start")
