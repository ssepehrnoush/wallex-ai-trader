"""Multi-hour trend / momentum with global confirmation -- shared library.

Read-only on the research data. All decisions are made at hourly bar close
(decision time T = bar label + 1h) using data <= T; execution happens at a
later Wallex 1m close (default: the close of the 1m bar that starts at T,
i.e. ~1 minute after the signal) at executable prices close*(1+h) / close*(1-h).
"""
import os as _os
from pathlib import Path as _P
HERE = str(_P(__file__).resolve().parent)          # this study's folder (outputs)
REPO = str(_P(__file__).resolve().parents[2])      # repo root (research/candles_30d, research/premium)
MAIN = _os.environ.get("WALLEX_MAIN", str(_P(REPO).parent / "wallex-bot"))   # live bot folder (ticks.csv, team only)
import json
import numpy as np
import pandas as pd

R = rf"{REPO}\research"
FEE = 0.003           # taker, per side
ACCOUNT = 5_000_000
NOTIONAL = 1_000_000
MAXPOS = 3

SPREADS = json.load(open(R + r"\live_spreads_shadow_v1.json"))["spreads"]
ALL_BASES = ["BTC", "ETH", "SOL", "XRP", "DOGE", "PEPE", "BNB", "ARB", "POL", "SAND"]
# skip markets with spread > 0.6% or no spread measurement (POL, SAND)
UNIVERSE = [b for b in ALL_BASES if SPREADS.get(b + "TMN", 1.0) <= 0.006]
HALF = {b: SPREADS[b + "TMN"] / 2 for b in UNIVERSE}

T0 = pd.Timestamp("2026-09-22 07:00", tz="UTC")
T1 = pd.Timestamp("2026-10-22 07:00", tz="UTC")
IS_START = pd.Timestamp("2026-09-24 07:00", tz="UTC")   # 48h indicator warm-up
SPLIT = pd.Timestamp("2026-10-12 07:00", tz="UTC")
OOS_END = T1


def ema(a, n):
    return pd.Series(a).ewm(span=n, adjust=False).mean().to_numpy()


# ----------------------------------------------------------------------------- data
def load_30d(delay_min=1, global_src="binance"):
    """Hourly feature panel from Wallex 1m + Binance 1m (30 days).

    global_src='binance' -> global price = Binance USDT close
    global_src='proxy'   -> global price = Wallex TMN close / Wallex USDTTMN close
    """
    mi = pd.date_range(T0, T1 - pd.Timedelta(minutes=1), freq="1min")
    usdt = pd.read_csv(R + r"\candles_30d\USDTTMN_1.csv")
    usdt.index = pd.to_datetime(usdt.ts, unit="s", utc=True)
    usdt_c = usdt.close.reindex(mi).ffill().bfill()
    hours = pd.date_range(T0, T1 - pd.Timedelta(hours=1), freq="1h")
    P = {"hours": hours, "usdt": usdt_c.resample("1h").last().reindex(hours).to_numpy(), "sym": {}}
    for b in UNIVERSE:
        w = pd.read_csv(R + rf"\candles_30d\{b}TMN_1.csv")
        w.index = pd.to_datetime(w.ts, unit="s", utc=True)
        w = w[~w.index.duplicated()]
        c = w.close.reindex(mi).ffill().bfill()
        v = (w.volume * w.close).reindex(mi).fillna(0.0)
        if global_src == "binance":
            g = pd.read_csv(R + rf"\premium\binance_{b}USDT_1m.csv", index_col=0)
            g.index = pd.to_datetime(g.index, utc=True)
            g = g.close.reindex(mi).ffill().bfill()
        else:
            g = c / usdt_c
        rs = lambda s, f: getattr(s.resample("1h"), f)().reindex(hours)
        cm = c.to_numpy()
        # execution minute for decision time T=label+1h: bar starting at T+delay-1
        idx = np.arange(len(hours)) * 60 + 60 + delay_min - 1
        idx = np.clip(idx, 0, len(mi) - 1)
        h = HALF[b]
        P["sym"][b] = dict(
            Wc=rs(c, "last").to_numpy(), Wh=rs(c, "max").to_numpy(), Wl=rs(c, "min").to_numpy(),
            Wv=rs(v, "sum").to_numpy(), Gc=rs(g, "last").to_numpy(),
            ask=cm[idx] * (1 + h), bid=cm[idx] * (1 - h),
        )
        P["sym"][b]["mark"] = P["sym"][b]["Wc"] * (1 - h)
    return P


def load_60d():
    """Hourly panel from Wallex 60m candles (~60 days). No Binance -> proxy global price.
    Execution at NEXT hourly bar open (+/- half spread)."""
    usdt = pd.read_csv(R + r"\candles\USDTTMN_60.csv")
    usdt.index = pd.to_datetime(usdt.ts, unit="s", utc=True)
    bases = [b for b in UNIVERSE if b != "ARB"]
    first = pd.read_csv(R + r"\candles\BTCTMN_60.csv")
    hours = pd.to_datetime(first.ts, unit="s", utc=True)
    hours = pd.date_range(hours.iloc[0], hours.iloc[-1], freq="1h")
    usdt_c = usdt.close.reindex(hours).ffill().bfill()
    P = {"hours": hours, "usdt": usdt_c.to_numpy(), "sym": {}}
    for b in bases:
        w = pd.read_csv(R + rf"\candles\{b}TMN_60.csv")
        w.index = pd.to_datetime(w.ts, unit="s", utc=True)
        w = w.reindex(hours)
        c = w.close.ffill().bfill()
        o = w.open.fillna(c.shift(1)).fillna(c)
        nxt_open = o.shift(-1).fillna(c).to_numpy()
        h = HALF[b]
        P["sym"][b] = dict(
            Wc=c.to_numpy(), Wh=w.high.fillna(c).to_numpy(), Wl=w.low.fillna(c).to_numpy(),
            Wv=(w.volume.fillna(0) * c).to_numpy(), Gc=(c / usdt_c).to_numpy(),
            ask=nxt_open * (1 + h), bid=nxt_open * (1 - h), mark=c.to_numpy() * (1 - h),
        )
    return P


# ----------------------------------------------------------------------------- features
def regime_btc4h(P):
    """BTC global 4h trend up: last COMPLETED 4h bar close > EMA(12) of 4h closes."""
    hours = P["hours"]
    g = pd.Series(P["sym"]["BTC"]["Gc"], index=hours)  # hourly close, label = bar start
    # 4h bars by close time: hourly bar labelled t closes at t+1h
    close_t = pd.Series(g.to_numpy(), index=hours + pd.Timedelta(hours=1))
    c4 = close_t[close_t.index.hour % 4 == 0]          # closes at 00,04,08.. UTC
    up4 = (c4 > c4.ewm(span=12, adjust=False).mean())
    # at decision time T=label+1h, use latest 4h close with close time <= T
    dec = hours + pd.Timedelta(hours=1)
    reg = up4.reindex(dec, method="ffill").fillna(False).to_numpy().astype(bool)
    return reg


def atr(d, n=14):
    c, hi, lo = d["Wc"], d["Wh"], d["Wl"]
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(hi - lo, np.maximum(np.abs(hi - pc), np.abs(lo - pc)))
    return pd.Series(tr).rolling(n, min_periods=1).mean().to_numpy()


def roll_max_prev(a, n):
    return pd.Series(a).shift(1).rolling(n, min_periods=n).max().to_numpy()


def roll_min_prev(a, n):
    return pd.Series(a).shift(1).rolling(n, min_periods=n).min().to_numpy()


def shift(a, n):
    return pd.Series(a).shift(n).to_numpy()


# ----------------------------------------------------------------------------- rules
def signals_A(P, f, s, k, use_reg):
    """(a) Global EMA cross: Binance 1h EMA(f) > EMA(s), Binance > EMA24 and Wallex > EMA24.
    Enter on the hour the combined state turns true; exit on EMA(f)<EMA(s), ATR trail, 24h, regime off."""
    reg = regime_btc4h(P)
    out = {}
    for b, d in P["sym"].items():
        G, W = d["Gc"], d["Wc"]
        ef, es, g24, w24 = ema(G, f), ema(G, s), ema(G, 24), ema(W, 24)
        state = (ef > es) & (G > g24) & (W > w24)
        if use_reg:
            state = state & reg
        prev = np.r_[False, state[:-1]]
        enter = state & ~prev
        ex = ef < es
        if use_reg:
            ex = ex | ~reg
        out[b] = dict(enter=enter, exit=ex, atr=atr(d), trail=k, maxhold=24,
                      rank=G / shift(G, 24) - 1)
    return out


def signals_B(P, N, m, use_reg):
    """(b) Donchian breakout: Wallex 1h close > max of previous N closes, Wallex 1h TMN volume
    > m x median of previous 24h, global confirmation Binance > EMA24 and Binance N-hour return > 0.
    Exit: close < min of previous N/2 closes, ATR(14) trail 3x, 24h max, regime off."""
    reg = regime_btc4h(P)
    out = {}
    for b, d in P["sym"].items():
        G, W, V = d["Gc"], d["Wc"], d["Wv"]
        brk = W > roll_max_prev(W, N)
        vmed = pd.Series(V).shift(1).rolling(24, min_periods=24).median().to_numpy()
        vol_ok = V > m * vmed
        conf = (G > ema(G, 24)) & (G > shift(G, N))
        enter = brk & vol_ok & conf
        ex = W < roll_min_prev(W, N // 2)
        if use_reg:
            enter = enter & reg
            ex = ex | ~reg
        out[b] = dict(enter=np.nan_to_num(enter).astype(bool), exit=np.nan_to_num(ex).astype(bool),
                      atr=atr(d), trail=3.0, maxhold=24, rank=G / shift(G, 24) - 1)
    return out


# ----------------------------------------------------------------------------- simulators
def _mark_equity(cash, pos, P, h):
    v = cash
    for b, p in pos.items():
        v += p["qty"] * P["sym"][b]["mark"][h] * (1 - FEE)
    return v


def _close(pos, b, P, h, reason, trades, hours):
    p = pos.pop(b)
    d = P["sym"][b]
    bid = d["bid"][h]
    proceeds = p["qty"] * bid * (1 - FEE)
    net = proceeds / NOTIONAL - 1
    trades.append(dict(sym=b, entry=str(hours[p["h"]] + pd.Timedelta(hours=1)),
                       exit=str(hours[h] + pd.Timedelta(hours=1)), hold_h=int(h - p["h"]),
                       entry_ask=float(p["ask"]), exit_bid=float(bid), net=float(net),
                       pnl=float(proceeds - NOTIONAL), reason=reason,
                       usdt_chg=float(P["usdt"][h] / P["usdt"][p["h"]] - 1),
                       g_chg=float(d["Gc"][h] / d["Gc"][p["h"]] - 1)))
    return proceeds


def _open(pos, b, P, h):
    ask = P["sym"][b]["ask"][h]
    pos[b] = dict(h=h, ask=ask, qty=NOTIONAL * (1 - FEE) / ask, peak=P["sym"][b]["Wc"][h])
    return NOTIONAL


def sim_event(P, sig, h0, h1, no_entry_last=3):
    """Event-driven sim on decision hours h0..h1-1, flat at start, force-close at h1-1."""
    hours = P["hours"]
    cash, pos, trades, eq = float(ACCOUNT), {}, [], []
    for h in range(h0, h1):
        last = h == h1 - 1
        for b in list(pos):
            s, d, p = sig[b], P["sym"][b], pos[b]
            p["peak"] = max(p["peak"], d["Wc"][h])
            reason = None
            if last:
                reason = "end"
            elif s["exit"][h]:
                reason = "signal"
            elif d["Wc"][h] < p["peak"] - s["trail"] * s["atr"][h]:
                reason = "trail"
            elif h - p["h"] >= s["maxhold"]:
                reason = "maxhold"
            if reason:
                cash += _close(pos, b, P, h, reason, trades, hours)
        if not last and h < h1 - no_entry_last and len(pos) < MAXPOS:
            cands = [b for b in sig if sig[b]["enter"][h] and b not in pos]
            cands.sort(key=lambda b: -np.nan_to_num(sig[b]["rank"][h], nan=-9))
            for b in cands[: MAXPOS - len(pos)]:
                cash -= _open(pos, b, P, h)
        eq.append(_mark_equity(cash, pos, P, h))
    return trades, np.array(eq)


def sim_xs(P, L, K, use_reg, h0, h1, rebal=4, no_entry_last=3):
    """(c) Cross-sectional momentum: at every 4h boundary (00,04,.. UTC) hold the top-K coins by
    trailing L-hour GLOBAL return, only if global L-ret > 0 and Wallex L-ret > 0 (and BTC regime)."""
    hours = P["hours"]
    reg = regime_btc4h(P) if use_reg else np.ones(len(hours), bool)
    syms = list(P["sym"])
    G = np.vstack([P["sym"][b]["Gc"] for b in syms])
    W = np.vstack([P["sym"][b]["Wc"] for b in syms])
    gret = G / np.hstack([np.full((len(syms), L), np.nan), G[:, :-L]]) - 1
    wret = W / np.hstack([np.full((len(syms), L), np.nan), W[:, :-L]]) - 1
    cash, pos, trades, eq = float(ACCOUNT), {}, [], []
    for h in range(h0, h1):
        last = h == h1 - 1
        dec_hour = (hours[h] + pd.Timedelta(hours=1)).hour
        if last:
            for b in list(pos):
                cash += _close(pos, b, P, h, "end", trades, hours)
        elif dec_hour % rebal == 0:
            tgt = []
            if reg[h]:
                ok = [(gret[i, h], syms[i]) for i in range(len(syms))
                      if np.isfinite(gret[i, h]) and gret[i, h] > 0 and wret[i, h] > 0]
                ok.sort(reverse=True)
                tgt = [b for _, b in ok[:K]]
            for b in list(pos):
                if b not in tgt:
                    cash += _close(pos, b, P, h, "rebal", trades, hours)
            if h < h1 - no_entry_last:
                for b in tgt:
                    if b not in pos and len(pos) < MAXPOS:
                        cash -= _open(pos, b, P, h)
        eq.append(_mark_equity(cash, pos, P, h))
    return trades, np.array(eq)


# ----------------------------------------------------------------------------- rule registry
GRID = []
for f, s in [(4, 12), (6, 24), (12, 48)]:
    for k in (2.0, 3.0):
        for r in (0, 1):
            GRID.append(("A", dict(f=f, s=s, k=k, reg=r)))
for N in (12, 24, 48):
    for m in (1.0, 2.0):
        for r in (0, 1):
            GRID.append(("B", dict(N=N, m=m, reg=r)))
for L in (12, 24):
    for K in (1, 2):
        for r in (0, 1):
            GRID.append(("C", dict(L=L, K=K, reg=r)))


def rule_name(fam, p):
    return fam + ":" + ",".join(f"{k}={v}" for k, v in p.items())


def make_runner(P, fam, p):
    if fam == "A":
        sig = signals_A(P, p["f"], p["s"], p["k"], p["reg"])
        return lambda h0, h1, nel=3: sim_event(P, sig, h0, h1, nel)
    if fam == "B":
        sig = signals_B(P, p["N"], p["m"], p["reg"])
        return lambda h0, h1, nel=3: sim_event(P, sig, h0, h1, nel)
    return lambda h0, h1, nel=3: sim_xs(P, p["L"], p["K"], p["reg"], h0, h1, no_entry_last=nel)


# ----------------------------------------------------------------------------- stats
def max_dd(eq):
    if len(eq) == 0:
        return 0.0
    e = np.r_[ACCOUNT, eq]
    pk = np.maximum.accumulate(e)
    return float(((pk - e) / pk).max())


def stats(trades, eq, days):
    n = len(trades)
    nets = np.array([t["net"] for t in trades]) if n else np.array([])
    return dict(
        trades=n, trades_per_day=round(n / days, 2),
        mean_net_pct=round(100 * nets.mean(), 3) if n else None,
        median_net_pct=round(100 * float(np.median(nets)), 3) if n else None,
        win_rate=round(float((nets > 0).mean()), 3) if n else None,
        sum_net_pct=round(100 * nets.sum(), 2),
        pnl_toman=round(float(sum(t["pnl"] for t in trades))),
        account_return_pct=round(100 * (eq[-1] / ACCOUNT - 1), 3) if len(eq) else 0.0,
        max_dd_pct=round(100 * max_dd(eq), 3),
        avg_hold_h=round(float(np.mean([t["hold_h"] for t in trades])), 1) if n else None,
        mean_usdt_chg_pct=round(100 * float(np.mean([t["usdt_chg"] for t in trades])), 3) if n else None,
        mean_global_chg_pct=round(100 * float(np.mean([t["g_chg"] for t in trades])), 3) if n else None,
    )


def hidx(P, ts):
    """index of the hourly bar whose DECISION time (label+1h) is ts"""
    return int(np.searchsorted(P["hours"], ts - pd.Timedelta(hours=1)))
