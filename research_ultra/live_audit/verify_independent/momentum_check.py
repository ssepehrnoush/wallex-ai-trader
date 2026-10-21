"""Would the engine's momentum filter (global 60 s drop >= 0.4%) have skipped backtest entries? Proxy: Binance
1-minute return into the entry minute (close[i]/close[i-1]-1). Also the Binance 15-min move before entry."""
import os
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
import pandas as pd
MAIN = "C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot"
tr = pd.read_csv("trades_current_policy_no_risk_rules.csv")
out = []
for _, r in tr.iterrows():
    g = pd.read_csv(f"{MAIN}/research/premium/binance_{r.symbol[:-3]}USDT_1m.csv", index_col=0)["close"]
    g.index = pd.to_datetime(g.index, utc=True)
    t = pd.Timestamp(int(r.t), unit="s", tz="UTC")
    i = g.index.get_loc(t)
    out.append(dict(symbol=r.symbol, r1=(g.iloc[i] / g.iloc[i - 1] - 1) * 100, r15=(g.iloc[i] / g.iloc[i - 15] - 1) * 100, ret=r.ret))
d = pd.DataFrame(out)
print("entries", len(d), "| Binance 1m return into entry minute <= -0.4%:", int((d.r1 <= -0.4).sum()),
      "| median 1m ret %", round(d.r1.median(), 3), "| share global UP in 15m before entry:", round((d.r15 > 0).mean() * 100, 1),
      "| median |15m move| %", round(d.r15.abs().median(), 3))
