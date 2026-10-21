"""Independent check of the depth claim (does not import the author's code).

Reads ticks.csv of shadow_v2..v6 and live_main (READ-ONLY snapshot), TMN markets only.
fill(size) = min(1, ask*ask_qty / planned_notional(size)), as the engine buys min(planned/ask, ask_qty).
Planned notional = E*0.006*sf/(stop 0.009 + taker 0.003 + maker 0.0025), capped at 35% E.
"""
import io
import json
from pathlib import Path

import numpy as np
import pandas as pd

MAIN = Path("C:/Users/Sadeq/Desktop/agentic-hackathon/wallex-bot")
OUT = Path(__file__).resolve().parent
RUNS = ["shadow_v2", "shadow_v3", "shadow_v4", "shadow_v5", "shadow_v6", "live_main"]
TMN = ["BTCTMN", "ETHTMN", "SOLTMN", "XRPTMN", "DOGETMN", "PEPETMN", "BNBTMN", "ARBTMN"]
E = 5_000_000.0


def planned(sf, stop=0.009):
    return min(E * 0.006 * sf / (stop + 0.003 + 0.0025), 0.35 * E)


SIZES = {"0.4": planned(0.4), "0.7": planned(0.7), "1.0": planned(1.0)}


def load():
    fr = []
    for r in RUNS:
        raw = (MAIN / "runs" / r / "ticks.csv").read_bytes()
        txt = raw.decode("utf-8", "ignore")
        if not txt.endswith("\n"):
            txt = txt[: txt.rfind("\n") + 1]          # drop partial last line
        d = pd.read_csv(io.StringIO(txt))
        d["run"] = r
        fr.append(d)
    d = pd.concat(fr, ignore_index=True)
    d = d[d.symbol.isin(TMN)].copy()
    for c in ["bid", "ask", "bid_qty", "ask_qty", "buy_dev", "sell_dev"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["bid", "ask", "bid_qty", "ask_qty", "buy_dev"])
    d["ask_n"] = d.ask * d.ask_qty
    d["bid_n"] = d.bid * d.bid_qty
    d["spread"] = d.ask / d.bid - 1
    return d.sort_values(["symbol", "t"]).reset_index(drop=True)


def fills(n):
    return {k: float(np.mean(np.minimum(1.0, n / v))) for k, v in SIZES.items()}


def binds(n):
    return {k: float(np.mean(n < v)) for k, v in SIZES.items()}


def episodes(d, thr):
    """First tick of each run of consecutive (<= 10 s apart) ticks with buy_dev <= -thr and spread <= 0.6%."""
    out = []
    for s, g in d.groupby("symbol"):
        m = (g.buy_dev <= -thr) & (g.spread <= 0.006)
        prev_t = None
        prev_in = False
        for t, inside, row in zip(g.t.values, m.values, g.itertuples()):
            new_run = inside and (not prev_in or (prev_t is not None and t - prev_t > 10))
            if new_run:
                out.append(row)
            prev_in, prev_t = inside, t
    return pd.DataFrame(out)


def main():
    d = load()
    hours = d.groupby("symbol").t.agg(lambda x: len(x) * 2.5 / 3600).round(2).to_dict()
    res = {"rows": int(len(d)), "t_min_utc": str(pd.to_datetime(d.t.min(), unit="s")),
           "t_max_utc": str(pd.to_datetime(d.t.max(), unit="s")), "hours_per_symbol": hours,
           "planned_notional": {k: round(v) for k, v in SIZES.items()}, "buckets": {}, "episodes": {}, "per_market": {}}
    for thr in [None, 0.003, 0.004, 0.005, 0.006, 0.007]:
        sub = d if thr is None else d[d.buy_dev <= -thr]
        key = "all" if thr is None else f"buy_dev<=-{thr*100:.1f}%"
        res["buckets"][key] = {"ticks": int(len(sub)),
                               "fill": {k: round(v, 3) for k, v in fills(sub.ask_n.values).items()} if len(sub) else None,
                               "binds": {k: round(v, 3) for k, v in binds(sub.ask_n.values).items()} if len(sub) else None,
                               "by_symbol": sub.symbol.value_counts().to_dict()}
    for thr in [0.003, 0.004, 0.005]:
        ep = episodes(d, thr)
        if len(ep):
            res["episodes"][f"{thr*100:.1f}%"] = {
                "n": int(len(ep)), "fill": {k: round(v, 3) for k, v in fills(ep.ask_n.values).items()},
                "binds": {k: round(v, 3) for k, v in binds(ep.ask_n.values).items()},
                "by_symbol": ep.symbol.value_counts().to_dict(),
                "rows": ep[["symbol", "t", "buy_dev", "ask_n", "spread"]].assign(
                    t=lambda x: pd.to_datetime(x.t, unit="s").astype(str), ask_n=lambda x: x.ask_n.round(0),
                    buy_dev=lambda x: (x.buy_dev * 100).round(3), spread=lambda x: (x.spread * 100).round(3)).to_dict("records")}
        else:
            res["episodes"][f"{thr*100:.1f}%"] = {"n": 0}
    for s, g in d.groupby("symbol"):
        cheap = g[g.buy_dev <= -0.003]
        res["per_market"][s] = {
            "ticks": int(len(g)), "median_ask_notional": round(float(g.ask_n.median())),
            "p10_ask_notional": round(float(g.ask_n.quantile(0.1))),
            "share_ask_below_0.4": round(float((g.ask_n < SIZES["0.4"]).mean()), 3),
            "share_ask_below_1.0": round(float((g.ask_n < SIZES["1.0"]).mean()), 3),
            "median_spread_pct": round(float(g.spread.median() * 100), 3),
            "cheap_ticks_0.3": int(len(cheap)),
            "cheap_fill": {k: round(v, 3) for k, v in fills(cheap.ask_n.values).items()} if len(cheap) else None,
            "min_buy_dev_pct": round(float(g.buy_dev.min() * 100), 3),
            "minutes_le_0.3": round(float((g.buy_dev <= -0.003).sum() * 2.5 / 60), 2),
            "minutes_le_0.5": round(float((g.buy_dev <= -0.005).sum() * 2.5 / 60), 2),
            "minutes_le_0.7": round(float((g.buy_dev <= -0.007).sum() * 2.5 / 60), 2),
            # exit side: bid notional when sell_dev in the target zone
            "target_zone_ticks": int((g.sell_dev >= -0.0005).sum()),
            "target_zone_bid_below_1.0": round(float((g[g.sell_dev >= -0.0005].bid_n < SIZES["1.0"]).mean()), 3)
            if (g.sell_dev >= -0.0005).any() else None,
        }
    tot_h = d.groupby("symbol").size().sum() * 2.5 / 3600
    res["market_hours_total"] = round(float(tot_h), 2)
    res["cheap_minutes_total"] = {f"{x}": round(float((d.buy_dev <= -x / 100).sum() * 2.5 / 60), 2) for x in [0.3, 0.4, 0.5, 0.6, 0.7]}
    (OUT / "v_depth.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "episodes"}, indent=1, default=str))
    for k, v in res["episodes"].items():
        print("EPISODES", k, {kk: vv for kk, vv in v.items() if kk != "rows"})
        for r in v.get("rows", []):
            print("   ", r)


if __name__ == "__main__":
    main()
