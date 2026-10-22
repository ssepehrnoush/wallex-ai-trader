"""Step 3: validate on the live order-book ticks (bid/ask every ~2.5 s, read only).
Part 1: candle-defined signals (with volume filter) inside the candle/tick overlap, filled at real ask, exited at real bid.
Part 2: tick-native signals over all ~16 h (mid / fair from the book; no volume available), filled at ask/bid."""
import os as _os
from pathlib import Path as _P
HERE = str(_P(__file__).resolve().parent)          # this study's folder (outputs)
REPO = str(_P(__file__).resolve().parents[2])      # repo root (research/candles_30d, research/premium)
MAIN = _os.environ.get("WALLEX_MAIN", str(_P(REPO).parent / "wallex-bot"))   # live bot folder (ticks.csv, team only)
import json
import os
import numpy as np
import pandas as pd
import common as c
import engine as E

tk = pd.read_csv(c.TICKS, on_bad_lines="skip")
for col in ("t", "bid", "ask", "fair"):
    tk[col] = pd.to_numeric(tk[col], errors="coerce")
tk = tk.dropna(subset=["t", "bid", "ask", "fair"])
tk = tk[(tk.bid > 0) & (tk.ask >= tk.bid)]
MK = c.tradable()
tk = tk[tk.symbol.isin(MK)].sort_values("t")
T0, T1 = tk.t.min(), tk.t.max()
print("ticks", len(tk), pd.to_datetime(T0, unit="s", utc=True), "->", pd.to_datetime(T1, unit="s", utc=True))
S = {m: g[["t", "bid", "ask", "fair"]].to_numpy() for m, g in tk.groupby("symbol")}
med_spread = {m: float(np.median(a[:, 2] / a[:, 1] - 1)) for m, a in S.items()}


def tick_at(m, t):
    a = S[m]
    j = np.searchsorted(a[:, 0], t)
    if j >= len(a) or a[j, 0] - t > 30:   # need a tick within 30 s
        return None
    return a[j]


CONFIGS = [("C", 15, 0.02, 3.0), ("A", 5, 0.005, 3.0), ("A", 15, 0.01, 3.0), ("B", 5, 0.01, 3.0), ("C", 5, 0.01, 3.0),
           ("B", 15, 0.01, 3.0), ("C", 15, 0.01, 3.0)]
HS = (15, 60)

# ---------- Part 1: candle signals, tick execution ----------
D = c.load_all()
grid = next(iter(D.values())).index
p1 = []
for cfg in CONFIGS:
    v, k, thr, Z = cfg
    for m, df in D.items():
        sig = E.signal(df, v, k, thr, Z)
        idx = E.dedupe(np.flatnonzero(sig), E.COOLDOWN_ES)
        for i in idx:
            t_sig = grid[i].timestamp() + 60          # bar i closes
            if t_sig < T0 + 60:
                continue
            for lag in (0, 1):
                te = t_sig + 60 * lag
                ent = tick_at(m, te)
                if ent is None:
                    continue
                for H in HS:
                    ex = tick_at(m, ent[0] + 60 * H)
                    if ex is None:
                        continue
                    model = np.nan
                    if i + lag + H < len(df):
                        model = c.net_ret(df.buy.values[i + lag], df.sell.values[i + lag + H])
                    p1.append({"cfg": f"{v}-k{k}-{thr}-Z{Z}", "market": m, "t": str(grid[i]), "lag": lag, "H": H,
                               "ask": ent[2], "bid_exit": ex[1], "spread_at_entry": ent[2] / ent[1] - 1,
                               "med_spread": med_spread[m], "net_tick": c.net_ret(ent[2], ex[1]), "net_candle_model": model,
                               "candle_close": df.close.values[i], "ask_vs_close": ent[2] / df.close.values[i] - 1})
p1 = pd.DataFrame(p1)

# ---------- Part 2: tick-native 1-min signals (no volume) ----------
p2 = []
for m, a in S.items():
    d = pd.DataFrame(a, columns=["t", "bid", "ask", "fair"])
    d.index = pd.to_datetime(d.t, unit="s", utc=True)
    b = d.resample("1min").last().dropna()
    b["mid"] = (b.bid + b.ask) / 2
    b["prem"] = b.mid / b.fair - 1
    b["prem_med"] = b.prem.shift(1).expanding(60).median()
    ts = b.t.values
    for k in (5, 15):
        r = b.mid / b.mid.shift(k) - 1
        g = b.fair / b.fair.shift(k) - 1
        dp = b.prem - b.prem.shift(k)
        rules = {f"A'-k{k}-0.005": (dp >= 0.005) & (r > 0) & (b.prem > b.prem_med),
                 f"A'-k{k}-0.01": (dp >= 0.01) & (r > 0) & (b.prem > b.prem_med),
                 f"B'-k{k}-0.01": (r >= 0.01) & (r - g >= 0.005),
                 f"C'-k{k}-0.01": (r >= 0.01) & (r - g >= 0.005) & (g > 0)}
        for name, s in rules.items():
            idx = E.dedupe(np.flatnonzero(s.fillna(False).values), E.COOLDOWN_ES)
            for i in idx:
                for lag in (0, 1):
                    ent = tick_at(m, ts[i] + 0.1 + 60 * lag)
                    if ent is None:
                        continue
                    for H in HS:
                        ex = tick_at(m, ent[0] + 60 * H)
                        if ex is None:
                            continue
                        p2.append({"rule": name, "market": m, "t": str(b.index[i]), "lag": lag, "H": H,
                                   "net": c.net_ret(ent[2], ex[1]), "spread_at_entry": ent[2] / ent[1] - 1,
                                   "mid_move": ((ex[1] + ex[2]) / 2) / ((ent[1] + ent[2]) / 2) - 1})
p2 = pd.DataFrame(p2)
p1.to_csv(os.path.join(c.OUT, "ticks_part1_candle_signals.csv"), index=False)
p2.to_csv(os.path.join(c.OUT, "ticks_part2_native_signals.csv"), index=False)

pd.set_option("display.width", 250)
print("\nPART 1 (candle signals, real bid/ask):")
if len(p1):
    print(p1.groupby(["cfg", "lag", "H"]).agg(n=("net_tick", "size"), net_tick=("net_tick", "mean"), model=("net_candle_model", "mean"),
                                              win=("net_tick", lambda x: (x > 0).mean()), spread=("spread_at_entry", "median"),
                                              ask_vs_close=("ask_vs_close", "median")).round(4).to_string())
    print(p1[(p1.lag == 1) & (p1.H == 60)][["cfg", "market", "t", "net_tick", "net_candle_model", "spread_at_entry"]].round(4).to_string())
print("\nPART 2 (tick-native, no volume filter):")
if len(p2):
    print(p2.groupby(["rule", "lag", "H"]).agg(n=("net", "size"), net=("net", "mean"), win=("net", lambda x: (x > 0).mean()),
                                               mid_move=("mid_move", "mean"), spread=("spread_at_entry", "median")).round(4).to_string())

summary = {
    "tick_span_utc": [str(pd.to_datetime(T0, unit="s", utc=True)), str(pd.to_datetime(T1, unit="s", utc=True))],
    "candle_overlap_end_utc": str(c.END),
    "median_spread_ticks": med_spread,
    "part1": p1.groupby(["cfg", "lag", "H"]).agg(n=("net_tick", "size"), mean_net_tick_pct=("net_tick", lambda x: x.mean() * 100),
                                                mean_net_candle_model_pct=("net_candle_model", lambda x: x.mean() * 100),
                                                win_pct=("net_tick", lambda x: (x > 0).mean() * 100)).round(3).reset_index().to_dict("records") if len(p1) else [],
    "part2": p2.groupby(["rule", "lag", "H"]).agg(n=("net", "size"), mean_net_pct=("net", lambda x: x.mean() * 100),
                                                 win_pct=("net", lambda x: (x > 0).mean() * 100),
                                                 mean_mid_move_pct=("mid_move", lambda x: x.mean() * 100)).round(3).reset_index().to_dict("records") if len(p2) else [],
}
json.dump(summary, open(os.path.join(c.OUT, "ticks_summary.json"), "w"), indent=1, default=str)
