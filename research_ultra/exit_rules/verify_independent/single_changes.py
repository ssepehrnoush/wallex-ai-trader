"""Full-period single exit changes vs current, with cluster bootstrap and per-half split. Independent code."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vsim import MKTS, build, simulate, summ, tick_spreads  # noqa: E402

OUT = Path(__file__).resolve().parent
SPLIT = int(pd.Timestamp("2026-10-18 14:05", tz="UTC").timestamp())
sp, ticks = tick_spreads()
spreads = {s: sp[s] for s in MKTS}
res = {}
for minp in (30, 60):
    data = build(spreads, 1.0, minp)
    CUR = (0.009, 25, 0.0005)
    changes = {"hold45": (0.009, 45, 0.0005), "hold15": (0.009, 15, 0.0005), "hold10": (0.009, 10, 0.0005),
               "stop0.6": (0.006, 25, 0.0005), "stop1.2": (0.012, 25, 0.0005), "stop1.5": (0.015, 25, 0.0005),
               "slack0": (0.009, 25, 0.0), "X0.1": (0.009, 25, 0.001), "X0.2": (0.009, 25, 0.002), "X0.3": (0.009, 25, 0.003),
               "pick0.9/45/0": (0.009, 45, 0.0)}
    for label, mk in (("8m", MKTS), ("7m", [m for m in MKTS if m != "BNBTMN"])):
        cur = simulate(data, *CUR, markets=mk)
        out = {"current": summ(cur), "current_by_reason_mean": cur.groupby("why").net_pct.mean().round(3).to_dict(),
               "current_stop_count": int((cur.why == "stop").sum())}
        # live override: ARB 0.8/15
        variants = dict(changes)
        for name, c in list(variants.items()) + [("live_ARB_0.8/15", None)]:
            if c is None:
                tr = simulate(data, *CUR, markets=mk, overrides={"ARBTMN": (0.008, 15, 0.0005)})
            else:
                tr = simulate(data, *c, markets=mk)

            def cl(x):
                return x.groupby([x.symbol, x.entry_t // (6 * 3600)]).net_pct.sum() if len(x) else pd.Series(dtype=float)
            A, B = cl(tr), cl(cur)
            keys = A.index.union(B.index)
            d = A.reindex(keys).fillna(0).to_numpy() - B.reindex(keys).fillna(0).to_numpy()
            rng = np.random.default_rng(5)
            bs = d[rng.integers(0, len(d), (20000, len(d)))].sum(1)
            # day-block bootstrap (calendar UTC day of entry)
            def dl(x):
                return x.groupby(x.entry_t // 86400).net_pct.sum() if len(x) else pd.Series(dtype=float)
            Ad, Bd = dl(tr), dl(cur)
            kd = Ad.index.union(Bd.index)
            dd = Ad.reindex(kd).fillna(0).to_numpy() - Bd.reindex(kd).fillna(0).to_numpy()
            bsd = dd[rng.integers(0, len(dd), (20000, len(dd)))].sum(1)
            h1 = tr[tr.entry_t < SPLIT].net_pct.sum() - cur[cur.entry_t < SPLIT].net_pct.sum()
            h2 = tr[tr.entry_t >= SPLIT].net_pct.sum() - cur[cur.entry_t >= SPLIT].net_pct.sum()
            out[name] = dict(s=summ(tr), diff=round(d.sum(), 2),
                             cluster6h_ci90=[round(np.percentile(bs, 5), 2), round(np.percentile(bs, 95), 2)],
                             dayblock_ci90=[round(np.percentile(bsd, 5), 2), round(np.percentile(bsd, 95), 2)],
                             days=len(dd), d1_4=round(h1, 2), d5_7=round(h2, 2),
                             same_trades=bool(len(tr) == len(cur) and np.allclose(tr.net_pct.to_numpy(), cur.net_pct.to_numpy())),
                             per_market_diff={s: round(tr[tr.symbol == s].net_pct.sum() - cur[cur.symbol == s].net_pct.sum(), 2) for s in mk})
            print(minp, label, name, out[name]["s"]["n"], out[name]["s"]["mean"], out[name]["s"]["sum"], "diff", out[name]["diff"],
                  "cl6h", out[name]["cluster6h_ci90"], "day", out[name]["dayblock_ci90"], "h1/h2", out[name]["d1_4"], out[name]["d5_7"],
                  "same" if out[name]["same_trades"] else "", flush=True)
        print(minp, label, "CURRENT", out["current"], out["current_by_reason_mean"], "stops", out["current_stop_count"])
        res[f"mp{minp}_{label}"] = out
(OUT / "single_changes.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
