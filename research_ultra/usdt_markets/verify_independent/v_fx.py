"""Independent USDT/TMN 45-hour move statistics from the raw Wallex candles (1-min, 7 days; 60-min, longer)."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

MAIN = Path("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot")
OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)
H = 45
res = {}


def load(name):
    d = pd.read_csv(MAIN / "research" / "candles" / name)
    d.index = pd.to_datetime(d.ts.astype("int64"), unit="s", utc=True)
    return d[~d.index.duplicated(keep="last")].sort_index()


m1 = load("USDTTMN_1.csv")
c = m1.close.resample("1min").last().ffill()
days = (c.index[-1] - c.index[0]).total_seconds() / 86400
w = H * 60
ch = (c.shift(-w) / c - 1).dropna() * 100
res["1m"] = {"start": str(c.index[0]), "end": str(c.index[-1]), "days": round(days, 2),
             "total_change_pct": round((c.iloc[-1] / c.iloc[0] - 1) * 100, 3),
             "daily_drift_pct": round(((c.iloc[-1] / c.iloc[0]) ** (1 / days) - 1) * 100, 4),
             "n_windows_overlapping": len(ch), "independent_windows": round(days * 24 / H, 2),
             "mean45": round(ch.mean(), 3), "std45": round(ch.std(), 3), "abs_mean45": round(ch.abs().mean(), 3),
             "p05": round(ch.quantile(.05), 3), "p50": round(ch.quantile(.5), 3), "p95": round(ch.quantile(.95), 3),
             "non_overlapping": [round(x, 3) for x in (c.iloc[::w].pct_change().dropna() * 100)]}
# realised vol from hourly returns, scaled by sqrt(45); and from 15-min and daily (bounce check)
for lab, rule in [("15min", "15min"), ("1h", "1h"), ("4h", "4h")]:
    r = np.log(c.resample(rule).last()).diff().dropna()
    per_h = {"15min": 4, "1h": 1, "4h": 0.25}[lab]
    res["1m"][f"rv_{lab}_x_sqrt45_pct"] = round(r.std() * np.sqrt(H * per_h) * 100, 3)
    res["1m"][f"lag1_autocorr_{lab}"] = round(r.autocorr(1), 3)
# longer history at 60-min resolution, if present
m60 = load("USDTTMN_60.csv")
c60 = m60.close.resample("1h").last().ffill()
d60 = (c60.index[-1] - c60.index[0]).total_seconds() / 86400
ch60 = (c60.shift(-H) / c60 - 1).dropna() * 100
r60 = np.log(c60).diff().dropna()
res["60m"] = {"start": str(c60.index[0]), "end": str(c60.index[-1]), "days": round(d60, 1),
              "total_change_pct": round((c60.iloc[-1] / c60.iloc[0] - 1) * 100, 2),
              "daily_drift_pct": round(((c60.iloc[-1] / c60.iloc[0]) ** (1 / d60) - 1) * 100, 4),
              "mean45": round(ch60.mean(), 3), "std45": round(ch60.std(), 3),
              "p05": round(ch60.quantile(.05), 3), "p95": round(ch60.quantile(.95), 3),
              "rv_1h_x_sqrt45_pct": round(r60.std() * np.sqrt(H) * 100, 3)}
last14 = c60[c60.index >= c60.index[-1] - pd.Timedelta(days=14)]
ch14 = (last14.shift(-H) / last14 - 1).dropna() * 100
res["60m_last14d"] = {"daily_drift_pct": round(((last14.iloc[-1] / last14.iloc[0]) ** (1 / 14) - 1) * 100, 4),
                      "mean45": round(ch14.mean(), 3), "std45": round(ch14.std(), 3),
                      "p05": round(ch14.quantile(.05), 3), "p95": round(ch14.quantile(.95), 3)}
# spread proxy: high/low of 1-min candles with volume (upper bound) - not a book, just a sanity range
print(json.dumps(res, indent=1))
(OUT / "v_fx.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
