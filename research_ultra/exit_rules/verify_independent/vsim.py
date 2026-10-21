"""Independent re-implementation (does NOT import the author's common.py / grid_wf.py).

Reads only recorded data from MAIN (read-only). No network.
Model:
  * Wallex 1m candles, traded minutes only (volume > 0). ARB from research/premium.
  * fair = Binance 1m close x USDTTMN close (USDTTMN: traded-minute closes, ffilled on the minute grid).
  * baseline = rolling 180-min median of close/fair-1 over traded minutes, shift(1) (known before minute).
  * ask/bid = close x (1 +/- half median live spread from ticks.csv).
  * entry: buy_dev <= -0.7%, Binance 1m return >= -0.4%, market flat, not cooling down, < 3 open.
  * exits (engine order): stop (bid <= e(1-stop)), target (sell_dev >= -X and bid >= e(1+2*taker+0.0005)),
    time (held >= hold min). Fill at bid. net = bid/ask * (1-taker)^2 - 1.
  * 120-min cooldown after a losing exit; max 3 positions; daily 1.5% / kill 3% tracked (reported if binding).
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pandas as pd

MAIN = Path("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot")
MKTS = ["BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN", "PEPETMN", "BNBTMN", "ARBTMN"]
TAKER = 0.003
MAKER = 0.0025
TICK_RUNS = ["shadow_v2", "shadow_v3", "shadow_v4", "shadow_v5", "shadow_v6", "live_main"]


def read_csv_snapshot(p: Path, **kw) -> pd.DataFrame:
    raw = p.read_bytes()
    raw = raw[: raw.rfind(b"\n") + 1]
    return pd.read_csv(io.BytesIO(raw), **kw)


def tick_spreads() -> dict:
    fr = []
    for r in TICK_RUNS:
        d = read_csv_snapshot(MAIN / "runs" / r / "ticks.csv")
        d = d[["t", "symbol", "bid", "ask", "buy_dev", "sell_dev"]].copy()
        for c in ("t", "bid", "ask", "buy_dev", "sell_dev"):
            d[c] = pd.to_numeric(d[c], errors="coerce")
        d["run"] = r
        fr.append(d.dropna(subset=["bid", "ask"]))
    t = pd.concat(fr, ignore_index=True)
    t = t[(t.bid > 0) & (t.ask > 0)]
    sp = (t.ask / t.bid - 1).groupby(t.symbol).median().to_dict()
    return sp, t


def load_wallex(sym: str) -> pd.DataFrame:
    p = MAIN / "research" / "premium" / "wallex_ARBTMN_1m.csv" if sym == "ARBTMN" else MAIN / "research" / "candles" / f"{sym}_1.csv"
    d = pd.read_csv(p)
    d["ts"] = d["ts"].astype("int64")
    return d.drop_duplicates("ts").sort_values("ts").set_index("ts")


def load_binance(base: str) -> pd.Series:
    d = pd.read_csv(MAIN / "research" / "premium" / f"binance_{base}USDT_1m.csv", index_col=0)
    idx = pd.to_datetime(d.index, utc=True)
    secs = np.asarray((idx - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta("1s"), dtype="int64")
    s = pd.Series(d["close"].to_numpy(float), index=secs)
    return s[~s.index.duplicated()].sort_index()


def build(spreads: dict, spread_mult: float = 1.0, min_periods: int = 30, usdt_mode: str = "traded",
          markets=MKTS) -> dict:
    u = load_wallex("USDTTMN")
    if usdt_mode == "traded":
        u = u[u.volume > 0]
    uc = u["close"]
    grid = np.arange(uc.index.min(), uc.index.max() + 60, 60)
    uc = uc.reindex(grid).ffill()
    out = {}
    for s in markets:
        w = load_wallex(s)
        w = w[w.volume > 0]
        g = load_binance(s[:-3])
        gfull = g.reindex(np.arange(g.index.min(), g.index.max() + 60, 60))   # gaps stay NaN
        gret = gfull / gfull.shift(1) - 1
        fair = (g * uc.reindex(g.index)).dropna()
        df = pd.DataFrame({"c": w["close"], "lo": w["low"]}).join(fair.rename("fair"), how="inner")
        df = df.join(gret.rename("gret"), how="left").dropna(subset=["fair"])
        df = df.sort_index()
        hs = spreads[s] * spread_mult / 2
        prem = df.c / df.fair - 1
        ti = pd.to_datetime(df.index, unit="s", utc=True)
        base = pd.Series(prem.to_numpy(), index=ti).rolling("180min", min_periods=min_periods).median().shift(1).to_numpy()
        ask = df.c.to_numpy() * (1 + hs)
        bid = df.c.to_numpy() * (1 - hs)
        out[s] = dict(t=df.index.to_numpy(np.int64), ask=ask, bid=bid, lobid=df.lo.to_numpy() * (1 - hs),
                      buy_dev=ask / df.fair.to_numpy() - 1 - base, sell_dev=bid / df.fair.to_numpy() - 1 - base,
                      gret=df.gret.to_numpy(), hs=hs)
    return out


def exit_scan(m: dict, i: int, stop: float, hold: float, X: float, t_end: int | None, stop_mode: str):
    t, bid, sd = m["t"], m["bid"], m["sell_dev"]
    e = m["ask"][i]
    be = e * (1 + 2 * TAKER + 0.0005)
    sl = e * (1 - stop)
    n = len(t)
    j = i + 1
    while j < n:
        if t_end is not None and t[j] >= t_end:
            k = j - 1 if j - 1 > i else j
            return k, "cut", bid[k]
        lowb = m["lobid"][j] if stop_mode == "low" else bid[j]
        if lowb <= sl:
            px = bid[j] if stop_mode == "close" else min(sl, bid[j])
            return j, "stop", px
        if np.isfinite(sd[j]) and sd[j] >= -X and bid[j] >= be:
            return j, "target", bid[j]
        if t[j] - t[i] >= hold * 60:
            return j, "time", bid[j]
        j += 1
    return n - 1, "end", bid[n - 1]


def simulate(data: dict, stop: float, hold: float, X: float, t_start=None, t_end=None, stop_mode="close",
             markets=None, entry=0.007, overrides=None, equity0=5_000_000.0, size_factor=0.4):
    """Event-driven portfolio sim. overrides: {sym: (stop, hold, X)}."""
    markets = markets or list(data)
    cands = []
    for s in markets:
        m = data[s]
        ok = np.isfinite(m["buy_dev"]) & (m["buy_dev"] <= -entry) & np.isfinite(m["gret"]) & (m["gret"] >= -0.004)
        # missing gret (Binance gap at t-1) -> engine has no 60s history -> mom None -> allowed
        ok |= np.isfinite(m["buy_dev"]) & (m["buy_dev"] <= -entry) & ~np.isfinite(m["gret"])
        idx = np.nonzero(ok)[0]
        for i in idx:
            tt = int(m["t"][i])
            if t_start is not None and tt < t_start:
                continue
            if t_end is not None and tt >= t_end:
                continue
            cands.append((tt, s, int(i)))
    cands.sort()
    open_pos = {}          # sym -> (exit_t, net, ...)
    cool = {}
    trades = []
    eq, peak = equity0, equity0
    day, day_start = None, equity0
    halted = ""
    halts = 0
    for tt, s, i in cands:
        # release positions that have exited by now (other markets: exit_t <= tt; same market: exit_t < tt)
        for k in list(open_pos):
            ex_t = open_pos[k]["exit_t"]
            if ex_t < tt or (k != s and ex_t <= tt):
                p = open_pos.pop(k)
                eq += p["pnl_tmn"]
                peak = max(peak, eq)
                if p["net"] < 0:
                    cool[k] = ex_t + 120 * 60
                d = pd.Timestamp(ex_t, unit="s", tz="UTC").tz_convert("Asia/Tehran").date()
                if d != day:
                    day, day_start = d, eq - p["pnl_tmn"]
                if 1 - eq / peak >= 0.03 or eq / day_start - 1 <= -0.015:
                    halted = "halt"
        d = pd.Timestamp(tt, unit="s", tz="UTC").tz_convert("Asia/Tehran").date()
        if d != day:
            day, day_start = d, eq
            if halted == "halt" and 1 - eq / peak < 0.03:
                halted = ""
        if halted:
            halts += 1
            continue
        if s in open_pos or len(open_pos) >= 3:
            continue
        if cool.get(s, -1) > tt:
            continue
        st, ho, xx = (overrides or {}).get(s, (stop, hold, X))
        m = data[s]
        j, why, px = exit_scan(m, i, st, ho, xx, t_end, stop_mode)
        net = px / m["ask"][i] * (1 - TAKER) ** 2 - 1
        notional = min(eq * 0.006 * size_factor / (st + TAKER + MAKER), eq * 0.35)
        rec = dict(symbol=s, entry_t=tt, exit_t=int(m["t"][j]), why=why, net_pct=net * 100,
                   minutes=(int(m["t"][j]) - tt) / 60, entry_dev_pct=m["buy_dev"][i] * 100,
                   net=net, pnl_tmn=notional * net, notional=notional)
        open_pos[s] = rec
        trades.append(rec)
    tr = pd.DataFrame(trades)
    if len(tr):
        tr = tr.drop(columns=["net"])
    tr.attrs["halts"] = halts
    return tr


def summ(tr: pd.DataFrame) -> dict:
    if tr is None or not len(tr):
        return dict(n=0, mean=None, sum=0.0, win=None, exits={})
    return dict(n=int(len(tr)), mean=round(float(tr.net_pct.mean()), 3), sum=round(float(tr.net_pct.sum()), 2),
                win=round(float((tr.net_pct > 0).mean() * 100), 1), exits=tr.why.value_counts().to_dict(),
                pnl_tmn=round(float(tr.pnl_tmn.sum())))
