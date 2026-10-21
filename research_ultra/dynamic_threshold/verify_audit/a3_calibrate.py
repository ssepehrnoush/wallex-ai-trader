"""Audit 3: is the candle model's fast reversion real? Compare forward gross returns after a dip
(ask at the dip -> bid k minutes later) in the 1-minute candle model vs the real 2.5 s order-book ticks.
Gross return = bid(t+k)/ask(t) - 1 (fees not included; a round trip costs 0.6 %)."""
import numpy as np
import pandas as pd
import audit_sim as S
D = S.D

LV = [0.003, 0.004, 0.005, 0.006]
HOR = [1, 5, 10, 25]
tk = D.load_ticks()
SP = D.live_spreads()
u, uage = D.usdt_series()

def episodes(below, rearm):
    ev, armed = [], True
    for j in range(len(below)):
        if armed and below[j]:
            ev.append(j); armed = False
        elif not armed and rearm[j]:
            armed = True
    return ev

rows = []
for s in D.SYMS:
    g = tk[tk.symbol == s].sort_values("t").reset_index(drop=True)
    T, bid, ask, bd = g.t.to_numpy(), g.bid.to_numpy(), g.ask.to_numpy(), g.buy_dev.to_numpy()
    df = D.market_frame(s, u, uage, SP[s] / 2, 1e9).dropna(subset=["buy_dev"])
    h = df.index.hour + df.index.minute / 60
    for X in LV:
        # ticks
        ev = episodes(bd <= -X, bd > -X / 2)
        for j in ev:
            r = {"src": "ticks", "sym": s, "X": X}
            for k in HOR:
                jj = np.searchsorted(T, T[j] + k * 60)
                r[f"r{k}"] = (bid[jj] / ask[j] - 1) * 100 if jj < len(T) and T[jj] - T[j] < k * 60 + 30 else np.nan
            jj = np.searchsorted(T, T[j] + 25 * 60)
            r["maxbid25"] = (bid[j + 1:jj].max() / ask[j] - 1) * 100 if jj > j + 1 else np.nan
            rows.append(r)
        # candles (all hours, and the same UTC hours as the ticks)
        cb, cask, cbid = df.buy_dev.to_numpy(), df.ask.to_numpy(), df.bid.to_numpy()
        tm = S.tmin(df.index)
        ev = episodes(cb <= -X, cb > -X / 2)
        for j in ev:
            r = {"src": "candles_14-18.5h" if 14 <= h[j] < 18.5 else "candles_other_h", "sym": s, "X": X}
            for k in HOR:
                jj = np.searchsorted(tm, tm[j] + k)          # first traded minute at or after t+k
                r[f"r{k}"] = (cbid[jj] / cask[j] - 1) * 100 if jj < len(tm) and tm[jj] - tm[j] <= k + 2 else np.nan
            jj = np.searchsorted(tm, tm[j] + 25, side="right")
            r["maxbid25"] = (cbid[j + 1:jj].max() / cask[j] - 1) * 100 if jj > j + 1 else np.nan
            rows.append(r)
R = pd.DataFrame(rows)
pd.set_option("display.width", 250)
agg = R.groupby(["X", "src"]).agg(n=("r1", "size"), r1=("r1", "mean"), r5=("r5", "mean"), r10=("r10", "mean"), r25=("r25", "mean"),
                                  maxbid25=("maxbid25", "mean"), p_max_ge_065=("maxbid25", lambda x: (x >= 0.65).mean())).round(3)
print(agg)
tick_hours = {s: round((tk.symbol == s).sum() * 2.5 / 3600, 2) for s in D.SYMS}
print("tick hours per symbol:", tick_hours)
R.to_csv(S.Path(__file__).resolve().parent / "out" / "a3_dip_events.csv", index=False)
