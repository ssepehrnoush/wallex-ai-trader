"""Independent re-implementation (does not import the author's code) of the USDT-market candle backtest.

Reads raw csv files from MAIN (read-only). Rule: fair = Binance 1m close; Wallex minutes with volume > 0 only;
baseline = rolling 180-min median premium, shift(1); entry buy_dev <= -thr; exits stop / target (gap closed AND
bid >= entry*(1+2*taker+0.05%)) / time 25 min; taker 0.13% both legs.
Variants: fill model (mid = half median spread, cons = full spread + 0.1% on stop/time), stop 0.8 or 0.9%,
cooldown after loss on/off, baseline min_periods.
Walk-forward: train = first 4 days of data, test = the rest.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

MAIN = Path("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot")
OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
TAKER = 0.0013
SLACK = 0.0005


def tick_spreads():
    fr = []
    for r in ["shadow_v2", "shadow_v3", "shadow_v4", "shadow_v5", "shadow_v6"]:
        t = pd.read_csv(MAIN / "runs" / r / "ticks.csv")
        fr.append(t[t.symbol.isin(SYMS)])
    t = pd.concat(fr)
    return ((t.ask / t.bid - 1).groupby(t.symbol).median()).to_dict()


def load(sym):
    w = pd.read_csv(MAIN / "research" / "premium" / f"wallex_{sym}_1m.csv")
    w.index = pd.to_datetime(w.ts, unit="s", utc=True)
    b = pd.read_csv(MAIN / "research" / "premium" / f"binance_{sym}_1m.csv", index_col=0)
    b.index = pd.to_datetime(b.index, utc=True)
    bret = b["close"].pct_change()          # Binance 1-minute return (known at the close of minute i)
    df = pd.DataFrame({"c": w["close"], "v": w["volume"]}).join(
        pd.DataFrame({"fair": b["close"], "bret": bret}), how="inner").dropna(subset=["c", "fair"])
    n_all = len(df)
    df = df[df.v > 0].copy()
    return df, n_all


def run(df, spread, thr, model, stop, cooldown, minp, bug=False):
    hs = spread / 2
    if model == "mid":
        ask = df.c * (1 + hs)
        bid = df.c * (1 - hs)
        extra = 0.0
    elif model == "cons":   # conservative: the close could be either side of the book
        ask = df.c * (1 + spread)
        bid = df.c * (1 - spread)
        extra = 0.001
    else:   # "acons" = the author's cons definition: ask full spread above, bid half spread below
        ask = df.c * (1 + spread)
        bid = df.c * (1 - hs)
        extra = 0.001
    prem = df.c / df.fair - 1
    base = prem.rolling("180min", min_periods=minp).median().shift(1)
    bdev = (ask / df.fair - 1 - base).to_numpy()
    sdev = (bid / df.fair - 1 - base).to_numpy()
    A, B = ask.to_numpy(), bid.to_numpy()
    ts = ((df.index - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)).to_numpy().astype(np.int64)
    bret = df.bret.to_numpy()
    trades = []
    i, n = 0, len(df)
    cd_until = -1
    floor = max(thr, 2 * TAKER + 0.001)
    while i < n - 1:
        ok = (np.isfinite(bdev[i]) and bdev[i] <= -floor and spread <= 0.006 and ts[i] >= cd_until
              and not (np.isfinite(bret[i]) and bret[i] < -0.004))
        if not ok:
            i += 1
            continue
        e = A[i]
        why = "end"
        j = i + 1
        while j < n:
            if B[j] <= e * (1 - stop):
                why = "stop"; break
            if sdev[j] >= -SLACK and B[j] >= e * (1 + 2 * TAKER + 0.0005):
                why = "target"; break
            if not bug and ts[j] - ts[i] >= 25 * 60:
                why = "time"; break
            j += 1
        if j >= n:
            break      # open at end of data: drop (no exit price)
        px = B[j] * (1 - (extra if why in ("stop", "time") else 0))
        r = px * (1 - TAKER) / (e * (1 + TAKER)) - 1
        trades.append({"t": int(ts[i]), "ret": r * 100, "why": why, "min": (ts[j] - ts[i]) / 60,
                       "dev": bdev[i] * 100})
        if r < 0 and cooldown:
            cd_until = ts[j] + (10**12 if bug else 120 * 60)
        i = j + 1
    return trades


def stats(tr):
    if not tr:
        return {"n": 0}
    r = np.array([x["ret"] for x in tr])
    ex = pd.Series([x["why"] for x in tr]).value_counts().to_dict()
    return {"n": len(r), "mean": round(r.mean(), 3), "sum": round(r.sum(), 2), "win": round((r > 0).mean() * 100, 1),
            "se": round(r.std(ddof=1) / np.sqrt(len(r)), 3) if len(r) > 1 else None, "exits": ex}


def main():
    sp = tick_spreads()
    data = {s: load(s) for s in SYMS}
    t0 = min(d[0].index.min() for d in data.values())
    t1 = max(d[0].index.max() for d in data.values())
    split = int((t0 + pd.Timedelta(days=4)).timestamp())
    test_days = (t1.timestamp() - split) / 86400
    print("data", t0, "->", t1, "split", pd.Timestamp(split, unit="s", tz="UTC"), "test days", round(test_days, 2))
    print("median tick spreads %", {k: round(v * 100, 3) for k, v in sp.items()})
    for s, (d, nall) in data.items():
        print(s, "traded minutes", len(d), "of", nall, f"({len(d) / nall * 100:.1f}%)")
    res = {}
    for model in ["mid", "cons", "acons"]:
        for stop in [0.009, 0.008]:
            for cooldown in [True, False]:
                for minp in [30, 10]:
                    for thr in [0.0036, 0.0045, 0.006]:
                        allt = []
                        for s in SYMS:
                            tr = run(data[s][0], sp[s], thr, model, stop, cooldown, minp)
                            for x in tr:
                                x["sym"] = s
                            allt += tr
                        tr_ = [x for x in allt if x["t"] < split]
                        te_ = [x for x in allt if x["t"] >= split]
                        key = f"{model}|stop{stop}|cd{int(cooldown)}|minp{minp}|thr{thr}"
                        per = {s: stats([x for x in allt if x["sym"] == s]) for s in SYMS}
                        per_te = {s: stats([x for x in te_ if x["sym"] == s]) for s in SYMS}
                        res[key] = {"train": stats(tr_), "test": stats(te_), "all": stats(allt),
                                    "per_market_all": per, "per_market_test": per_te,
                                    "test_trades": [(pd.Timestamp(x["t"], unit="s").strftime("%m-%d %H:%M"), x["sym"],
                                                     round(x["ret"], 3), x["why"]) for x in te_]}
    # walk-forward selection: choose thr with the best train SUM (ties -> mean) per variant
    wf = {}
    for model in ["mid", "cons", "acons"]:
        for stop in [0.009, 0.008]:
            for cooldown in [True, False]:
                for minp in [30, 10]:
                    pre = f"{model}|stop{stop}|cd{int(cooldown)}|minp{minp}|thr"
                    cands = [(res[pre + str(t)]["train"].get("sum", 0) if res[pre + str(t)]["train"]["n"] else -9, t)
                             for t in [0.0036, 0.0045, 0.006]]
                    best = max(cands)[1]
                    wf[pre[:-4]] = {"chosen_thr": best, "train": res[pre + str(best)]["train"],
                                    "test": res[pre + str(best)]["test"],
                                    "test_trades_per_day": round(res[pre + str(best)]["test"]["n"] / test_days, 2)}
    (OUT / "v_candles.json").write_text(json.dumps({"split": split, "test_days": test_days, "spreads": sp,
                                                   "grid": res, "walk_forward": wf}, indent=1, default=str),
                                        encoding="utf-8")
    print("\n== grid (train | test) ==")
    for k, v in res.items():
        tr, te = v["train"], v["test"]
        print(f"{k:42s} train n={tr['n']:2d} mean={tr.get('mean')} sum={tr.get('sum')} | test n={te['n']:2d} "
              f"mean={te.get('mean')} sum={te.get('sum')} win={te.get('win')} ex={te.get('exits')}")
    print("\n== walk-forward ==")
    for k, v in wf.items():
        print(k, "chosen", v["chosen_thr"], "test", v["test"], "per day", v["test_trades_per_day"])
    print("\n== per market, all days, mid|stop0.009|cd1|minp30|thr0.0036 ==")
    for s, v in res["mid|stop0.009|cd1|minp30|thr0.0036"]["per_market_all"].items():
        print(s, v)
    for m_ in ["cons", "acons"]:
        print(f"== per market, all days, {m_}|stop0.009|cd1|minp30|thr0.0036 ==")
        for s, v in res[f"{m_}|stop0.009|cd1|minp30|thr0.0036"]["per_market_all"].items():
            print(s, v)
    print("== per market, TEST, mid|stop0.009|cd1|minp30 each thr ==")
    for t_ in [0.0036, 0.0045, 0.006]:
        print(t_, {k: (v["n"], v.get("mean")) for k, v in res[f"mid|stop0.009|cd1|minp30|thr{t_}"]["per_market_test"].items()})
    print("\n== reproduction with the unit bug (no 25-min time exit, permanent cooldown) ==")
    for model in ["mid", "acons"]:
        for thr in [0.0036, 0.0045, 0.006]:
            allt = []
            for s_ in SYMS:
                tr = run(data[s_][0], sp[s_], thr, model, 0.009, True, 30, bug=True)
                for x in tr:
                    x["sym"] = s_
                allt += tr
            tr_ = [x for x in allt if x["t"] < split]
            te_ = [x for x in allt if x["t"] >= split]
            hold = [x["min"] for x in allt]
            print("BUG", model, thr, "train", stats(tr_), "| test", stats(te_), "| max hold min", round(max(hold), 1) if hold else None,
                  "| holds>25min", sum(h > 25 for h in hold))
    print("test trades mid 0.36:", res["mid|stop0.009|cd1|minp30|thr0.0036"]["test_trades"])


if __name__ == "__main__":
    main()
