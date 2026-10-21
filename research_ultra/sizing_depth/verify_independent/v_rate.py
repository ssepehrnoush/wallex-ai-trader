"""Opportunity rate: candle model vs live ticks, and a latency (next-minute entry) robustness test.
Own code. Candle buy_dev built exactly as in v_bt.py (base_ff variant)."""
import io
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from v_bt import MAIN, OUT, TMN, binance, half_spreads, wallex

TICK_HOURS = json.loads((OUT / "v_depth.json").read_text(encoding="utf-8"))["hours_per_symbol"]
THR = [0.003, 0.004, 0.005, 0.006, 0.007]


def series():
    hs_map = half_spreads()
    u = wallex("USDTTMN")
    grid = pd.date_range(u.index[0], u.index[-1], freq="1min", tz="UTC")
    uc = u["close"].reindex(grid).ffill()
    out = {}
    for s in TMN:
        w = wallex(s)
        g = binance(s[:-3])
        df = pd.DataFrame({"c": w["close"], "v": w["volume"]}).join(g.rename("g"), how="inner").join(uc.rename("u"), how="inner")
        df["gret"] = (g / g.shift(1) - 1).reindex(df.index)
        df = df[df.v > 0].dropna(subset=["c", "g", "u"])
        df = df[df.index >= pd.Timestamp("2026-10-14 17:41", tz="UTC")]
        hs = hs_map[s]
        fair = df.g * df.u
        base = (df.c / fair - 1).rolling("180min", min_periods=30).median().shift(1)
        df["fair"], df["base"] = fair, base
        df["buy_dev"] = df.c * (1 + hs) / fair - 1 - base
        df["sell_dev"] = df.c * (1 - hs) / fair - 1 - base
        df["hs"] = hs
        out[s] = df
    return out


def episodes_count(idx, mask, gap_s=10 * 60):
    """first minute of each run of cheap minutes, runs separated by > gap_s"""
    t = idx[mask]
    if len(t) == 0:
        return 0
    d = np.diff((t - t[0]).total_seconds().to_numpy())
    return int(1 + (d > gap_s).sum())


def main():
    S = series()
    t0, t1 = pd.Timestamp("2026-10-14 17:41", tz="UTC"), max(df.index.max() for df in S.values())
    hours = (t1 - t0).total_seconds() / 3600
    tick = json.loads((OUT / "v_depth.json").read_text(encoding="utf-8"))
    res = {"candle_hours": round(hours, 1), "rate": {}}
    for x in THR:
        exp_min, exp_ep, per = 0.0, 0.0, {}
        for s, df in S.items():
            m = (df.buy_dev <= -x).to_numpy()
            mins, ep = int(m.sum()), episodes_count(df.index, m)
            per[s] = {"cheap_minutes": mins, "episodes": ep}
            exp_min += mins / hours * TICK_HOURS[s]
            exp_ep += ep / hours * TICK_HOURS[s]
        obs_min = tick["cheap_minutes_total"][f"{x*100:.1f}".rstrip("0").rstrip(".")] if f"{x*100:.1f}".rstrip("0").rstrip(".") in tick["cheap_minutes_total"] else None
        obs_ep = tick["episodes"].get(f"{x*100:.1f}%", {}).get("n")
        res["rate"][f"{x*100:.1f}%"] = {"candle_expected_cheap_minutes_in_tick_hours": round(exp_min, 2),
                                         "tick_observed_cheap_minutes": obs_min,
                                         "candle_expected_episodes_in_tick_hours": round(exp_ep, 2),
                                         "tick_observed_episodes": obs_ep,
                                         "poisson_P(obs<=observed | candle rate)": round(sum(math.exp(-exp_ep) * exp_ep ** k / math.factorial(k) for k in range(0, (obs_ep or 0) + 1)), 4) if obs_ep is not None else None,
                                         "per_market": per}
    # observed tick episodes at 0.6/0.7 are 0
    for k in ["0.6%", "0.7%"]:
        e = res["rate"][k]["candle_expected_episodes_in_tick_hours"]
        res["rate"][k]["tick_observed_episodes"] = 0
        res["rate"][k]["poisson_P(obs<=observed | candle rate)"] = round(math.exp(-e), 4)

    # Latency test: signal at minute i (buy_dev <= -0.7%), but buy at the NEXT traded minute's ask (close*(1+hs)).
    # Exit rule as live; returns compared with same-minute entry.
    lat = []
    for s, df in S.items():
        A = (df.c * (1 + df.hs)).to_numpy(); B = (df.c * (1 - df.hs)).to_numpy()
        bd, sd, T, gr = df.buy_dev.to_numpy(), df.sell_dev.to_numpy(), df.index, df.gret.to_numpy()
        hold = 15 if s == "ARBTMN" else 25
        i, n = 0, len(df)
        while i < n - 2:
            if np.isfinite(bd[i]) and bd[i] <= -0.007 and not (np.isfinite(gr[i]) and gr[i] < -0.004):
                for delay in (0, 1):
                    k = i + delay
                    e = A[k]
                    j, why = k + 1, "end"
                    while j < n:
                        if B[j] <= e * 0.991: why = "stop"; break
                        if sd[j] >= -0.0005 and B[j] >= e * 1.0065: why = "target"; break
                        if (T[j] - T[k]).total_seconds() >= hold * 60: why = "time"; break
                        j += 1
                    j = min(j, n - 1)
                    r = B[j] * 0.997 / (e * 1.003) - 1
                    lat.append({"symbol": s, "t": str(T[i]), "delay": delay, "ret": r, "why": why,
                                "gap_to_next_min": (T[i + 1] - T[i]).total_seconds() / 60, "dev_next": bd[i + 1]})
                    if delay == 0:
                        j0 = j
                i = j0 + 1
            else:
                i += 1
    L = pd.DataFrame(lat)
    d0, d1 = L[L.delay == 0], L[L.delay == 1]
    res["latency"] = {"n": int(len(d0)), "mean_same_minute_pct": round(d0.ret.mean() * 100, 3),
                      "mean_next_minute_pct": round(d1.ret.mean() * 100, 3),
                      "win_next_minute": round((d1.ret > 0).mean() * 100, 1),
                      "share_signal_still_le_0.7_next_min": round(float((d1.dev_next <= -0.007).mean()), 3),
                      "share_signal_still_le_0.35_next_min": round(float((d1.dev_next <= -0.0035).mean()), 3),
                      "median_gap_to_next_traded_min": float(d1.gap_to_next_min.median()),
                      "per_market_next_minute": d1.groupby("symbol").ret.agg(["count", "mean"]).assign(mean=lambda x: (x["mean"] * 100).round(3)).to_dict("index")}
    # global move at the signal minute: are cheap closes mostly minutes where Binance jumped up?
    sig = []
    for s, df in S.items():
        m = df.buy_dev <= -0.007
        sig.append(df.loc[m, ["gret"]].assign(symbol=s))
    G = pd.concat(sig)
    res["signal_minute_binance_ret"] = {"n_minutes": int(len(G)), "median_pct": round(float(G.gret.median() * 100), 3),
                                         "share_gret_gt_0.2pct": round(float((G.gret > 0.002).mean()), 3),
                                         "share_gret_gt_0.4pct": round(float((G.gret > 0.004).mean()), 3)}
    (OUT / "v_rate.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
