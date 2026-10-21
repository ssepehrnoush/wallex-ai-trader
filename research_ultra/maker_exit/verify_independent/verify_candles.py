"""Independent re-implementation (verifier) of the maker-exit vs taker-exit comparison on 1m candles.

Written from scratch from the raw csv files in MAIN (read-only). Does NOT import the author's code.
No network access at all.

Model
- fair(TMN) = Binance 1m close x Wallex USDTTMN 1m close (USDTTMN forward-filled on a 1-minute grid).
- Only Wallex minutes with volume > 0. ask/bid = close x (1 +/- hs), hs = half the median live spread (ticks.csv).
- baseline = rolling 180-min median of (close/fair - 1), min 30 samples, shift(1) (known before the minute).
- Entry (current rule): buy_dev <= -0.7% and Binance 1-minute change > -0.4%, one position per market.
- Exits (same entries for all arms = paired):
    cur      : stop if bid <= e(1-0.9%); target if sell_dev >= -0.05% and bid >= e*1.0065 (sold at the close bid);
               time if held >= 25 min (ARB 15) -> sold at the bid.
    cur_thr  : same triggers, target sold exactly at its threshold price (taker lower bound for smooth moves).
    mk_a/b/c : resting ask at P_j (re-priced each minute from the PREVIOUS calendar minute's fair and the shift(1)
               baseline, floored at the maker break-even e*1.006). Fills in minute j>entry only if high_j > P_j.
               Stop/time handled exactly as cur (taker at the bid). Same-minute stop+fill -> stop wins (pessimistic).
    mk_bT    : mk_b plus the taker target of cur as a fallback.
- Net return: buy fee taken in coin; r = (1-taker) * x * (1-fee_exit) / e - 1.

  python verify_candles.py [--bid-model mid|ask_print|bid_print] [--usdt-max-stale MIN] [--split ISO]
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

MAIN = Path("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot")
OUT = Path(__file__).resolve().parent
SYMS = ["BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN", "PEPETMN", "BNBTMN", "ARBTMN"]
TAKER, MAKER = 0.003, 0.0025
ENTRY, SLACK, STOP = 0.007, 0.0005, 0.009
BE_T = 1 + 2 * TAKER + 0.0005      # engine target break-even
BE_M = 1 + TAKER + MAKER + 0.0005  # maker break-even floor
MAX_DROP_1M = 0.004

ap = argparse.ArgumentParser()
ap.add_argument("--bid-model", default="mid", choices=["mid", "ask_print", "bid_print"],
                help="exit-side bid = close*(1-hs) [mid], close*(1-2hs) [every close an ask print], close [bid print]")
ap.add_argument("--usdt-max-stale", type=float, default=0,
                help="if >0, skip entries where USDTTMN last real trade is older than this many minutes")
ap.add_argument("--split", default="2026-10-18 14:05", help="days 1-4 / 5-7 split (UTC)")
ap.add_argument("--spreads", default="ticks", choices=["ticks", "wide"])
ap.add_argument("--tag", default="")
a = ap.parse_args()


def epoch_s(idx):
    return ((idx - pd.Timestamp(0, tz="UTC")) / pd.Timedelta(seconds=1)).to_numpy().astype(float)


def wallex_file(sym):
    p = MAIN / "research" / "candles" / f"{sym}_1.csv"
    if not p.exists():
        p = MAIN / "research" / "premium" / f"wallex_{sym}_1m.csv"
    d = pd.read_csv(p)
    d.index = pd.to_datetime(d.ts, unit="s", utc=True)
    return d[["open", "high", "low", "close", "volume"]].astype(float)


def binance_file(base):
    d = pd.read_csv(MAIN / "research" / "premium" / f"binance_{base}USDT_1m.csv", index_col=0)
    d.index = pd.to_datetime(d.index, utc=True)
    return d["close"].astype(float)


# ---- spreads from live ticks (median ask/bid-1), shadow_v2..v6 + live_main snapshot
ticks = []
for r in ["shadow_v2", "shadow_v3", "shadow_v4", "shadow_v5", "shadow_v6", "live_main"]:
    t = pd.read_csv(MAIN / "runs" / r / "ticks.csv", on_bad_lines="skip")
    t = t[pd.to_numeric(t.ask, errors="coerce").notna() & pd.to_numeric(t.bid, errors="coerce").notna()]
    ticks.append(t[["symbol", "bid", "ask"]].astype({"bid": float, "ask": float}))
ticks = pd.concat(ticks)
ticks = ticks[(ticks.bid > 0) & (ticks.ask >= ticks.bid)]
spreads = (ticks.ask / ticks.bid - 1).groupby(ticks.symbol).median().to_dict()
if a.spreads == "wide":   # older, wider spreads quoted in the brief / author's sensitivity
    spreads.update({"XRPTMN": 0.00377, "SOLTMN": 0.00268, "BNBTMN": 0.00417, "ARBTMN": 0.00469})

# ---- USDTTMN on a full minute grid
u = wallex_file("USDTTMN")
grid = pd.date_range(u.index[0], u.index[-1] + pd.Timedelta(minutes=0), freq="1min")
u_close = u.close.reindex(grid).ffill()
last_real = pd.Series(np.where(u.volume > 0, epoch_s(u.index), np.nan), index=u.index)
last_real = last_real.reindex(grid).ffill()
usdt_age_min = (pd.Series(epoch_s(grid), index=grid) - last_real) / 60

split = pd.Timestamp(a.split, tz="UTC")


def net(e, x, fee_exit):
    return (1 - TAKER) * x * (1 - fee_exit) / e - 1


rows = []
for s in SYMS:
    hs = spreads[s] / 2
    hold = 15 if s == "ARBTMN" else 25
    w = wallex_file(s)
    g = binance_file(s.replace("TMN", ""))
    fair_full = (g * u_close.reindex(g.index)).dropna()           # every calendar minute
    g_chg = g / g.shift(1) - 1                                     # 1-minute Binance change
    df = w[w.volume > 0].join(fair_full.rename("fair"), how="inner")
    df["gchg"] = g_chg.reindex(df.index)
    df["uage"] = usdt_age_min.reindex(df.index)
    prem = df.close / df.fair - 1
    df["base"] = prem.rolling("180min", min_periods=30).median().shift(1)
    df["fair_prev"] = fair_full.reindex(df.index - pd.Timedelta(minutes=1)).to_numpy()
    c, h, f, b = df.close.to_numpy(), df.high.to_numpy(), df.fair.to_numpy(), df.base.to_numpy()
    o = df.open.to_numpy()
    fp = df.fair_prev.to_numpy()
    ask = c * (1 + hs)
    bid_mid = c * (1 - hs)
    bid_x = {"mid": bid_mid, "ask_print": c * (1 - 2 * hs), "bid_print": c}[a.bid_model]
    buy_dev = ask / f - 1 - b
    sell_dev_x = bid_x / f - 1 - b
    tmin = epoch_s(df.index) / 60.0
    n = len(df)

    def run_exit(i, e, arm):
        """returns (j, price, fee, reason) for an entry at row i with price e"""
        stop_px = e * (1 - STOP)
        for j in range(i + 1, n):
            held = tmin[j] - tmin[i]
            stop_hit = bid_x[j] <= stop_px
            tgt_hit = (sell_dev_x[j] >= -SLACK) and (bid_x[j] >= e * BE_T)
            if arm in ("cur", "cur_thr"):
                if stop_hit:
                    return j, bid_x[j], TAKER, "stop"
                if tgt_hit:
                    if arm == "cur":
                        return j, bid_x[j], TAKER, "target"
                    thr = max(e * BE_T, f[j] * (1 + b[j] - SLACK))
                    return j, min(thr, bid_x[j]), TAKER, "target"
                if held >= hold:
                    return j, bid_x[j], TAKER, "time"
                continue
            if arm == "cur_ohlc":
                # path-aware taker: if the minute OPENS already past the threshold (gap) the taker gets the opening bid,
                # else if the high crosses it intrabar the taker sells at the threshold
                if stop_hit:
                    return j, bid_x[j], TAKER, "stop"
                thr0 = max(e * BE_T, fp[j] * (1 + b[j] - SLACK)) if np.isfinite(fp[j]) and np.isfinite(b[j]) else np.inf
                kx = bid_x[j] / c[j]
                if o[j] * kx >= thr0:
                    return j, o[j] * kx, TAKER, "target_gap"
                if h[j] * kx >= thr0:
                    return j, thr0, TAKER, "target"
                if tgt_hit:
                    thr = max(e * BE_T, f[j] * (1 + b[j] - SLACK))
                    return j, min(thr, bid_x[j]), TAKER, "target"
                if held >= hold:
                    return j, bid_x[j], TAKER, "time"
                continue
            if arm == "cur_cont":
                # taker that watches intrabar: target fires when the implied intrabar bid (high scaled like the close)
                # crosses the threshold known at the start of the minute; sells exactly at that threshold
                if stop_hit:
                    return j, bid_x[j], TAKER, "stop"
                thr0 = max(e * BE_T, fp[j] * (1 + b[j] - SLACK)) if np.isfinite(fp[j]) and np.isfinite(b[j]) else np.inf
                if h[j] * (bid_x[j] / c[j]) >= thr0:
                    return j, thr0, TAKER, "target"
                if tgt_hit:
                    thr = max(e * BE_T, f[j] * (1 + b[j] - SLACK))
                    return j, min(thr, bid_x[j]), TAKER, "target"
                if held >= hold:
                    return j, bid_x[j], TAKER, "time"
                continue
            # maker arms
            ref_mid = fp[j] * (1 + b[j]) if np.isfinite(fp[j]) and np.isfinite(b[j]) else np.nan
            if arm == "mk_a":
                ref = ref_mid
            elif arm in ("mk_b", "mk_bT"):
                ref = ref_mid * (1 + hs)
            elif arm == "mk_c":
                ref = 0.0
            elif arm == "mk_a_raw":
                ref = ref_mid
            P = ref if arm == "mk_a_raw" else max(e * BE_M, ref if np.isfinite(ref) else 0.0)
            if not np.isfinite(P):
                P = np.inf
            if stop_hit:
                return j, bid_x[j], TAKER, "stop"
            if h[j] > P:
                return j, P, MAKER, "maker"
            if arm == "mk_bT" and tgt_hit:
                return j, bid_x[j], TAKER, "target"
            if held >= hold:
                return j, bid_x[j], TAKER, "time"
        return n - 1, bid_x[n - 1], TAKER, "end"

    # sequential entries of the current rule
    i = 0
    while i < n - 1:
        ok = (np.isfinite(buy_dev[i]) and buy_dev[i] <= -ENTRY
              and not (np.isfinite(df.gchg.iat[i]) and df.gchg.iat[i] < -MAX_DROP_1M))
        if ok and a.usdt_max_stale > 0 and df.uage.iat[i] > a.usdt_max_stale:
            ok = False
        if not ok:
            i += 1
            continue
        e = ask[i]
        rec = {"symbol": s, "t": df.index[i], "entry_dev_pct": buy_dev[i] * 100, "hs_pct": hs * 100,
               "usdt_age_min": df.uage.iat[i]}
        jc = None
        for arm in ["cur", "cur_thr", "cur_cont", "cur_ohlc", "mk_a", "mk_b", "mk_c", "mk_a_raw", "mk_bT"]:
            j, x, fee, why = run_exit(i, e, arm)
            rec[f"{arm}_ret"] = net(e, x, fee) * 100
            rec[f"{arm}_why"] = why
            rec[f"{arm}_hold"] = tmin[j] - tmin[i]
            rec[f"{arm}_gross"] = (x / e - 1) * 100
            if arm == "cur":
                jc = j
        rows.append(rec)
        i = jc + 1

tr = pd.DataFrame(rows)
tr["half"] = np.where(tr.t < split, "d1_4", "d5_7")
arms = ["cur", "cur_thr", "cur_cont", "cur_ohlc", "mk_a", "mk_b", "mk_c", "mk_a_raw", "mk_bT"]


def summ(d, arm):
    r = d[f"{arm}_ret"]
    return {"n": int(len(d)), "mean": round(r.mean(), 3), "sum": round(r.sum(), 2),
            "win": round((r > 0).mean() * 100, 1), "hold": round(d[f"{arm}_hold"].mean(), 1),
            "maker_fill_pct": round((d[f"{arm}_why"] == "maker").mean() * 100, 1),
            "exits": d[f"{arm}_why"].value_counts().to_dict(),
            "d1_4": round(d[d.half == "d1_4"][f"{arm}_ret"].mean(), 3),
            "d5_7": round(d[d.half == "d5_7"][f"{arm}_ret"].mean(), 3)}


rng = np.random.default_rng(7)


def paired(d, arm, ref):
    x = (d[f"{arm}_ret"] - d[f"{ref}_ret"]).to_numpy()
    bs = rng.choice(x, size=(20000, len(x)), replace=True).mean(1)
    srt = np.sort(x)
    return {"mean": round(x.mean(), 3), "t": round(x.mean() / (x.std(ddof=1) / np.sqrt(len(x))), 2),
            "ci95": [round(np.percentile(bs, 2.5), 3), round(np.percentile(bs, 97.5), 3)],
            "drop3_largest": round(srt[:-3].mean(), 3), "drop3_smallest": round(srt[3:].mean(), 3),
            "better_worse_same": [int((x > 1e-9).sum()), int((x < -1e-9).sum()), int((np.abs(x) <= 1e-9).sum())],
            "d1_4": round(x[(d.half == "d1_4").to_numpy()].mean(), 3),
            "d5_7": round(x[(d.half == "d5_7").to_numpy()].mean(), 3)}


res = {"args": vars(a), "spreads_pct": {k: round(spreads[k] * 100, 3) for k in SYMS},
       "n_d1_4": int((tr.half == "d1_4").sum()), "n_d5_7": int((tr.half == "d5_7").sum()),
       "arms": {arm: summ(tr, arm) for arm in arms},
       "paired": {f"{arm}-{ref}": paired(tr, arm, ref) for ref in ["cur", "cur_thr", "cur_cont", "cur_ohlc"]
                  for arm in ["mk_a", "mk_b", "mk_c", "mk_bT"]},
       "per_market": {s: {"n": int((tr.symbol == s).sum()),
                          **{arm: round(tr[tr.symbol == s][f"{arm}_ret"].mean(), 3) for arm in arms}}
                      for s in SYMS},
       "cur_target_gross_mean": round(tr[tr.cur_why == "target"].cur_gross.mean(), 3),
       "cur_thr_target_gross_mean": round(tr[tr.cur_thr_why == "target"].cur_thr_gross.mean(), 3)}
tag = a.tag or f"{a.bid_model}_{a.spreads}_stale{int(a.usdt_max_stale)}"
tr.to_csv(OUT / f"trades_{tag}.csv", index=False)
(OUT / f"summary_{tag}.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
print(json.dumps(res, indent=1, default=str))
