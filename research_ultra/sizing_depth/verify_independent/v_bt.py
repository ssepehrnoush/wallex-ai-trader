"""Independent 7-day candle backtest of the LIVE fair-value rule at the 0.7% entry, 8 TMN markets.
Written from scratch (does not import the author's code or scripts/premium.py). Reads csv files only.

Model: Wallex traded minutes only (volume > 0). ask/bid = close*(1 +/- hs), hs = half the median live
spread from ticks. fair = Binance close * Wallex USDTTMN close (forward filled on a 1-min grid).
baseline = rolling 180-min median of (close/fair - 1), min 30 obs, shift(1) (known before the minute).
Entry: buy_dev <= -0.7%, Binance 1-min change >= -0.4%. Exit (live rule): stop bid <= e*(1-0.009);
target sell_dev >= -0.05% and bid >= e*(1+2*0.003+0.0005); time held >= 25 min (ARB 15). Taker 0.3% both legs.
Variants: gap kept (ffill) vs gap minutes dropped; entry at close*(1+2hs) (full spread, pessimistic).
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

MAIN = Path("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot")
OUT = Path(__file__).resolve().parent
TMN = ["BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN", "PEPETMN", "BNBTMN", "ARBTMN"]
TAKER = 0.003
ENTRY = float(sys.argv[1]) if len(sys.argv) > 1 else 0.007


def wallex(sym):
    p = MAIN / "research" / "candles" / f"{sym}_1.csv"
    if not p.exists():
        p = MAIN / "research" / "premium" / f"wallex_{sym}_1m.csv"
    d = pd.read_csv(p)
    d.index = pd.to_datetime(d.ts, unit="s", utc=True)
    return d


def binance(base):
    d = pd.read_csv(MAIN / "research" / "premium" / f"binance_{base}USDT_1m.csv", index_col=0)
    d.index = pd.to_datetime(d.index, utc=True)
    return d["close"]


def half_spreads():
    v = json.loads((OUT / "v_depth.json").read_text(encoding="utf-8"))
    return {s: v["per_market"][s]["median_spread_pct"] / 100 / 2 for s in TMN}


def run(variant):
    hs_map = half_spreads()
    u = wallex("USDTTMN")
    grid = pd.date_range(u.index[0], u.index[-1], freq="1min", tz="UTC")
    u_close = u["close"].reindex(grid).ffill()
    # minutes inside a USDTTMN hole longer than 60 min (stale fx) -> flagged
    have = (u["volume"] > 0).reindex(grid).fillna(False).astype(bool) if variant.get("fresh5") else u["close"].reindex(grid).notna()
    last_seen = pd.Series(np.where(have, np.arange(len(grid)), np.nan), index=grid).ffill()
    stale_min = pd.Series(np.arange(len(grid)), index=grid) - last_seen
    trades = []
    for s in TMN:
        w = wallex(s)
        g = binance(s[:-3])
        df = pd.DataFrame({"c": w["close"], "v": w["volume"]})
        df = df.join(g.rename("g"), how="inner").join(u_close.rename("u"), how="inner")
        df = df.join(stale_min.rename("stale"), how="left")
        df["g_prev"] = g.shift(1).reindex(df.index)
        df = df[(df.v > 0)].dropna(subset=["c", "g", "u"])
        lim = 5 if variant.get("fresh5") else (60 if variant.get("nogap") else 1e9)
        stale_ok = (df.stale <= lim).to_numpy()   # entries only; exits still see every minute
        hs = hs_map[s]
        df["fair"] = df.g * df.u
        prem = df.c / df.fair - 1
        base = prem.rolling("180min", min_periods=30).median().shift(1)
        ask = df.c * (1 + hs)
        bid = df.c * (1 - hs)
        buy_ask = df.c * (1 + 2 * hs) if variant.get("fullentry") else ask
        buy_dev = (buy_ask / df.fair - 1 - base).to_numpy()
        sell_dev = (bid / df.fair - 1 - base).to_numpy()
        mom = (df.g / df.g_prev - 1).to_numpy()
        A, B, T = buy_ask.to_numpy(), bid.to_numpy(), df.index
        hold = 15 if s == "ARBTMN" else 25
        i, n = 0, len(df)
        while i < n - 1:
            if np.isfinite(buy_dev[i]) and buy_dev[i] <= -ENTRY and stale_ok[i] and not (np.isfinite(mom[i]) and mom[i] < -0.004):
                e = A[i]
                j, why = i + 1, "end"
                while j < n:
                    if B[j] <= e * (1 - 0.009):
                        why = "stop"; break
                    if sell_dev[j] >= -0.0005 and B[j] >= e * (1 + 2 * TAKER + 0.0005):
                        why = "target"; break
                    if (T[j] - T[i]).total_seconds() >= hold * 60:
                        why = "time"; break
                    j += 1
                j = min(j, n - 1)
                px = B[j] * (0.997 if (why == "stop" and variant.get("stopcross")) else 1.0)
                r = px * (1 - TAKER) / (e * (1 + TAKER)) - 1
                trades.append({"symbol": s, "t_in": T[i], "t_out": T[j], "ret": r, "why": why,
                               "dev": buy_dev[i], "min": (T[j] - T[i]).total_seconds() / 60})
                i = j + 1
            else:
                i += 1
    tr = pd.DataFrame(trades).sort_values("t_in").reset_index(drop=True)
    return tr, grid


def stats(tr):
    if not len(tr):
        return {"n": 0}
    r = tr.ret * 100
    return {"n": int(len(tr)), "mean_pct": round(r.mean(), 3), "sum_pct": round(r.sum(), 2),
            "median_pct": round(r.median(), 3), "sd_pct": round(r.std(), 3), "win": round((r > 0).mean() * 100, 1),
            "worst": round(r.min(), 3), "exits": tr.why.value_counts().to_dict()}


def main():
    res = {"entry": ENTRY}
    rng = np.random.default_rng(7)
    for name, var in {"base_ff": {}, "nogap": {"nogap": True}, "fresh5": {"fresh5": True}, "fullentry_ff": {"fullentry": True},
                      "fullentry_fresh5": {"fullentry": True, "fresh5": True}, "base_stopcross": {"stopcross": True}}.items():
        tr, grid = run(var)
        t0 = max(tr.t_in.min(), pd.Timestamp("2026-10-14 17:41", tz="UTC"))
        t1 = grid[-1]
        days = (t1 - pd.Timestamp("2026-10-14 17:41", tz="UTC")).total_seconds() / 86400
        split = pd.Timestamp("2026-10-14 17:41", tz="UTC") + pd.Timedelta(days=4)
        a, b = tr[tr.t_in < split], tr[tr.t_in >= split]
        st = stats(tr)
        st["days"] = round(days, 3)
        st["per_day"] = round(len(tr) / days, 2)
        st["wf_first4d"] = stats(a)
        st["wf_last"] = stats(b)
        st["per_market"] = {s: stats(g) for s, g in tr.groupby("symbol")}
        # bootstrap of the mean, (1) iid trades, (2) day blocks
        r = tr.ret.to_numpy()
        bm = [rng.choice(r, len(r)).mean() for _ in range(20000)]
        st["boot_mean_ci90_iid_pct"] = [round(np.percentile(bm, 5) * 100, 3), round(np.percentile(bm, 95) * 100, 3)]
        day = tr.t_in.dt.floor("D")
        groups = [g.ret.to_numpy() for _, g in tr.groupby(day)]
        bd = []
        for _ in range(20000):
            pick = rng.integers(0, len(groups), len(groups))
            x = np.concatenate([groups[k] for k in pick])
            bd.append(x.mean())
        st["boot_mean_ci90_dayblock_pct"] = [round(np.percentile(bd, 5) * 100, 3), round(np.percentile(bd, 95) * 100, 3)]
        st["daily_counts"] = {str(k.date()): int(len(g)) for k, g in tr.groupby(day)}
        st["daily_sum_pct"] = {str(k.date()): round(g.ret.sum() * 100, 2) for k, g in tr.groupby(day)}
        res[name] = st
        tr.assign(t_in=tr.t_in.astype(str), t_out=tr.t_out.astype(str)).to_csv(OUT / f"v_trades_{name}_{ENTRY}.csv", index=False)
        print("==", name, {k: v for k, v in st.items() if k not in ("per_market", "daily_counts", "daily_sum_pct")})
        for s, v in st["per_market"].items():
            print("   ", s, v)
        print("    daily n", st["daily_counts"])
        print("    daily sum%", st["daily_sum_pct"])
    (OUT / f"v_bt_{ENTRY}.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
