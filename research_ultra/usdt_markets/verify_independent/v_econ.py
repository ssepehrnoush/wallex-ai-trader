"""Independent 45-hour toman economics, built only from this folder's own outputs (v_candles / v_ticks / v_fx)
plus raw files: net(X) = N45 * mean * per_trade - conversion(X) - k * sigma45 * X, versus idle toman (0)."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

MAIN = Path("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot")
OUT = Path(__file__).resolve().parent / "out"
C = json.loads((OUT / "v_candles.json").read_text(encoding="utf-8"))
T = json.loads((OUT / "v_ticks.json").read_text(encoding="utf-8"))
F = json.loads((OUT / "v_fx.json").read_text(encoding="utf-8"))

E = 5_000_000
u = pd.read_csv(MAIN / "research" / "candles" / "USDTTMN_1.csv").close.iloc[-1]
snap = pd.read_csv(MAIN / "research" / "markets_snapshot.csv")
row = snap[snap.symbol == "USDTTMN"].iloc[0]
# spread_pct_* columns are in PERCENT (market_snapshot.py: (ask-bid)/mid*100)
usdt_spread = row.spread_pct_median / 100
usdt_spread_p75 = row.spread_pct_p75 / 100
conv = 2 * 0.003 + usdt_spread                   # taker both ways + one full spread (half each way)
conv_author = 2 * 0.003 + 0.00114                # the original study's figure (spread read as a fraction)
size = E * 0.006 * 0.4 / (0.009 + 0.003 + 0.0025)  # engine toman notional at size_factor 0.4
H = 45
days45 = H / 24
print(f"USDTTMN last close {u:,.0f}; equity = {E / u:.2f} USDT; engine size {size:,.0f} TMN = {size / u:.2f} USDT")
print(f"USDTTMN spread median {usdt_spread * 100:.4f}% (p75 {usdt_spread_p75 * 100:.4f}%) -> conversion {conv * 100:.3f}% "
      f"(original study used {conv_author * 100:.3f}%)")

td = C["test_days"]
g = C["grid"]


def cand(key):
    te = g[key]["test"]
    return te.get("n", 0), te.get("mean")


# per-market walk-forward on the corrected grid (train = all - test), choose by train sum, disable if <= 0
def per_market_wf(prefix):
    tot_n, tot_sum = 0, 0.0
    chosen = {}
    for s in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]:
        best, bsum = None, 0.0
        for thr in ["0.0036", "0.0045", "0.006"]:
            a = g[f"{prefix}|thr{thr}"]["per_market_all"][s]
            t = g[f"{prefix}|thr{thr}"]["per_market_test"][s]
            trs = (a.get("sum", 0) or 0) - (t.get("sum", 0) or 0)
            if (a["n"] - t["n"]) > 0 and trs > bsum:
                best, bsum = thr, trs
        chosen[s] = best
        if best:
            t = g[f"{prefix}|thr{best}"]["per_market_test"][s]
            tot_n += t["n"]
            tot_sum += t.get("sum", 0) or 0
    return chosen, tot_n, (tot_sum / tot_n if tot_n else None)


scen = {}
n, m = cand("mid|stop0.009|cd1|minp30|thr0.0036")
scen["candles mid, fixed 0.36% (test)"] = (n / td * days45, m, n)
wf = C["walk_forward"]["mid|stop0.009|cd1|minp30"]
scen[f"candles mid, walk-forward (chose {wf['chosen_thr']})"] = (wf["test"]["n"] / td * days45, wf["test"].get("mean"), wf["test"]["n"])
n, m = cand("acons|stop0.009|cd1|minp30|thr0.0036")
scen["candles cons, fixed 0.36% (test)"] = (n / td * days45, m, n)
wf = C["walk_forward"]["acons|stop0.009|cd1|minp30"]
scen[f"candles cons, walk-forward (chose {wf['chosen_thr']})"] = (wf["test"]["n"] / td * days45, wf["test"].get("mean"), wf["test"]["n"])
for pre in ["mid|stop0.009|cd1|minp30", "acons|stop0.009|cd1|minp30"]:
    ch, n, m = per_market_wf(pre)
    scen[f"candles {pre.split('|')[0]}, per-market WF {ch}"] = (n / td * days45, m, n)
span_h = T["market_hours"] / 5
for thr in ["0.0036", "0.0045"]:
    r = T["replay"][f"thr{thr}|pen0.0"]
    scen[f"ticks {thr} (n={r['n']} in {span_h * 60:.0f} min, no WF)"] = (r["n"] / span_h * H, r["mean"], r["n"])

sig = {"7d window std": F["1m"]["std45"] / 100, "7d hourly RV": F["1m"]["rv_1h_x_sqrt45_pct"] / 100,
       "60d": F["60m"]["std45"] / 100}
rows = []
for X in [size, 1_750_000, 2_500_000]:
    per_trade = min(size, X * (1 - conv / 2) * 0.98)       # engine size (sf 0.4), never more than the USDT on hand
    for name, (N, mean, nobs) in scen.items():
        gain = N * (mean or 0) / 100 * per_trade
        cost = conv * X
        rows.append({"X": round(X), "scenario": name, "n_obs": nobs, "N45": round(N, 1), "mean_pct": mean,
                     "gain": round(gain), "conversion": round(cost), "net_noFX": round(gain - cost),
                     **{f"net_1sig[{k}]": round(gain - cost - v * X) for k, v in sig.items()}})
df = pd.DataFrame(rows)
pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 70)
print(df.to_string(index=False))
# break-even per-trade mean at a given frequency (per_trade ~ X)
print("\nbreak-even per-trade mean (% of trade) to pay conversion [+1 sigma] when per-trade size = X:")
for name, (N, mean, nobs) in scen.items():
    if N > 0:
        print(f"  {name:70s} N45={N:6.1f}: conv only {conv / N * 100:.3f}% | +1sig(7d window) {(conv + sig['7d window std']) / N * 100:.3f}%"
              f" | +1sig(hourly RV) {(conv + sig['7d hourly RV']) / N * 100:.3f}% | observed mean {mean}")
# tick-sample uncertainty
r = np.array([x["ret"] for x in T["replay"]["thr0.0036|pen0.0"]["trades"]])
print(f"\ntick 0.36% sample: n={len(r)} mean={r.mean():.3f}% sd={r.std(ddof=1):.3f}% se={r.std(ddof=1) / np.sqrt(len(r)):.3f}% "
      f"-> 95% CI ~ [{r.mean() - 3.18 * r.std(ddof=1) / 2:.2f}, {r.mean() + 3.18 * r.std(ddof=1) / 2:.2f}]% (t, 3 df)")
(OUT / "v_econ.json").write_text(json.dumps({"conv": conv, "conv_author": conv_author, "size": size, "usdttmn": float(u),
                                             "rows": rows}, indent=1, default=str), encoding="utf-8")
