"""Historical backtest of the fair-value reversion rule on 1-minute data (--days before a fixed --end).

Wallex history has no order book, only 1m last-trade candles, so executable prices are modelled:
buy at close * (1 + half_spread), sell at close * (1 - half_spread), where half_spread is half that
market's median live spread (--spreads-json, else the medians of --ticks; 0.3% if unknown).
Fair value = Binance 1m close (x Wallex USDTTMN 1m close for toman markets). Baseline = rolling 180-minute
median premium, known before the minute. Only minutes in which Wallex actually traded are used.

Data (this script never calls the Wallex API; a missing candle file is an error):
  Wallex   research/candles/<SYM>_1.csv, else research/premium/wallex_<SYM>_1m.csv (here, then the live bot's
           copy, read only; USDT-quoted markets exist only there). The committed candles end
           2026-10-21 14:04 UTC (the default --end). research/candles/ARBTMN_1.csv is a copy of the live bot's
           research/premium/wallex_ARBTMN_1m.csv (fetched 2026-10-21 15:53 UTC; it starts 2026-10-14 15:51).
  Binance  research/premium/binance_<BASE>USDT_1m.csv here, else the live bot's copy (read only); when neither
           covers the window the public Binance klines are fetched and cached here.

Options beyond the original rule:
  --live-exit   the engine's rule: target needs sell_dev >= -slack AND bid >= entry x (1 + 2 x taker + 0.0005);
                net = (1 - taker)^2 x bid / ask - 1; the entry threshold is never below 2 x taker + 0.1%; a market
                whose modelled spread exceeds fairvalue.max_spread gets no entries.
  --max-drop X  skip an entry when the Binance 1-minute return at the signal minute is below -X (live: 0.004).
Every run also replays entry at the NEXT traded minute's close ("robustness" in summary.json,
trades_next_minute.csv) and splits out trades whose USDT/TMN rate was stale: Wallex USDTTMN hardly trades at
night, so there the fair value uses an old last price ("stale_usdt_rate", trades.csv column usdt_age_min).
Both checks are also written per market (markets.<SYM>.next_minute_mean_ret_pct, stale_usdt_trades,
stale_usdt_mean_ret_pct), so every per-market number the live AI reads has its caveat next to it.

Reproduce runs/backtests/fv_live_rule_0.007 (the file the live AI reads via fairvalue.backtest_reference) into a
fresh folder, then compare its trades.csv and trades_next_minute.csv with the committed ones (never pass
--out runs/backtests/fv_live_rule_0.007: that overwrites the reference the AI reads):
  python scripts/bt_fairvalue.py --live-exit --max-drop 0.004 --entry-tmn 0.007 --slack 0.0005 --stop 0.009 --hold 25 --spreads-json research/live_spreads_shadow_v1.json --symbols BTCTMN,ETHTMN,SOLTMN,XRPTMN,DOGETMN,PEPETMN,BNBTMN,ARBTMN --out runs/backtests/rerun_live_rule
Team only (needs the live bot's ticks.csv, which is not in git; read only): rebuild the committed spreads file
into a check file and compare (without --out it overwrites research/live_spreads_shadow_v1.json):
  python research/live_spreads.py --main ../wallex-bot --cutoff 2026-10-21T19:00:00Z --out research/live_spreads_check.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import numpy as np
import pandas as pd

from wallexbot.config import ROOT, load_config
from wallexbot.data import CANDLE_DIR, _frame
from wallexbot.fairvalue import base_of, quote_of

ap = argparse.ArgumentParser()
ap.add_argument("--ticks", default="runs/shadow_v1/ticks.csv", help="spreads from these ticks (if no --spreads-json)")
ap.add_argument("--spreads-json", default="", help="median spreads per symbol, fractions ({sym: x} or {'spreads': {...}})")
ap.add_argument("--entry-tmn", type=float, default=0.008)
ap.add_argument("--entry-usdt", type=float, default=0.005)
ap.add_argument("--slack", type=float, default=0.0005)
ap.add_argument("--stop", type=float, default=0.009)
ap.add_argument("--hold", type=int, default=25)
ap.add_argument("--days", type=float, default=7)
ap.add_argument("--end", default="2026-10-21T14:04:00Z", help="last minute of the window (ISO UTC) or 'now'")
ap.add_argument("--live-exit", action="store_true", help="the live engine's exit rule, fee floor and spread cap")
ap.add_argument("--max-drop", type=float, default=0.0, help="skip entries when Binance fell more than this in the minute")
ap.add_argument("--out", default="runs/backtests/fairvalue")
ap.add_argument("--symbols", default="", help="comma list; default = config fairvalue.symbols")
a = ap.parse_args()
at_root = lambda p: Path(p) if Path(p).is_absolute() else ROOT / p

cfg = load_config().raw
fees = {"TMN": cfg["fees"], "USDT": cfg["fees_usdt"]}
syms = a.symbols.split(",") if a.symbols else cfg["fairvalue"]["symbols"]
PREM = ROOT / "research" / "premium"
MAIN_PREM = ROOT.parent / "wallex-bot" / "research" / "premium"      # the live bot's cache, read only

if a.spreads_json:
    d = json.loads(at_root(a.spreads_json).read_text(encoding="utf-8"))
    spreads = {k: float(v) for k, v in d.get("spreads", d).items() if isinstance(v, (int, float))}
elif at_root(a.ticks).exists():
    t = pd.read_csv(at_root(a.ticks))
    spreads = ((t.ask / t.bid - 1).groupby(t.symbol).median()).to_dict()
else:
    spreads = {}

end = int(time.time()) - 120 if a.end == "now" else int(pd.Timestamp(a.end).timestamp())
start = int(end - a.days * 86400)
utc = lambda x: pd.Timestamp(x, unit="s", tz="UTC") if isinstance(x, (int, float)) else x
iso = lambda x: utc(x).strftime("%Y-%m-%dT%H:%M:%SZ")


def wallex_1m(sym: str) -> pd.DataFrame:
    for p in (CANDLE_DIR / f"{sym}_1.csv", PREM / f"wallex_{sym}_1m.csv", MAIN_PREM / f"wallex_{sym}_1m.csv"):
        if p.exists():
            raw = pd.read_csv(p)
            return _frame(raw["ts"], raw["open"], raw["high"], raw["low"], raw["close"], raw["volume"])
    raise SystemExit(f"no Wallex 1m candles for {sym}: expected research/candles/{sym}_1.csv or "
                     f"research/premium/wallex_{sym}_1m.csv here or in {MAIN_PREM} (this script never fetches from Wallex)")


def binance_1m(sym: str) -> pd.Series:
    for p in (PREM / f"binance_{sym}_1m.csv", MAIN_PREM / f"binance_{sym}_1m.csv"):
        if p.exists():
            s = pd.read_csv(p, index_col=0, parse_dates=True)["close"]
            if len(s) and s.index[0].timestamp() <= start + 120 and s.index[-1].timestamp() >= end - 600:
                return s
    rows, t = [], start * 1000
    with httpx.Client(timeout=20) as h:
        while t <= end * 1000:
            r = h.get("https://api.binance.com/api/v3/klines",
                      params={"symbol": sym, "interval": "1m", "startTime": t, "endTime": end * 1000, "limit": 1000}).json()
            if not isinstance(r, list) or not r:
                break
            rows += r
            t = r[-1][0] + 60_000
            time.sleep(0.15)
    if not rows:
        raise SystemExit(f"no Binance 1m klines for {sym}")
    s = pd.Series([float(x[4]) for x in rows], index=pd.to_datetime([x[0] for x in rows], unit="ms", utc=True), name="close")
    s.to_csv(PREM / f"binance_{sym}_1m.csv")          # cache only under this repo
    return s


def simulate(s: str, df: pd.DataFrame, hs: float, lag: int = 0) -> list[dict]:
    """lag = 0: enter at the signal minute's modelled ask; lag = 1: at the next traded minute's."""
    q, f = quote_of(s), fees[quote_of(s)]["taker"]
    thr = a.entry_tmn if q == "TMN" else a.entry_usdt
    if a.live_exit:
        thr = max(thr, 2 * f + 0.001)
        if 2 * hs > cfg["fairvalue"]["max_spread"]:
            return []
    prem = df.c / df.fair - 1
    base = prem.rolling("180min", min_periods=30).median().shift(1).to_numpy()   # baseline known before the minute
    ask, bid, fair, idx = df.c.to_numpy() * (1 + hs), df.c.to_numpy() * (1 - hs), df.fair.to_numpy(), df.index
    buy_dev, sell_dev, gret, uage = ask / fair - 1 - base, bid / fair - 1 - base, df.gret.to_numpy(), df.uage.to_numpy()
    i, n, trades = 0, len(df), []
    while i < n - 1 - lag:
        if not buy_dev[i] <= -thr or (a.max_drop and gret[i] < -a.max_drop):
            i += 1
            continue
        k = i + lag
        e = ask[k]
        tgt = e * (1 + 2 * f + 0.0005) if a.live_exit else e * (1 + 1e-12)
        j, why = k + 1, "end"
        while j < n:
            if bid[j] <= e * (1 - a.stop):
                why = "stop"
                break
            if sell_dev[j] >= -a.slack and bid[j] >= tgt:
                why = "target"
                break
            if (idx[j] - idx[k]).total_seconds() >= a.hold * 60:
                why = "time"
                break
            j += 1
        j = min(j, n - 1)
        r = (1 - f) ** 2 * bid[j] / e - 1 if a.live_exit else bid[j] * (1 - f) / (e * (1 + f)) - 1
        trades.append({"symbol": s, "t": str(idx[i]), "t_entry": str(idx[k]), "t_exit": str(idx[j]), "ret_pct": r * 100,
                       "why": why, "minutes": (idx[j] - idx[k]).total_seconds() / 60, "entry_dev_pct": buy_dev[i] * 100,
                       "usdt_age_min": uage[i]})
        i = j + 1
    return trades


def stats(tr: pd.DataFrame) -> dict:
    ex = {k: int((tr.why == k).sum()) if len(tr) else 0 for k in ("target", "time", "stop", "end")}
    if not ex["end"]:
        ex.pop("end")
    return {"trades": len(tr), "mean_ret_pct": round(tr.ret_pct.mean(), 3) if len(tr) else None,
            "win_pct": round((tr.ret_pct > 0).mean() * 100, 1) if len(tr) else None,
            "sum_ret_pct": round(tr.ret_pct.sum(), 2) if len(tr) else 0, "exits": ex}


u = wallex_1m("USDTTMN")
grid = pd.date_range(u.index[0], u.index[-1], freq="1min", tz="UTC")
usdt = u["close"].reindex(grid).ffill()
real = u.index[u.volume > 0]                                             # minutes in which USDTTMN really traded
usdt_age = (grid.to_series() - pd.Series(real, index=real).reindex(grid).ffill()).dt.total_seconds() / 60
all_trades, lag_trades, summary, used = [], [], {}, {}
for s in syms:
    w = wallex_1m(s)
    g = binance_1m(f"{base_of(s)}USDT")
    fair = g * usdt if quote_of(s) == "TMN" else g
    df = pd.DataFrame({"c": w["close"], "v": w["volume"]}).join(fair.rename("fair"), how="inner").dropna()
    df["gret"] = g.pct_change().reindex(df.index)                      # Binance 1-minute return at that minute
    df["uage"] = usdt_age.reindex(df.index) if quote_of(s) == "TMN" else 0.0   # minutes since USDTTMN last traded
    df = df[(df.v > 0) & (df.index >= utc(start)) & (df.index <= utc(end))]
    hs = spreads.get(s, 0.003) / 2
    used[s] = round(hs * 200, 3)
    tr, tl = simulate(s, df, hs), simulate(s, df, hs, lag=1)
    all_trades += tr
    lag_trades += tl
    S, SL = (pd.DataFrame(x, columns=["ret_pct", "why", "usdt_age_min"]) for x in (tr, tl))
    st = S[S.usdt_age_min > 5]
    # each market's caveats sit next to its own numbers (the AI reads them per market, not only the overall note)
    summary[s] = {**stats(S), "next_minute_trades": len(SL), "next_minute_mean_ret_pct": stats(SL)["mean_ret_pct"],
                  "stale_usdt_trades": len(st), "stale_usdt_mean_ret_pct": stats(st)["mean_ret_pct"],
                  "half_spread_pct": round(hs * 100, 3),
                  "minutes_traded": len(df), "first_minute": iso(df.index[0]) if len(df) else None}

T, L = (pd.DataFrame(x, columns=["ret_pct", "why", "usdt_age_min"]) for x in (all_trades, lag_trades))
tot, lag = stats(T), stats(L)
stale = {"trades": int((T.usdt_age_min > 5).sum()), "mean_ret_pct": stats(T[T.usdt_age_min > 5])["mean_ret_pct"],
         "rest_mean_ret_pct": stats(T[~(T.usdt_age_min > 5)])["mean_ret_pct"]}
thr = max(a.entry_tmn, 2 * fees["TMN"]["taker"] + 0.001) if a.live_exit else a.entry_tmn
pct = lambda x: "n/a" if x is None else f"{x:+.3f}%"
note = (f"Backtest of the fair-value rule on Wallex 1-minute last-trade candles, {iso(start)[:16]} to {iso(end)[:16]} UTC "
        f"({a.days:g} days), markets {', '.join(syms)}. Fair value = Binance 1m close x Wallex USDTTMN close; entry when the "
        f"modelled ask is at least {thr * 100:.2f}% (toman) below fair value after the market's 180-minute median baseline"
        + (f", skipped when Binance fell more than {a.max_drop * 100:.1f}% in that minute" if a.max_drop else "")
        + (f"; exits as the live engine: target when sell_dev >= -{a.slack * 100:.2f}% AND the bid clears both taker fees "
           f"+ 0.05%, stop -{a.stop * 100:.1f}%, time {a.hold} min; net = (1 - taker)^2 x bid / ask - 1"
           if a.live_exit else f"; exit when sell_dev >= -{a.slack * 100:.2f}% and bid > entry, stop -{a.stop * 100:.1f}%, "
                               f"time {a.hold} min, both taker fees deducted")
        + f". Wallex history has no order book: fills are modelled at the signal minute's close +/- half the measured median "
        f"spread ({a.spreads_json or a.ticks}). Overall {tot['trades']} trades, mean {pct(tot['mean_ret_pct'])} per trade, "
        f"win {tot['win_pct']}%, sum {tot['sum_ret_pct']:+.2f} percentage points. Caveats: most signals are single-minute "
        f"last-trade prints that are gone by the next traded minute; entering one traded minute later (same rule, next traded "
        f"minute's close) turns the mean to {pct(lag['mean_ret_pct'])} per trade over {lag['trades']} trades. The live engine "
        f"polls the real order book every {cfg['fairvalue']['poll_seconds']} s, so only a real ask that far below fair counts. "
        f"{stale['trades']} of the {tot['trades']} trades came when Wallex USDTTMN had not traded for over 5 minutes (mostly at "
        f"night, about 20:00 to 05:30 UTC in this data), so their fair value uses a stale USDT/TMN price; their mean is "
        f"{pct(stale['mean_ret_pct'])} against {pct(stale['rest_mean_ret_pct'])} for the rest. "
        f"Each market's row carries the same checks for that market: next_minute_mean_ret_pct (entry one traded minute "
        f"later) and stale_usdt_trades / stale_usdt_mean_ret_pct; read mean_ret_pct only together with "
        f"next_minute_mean_ret_pct. "
        f"Treat these numbers as an optimistic upper bound, not a forecast; markets with few trades say little.")
res = {"note": note,
       "params": {**{k: v for k, v in vars(a).items()}, "window_utc": [iso(start), iso(end)],
                  "fees_taker": {k: v["taker"] for k, v in fees.items()}},
       "spreads_pct": used, "spreads_from_live": used, "overall": tot,
       "robustness": {"next_minute_entry_mean_ret_pct": lag["mean_ret_pct"], "trades": lag["trades"]},
       "stale_usdt_rate": stale,
       "markets": summary}
out = at_root(a.out)
out.mkdir(parents=True, exist_ok=True)
pd.DataFrame(all_trades).to_csv(out / "trades.csv", index=False)
pd.DataFrame(lag_trades).to_csv(out / "trades_next_minute.csv", index=False)
(out / "summary.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
print(json.dumps({k: res[k] for k in ("overall", "robustness", "markets")}, indent=1))
