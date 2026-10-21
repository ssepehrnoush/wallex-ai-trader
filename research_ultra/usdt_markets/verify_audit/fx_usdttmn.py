"""Part 2: how much can USDT/TMN move over the remaining ~45 trading hours?

7-day Wallex USDTTMN 1-minute candles (traded minutes only, forward-filled on a 1-minute grid):
  - every overlapping 45-hour window: change distribution
  - 24-hour changes and the daily drift
  - realised volatility (1-hour log returns) scaled to 45 hours
Robustness: the same on the 60-day hourly file (USDTTMN_60.csv), incl. non-overlapping 45-hour windows.
Also the USDT/TMN mid implied by the live ticks (fair_TMN / fair_USDT, i.e. Binance x USDTTMN / Binance) for
the shadow window, and the live_main run (fair_BTCTMN / Binance BTC is not in live ticks, so only shadow).
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from common import MAIN, save_json, shadow_ticks, read_csv_snapshot

H = 45


def load(path):
    raw = pd.read_csv(path)
    df = raw.set_index(pd.to_datetime(raw.ts.astype("int64"), unit="s", utc=True)).sort_index()
    return df[~df.index.duplicated(keep="last")].astype(float)


def dist(x: pd.Series) -> dict:
    x = x.dropna() * 100
    q = x.quantile([0.05, 0.16, 0.5, 0.84, 0.95])
    return {"n_windows": int(len(x)), "mean_pct": round(x.mean(), 3), "std_pct": round(x.std(), 3),
            "p05": round(q[0.05], 3), "p16": round(q[0.16], 3), "p50": round(q[0.5], 3), "p84": round(q[0.84], 3),
            "p95": round(q[0.95], 3), "min": round(x.min(), 3), "max": round(x.max(), 3),
            "mean_abs_pct": round(x.abs().mean(), 3)}


out = {}
# --- 7 days of 1-minute data -------------------------------------------------------------
m1 = load(MAIN / "research" / "candles" / "USDTTMN_1.csv")
traded = m1[m1.volume > 0].close
grid = traded.reindex(pd.date_range(traded.index[0], traded.index[-1], freq="1min", tz="UTC")).ffill()
span_days = (grid.index[-1] - grid.index[0]).total_seconds() / 86400
w45 = grid.shift(-H * 60) / grid - 1
w24 = grid.shift(-24 * 60) / grid - 1
h1 = grid.resample("1h").last()
lr = np.log(h1).diff().dropna()
out["7d_1m"] = {
    "start": str(grid.index[0]), "end": str(grid.index[-1]), "days": round(span_days, 2),
    "first": float(grid.iloc[0]), "last": float(grid.iloc[-1]),
    "total_change_pct": round((grid.iloc[-1] / grid.iloc[0] - 1) * 100, 3),
    "daily_drift_pct": round(((grid.iloc[-1] / grid.iloc[0]) ** (1 / span_days) - 1) * 100, 3),
    "range_min_max": [float(grid.min()), float(grid.max())],
    "chg_45h_overlapping": dist(w45),
    "independent_45h_windows": round(span_days * 24 / H, 1),
    "chg_24h_overlapping": dist(w24),
    "realised_vol_1h_logret_std_pct": round(lr.std() * 100, 4),
    "realised_vol_scaled_to_45h_pct": round(lr.std() * np.sqrt(H) * 100, 3),
    "realised_vol_scaled_to_24h_pct": round(lr.std() * np.sqrt(24) * 100, 3),
    "longest_gap_in_trades_min": round(float(traded.index.to_series().diff().max().total_seconds() / 60), 1),
}
# daily (Tehran calendar day close-to-close)
teh = grid.tz_convert("Asia/Tehran").resample("1D").last().dropna()
out["7d_1m"]["tehran_daily_close_changes_pct"] = {str(k.date()): round(v * 100, 3) for k, v in (teh / teh.shift(1) - 1).dropna().items()}

# --- 60 days of hourly data ----------------------------------------------------------------
h = load(MAIN / "research" / "candles" / "USDTTMN_60.csv").close
h = h.reindex(pd.date_range(h.index[0], h.index[-1], freq="1h", tz="UTC")).ffill()
d60 = (h.index[-1] - h.index[0]).total_seconds() / 86400
c45 = h.shift(-H) / h - 1
non_ovl = h.iloc[::H]
c45n = (non_ovl.shift(-1) / non_ovl - 1).dropna()
lr60 = np.log(h).diff().dropna()
out["60d_1h"] = {
    "start": str(h.index[0]), "end": str(h.index[-1]), "days": round(d60, 1),
    "total_change_pct": round((h.iloc[-1] / h.iloc[0] - 1) * 100, 2),
    "daily_drift_pct": round(((h.iloc[-1] / h.iloc[0]) ** (1 / d60) - 1) * 100, 3),
    "chg_45h_overlapping": dist(c45),
    "chg_45h_non_overlapping": dist(c45n),
    "share_45h_windows_down_pct": round(float((c45.dropna() < 0).mean()) * 100, 1),
    "realised_vol_scaled_to_45h_pct": round(lr60.std() * np.sqrt(H) * 100, 3),
    "last_30d": {},
    "last_14d": {},
}
for label, days in (("last_30d", 30), ("last_14d", 14)):
    hh = h[h.index >= h.index[-1] - pd.Timedelta(days=days)]
    cc = hh.shift(-H) / hh - 1
    lrr = np.log(hh).diff().dropna()
    out["60d_1h"][label] = {"total_change_pct": round((hh.iloc[-1] / hh.iloc[0] - 1) * 100, 2),
                            "daily_drift_pct": round(((hh.iloc[-1] / hh.iloc[0]) ** (1 / days) - 1) * 100, 3),
                            "chg_45h_overlapping": dist(cc),
                            "realised_vol_scaled_to_45h_pct": round(lrr.std() * np.sqrt(H) * 100, 3)}

# --- USDT/TMN implied by the live shadow ticks (BTCTMN fair / BTCUSDT fair) ----------------------
tk = shadow_ticks(["BTCTMN", "BTCUSDT"])
p = tk.pivot_table(index="t", columns="symbol", values="fair").dropna()
imp = (p.BTCTMN / p.BTCUSDT)
out["shadow_implied_usdttmn_mid"] = {"first": round(float(imp.iloc[0])), "last": round(float(imp.iloc[-1])),
                                     "min": round(float(imp.min())), "max": round(float(imp.max())),
                                     "start": str(pd.to_datetime(imp.index[0], unit="s", utc=True)),
                                     "end": str(pd.to_datetime(imp.index[-1], unit="s", utc=True))}
# live run: fair_BTCTMN only; divide by the shadow-era BTC? not possible without Binance. Report live BTCTMN fair range only.
lm = read_csv_snapshot(MAIN / "runs" / "live_main" / "ticks.csv")
out["live_main_ticks"] = {"rows": int(len(lm)), "start": str(pd.to_datetime(lm.t.min(), unit="s", utc=True)),
                          "end": str(pd.to_datetime(lm.t.max(), unit="s", utc=True))}

save_json("fx_usdttmn.json", out)
print(json.dumps(out, indent=1))
