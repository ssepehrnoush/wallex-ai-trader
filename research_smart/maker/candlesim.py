"""30-day cross-check of the maker variants on Wallex 1m candles (walk-forward 20 d choose / 10 d report).

Fill model (candles): an order decided at the end of minute i is live from minute i+1. A BUY at P fills
at P in a later minute whose LOW < P strictly (volume > 0); a SELL at Q fills at Q if HIGH > Q.
Time-stop / end-of-test exits are taker sells at a bid proxy = last traded close x (1 - half spread),
half spread = median from the live tick file for that market.
fair = Binance close x USDTTMN close (ffill); baseline = 180-minute rolling median of the premium of
traded minutes (close/fair - 1), known at decision time; fair-adjusted FA = fair x (1 + baseline).
Guard: no buy (and resting buy cancelled) if the coin's Binance close fell > 0.3% in the last minute.
"""
import os as _os
from pathlib import Path as _P
HERE = str(_P(__file__).resolve().parent)          # this study's folder (outputs)
REPO = str(_P(__file__).resolve().parents[2])      # repo root (research/candles_30d, research/premium)
MAIN = _os.environ.get("WALLEX_MAIN", str(_P(REPO).parent / "wallex-bot"))   # live bot folder (ticks.csv, team only)
import itertools
import json
import math
import numpy as np
import pandas as pd

MAKER, TAKER = 0.0025, 0.003
SIZE = 700_000
CANDLES = rf"{REPO}\research\candles_30d\{{}}_1.csv"
BIN = rf"{REPO}\research\premium\binance_{{}}USDT_1m.csv"
OUT = rf"{HERE}"
SYMS = ["ARBTMN", "BNBTMN", "BTCTMN", "DOGETMN", "ETHTMN", "PEPETMN", "POLTMN", "SANDTMN", "SOLTMN", "XRPTMN"]
WIDE = {"POLTMN", "SANDTMN"}
STEP = {"PEPETMN": 0.0001}


def wallex(sym):
    c = pd.read_csv(CANDLES.format(sym))
    c["ts"] = c.ts.astype(np.int64)
    c = c.drop_duplicates("ts", keep="last")
    return c.set_index("ts").sort_index()


def prep(sym, hs):
    w = wallex(sym)
    u = wallex("USDTTMN")
    b = pd.read_csv(BIN.format(sym.replace("TMN", "")), index_col=0)
    b.index = ((pd.to_datetime(b.index, utc=True) - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta("1s")).astype(np.int64)
    b = b[~b.index.duplicated(keep="last")].sort_index()
    start = int(max(w.index.min(), b.index.min()))
    end = int(min(w.index.max(), b.index.max()))
    grid = np.arange(start, end + 60, 60, dtype=np.int64)
    w = w.reindex(grid)
    vol = w.volume.fillna(0).values
    traded = vol > 0
    lo = np.where(traded, w.low.values, np.nan)
    hi = np.where(traded, w.high.values, np.nan)
    close = pd.Series(np.where(traded, w.close.values, np.nan), index=grid)
    clast = close.ffill().values
    bser = b["close"].reindex(grid)
    have = bser.notna().values
    age = np.zeros(len(grid))
    last = -10**9
    for i in range(len(grid)):
        if have[i]:
            last = i
        age[i] = i - last
    bc = bser.ffill().values
    uu = u.reindex(grid)
    us = pd.Series(np.where(uu.volume.fillna(0).values > 0, uu.close.values, np.nan), index=grid).ffill().values
    fair = bc * us
    prem = pd.Series(close.values / fair - 1, index=grid)
    base = prem.rolling(180, min_periods=20).median().values
    fa = fair * (1 + base)
    valid = (age <= 5) & np.isfinite(fa) & np.isfinite(clast)
    bret = np.full(len(grid), np.nan)
    bret[1:] = bc[1:] / bc[:-1] - 1
    bret[age > 0] = np.nan
    return dict(ts=grid, lo=lo, hi=hi, clast=clast, fa=fa, valid=valid, bret=bret, traded=traded,
                hs=hs, step=STEP.get(sym, 1.0), sym=sym)


def fl(x, step):
    return round(math.floor(x / step + 1e-9) * step, 6)


def ce(x, step):
    return round(math.ceil(x / step - 1e-9) * step, 6)


def sim(d, variant, d1=0.003, m=0.002, s=0.003, T2=30, guard=False, reprice=0.001, i0=0, i1=None):
    ts, lo, hi, cl, fa, ok, br, tr = d["ts"], d["lo"], d["hi"], d["clast"], d["fa"], d["valid"], d["bret"], d["traded"]
    hs, step = d["hs"], d["step"]
    i1 = len(ts) if i1 is None else i1
    trades = []
    buyP = None  # resting buy price (live from next minute)
    buy_ref = None
    buy_exp = None  # variant a: minute index after which the buy is gone
    pos = None
    sellQ = None
    sell_ref = None
    last_fa = np.nan
    for i in range(i0, i1):
        if ok[i]:
            last_fa = fa[i]
        # ---- fills during minute i (orders set at end of i-1 or earlier)
        if buyP is not None:
            if tr[i] and lo[i] < buyP:
                pos = {"P": buyP, "i": i}
                buyP = None
                f = last_fa
                if variant == "a":
                    Q = max(pos["P"] * (1 + 0.005 + m), f)
                else:
                    Q = max(f * (1 + s), pos["P"] * 1.0055)
                sellQ = ce(Q, step)
                sell_ref = f
                continue
            if variant == "a" and i >= buy_exp:
                buyP = None
        elif pos is not None:
            if tr[i] and hi[i] > sellQ:
                trades.append({"sym": d["sym"], "i_in": pos["i"], "i_out": i, "net": sellQ * (1 - MAKER) / (pos["P"] * (1 + MAKER)) - 1,
                               "gross": sellQ / pos["P"] - 1, "why": "maker"})
                pos = None
                sellQ = None
            elif i - pos["i"] >= T2:
                X = cl[i] * (1 - hs)
                trades.append({"sym": d["sym"], "i_in": pos["i"], "i_out": i, "net": X * (1 - TAKER) / (pos["P"] * (1 + MAKER)) - 1,
                               "gross": X / pos["P"] - 1, "why": "timestop"})
                pos = None
                sellQ = None
                continue
            elif variant == "b" and ok[i] and abs(fa[i] / sell_ref - 1) > reprice:
                sellQ = ce(max(fa[i] * (1 + s), pos["P"] * 1.0055), step)
                sell_ref = fa[i]
            continue
        # ---- decisions at end of minute i (flat or resting buy)
        gblock = guard and br[i] == br[i] and br[i] < -0.003
        if pos is None:
            if buyP is not None:
                if gblock or not ok[i]:
                    buyP = None
                elif variant == "b" and abs(fa[i] / buy_ref - 1) > reprice:
                    buyP = fl(min(fa[i] * (1 - s), cl[i]), step)
                    buy_ref = fa[i]
            elif ok[i] and not gblock:
                if variant == "b":
                    buyP = fl(min(fa[i] * (1 - s), cl[i]), step)
                    buy_ref = fa[i]
                elif tr[i]:
                    ask = cl[i] * (1 + hs)
                    if ask / fa[i] - 1 <= -d1:
                        bidp = cl[i] * (1 - hs)
                        P = bidp + step if bidp + step <= fa[i] * (1 - d1) else bidp
                        buyP = fl(P, step)
                        buy_exp = i + 1  # live during minute i+1 only (T1 = 60 s)
    if pos is not None:
        X = cl[i1 - 1] * (1 - hs)
        trades.append({"sym": d["sym"], "i_in": pos["i"], "i_out": i1 - 1, "net": X * (1 - TAKER) / (pos["P"] * (1 + MAKER)) - 1,
                       "gross": X / pos["P"] - 1, "why": "eod"})
    return trades


def summ(trs, days):
    if not trs:
        return {"n": 0, "toman": 0}
    t = pd.DataFrame(trs)
    out = {"n": int(len(t)), "mean_net_pct": round(float(t.net.mean() * 100), 4), "mean_gross_pct": round(float(t.gross.mean() * 100), 4),
           "win_rate": round(float((t.net > 0).mean()), 3), "timestop_share": round(float((t.why != "maker").mean()), 3),
           "worst_pct": round(float(t.net.min() * 100), 3), "toman": round(float(t.net.sum() * SIZE)),
           "per_hour": round(len(t) / (days * 24), 3), "per_day": round(len(t) / days, 2)}
    pm = {}
    for s, g in t.groupby("sym"):
        pm[s] = {"n": int(len(g)), "mean_net_pct": round(float(g.net.mean() * 100), 3), "win": round(float((g.net > 0).mean()), 2),
                 "timestop": round(float((g.why != "maker").mean()), 2), "toman": round(float(g.net.sum() * SIZE))}
    out["per_market"] = pm
    return out


if __name__ == "__main__":
    import sys
    sys.path.insert(0, OUT)
    from ticksim import load_ticks
    tk = load_ticks()
    hsd = ((tk.ask / tk.bid - 1) / 2).groupby(tk.symbol).median().to_dict()
    data = {s: prep(s, hsd[s]) for s in SYMS}
    ts0 = min(d["ts"][0] for d in data.values())
    split = ts0 + 20 * 86400
    GRID = []
    for s_, T2, g in itertools.product([0.002, 0.003, 0.004, 0.006], [10, 30, 60], [False, True]):
        GRID.append(("b", dict(s=s_, T2=T2, guard=g), f"b s={s_*100:.1f}% T2={T2} guard={'on' if g else 'off'}"))
    for d1, m, T2, g in itertools.product([0.002, 0.003, 0.005], [0.001, 0.002, 0.004], [10, 30, 60], [False, True]):
        GRID.append(("a", dict(d1=d1, m=m, T2=T2, guard=g), f"a d1={d1*100:.1f}% m={m*100:.1f}% T2={T2} guard={'on' if g else 'off'}"))
    rows = []
    for v, p, lab in GRID:
        rec = {"label": lab, "variant": v}
        for per in ["WF_first20d", "WF_last10d"]:
            trs = []
            days = 0
            for s, d in data.items():
                ts = d["ts"]
                if per == "WF_first20d":
                    i0, i1 = 0, int(np.searchsorted(ts, split))
                else:
                    i0, i1 = int(np.searchsorted(ts, split)), len(ts)
                trs += sim(d, v, i0=i0, i1=i1, **p)
            days = 20 if per == "WF_first20d" else (max(d["ts"][-1] for d in data.values()) - split) / 86400
            rec[per + "_core"] = summ([x for x in trs if x["sym"] not in WIDE], days)
            rec[per + "_wide"] = summ([x for x in trs if x["sym"] in WIDE], days)
        rows.append(rec)
        print(lab, rec["WF_first20d_core"].get("n"), rec["WF_first20d_core"].get("mean_net_pct"), rec["WF_first20d_core"].get("toman"), "|",
              rec["WF_last10d_core"].get("n"), rec["WF_last10d_core"].get("mean_net_pct"), rec["WF_last10d_core"].get("toman"), "| wide L10",
              rec["WF_last10d_wide"].get("n"), rec["WF_last10d_wide"].get("mean_net_pct"), flush=True)
    res = {"split_utc": str(pd.to_datetime(split, unit="s")), "half_spreads_used": {k: round(v, 5) for k, v in hsd.items()}}
    for v in ["b", "a", "any"]:
        cand = [r for r in rows if (v == "any" or r["variant"] == v) and r["WF_first20d_core"].get("n", 0) >= 10]
        best = max(cand, key=lambda r: r["WF_first20d_core"]["toman"])
        res["select_" + v] = {"label": best["label"], "first20d_core": best["WF_first20d_core"], "last10d_core": best["WF_last10d_core"],
                              "last10d_wide_flagged": best["WF_last10d_wide"], "first20d_wide_flagged": best["WF_first20d_wide"]}
    res["configs_positive_core"] = {"first20d": sum(1 for r in rows if r["WF_first20d_core"].get("toman", 0) > 0),
                                    "last10d": sum(1 for r in rows if r["WF_last10d_core"].get("toman", 0) > 0), "of": len(rows)}
    res["configs_positive_wide"] = {"first20d": sum(1 for r in rows if r["WF_first20d_wide"].get("toman", 0) > 0),
                                    "last10d": sum(1 for r in rows if r["WF_last10d_wide"].get("toman", 0) > 0), "of": len(rows)}
    res["table"] = [{"label": r["label"], "F20_n": r["WF_first20d_core"].get("n"), "F20_mean": r["WF_first20d_core"].get("mean_net_pct"),
                     "F20_toman": r["WF_first20d_core"].get("toman"), "L10_n": r["WF_last10d_core"].get("n"),
                     "L10_mean": r["WF_last10d_core"].get("mean_net_pct"), "L10_toman": r["WF_last10d_core"].get("toman"),
                     "L10_ts": r["WF_last10d_core"].get("timestop_share"),
                     "wide_F20_n": r["WF_first20d_wide"].get("n"), "wide_F20_mean": r["WF_first20d_wide"].get("mean_net_pct"),
                     "wide_L10_n": r["WF_last10d_wide"].get("n"), "wide_L10_mean": r["WF_last10d_wide"].get("mean_net_pct")} for r in rows]
    json.dump(res, open(OUT + r"\candle_wf.json", "w"), indent=1)
    for v in ["b", "a", "any"]:
        print(v, json.dumps({k: res["select_" + v][k] for k in ["label"]}), res["select_" + v]["first20d_core"].get("mean_net_pct"),
              res["select_" + v]["last10d_core"])
    print(res["configs_positive_core"], res["configs_positive_wide"])
