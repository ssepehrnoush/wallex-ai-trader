"""Independent re-implementation of the author's simulator with stress options (audit only).

Options:
  delay   : 0 = fill at the signal minute's modelled ask (author); 1 = fill at the NEXT traded minute's ask
            of that market (must be <= 2 min later, else no trade) -> no same-close signal+fill
  adverse : extra fraction added to the entry price (e.g. half spread more = close printed at the bid)
  lowstop : stop checked on the candle LOW bid (low*(1-hs)); a breached stop fills at the stop level
            (or at the low bid if pessimistic='low')
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "rerun"))
import numpy as np
import pandas as pd
import dt_data as D

T0 = pd.Timestamp("2026-10-14 14:05", tz="UTC")
SPLIT = T0 + pd.Timedelta(days=4)
END = T0 + pd.Timedelta(days=7)
EPOCH = pd.Timestamp("2026-10-14 00:00", tz="UTC")
F = D.TAKER

def tmin(idx):
    return np.asarray((idx - EPOCH) / pd.Timedelta(minutes=1), dtype=float)

T0m, SPLITm, ENDm = (float((x - EPOCH) / pd.Timedelta(minutes=1)) for x in (T0, SPLIT, END))

_U = None
def load(usdt_max_age=1e9, spreads=None, alt_baseline_noshift=False):
    global _U
    if _U is None:
        _U = D.usdt_series()
    u, uage = _U
    SP = spreads or D.live_spreads()
    M = {}
    for s in D.SYMS:
        df = D.market_frame(s, u, uage, SP[s] / 2, usdt_max_age)
        w = D.wallex_1m(s)
        df["low"] = w.low.reindex(df.index)
        df["high"] = w.high.reindex(df.index)
        df = df[df.buy_dev.notna() & (df.index < END + pd.Timedelta(hours=1))]
        hs = SP[s] / 2
        M[s] = {"t": tmin(df.index), "ask": df.ask.to_numpy(), "bid": df.bid.to_numpy(), "bd": df.buy_dev.to_numpy(),
                "sd": df.sell_dev.to_numpy(), "gret": df.gret.fillna(0).to_numpy(), "vol": df.vol3h.to_numpy(),
                "spread": df.spread.to_numpy(), "n": len(df), "lowbid": df.low.to_numpy() * (1 - hs), "hs": hs,
                "close": df.c.to_numpy(), "idx": df.index, "open_ask": D.wallex_1m(s).open.reindex(df.index).to_numpy() * (1 + hs)}
    return M, SP


def simulate(M, thr, delay=0, adverse=0.0, lowstop=False, low_fill="stop", t_lo=None, t_hi=None, max_pos=None, delay_px="ask", flat=0.0):
    syms = D.SYMS
    t_lo = T0m if t_lo is None else t_lo
    t_hi = ENDm if t_hi is None else t_hi
    ts = np.concatenate([M[s]["t"] for s in syms])
    ks = np.concatenate([np.full(M[s]["n"], k) for k, s in enumerate(syms)])
    ii = np.concatenate([np.arange(M[s]["n"]) for s in syms])
    order = np.lexsort((ks, ts))
    pos, pend, cool, trades = {}, {}, {}, []
    for o in order:
        t, k, i = ts[o], ks[o], ii[o]
        s = syms[k]
        A = M[s]
        if s in pend:                       # delayed fill at this (next traded) minute
            t_sig, i_sig, bd0, th0 = pend.pop(s)
            if t - t_sig <= 2 and not (max_pos and len(pos) >= max_pos):
                pos[s] = (A[delay_px][i] * (1 + adverse * A["hs"] + flat), t, i, bd0, th0)
            if delay_px == "ask" or s not in pos:
                continue                    # fill at this minute's close: not checked for exit (as author: entry row)
            # fill at this minute's OPEN: the same minute's close may already exit (fall through)
        if s in pos:
            e, t_open, i_open, bd0, th0 = pos[s]
            bid = A["bid"][i]
            why = None
            if bid <= e * (1 - D.STOP):
                why, px = "stop", bid
            elif lowstop and A["lowbid"][i] <= e * (1 - D.STOP):
                why, px = "stop", (e * (1 - D.STOP) if low_fill == "stop" else A["lowbid"][i])
            elif A["sd"][i] >= -D.SLACK and bid >= e * D.BE:
                why, px = "target", bid
            elif t - t_open >= D.HOLD[s]:
                why, px = "time", bid
            if why:
                r = px * (1 - F) ** 2 / e - 1
                trades.append((s, t_open, t, r * 100, why, bd0 * 100, th0 * 100))
                del pos[s]
                if r < 0:
                    cool[s] = t + D.COOLDOWN_MIN
            continue
        if not (t_lo <= t < t_hi):
            continue
        th = thr[s][i]
        if not np.isfinite(th) or A["bd"][i] > -th:
            continue
        if A["gret"][i] < -D.MAX_DROP_60S or A["spread"][i] > D.MAX_SPREAD:
            continue
        if cool.get(s, -1e18) > t:
            continue
        if max_pos and len(pos) >= max_pos:
            continue
        if delay:
            pend[s] = (t, i, A["bd"][i], th)
        else:
            pos[s] = (A["ask"][i] * (1 + adverse * A["hs"] + flat), t, i, A["bd"][i], th)
    for s, (e, t_open, i_open, bd0, th0) in pos.items():
        A = M[s]
        r = A["bid"][-1] * (1 - F) ** 2 / e - 1
        trades.append((s, t_open, A["t"][-1], r * 100, "end", bd0 * 100, th0 * 100))
    tr = pd.DataFrame(trades, columns=["symbol", "t_open", "t_close", "ret_pct", "why", "entry_dev_pct", "thr_pct"])
    tr["period"] = np.where(tr.t_open < SPLITm, "IS", "OOS")
    tr["minutes"] = tr.t_close - tr.t_open
    return tr.sort_values("t_open").reset_index(drop=True)


def stats(tr):
    n = len(tr)
    if not n:
        return {"n": 0, "mean": None, "sum": 0.0, "win": None, "exits": {}}
    return {"n": n, "mean": round(tr.ret_pct.mean(), 3), "sum": round(tr.ret_pct.sum(), 3),
            "win": round((tr.ret_pct > 0).mean() * 100, 1), "exits": tr.why.value_counts().to_dict()}


def boot_diff(tr_v, tr_b, lo, hi, nboot=5000, block=120.0, seed=7):
    nblk = int(np.ceil((hi - lo) / block))
    def bs(tr):
        x = tr[(tr.t_open >= lo) & (tr.t_open < hi)]
        b = ((x.t_open - lo) // block).astype(int).clip(0, nblk - 1)
        return np.bincount(b, weights=x.ret_pct, minlength=nblk)
    d = bs(tr_v) - bs(tr_b)
    rng = np.random.default_rng(seed)
    boot = d[rng.integers(0, nblk, size=(nboot, nblk))].sum(1)
    return round(float(d.sum()), 3), [round(float(np.quantile(boot, 0.05)), 3), round(float(np.quantile(boot, 0.95)), 3)], round(float((boot > 0).mean()), 3)
