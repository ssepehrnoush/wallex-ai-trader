"""Part 3: toman economics of converting X toman to USDT and trading the 5 USDT markets for ~45 hours.

net(X) = expected USDT-trading gain (in toman)  -  conversion cost on X  -  1-sigma adverse USDT/TMN move on X
versus 0 for leaving X idle in toman (toman cash earns nothing on the exchange; the TMN engine uses at most
3 x 827k of the 5M, so X up to ~2.5M is genuinely idle today).

Inputs all come from the other scripts' outputs (out/*.json). Engine sizing as coded in risk.py (it uses the
TMN fee pair even for USDT markets): notional = equity x 0.6% x size_factor / (stop 0.9% + 0.3% + 0.25%).
"""
from __future__ import annotations

import json

from common import OUT, save_json

cb = json.loads((OUT / "candle_backtest.json").read_text(encoding="utf-8"))
tr = json.loads((OUT / "tick_replay.json").read_text(encoding="utf-8"))
fx = json.loads((OUT / "fx_usdttmn.json").read_text(encoding="utf-8"))

E = 5_000_000
R = fx["shadow_implied_usdttmn_mid"]["last"]          # toman per USDT, live-tick implied
HOURS = 45.0
DAYS = HOURS / 24


def engine_size(sf: float) -> float:
    return min(E * 0.006 * sf / (0.009 + 0.003 + 0.0025), 0.35 * E)


# conversion: taker 0.3% each way + one full USDTTMN spread (half each way). Spread from the 20-snapshot
# market study (research/markets_snapshot.csv: median 0.114%, p75 0.37%); Wallex itself is not called here.
CONV = {"typical": 2 * 0.003 + 0.00114, "wide_spread_p75": 2 * 0.003 + 0.0037, "maker_both_ways": 2 * 0.0025}
SIG = {"7d_realised_vol_45h": fx["7d_1m"]["realised_vol_scaled_to_45h_pct"] / 100,
       "7d_45h_window_std": fx["7d_1m"]["chg_45h_overlapping"]["std_pct"] / 100,
       "60d_45h_window_std": fx["60d_1h"]["chg_45h_overlapping"]["std_pct"] / 100}

# depth: top-ask USDT at the moment of each replayed tick signal (all thresholds)
tops = sorted({round(t["entry_top_ask_usdt"], 2) for v in tr["replay"].values() for t in v["trades"]})
med_top = tops[len(tops) // 2] if tops else None
depth_cap_tmn = med_top * R if med_top else float("inf")

mid, cons = cb["models"]["mid"], cb["models"]["cons"]
rp36 = tr["replay"]["0.36%"]["all"]
scen = {
    "A_candles_mid_walkforward": {"trades_per_day": mid["walk_forward_global"]["test_trades_per_day"],
                                  "mean_pct": mid["walk_forward_global"]["test"]["mean_pct"],
                                  "n_evidence": mid["walk_forward_global"]["test"]["n"]},
    "B_candles_conservative_walkforward_per_market": {"trades_per_day": cons["walk_forward_per_market"]["test_trades_per_day"],
                                                      "mean_pct": cons["walk_forward_per_market"]["test"]["mean_pct"],
                                                      "n_evidence": cons["walk_forward_per_market"]["test"]["n"]},
    "C_live_ticks_0.36pct_104min": {"trades_per_day": round(rp36["n"] / (tr["total_minutes"] / 1440), 1),
                                    "mean_pct": rp36["mean_pct"], "n_evidence": rp36["n"]},
}

rows = []
for X_label, X, sf in (("one position (X = 827k, sf 0.4)", engine_size(0.4), 0.4),
                       ("35% cap (X = 1.75M, sf 1.0)", 0.35 * E, 1.0),
                       ("half of idle cash (X = 2.5M, sf 1.0)", 2_500_000, 1.0)):
    size = min(engine_size(sf), X * 0.98, depth_cap_tmn)
    for sname, s in scen.items():
        n = s["trades_per_day"] * DAYS
        gain = n * s["mean_pct"] / 100 * size
        conv = CONV["typical"] * X
        for sig_name, sig in SIG.items():
            fxr = sig * X
            rows.append({"allocation": X_label, "X_tmn": round(X), "per_trade_tmn": round(size), "per_trade_usdt": round(size / R, 2),
                         "scenario": sname, "trades_45h": round(n, 1), "mean_net_pct": s["mean_pct"],
                         "gain_tmn": round(gain), "conversion_tmn": round(conv), "fx_sigma": sig_name,
                         "fx_1sigma_tmn": round(fxr), "net_tmn": round(gain - conv - fxr),
                         "net_without_fx_risk_tmn": round(gain - conv),
                         "net_pct_of_equity": round((gain - conv - fxr) / E * 100, 3)})

# break-even: trades needed in 45 h at the walk-forward mean to pay conversion (and conversion + 1 sigma)
be = {}
for sname, s in scen.items():
    if s["mean_pct"] and s["mean_pct"] > 0:
        m = s["mean_pct"] / 100
        be[sname] = {"trades_to_pay_conversion": round(CONV["typical"] / m, 1),
                     "trades_to_pay_conversion_plus_1sigma_7d_realised": round((CONV["typical"] + SIG["7d_realised_vol_45h"]) / m, 1),
                     "trades_to_pay_conversion_plus_1sigma_7d_window": round((CONV["typical"] + SIG["7d_45h_window_std"]) / m, 1),
                     "expected_trades_45h": round(s["trades_per_day"] * DAYS, 1)}

res = {"equity_tmn": E, "usdttmn": R, "hours": HOURS, "engine_size_sf0.4_tmn": round(engine_size(0.4)),
       "engine_size_sf0.4_usdt": round(engine_size(0.4) / R, 2), "conversion_cost_pct": {k: round(v * 100, 3) for k, v in CONV.items()},
       "fx_sigma_45h_pct": {k: round(v * 100, 3) for k, v in SIG.items()},
       "fx_drift_note": {"7d_daily_drift_pct": fx["7d_1m"]["daily_drift_pct"], "60d_daily_drift_pct": fx["60d_1h"]["daily_drift_pct"],
                         "14d_daily_drift_pct": fx["60d_1h"]["last_14d"]["daily_drift_pct"]},
       "depth": {"top_ask_usdt_at_tick_signals": tops, "median": med_top, "median_tmn": round(depth_cap_tmn) if med_top else None},
       "scenarios": scen, "table": rows, "break_even": be}
save_json("economics.json", res)
print(json.dumps({k: v for k, v in res.items() if k != "table"}, indent=1))
print()
for r in rows:
    print(f"{r['allocation'][:28]:28s} | {r['scenario'][:30]:30s} | size {r['per_trade_tmn']:>9,} | n {r['trades_45h']:>5} | mean {r['mean_net_pct']:>6} "
          f"| gain {r['gain_tmn']:>8,} | conv {r['conversion_tmn']:>7,} | fx[{r['fx_sigma'][:18]:18s}] {r['fx_1sigma_tmn']:>7,} | net {r['net_tmn']:>8,} | net_noFX {r['net_without_fx_risk_tmn']:>7,}")
