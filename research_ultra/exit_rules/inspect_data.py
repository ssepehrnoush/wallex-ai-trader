"""Read-only inspection of the recorded data used by the exit-rule study."""
import io
from pathlib import Path
import pandas as pd

MAIN = Path("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot")
SYMS = ["BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN", "PEPETMN", "BNBTMN", "ARBTMN"]


def read_ticks(p):
    raw = p.read_bytes()
    raw = raw[: raw.rfind(b"\n") + 1]          # drop a partial last line
    return pd.read_csv(io.BytesIO(raw))


for run in ["shadow_v2", "shadow_v3", "shadow_v4", "shadow_v5", "shadow_v6", "live_main"]:
    t = read_ticks(MAIN / "runs" / run / "ticks.csv")
    t = t[t.symbol.isin(SYMS)]
    print(run, len(t), pd.to_datetime(t.t.min(), unit="s"), pd.to_datetime(t.t.max(), unit="s"))
    g = t.groupby("symbol")
    print(pd.DataFrame({"n": g.size(), "spread_med_pct": ((t.ask / t.bid - 1) * 100).groupby(t.symbol).median().round(3),
                        "buy_dev_min_pct": (g.buy_dev.min() * 100).round(3),
                        "n_buydev_le_-0.7": g.buy_dev.apply(lambda s: (s <= -0.007).sum()),
                        "n_buydev_le_-0.5": g.buy_dev.apply(lambda s: (s <= -0.005).sum()),
                        "n_buydev_le_-0.4": g.buy_dev.apply(lambda s: (s <= -0.004).sum())}))

for s in SYMS:
    p = MAIN / "research" / "candles" / f"{s}_1.csv"
    if not p.exists():
        p = MAIN / "research" / "premium" / f"wallex_{s}_1m.csv"
    w = pd.read_csv(p)
    b = pd.read_csv(MAIN / "research" / "premium" / f"binance_{s[:-3]}USDT_1m.csv", index_col=0, parse_dates=True)
    print(s, p.name, len(w), "vol>0:", (w.volume > 0).sum(), pd.to_datetime(w.ts.min(), unit="s"), pd.to_datetime(w.ts.max(), unit="s"),
          "| binance", len(b), b.index.min(), b.index.max())
u = pd.read_csv(MAIN / "research" / "candles" / "USDTTMN_1.csv")
print("USDTTMN", len(u), (u.volume > 0).sum(), pd.to_datetime(u.ts.min(), unit="s"), pd.to_datetime(u.ts.max(), unit="s"))
