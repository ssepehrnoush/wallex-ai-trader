"""Replay the exit alternatives on the recorded live order-book ticks (2.5 s), 8 live TMN markets.

Uses the engine's own recorded bid/ask/buy_dev/sell_dev (runs shadow_v2..v6 + live_main, about 3.8 h).
Entry: buy_dev <= -E, spread <= 0.6%, fair 60-s change >= -0.4% (stand-in for the Binance 60-s drop check).
The first 10 minutes of shadow_v2 are skipped (baseline not yet warm). Same portfolio rules as the candle test.
E = 0.7% is the real entry; 0.5 / 0.4 / 0.3% are DIAGNOSTIC only (more trades to compare exits on;
entries below the 0.7% fee floor are not tradable and lose money after fees).

  python research_ultra/exit_rules/replay_ticks.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import MAIN, OUT, SYMS, TICK_RUNS, Market, read_ticks, signals, simulate, stats  # noqa: E402

ALTS = {
    "current_0.9_25_0.05": ((0.009, 25, 0.0005), None),
    "hold45": ((0.009, 45, 0.0005), None),
    "wf_choice_0.9_45_0.00": ((0.009, 45, 0.0), None),
    "hold15": ((0.009, 15, 0.0005), None),
    "hold10": ((0.009, 10, 0.0005), None),
    "stop0.6": ((0.006, 25, 0.0005), None),
    "partialX0.1": ((0.009, 25, 0.001), None),
    "partialX0.2": ((0.009, 25, 0.002), None),
    "partialX0.3": ((0.009, 25, 0.003), None),
    "live_now_ARB_0.8_15": ((0.009, 25, 0.0005), {"ARBTMN": (0.008, 15, 0.0005)}),
}


def load():
    parts = []
    for r in TICK_RUNS:
        t = read_ticks(MAIN / "runs" / r / "ticks.csv")
        t = t[t.symbol.isin(SYMS)].copy()
        if r == "shadow_v2":
            t = t[t.t >= t.t.min() + 600]
        t["run"] = r
        parts.append(t)
    t = pd.concat(parts).sort_values("t")
    mk = {}
    for s, d in t.groupby("symbol"):
        d = d.drop_duplicates("t")
        ts = d.t.to_numpy()
        fair = d.fair.to_numpy()
        # fair change over the last 60 s (from the recorded fair; Binance x USDTTMN)
        k = np.searchsorted(ts, ts - 60)
        mom = fair / fair[np.clip(k, 0, len(fair) - 1)] - 1
        bd = d.buy_dev.to_numpy().astype(float).copy()
        bd[(d.ask / d.bid - 1).to_numpy() > 0.006] = np.nan
        bid = d.bid.to_numpy()
        mk[s] = Market(s, 0.0, ts.astype("int64"), d.ask.to_numpy(), bid, bid, bid, bd, d.sell_dev.to_numpy(), mom)
    span = {"from": str(pd.Timestamp(t.t.min(), unit="s", tz="UTC")), "to": str(pd.Timestamp(t.t.max(), unit="s", tz="UTC")),
            "rows": int(len(t)), "hours_with_data": round(t.groupby("run").t.agg(lambda x: x.max() - x.min()).sum() / 3600, 2)}
    return mk, span


if __name__ == "__main__":
    mk, span = load()
    print(span)
    out = {"span": span, "by_entry": {}}
    for E in (0.007, 0.005, 0.004, 0.003):
        sig = signals(mk, entry=E)
        res = {"signal_ticks": len(sig)}
        for name, (c, per) in ALTS.items():
            tr = simulate(mk, sig, *c, per_sym=per)
            s = stats(tr)
            s["trades"] = tr.assign(t=pd.to_datetime(tr.t_entry, unit="s", utc=True).astype(str))[
                ["symbol", "t", "why", "ret_pct", "minutes", "entry_dev_pct"]].round(3).to_dict("records") if len(tr) else []
            res[name] = s
        out["by_entry"][str(E * 100)] = res
        print(f"== entry {E*100:.1f}%  signal ticks {len(sig)}")
        for name in ALTS:
            s = res[name]
            print(f"   {name:24s} n={s['n']} mean={s['mean_pct']} sum={s['sum_pct']} win={s['win_pct']} exits={s['exits']}")
    (OUT / "replay_ticks_results.json").write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")
