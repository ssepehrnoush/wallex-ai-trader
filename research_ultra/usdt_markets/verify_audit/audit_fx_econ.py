"""Adversarial audit of fx_usdttmn.py and economics.py.

FX: is the 45-hour USDT/TMN sigma right? Variance ratio (std of k-hour log returns / sqrt(k)) on 7 d 1-minute and
60 d hourly data; non-overlapping 45 h windows.
Economics: expected value (no 1-sigma penalty), P(net > 0) under the FX distribution, with the per-trade means
corrected by this audit (no intra-minute look-ahead, latency) and the tick frequency corrected for how active the
shadow window was. Conversion variants: round trip 0.714%; one-way 0.414% if USDT left in the account is valued at
the USDTTMN bid at the end (equity_tmn does that; whether the hackathon score does is NOT known).
"""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

from common import MAIN, OUT, save_json

out = {}


def load(path):
    raw = pd.read_csv(path)
    df = raw.set_index(pd.to_datetime(raw.ts.astype("int64"), unit="s", utc=True)).sort_index()
    return df[~df.index.duplicated(keep="last")].astype(float)


m1 = load(MAIN / "research" / "candles" / "USDTTMN_1.csv")
tr = m1[m1.volume > 0].close
grid = tr.reindex(pd.date_range(tr.index[0], tr.index[-1], freq="1min", tz="UTC")).ffill()
h1 = np.log(grid.resample("1h").last())
vr7 = {}
for k in (1, 3, 6, 12, 24):
    rk = (h1 - h1.shift(k)).dropna()
    vr7[f"{k}h"] = round(float(rk.std() / math.sqrt(k) * math.sqrt(45) * 100), 3)
out["7d_sigma45_scaled_from_k_hour_returns_pct"] = vr7
c45 = (h1.shift(-45) - h1).dropna()
out["7d_45h_overlapping"] = {"n": int(len(c45)), "std_pct": round(float(c45.std() * 100), 3), "mean_pct": round(float(c45.mean() * 100), 3)}
nov = h1.iloc[::45]
out["7d_45h_non_overlapping_pct"] = [round(float(x) * 100, 3) for x in (nov.shift(-1) - nov).dropna()]

h = load(MAIN / "research" / "candles" / "USDTTMN_60.csv").close
h = np.log(h.reindex(pd.date_range(h.index[0], h.index[-1], freq="1h", tz="UTC")).ffill())
vr60 = {}
for k in (1, 3, 6, 12, 24, 45):
    rk = (h - h.shift(k)).dropna()
    vr60[f"{k}h"] = round(float(rk.std() / math.sqrt(k) * math.sqrt(45) * 100), 3)
out["60d_sigma45_scaled_from_k_hour_returns_pct"] = vr60
for lab, days in (("last14d", 14), ("last30d", 30)):
    hh = h[h.index >= h.index[-1] - pd.Timedelta(days=days)]
    out[f"{lab}_sigma45_from_6h_pct"] = round(float((hh - hh.shift(6)).dropna().std() / math.sqrt(6) * math.sqrt(45) * 100), 3)
    nv = hh.iloc[::45]
    out[f"{lab}_45h_non_overlapping_pct"] = [round(float(x) * 100, 3) for x in (nv.shift(-1) - nv).dropna()]

# ---------------- economics -------------------------------------------------------------------------------
E, R, HOURS = 5_000_000, 264_602, 45.0
DAYS = HOURS / 24
SIZE = 827_586 * 0.98                 # engine size at sf 0.4 (author used min(engine, 0.98 X, depth))
CONV = {"round_trip_0.714": 0.00714, "one_way_valued_at_bid_0.414": 0.00414}
SIG = {"0.78 (7d windows)": 0.0078, "1.3 (7d 6h-scaled)": vr7["6h"] / 100, "1.71 (7d 1h-scaled)": 0.0171}
ca = json.loads((OUT / "audit_candles.json").read_text(encoding="utf-8"))["variants"]
ta = json.loads((OUT / "audit_ticks.json").read_text(encoding="utf-8"))
act = ta["binance_btc_activity"]
# the shadow window had 4 Binance 5-min up-moves > 0.3% in 1.78 h (54/day) vs 7.43/day over 7 days
activity_ratio = (act["shadow_window_5m_up_gt_0.3pct_events"] / act["shadow_window_hours"] * 24) / act["7d_5m_up_gt_0.3pct_events_per_day"]
tick_tpd_raw = 4 / (104.6 / 1440)
scen = {
    "A author: candles mid, same-minute fair (WF test)": (ca["mid|same|samefill"]["wf_fwd_4d"]["trades_per_day"], ca["mid|same|samefill"]["wf_fwd_4d"]["mean_pct"]),
    "A' audit: candles mid, prior-minute fair (WF test)": (ca["mid|lagfair|samefill"]["wf_fwd_4d"]["trades_per_day"], ca["mid|lagfair|samefill"]["wf_fwd_4d"]["mean_pct"]),
    "A'' audit: candles mid, prior-minute fair, all 7 d @0.36": (20 / 6.85, ca["mid|lagfair|samefill"]["all_7d"]["0.36%"]["mean_pct"]),
    "C author: ticks lat0, raw freq": (tick_tpd_raw, ta["replays"]["thr0.36|lat0|cross0.0"]["mean_pct"]),
    "C' audit: ticks lat0, freq / activity ratio": (tick_tpd_raw / activity_ratio, ta["replays"]["thr0.36|lat0|cross0.0"]["mean_pct"]),
    "C'' audit: ticks 1-tick latency + 0.3% cross, freq / activity": (tick_tpd_raw / activity_ratio, ta["replays"]["thr0.36|lat1|cross0.3"]["mean_pct"]),
    "optimistic what-if: ticks freq / activity at +0.25%/trade": (tick_tpd_raw / activity_ratio, 0.25),
}
rows = []
for sname, (tpd, mean) in scen.items():
    n = tpd * DAYS
    gain = n * mean / 100 * SIZE
    for cname, c in CONV.items():
        conv = c * 827_586
        ev = gain - conv
        r = {"scenario": sname, "trades_45h": round(n, 1), "mean_pct": mean, "gain_tmn": round(gain), "conversion": cname,
             "conv_tmn": round(conv), "EV_tmn": round(ev), "EV_pct_equity": round(ev / E * 100, 3)}
        for sn, s in SIG.items():
            r[f"P(net>0) sigma {sn}"] = round(0.5 * (1 + math.erf(ev / (s * 827_586) / math.sqrt(2))), 2)
            r[f"net_after_1sigma {sn}"] = round(ev - s * 827_586)
        rows.append(r)
out["activity_ratio_shadow_vs_7d"] = round(activity_ratio, 2)
out["tick_trades_per_day_raw"] = round(tick_tpd_raw, 1)
out["economics"] = rows
save_json("audit_fx_econ.json", out)
print(json.dumps({k: v for k, v in out.items() if k != "economics"}, indent=1))
for r in rows:
    print(f"{r['scenario'][:62]:62s} | {r['conversion'][:14]:14s} | n45 {r['trades_45h']:6} | mean {r['mean_pct']:7} | gain {r['gain_tmn']:>8,} | "
          f"EV {r['EV_tmn']:>8,} ({r['EV_pct_equity']:+.3f}%) | P>0 {[r[k] for k in r if k.startswith('P(')]} | net1s {[r[k] for k in r if k.startswith('net_after')]}")
