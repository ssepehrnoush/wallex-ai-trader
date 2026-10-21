"""Named exit alternatives vs the current exits, on the full 7 days and on each half, with
  * a paired trade-level bootstrap (pairs on market + entry minute; a trade only one side takes counts 0 on the other)
  * a day-block bootstrap (resample whole UTC days of paired differences), which respects within-day clustering
  * per-market breakdown, exit-reason mix, worst trade, and the average result of each exit reason.
Also the live ARB override (stop 0.8%, hold 15) that the AI set on 2026-10-21.

  python research_ultra/exit_rules/alternatives.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUT, SYMS, load_markets, per_market, signals, simulate, stats, suffix  # noqa: E402

SPLIT = int(pd.Timestamp("2026-10-18 14:05", tz="UTC").timestamp())
CUR = (0.009, 25, 0.0005)
ALTS = {
    "current_0.9_25_0.05": dict(c=CUR),
    "hold45_only": dict(c=(0.009, 45, 0.0005)),
    "wf_choice_0.9_45_0.00": dict(c=(0.009, 45, 0.0)),
    "hold15": dict(c=(0.009, 15, 0.0005)),
    "hold10": dict(c=(0.009, 10, 0.0005)),
    "stop0.6": dict(c=(0.006, 25, 0.0005)),
    "stop1.2": dict(c=(0.012, 25, 0.0005)),
    "partialX0.1": dict(c=(0.009, 25, 0.001)),
    "partialX0.2": dict(c=(0.009, 25, 0.002)),
    "partialX0.3": dict(c=(0.009, 25, 0.003)),
    "slack0": dict(c=(0.009, 25, 0.0)),
    "live_now_ARB_0.8_15": dict(c=CUR, per_sym={"ARBTMN": (0.008, 15, 0.0005)}),
}
rng = np.random.default_rng(11)


def paired(a, b):
    ka = a.set_index(["symbol", "t_entry"]).ret_pct
    kb = b.set_index(["symbol", "t_entry"]).ret_pct
    u = ka.index.union(kb.index)
    d = (ka.reindex(u).fillna(0) - kb.reindex(u).fillna(0))
    return d


def boot(d: pd.Series, n=10000):
    if len(d) == 0:
        return {}
    v = d.to_numpy()
    bs = rng.choice(v, size=(n, len(v)), replace=True).sum(1)
    day = pd.to_datetime(d.index.get_level_values(1), unit="s", utc=True).floor("1D")
    per_day = d.groupby(day.values).sum().to_numpy()
    bd = rng.choice(per_day, size=(n, len(per_day)), replace=True).sum(1)
    return {"diff_sum_pct": round(float(v.sum()), 3), "n_pairs": int(len(v)), "n_changed": int((np.abs(v) > 1e-9).sum()),
            "trade_boot_ci90": [round(float(np.quantile(bs, .05)), 3), round(float(np.quantile(bs, .95)), 3)],
            "trade_boot_p_gt0": round(float((bs > 0).mean()), 3),
            "day_boot_ci90": [round(float(np.quantile(bd, .05)), 3), round(float(np.quantile(bd, .95)), 3)],
            "day_boot_p_gt0": round(float((bd > 0).mean()), 3), "days": int(len(per_day)),
            "per_day_diff": {str(k)[:10]: round(float(x), 3) for k, x in d.groupby(day.values).sum().items()}}


def detail(tr):
    s = stats(tr)
    if len(tr):
        s["worst_pct"] = round(float(tr.ret_pct.min()), 3)
        s["best_pct"] = round(float(tr.ret_pct.max()), 3)
        s["mean_minutes"] = round(float(tr.minutes.mean()), 1)
        s["mean_by_exit"] = {k: round(float(v), 3) for k, v in tr.groupby("why").ret_pct.mean().items()}
    return s


def main(stop_mode="close", spread_mult=1.0):
    mk = load_markets(spread_mult=spread_mult)
    sig_all, sig_is, sig_oos = signals(mk), signals(mk, t1=SPLIT), signals(mk, t0=SPLIT)
    res = {}
    base = {}
    for name, a in ALTS.items():
        kw = dict(stop_mode=stop_mode, per_sym=a.get("per_sym"))
        full = simulate(mk, sig_all, *a["c"], **kw)
        tis = simulate(mk, sig_is, *a["c"], t_end=SPLIT - 60, **kw)
        too = simulate(mk, sig_oos, *a["c"], **kw)
        if name.startswith("current"):
            base = {"full": full, "is": tis, "oos": too}
            full.to_csv(OUT / f"alt_trades_current_{stop_mode}_sp{spread_mult}{suffix()}.csv", index=False)
        res[name] = {"cfg": a["c"], "per_sym": a.get("per_sym"),
                     "full": detail(full), "is": detail(tis), "oos": detail(too),
                     "full_per_market": per_market(full), "oos_per_market": per_market(too)}
        if not name.startswith("current"):
            res[name]["vs_current_full"] = boot(paired(full, base["full"]))
            res[name]["vs_current_is"] = boot(paired(tis, base["is"]))
            res[name]["vs_current_oos"] = boot(paired(too, base["oos"]))
            if name in ("wf_choice_0.9_45_0.00", "hold45_only"):
                full.to_csv(OUT / f"alt_trades_{name}_{stop_mode}_sp{spread_mult}{suffix()}.csv", index=False)
    return res


if __name__ == "__main__":
    out = {}
    for sm, sp in (("close", 1.0), ("low", 1.0), ("close", 1.5)):
        r = main(sm, sp)
        out[f"{sm}_sp{sp}"] = r
        print(f"===== stop on {sm}, spread x{sp}")
        for k, v in r.items():
            f, o = v["full"], v["oos"]
            line = (f"{k:24s} full n={f['n']:3d} mean={f['mean_pct']} sum={f['sum_pct']:7.2f} win={f['win_pct']} tmn={f['pnl_tmn']:7d} "
                    f"exits={f['exits']} | OOS n={o['n']} mean={o['mean_pct']} sum={o['sum_pct']:.2f}")
            if "vs_current_full" in v:
                b, bo = v["vs_current_full"], v["vs_current_oos"]
                line += (f"\n{'':24s} diff full {b.get('diff_sum_pct')} tradeCI {b.get('trade_boot_ci90')} dayCI {b.get('day_boot_ci90')}"
                         f" | diff IS {v['vs_current_is'].get('diff_sum_pct')} OOS {bo.get('diff_sum_pct')} OOS tradeCI {bo.get('trade_boot_ci90')}")
            print(line)
    (OUT / f"alternatives_results{suffix()}.json").write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")
