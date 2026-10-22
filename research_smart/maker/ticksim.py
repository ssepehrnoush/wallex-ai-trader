"""Maker (resting limit order) simulator on real Wallex top-of-book ticks.

READ ONLY on the bot folders. Writes nothing itself; driver scripts save results.

Fill model ("primary", conservative):
  Orders are decided on tick k and become live on tick k+1. Cancels (time-outs, re-prices, guard,
  time-stop) are also effective one tick later: the old order can still fill on tick k+1, and a
  replacement is only placed after the old one is gone (live one tick after that).
  BUY at P, with b0 = best bid on the placement tick:
    - fills at P if a live tick shows ask <= P (a seller crossed down to us), or
    - P == b0 (joined the queue): fills if a live tick shows bid < P (our level traded through);
    - P >  b0 (improved, alone at the front): fills if a live tick shows bid < b0 (the level we
      jumped ahead of was taken out);
    - P <  b0 (resting below the touch): first the bid must come down to/through P, which only
      makes us the front of the book (no fill on that tick); reference R = that bid; then a later
      tick must show bid < R.
  SELL at Q is the mirror image (bid >= Q, or ask > reference).
  No fill on mere touches. Fill is always at our limit price, full size (no partials modelled).
"trade" model: as primary, but a book trade-through only counts if the Wallex 1m candle of that
  exchange minute shows a real trade strictly through our price (crosses always count).
"strict" model: fills only on a cross (ask <= P for buys, bid >= Q for sells).
Fees: maker 0.25%, taker 0.30%, charged on each side. net = Q*(1-f_out) / (P*(1+f_in)) - 1.
"""
import os as _os
from pathlib import Path as _P
HERE = str(_P(__file__).resolve().parent)          # this study's folder (outputs)
REPO = str(_P(__file__).resolve().parents[2])      # repo root (research/candles_30d, research/premium)
MAIN = _os.environ.get("WALLEX_MAIN", str(_P(REPO).parent / "wallex-bot"))   # live bot folder (ticks.csv, team only)
import io
import math
import numpy as np
import pandas as pd

MAKER = 0.0025
TAKER = 0.003
TICKS = rf"{MAIN}\runs\live_main\ticks.csv"
STEP = {"PEPETMN": 0.0001}
WIDE = {"POLTMN", "SANDTMN"}
T_END = 1792666725.0  # 2026-10-22 10:58:45 (laptop clock): fixed data cut for reproducibility


def load_ticks():
    raw = open(TICKS, "rb").read().decode("utf-8", errors="replace")
    lines = raw.split("\n")
    if not raw.endswith("\n"):
        lines = lines[:-1]  # drop partial last line (file is live)
    df = pd.read_csv(io.StringIO("\n".join(lines)))
    df = df[df.t <= T_END]  # freeze: the file is live and keeps growing
    df = df.dropna(subset=["bid", "ask", "fair", "baseline", "buy_dev"])
    df = df[(df.fair > 0) & (df.bid > 0) & (df.ask > df.bid)].copy()
    df["fa"] = df.fair * (1 + df.baseline)
    out = []
    for s, g in df.groupby("symbol"):
        g = g.sort_values("t").copy()
        lag = pd.merge_asof(
            pd.DataFrame({"tl": g.t.values - 60.0}),
            pd.DataFrame({"tl": g.t.values, "fl": g.fair.values, "tt": g.t.values}),
            on="tl", direction="backward")
        r = g.fair.values / lag.fl.values - 1
        stale = (g.t.values - 60.0 - lag.tt.values) > 15  # lag tick too old (data gap)
        r[stale | np.isnan(lag.fl.values)] = np.nan
        g["ret60"] = r
        g["step"] = STEP.get(s, 1.0)
        out.append(g)
    df = pd.concat(out).sort_values(["t", "symbol"]).reset_index(drop=True)
    add_trade_tape(df)
    return df


CANDLES = rf"{REPO}\research\candles_30d\{{}}_1.csv"
CLOCK_OFFSET = 40.0  # laptop clock is ~40 s behind the exchange
CANDLE_END = 1792652400.0  # first minute NOT covered by the 30-day candles (07:00 UTC 10-22)


def add_trade_tape(df):
    """For each tick, the lowest/highest Wallex TRADE price in the exchange minute(s) that the
    2.5 s interval ending at this tick falls in (volume>0 minutes only). inf/-inf when no trade."""
    df["clow"] = np.inf
    df["chigh"] = -np.inf
    for s in df.symbol.unique():
        c = pd.read_csv(CANDLES.format(s))
        c = c[c.volume > 0]
        lo = dict(zip(c.ts.astype(np.int64), c.low))
        hi = dict(zip(c.ts.astype(np.int64), c.high))
        idx = df.index[df.symbol == s]
        te = df.loc[idx, "t"].values + CLOCK_OFFSET
        m1 = (np.floor((te - 2.5) / 60) * 60).astype(np.int64)
        m2 = (np.floor(te / 60) * 60).astype(np.int64)
        l = np.array([min(lo.get(a, np.inf), lo.get(b, np.inf)) for a, b in zip(m1, m2)])
        h = np.array([max(hi.get(a, -np.inf), hi.get(b, -np.inf)) for a, b in zip(m1, m2)])
        df.loc[idx, "clow"] = l
        df.loc[idx, "chigh"] = h


def fl(x, step):
    return round(math.floor(x / step + 1e-9) * step, 6)


def ce(x, step):
    return round(math.ceil(x / step - 1e-9) * step, 6)


class Mkt:
    __slots__ = ("k", "order", "pos", "exit_pending")

    def __init__(self):
        self.k = 0
        self.order = None
        self.pos = None
        self.exit_pending = False


def _ref_buy(P, bid):
    if P > bid:
        return bid
    if P == bid:
        return P
    return None


def _ref_sell(Q, ask):
    if Q < ask:
        return ask
    if Q == ask:
        return Q
    return None


def sim(df, variant, d1=0.003, m=0.002, s=0.003, T2=30, guard=False, fill="primary",
        T1=60.0, cap=None, prio=None, reprice=0.001, guard_th=-0.003, markets=None):
    """variant 'a' (passive dip-buy) or 'b' (fair-value market making). Returns (trades, stats)."""
    if markets is not None:
        df = df[df.symbol.isin(markets)]
    syms = sorted(df.symbol.unique())
    if prio is not None:
        syms = [x for x in prio if x in syms] + [x for x in syms if x not in prio]
    midx = {x: i for i, x in enumerate(syms)}
    T = df.t.values
    M = df.symbol.map(midx).values
    BID = df.bid.values
    ASK = df.ask.values
    FA = df.fa.values
    BD = df.buy_dev.values
    R60 = df.ret60.values
    STP = df.step.values
    CL = df.clow.values
    CH = df.chigh.values
    st = [Mkt() for _ in syms]
    trades = []
    n_place = 0
    n_cancel = 0
    n_buy = 0
    n_guard_block = 0
    strict = fill == "strict"
    tape = fill == "trade"
    T2s = T2 * 60.0
    open_slots = 0
    # for cap with priority: process rows in time order; within a time stamp, rows sorted by symbol.
    # reorder within same t by priority
    if prio is not None:
        order = np.lexsort((M, T))
        T, M, BID, ASK, FA, BD, R60, STP, CL, CH = (a[order] for a in (T, M, BID, ASK, FA, BD, R60, STP, CL, CH))
    n = len(T)
    last = [None] * len(syms)

    def close(mk, mi, t, px, fee_out, why):
        p = mk.pos
        net = px * (1 - fee_out) / (p["P"] * (1 + MAKER)) - 1
        trades.append({"sym": syms[mi], "t_in": p["t"], "P": p["P"], "t_out": t, "X": px,
                       "why": why, "net": net, "gross": px / p["P"] - 1, "hold_s": t - p["t"]})

    for i in range(n):
        mi = M[i]
        mk = st[mi]
        mk.k += 1
        k = mk.k
        t = T[i]
        bid = BID[i]
        ask = ASK[i]
        fa = FA[i]
        step = STP[i]
        last[mi] = (t, bid)
        # ---------- 1) fills / cancels on live orders
        o = mk.order
        if o is not None and k >= o["live"]:
            filled = False
            if o["side"] == "B":
                P = o["px"]
                if ask <= P:
                    filled = True
                elif not strict:
                    R = o["R"]
                    if R is None:
                        if bid <= P:
                            o["R"] = bid
                    elif bid < R and (not tape or CL[i] < P):
                        filled = True
                if filled:
                    mk.order = None
                    mk.pos = {"P": P, "t": t, "k": k}
                    mk.exit_pending = False
                    # place take-profit sell now (live next tick)
                    if variant == "a":
                        Q = max(P * (1 + 0.005 + m), fa)
                    else:
                        Q = max(fa * (1 + s), P * (1 + 0.0055))
                    Q = max(ce(Q, step), bid + step)
                    mk.order = {"side": "S", "px": Q, "live": k + 1, "R": _ref_sell(Q, ask),
                                "fa": fa, "cancel": None, "rp": False}
                    n_place += 1
                    continue
            else:
                Q = o["px"]
                if bid >= Q:
                    filled = True
                elif not strict:
                    R = o["R"]
                    if R is None:
                        if ask >= Q:
                            o["R"] = ask
                    elif ask > R and (not tape or CH[i] > Q):
                        filled = True
                if filled:
                    close(mk, mi, t, Q, MAKER, "maker")
                    mk.order = None
                    mk.pos = None
                    open_slots -= 1
                    mk.exit_pending = False
                    continue
            if o["cancel"] is not None and k >= o["cancel"]:
                mk.order = None
                if o["side"] == "B":
                    open_slots -= 1
                else:
                    if mk.exit_pending:
                        close(mk, mi, t, bid, TAKER, "timestop")
                        mk.pos = None
                        open_slots -= 1
                        mk.exit_pending = False
                        continue
                    if o["rp"]:  # re-price the sell
                        p = mk.pos
                        Q = max(fa * (1 + s), p["P"] * (1 + 0.0055))
                        Q = max(ce(Q, step), bid + step)
                        mk.order = {"side": "S", "px": Q, "live": k + 1, "R": _ref_sell(Q, ask),
                                    "fa": fa, "cancel": None, "rp": False}
                        n_place += 1
                        continue
        # ---------- 2) decisions
        o = mk.order
        gblock = guard and (R60[i] == R60[i]) and R60[i] < guard_th
        if mk.pos is None and o is None:
            if gblock:
                n_guard_block += 1
                continue
            if cap is not None and open_slots >= cap:
                continue
            if variant == "a":
                if BD[i] <= -d1:
                    P = bid + step
                    if not (P < ask and P <= fa * (1 - d1)):
                        P = bid
                    P = round(P, 6)
                else:
                    continue
            else:
                P = fl(min(fa * (1 - s), ask - step), step)
            mk.order = {"side": "B", "px": P, "live": k + 1, "R": _ref_buy(P, bid), "fa": fa,
                        "t0": t, "cancel": None}
            n_place += 1
            n_buy += 1
            open_slots += 1
        elif o is not None and o["side"] == "B" and o["cancel"] is None:
            if variant == "a":
                if t - o["t0"] >= T1 or gblock:
                    o["cancel"] = k + 1
                    n_cancel += 1
            else:
                if gblock or abs(fa / o["fa"] - 1) > reprice:
                    o["cancel"] = k + 1
                    n_cancel += 1
        elif mk.pos is not None and o is not None and o["side"] == "S" and o["cancel"] is None:
            if t - mk.pos["t"] >= T2s:
                o["cancel"] = k + 1
                mk.exit_pending = True
                n_cancel += 1
            elif variant == "b" and abs(fa / o["fa"] - 1) > reprice:
                o["cancel"] = k + 1
                o["rp"] = True
                n_cancel += 1
    # end of data: close open positions at last bid with taker fee
    for mi, mk in enumerate(st):
        if mk.pos is not None:
            t, bid = last[mi]
            close(mk, mi, t, bid, TAKER, "eod")
    hours = (df.t.max() - df.t.min()) / 3600.0
    stats = {"placements": n_place, "cancels": n_cancel, "hours": hours, "buy_orders": n_buy,
             "guard_block_ticks": n_guard_block}
    return trades, stats


def summarize(trades, stats, size=700_000):
    if not trades:
        return {"round_trips": 0, "toman": 0.0, "per_hour": 0.0}
    tr = pd.DataFrame(trades)
    done = tr[tr.why != "eod"]
    out = {
        "entries": int(len(tr)),
        "round_trips": int(len(done)),
        "eod_forced": int((tr.why == "eod").sum()),
        "mean_net_pct": round(float(tr.net.mean() * 100), 4),
        "median_net_pct": round(float(tr.net.median() * 100), 4),
        "win_rate": round(float((tr.net > 0).mean()), 3),
        "timestop_share": round(float((tr.why == "timestop").mean()), 3),
        "maker_exit_share": round(float((tr.why == "maker").mean()), 3),
        "worst_pct": round(float(tr.net.min() * 100), 3),
        "best_pct": round(float(tr.net.max() * 100), 3),
        "toman": round(float(tr.net.sum() * size)),
        "per_hour": round(len(tr) / stats["hours"], 2),
        "mean_hold_min": round(float(tr.hold_s.mean() / 60), 1),
        "mean_net_maker_exits_pct": round(float(tr[tr.why == "maker"].net.mean() * 100), 4) if (tr.why == "maker").any() else None,
        "mean_net_timestop_pct": round(float(tr[tr.why == "timestop"].net.mean() * 100), 4) if (tr.why == "timestop").any() else None,
        "placements_per_hour": round(stats["placements"] / stats["hours"], 1),
        "cancels_per_hour": round(stats["cancels"] / stats["hours"], 1),
    }
    pm = {}
    for sym, g in tr.groupby("sym"):
        pm[sym] = {"n": int(len(g)), "mean_net_pct": round(float(g.net.mean() * 100), 3),
                   "win": round(float((g.net > 0).mean()), 2),
                   "timestop": round(float((g.why == "timestop").mean()), 2),
                   "toman": round(float(g.net.sum() * size))}
    out["per_market"] = pm
    return out
