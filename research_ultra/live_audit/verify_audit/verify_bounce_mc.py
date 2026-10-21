"""Bid/ask-bounce Monte Carlo for the forecast (local files only, low memory).

The 1-minute close is a last trade: it sits at the bid or at the ask, not at the mid. The forecast
(bt_fairvalue.py method) treats the close as the mid (ask = close x (1 + hs)). Here each traded
minute's close is randomly assigned to the bid or the ask (50/50), so the true ask is close x (1 + 2hs)
or close, and the true bid is close or close x (1 - 2hs). The engine's rule (current AI policy, per-symbol
120-min cooldown after a loss) then runs on those true prices, and selection on a cheap ask happens
naturally. 200 repetitions. Also: share of 1.83 h windows in the 7-day candles with no minute at or
below -0.5% / -0.6% for PEPE and ARB (today's live ticks).
"""
import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import ctypes
import csv
import json
import statistics
import time
from pathlib import Path

import numpy as np
import pandas as pd

MAIN = Path("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot")
HERE = Path(__file__).resolve().parent
SYMS = ["BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN", "PEPETMN", "ARBTMN"]
TAKER, SLACK = 0.003, 0.0005
REPS = 200


def avail_commit_mb():
    class M(ctypes.Structure):
        _fields_ = [("l", ctypes.c_ulong), ("m", ctypes.c_ulong)] + [(x, ctypes.c_ulonglong) for x in "abcdefg"]
    m = M()
    m.l = ctypes.sizeof(M)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.d >> 20


for _ in range(60):
    if avail_commit_mb() >= 600:
        break
    time.sleep(5)
else:
    raise SystemExit("aborting: commit nearly exhausted on the live host")


def wallex(sym):
    p = MAIN / "research" / "candles" / f"{sym}_1.csv"
    if not p.exists():
        p = MAIN / "research" / "premium" / f"wallex_{sym}_1m.csv"
    raw = pd.read_csv(p, usecols=["ts", "close", "volume"])
    df = pd.DataFrame({"c": raw.close.astype(float).to_numpy(), "v": raw.volume.astype(float).to_numpy()},
                      index=pd.to_datetime(raw.ts.astype("int64"), unit="s", utc=True))
    return df[~df.index.duplicated(keep="last")].sort_index()


def binance(base):
    s = pd.read_csv(MAIN / "research" / "premium" / f"binance_{base}USDT_1m.csv", index_col=0, parse_dates=True)["close"]
    if s.index.tz is None:
        s.index = s.index.tz_localize("UTC")
    return s.astype(float)


def live_half_spreads():
    t0 = pd.Timestamp("2026-10-21 16:45", tz="UTC").timestamp()
    sp = {}
    with open(HERE / "ticks_snapshot.csv", encoding="utf-8", errors="ignore") as fh:
        for r in csv.DictReader(fh):
            try:
                if float(r["t"]) >= t0:
                    sp.setdefault(r["symbol"], []).append(float(r["ask"]) / float(r["bid"]) - 1)
            except (TypeError, ValueError, KeyError, ZeroDivisionError):
                continue
    return {s: statistics.median(v) / 2 for s, v in sp.items()}


def run(c, fair, base, tsec, hs, side_bid, entry, stop, hold, cooldown=True):
    if side_bid is None:          # close = mid (the forecast's assumption)
        ask, bid = c * (1 + hs), c * (1 - hs)
    else:
        ask = np.where(side_bid, c * (1 + 2 * hs), c)
        bid = np.where(side_bid, c, c * (1 - 2 * hs))
    bd = ask / fair - 1 - base
    sd = bid / fair - 1 - base
    cand = np.flatnonzero(np.isfinite(bd) & (bd <= -entry))
    out, free_from, cd_until = [], 0, -1.0
    n = len(c)
    for i in cand:
        if i < free_from or i >= n - 1 or tsec[i] < cd_until:
            continue
        e = ask[i]
        j, why = i + 1, "end"
        while j < n:
            if bid[j] <= e * (1 - stop):
                why = "stop"
                break
            if sd[j] >= -SLACK and bid[j] >= e * (1 + 2 * TAKER + SLACK):
                why = "target"
                break
            if tsec[j] - tsec[i] >= hold * 60:
                why = "time"
                break
            j += 1
        j = min(j, n - 1)
        r = bid[j] * (1 - TAKER) / (e * (1 + TAKER)) - 1
        out.append((r * 100, why))
        free_from = j + 1
        if cooldown and r < 0:
            cd_until = tsec[j] + 120 * 60
    return out


def main():
    st = json.loads((HERE / "state_snapshot.json").read_text(encoding="utf-8"))
    cur = st["policies"]
    hs_all = live_half_spreads()
    ucd = wallex("USDTTMN").c
    usdt = ucd.reindex(pd.date_range(ucd.index[0], ucd.index[-1], freq="1min", tz="UTC")).ffill()
    data = {}
    for s in SYMS:
        w = wallex(s)
        df = w.join((binance(s[:-3]) * usdt).rename("fair"), how="inner").dropna()
        df = df[df.v > 0]
        prem = df.c / df.fair - 1
        base = prem.rolling("180min", min_periods=30).median().shift(1)
        data[s] = (df.c.to_numpy(), df.fair.to_numpy(), base.to_numpy(), df.index.as_unit("s").asi8.astype(float), df.index)   # pandas 3: index may already be in seconds
    days = float(np.mean([(d[4][-1] - d[4][0]).total_seconds() / 86400 for d in data.values()]))
    rng = np.random.default_rng(99)
    pol = {s: (max(cur[s]["entry_dev"], 0.007), cur[s]["stop_pct"], cur[s]["max_hold_min"]) for s in SYMS if cur[s]["enabled"]}
    res = {"days": round(days, 3), "half_spread_pct": {s: round(hs_all[s] * 100, 4) for s in SYMS}, "policy": pol}

    # baseline (close = mid), with cooldown
    base_tr = {s: run(*data[s][:4], hs_all[s], None, *pol[s]) for s in pol}
    allr = [r for s in pol for r, _ in base_tr[s]]
    res["close_is_mid"] = {"trades": len(allr), "mean_pct": round(float(np.mean(allr)), 3),
                           "by_market": {s: [len(v), round(float(np.mean([r for r, _ in v])), 3) if v else None] for s, v in base_tr.items()}}
    # Monte Carlo over the bid/ask side of each close
    n_tot, m_tot, per = [], [], {s: ([], []) for s in pol}
    for _ in range(REPS):
        rs = []
        for s in pol:
            c = data[s][0]
            tr = run(*data[s][:4], hs_all[s], rng.random(len(c)) < 0.5, *pol[s])
            per[s][0].append(len(tr))
            if tr:
                per[s][1].append(float(np.mean([r for r, _ in tr])))
            rs += [r for r, _ in tr]
        n_tot.append(len(rs))
        m_tot.append(float(np.mean(rs)) if rs else np.nan)
    m_tot = np.array(m_tot)
    res["bounce_mc"] = {"reps": REPS, "trades_mean": round(float(np.mean(n_tot)), 1),
                        "trades_q5_q95": [int(np.quantile(n_tot, 0.05)), int(np.quantile(n_tot, 0.95))],
                        "mean_ret_pct_avg": round(float(np.nanmean(m_tot)), 3),
                        "mean_ret_pct_q5_q95": [round(float(np.nanquantile(m_tot, 0.05)), 3), round(float(np.nanquantile(m_tot, 0.95)), 3)],
                        "share_reps_mean_le_0": round(float(np.nanmean(m_tot <= 0)), 3),
                        "by_market": {s: {"trades_avg": round(float(np.mean(a)), 1), "mean_ret_avg": round(float(np.mean(b)), 3) if b else None}
                                      for s, (a, b) in per.items()}}

    # PEPE / ARB: share of 1.83 h windows with no minute at or below -0.5 / -0.6 / -0.7% (close = mid model)
    win = {}
    for s in ("PEPETMN", "ARBTMN", "BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN"):
        c, fair, base, tsec, idx = data[s]
        bd = pd.Series(c * (1 + hs_all[s]) / fair - 1 - base, index=idx)
        grid = pd.date_range(idx[0].ceil("min"), idx[-1], freq="1min", tz="UTC")
        row = {}
        for thr in (0.005, 0.006, 0.007):
            hit = (bd <= -thr).reindex(grid, fill_value=False).to_numpy().astype(int)
            ch = np.concatenate([[0], np.cumsum(hit)])
            m = int(1.83 * 60)
            cnt = ch[m:] - ch[:-m]
            row[f"share_zero_le_-{thr * 100:.1f}%"] = round(float((cnt == 0).mean()), 3)
        win[s] = row
    res["windows_1.83h"] = win
    (HERE / "verify_bounce_mc.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
