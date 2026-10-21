"""Descriptive statistics on the downloaded candles (research/candles/*.csv).

    python research/analyze.py            # prints markdown tables, writes research/stats.csv

All returns are in TOMAN terms (that is how the challenge is scored). Days are Tehran days (UTC+03:30).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
CANDLES = HERE / "candles"
TEHRAN = "Asia/Tehran"


def load(sym: str, res: str) -> pd.DataFrame:
    df = pd.read_csv(CANDLES / f"{sym}_{res}.csv")
    df["dt"] = pd.to_datetime(df.ts, unit="s", utc=True).dt.tz_convert(TEHRAN)
    return df.set_index("dt")


def regular(df: pd.DataFrame, freq: str) -> pd.Series:
    """Close series on a full regular grid (missing bars forward-filled, so a gap shows as one big move).
    Resampled in UTC, which is how the exchange stamps its 1h bars (Tehran is UTC+03:30, so
    Tehran-aligned hourly buckets would straddle two native bars)."""
    return df.close.tz_convert("UTC").resample(freq).last().ffill().tz_convert(TEHRAN)


def max_drawdown(close: pd.Series) -> float:
    return float((close / close.cummax() - 1).min() * 100)


def main() -> None:
    syms = sorted({p.stem.rsplit("_", 1)[0] for p in CANDLES.glob("*_60.csv")})
    order = ["USDTTMN", "BTCTMN", "ETHTMN"]
    syms = [s for s in order if s in syms] + [s for s in syms if s not in order]

    r60 = {}
    rows = []
    for s in syms:
        h = load(s, "60")
        m15 = load(s, "15")
        m1 = load(s, "1")
        c60, c15 = regular(h, "60min"), regular(m15, "15min")
        ret60 = c60.pct_change().dropna() * 100
        ret15 = c15.pct_change().dropna() * 100
        ret4h = regular(h, "240min").pct_change().dropna() * 100
        r60[s] = np.log(c60).diff()

        daily = h.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        daily = daily.iloc[1:-1]  # drop partial first/last Tehran day
        rng = (daily.high - daily.low) / daily.open * 100

        # cross-check: native 60m vs 60m rebuilt from the (1m-derived) 15m series
        rebuilt = m15.close.tz_convert("UTC").resample("60min").last().dropna().tz_convert(TEHRAN)
        both = pd.concat([h.close, rebuilt], axis=1, keys=["native", "rebuilt"], sort=True).dropna()
        diff = (both.native / both.rebuilt - 1).abs() * 100
        mism = diff.max() if len(both) else np.nan
        match = (diff < 1e-9).mean() * 100 if len(both) else np.nan
        retd = h.close.resample("1D").last().dropna().pct_change().dropna() * 100

        rows.append({
            "symbol": s,
            "days": round((h.ts.iloc[-1] - h.ts.iloc[0]) / 86400, 1),
            "ret_60d_pct": (h.close.iloc[-1] / h.close.iloc[0] - 1) * 100,
            "maxdd_60d_pct": max_drawdown(c60),
            "med_abs_15m_pct": ret15.abs().median(),
            "med_abs_60m_pct": ret60.abs().median(),
            "std_60m_pct": ret60.std(),
            "avg_daily_range_pct": rng.mean(),
            "p_1h_gt_1pct": (ret60.abs() > 1).mean() * 100,
            "p_1h_gt_2pct": (ret60.abs() > 2).mean() * 100,
            "p_4h_gt_1pct": (ret4h.abs() > 1).mean() * 100,
            "p_4h_gt_2pct": (ret4h.abs() > 2).mean() * 100,
            "med_abs_4h_pct": ret4h.abs().median(),
            "med_abs_1d_pct": retd.abs().median(),
            # lag-1 autocorrelation: >0 trending, <0 mean-reverting (small samples, treat as a hint only)
            "ac1_60m": ret60.autocorr(1),
            "ac1_4h": ret4h.autocorr(1),
            "ac1_1d": retd.autocorr(1),
            "zero_vol_1m_pct": ((m1.volume == 0).mean()) * 100,
            # API candle volume is ~2x the /v1/markets 24h_volume (both sides counted), so halve it
            "avg_daily_tmn_vol_B": (h.volume * h.close).sum() / 2 / ((h.ts.iloc[-1] - h.ts.iloc[0]) / 86400) / 1e9,
            "missing_60m_bars": int(len(c60) - h.close.tz_convert("UTC").resample("60min").last().notna().sum()),
            "native_vs_rebuilt_60m_maxdiff_pct": mism,
            "native_vs_rebuilt_60m_exact_match_pct": match,
        })

    R = pd.DataFrame(r60).dropna(how="all")
    corr_btc = R.corr().get("BTCTMN")
    corr_usdt = R.corr().get("USDTTMN")
    Rd = np.log(pd.DataFrame({s: regular(load(s, "60"), "60min") for s in syms}).resample("1D").last()).diff().dropna()
    dcorr = Rd.corr()
    # coin moves in dollar terms (strip out the toman/dollar leg): log(X/TMN) - log(USDT/TMN)
    usd = R.sub(R["USDTTMN"], axis=0) if "USDTTMN" in R else None
    for r in rows:
        r["corr_60m_btc"] = corr_btc.get(r["symbol"]) if corr_btc is not None else np.nan
        r["corr_60m_usdt"] = corr_usdt.get(r["symbol"]) if corr_usdt is not None else np.nan
        r["corr_1d_btc"] = dcorr.loc[r["symbol"], "BTCTMN"] if "BTCTMN" in dcorr else np.nan
        r["corr_1d_usdt"] = dcorr.loc[r["symbol"], "USDTTMN"] if "USDTTMN" in dcorr else np.nan
        if usd is not None and r["symbol"] not in ("USDTTMN", "BTCTMN"):
            r["corr_60m_btc_usd_terms"] = usd[r["symbol"]].corr(usd["BTCTMN"])
        else:
            r["corr_60m_btc_usd_terms"] = np.nan
    st = pd.DataFrame(rows).set_index("symbol")
    st.to_csv(HERE / "stats.csv", float_format="%.4f")

    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    print(st.round(3).to_string())

    # USDTTMN drift detail
    u = load("USDTTMN", "60")
    uc = regular(u, "60min")
    days = (u.ts.iloc[-1] - u.ts.iloc[0]) / 86400
    tot = uc.iloc[-1] / uc.iloc[0] - 1
    per_day = (1 + tot) ** (1 / days) - 1
    ud = u.resample("1D").close.last().dropna()
    print(f"\nUSDTTMN: {uc.index[0]} {uc.iloc[0]:.0f} -> {uc.index[-1]} {uc.iloc[-1]:.0f}  total {tot*100:.2f}% over {days:.1f}d,"
          f" {per_day*100:.3f}%/day compounded, implies {((1+per_day)**2.25-1)*100:.2f}% per 54h")
    for w in (7, 14, 30):
        cut = uc[uc.index >= uc.index[-1] - pd.Timedelta(days=w)]
        print(f"   last {w:>2}d: {(cut.iloc[-1]/cut.iloc[0]-1)*100:+.2f}%")
    print("   daily closes:", " ".join(f"{d.strftime('%m-%d')}:{v/1000:.1f}k" for d, v in ud.items()))
    up_days = (ud.pct_change().dropna() > 0).mean() * 100
    print(f"   share of up days: {up_days:.0f}%   max drawdown of USDTTMN itself: {max_drawdown(uc):.2f}%")

    # BTC in dollar terms over the same window
    b = regular(load("BTCTMN", "60"), "60min")
    bu = (b / uc).dropna()
    print(f"BTC in USDT terms (BTCTMN/USDTTMN): {(bu.iloc[-1]/bu.iloc[0]-1)*100:+.2f}% over window; BTCTMN in toman: {(b.iloc[-1]/b.iloc[0]-1)*100:+.2f}%")

    # activity profile by Tehran hour and weekday (BTCTMN + USDTTMN 60m volume in toman)
    for s in ("BTCTMN", "USDTTMN"):
        h = load(s, "60")
        tv = h.volume * h.close / 2  # one-sided, comparable with /v1/markets 24h_tmnVolume
        by_hour = tv.groupby(h.index.hour).median() / 1e9
        by_dow = (tv.resample("1D").sum() / 1e9)
        by_dow = by_dow.groupby(by_dow.index.day_name()).median()
        vol_hour = (h.close.pct_change().abs() * 100).groupby(h.index.hour).median()
        print(f"\n{s} median hourly toman volume (B) by Tehran hour: " + " ".join(f"{k}:{v:.2f}" for k, v in by_hour.items()))
        print(f"{s} median |1h return| % by Tehran hour: " + " ".join(f"{k}:{v:.2f}" for k, v in vol_hour.items()))
        print(f"{s} median daily toman volume (B) by weekday: " + " ".join(f"{k[:3]}:{v:.1f}" for k, v in by_dow.items()))


if __name__ == "__main__":
    main()
