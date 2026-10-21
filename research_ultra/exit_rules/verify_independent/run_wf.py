"""Independent walk-forward of the exit grid. Usage: python run_wf.py [tag] [spread_mult] [min_periods] [usdt_mode] [split_iso] [stop_mode]"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vsim import MKTS, build, simulate, summ, tick_spreads  # noqa: E402

OUT = Path(__file__).resolve().parent
tag = sys.argv[1] if len(sys.argv) > 1 else "base"
spm = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
minp = int(sys.argv[3]) if len(sys.argv) > 3 else 30
umode = sys.argv[4] if len(sys.argv) > 4 else "traded"
split_iso = sys.argv[5] if len(sys.argv) > 5 else "2026-10-18 14:05"
stop_mode = sys.argv[6] if len(sys.argv) > 6 else "close"
SPLIT = int(pd.Timestamp(split_iso, tz="UTC").timestamp())

sp, ticks = tick_spreads()
spreads = {s: sp[s] for s in MKTS}
data = build(spreads, spm, minp, umode)
t0 = min(int(d["t"][0]) for d in data.values())
t1 = max(int(d["t"][-1]) for d in data.values())

STOPS = [0.006, 0.009, 0.012, 0.015]
HOLDS = [10, 15, 25, 45]
XS = [0.0, 0.0005, 0.001, 0.002, 0.003]
CUR = (0.009, 25, 0.0005)
grid = [(a, b, c) for a in STOPS for b in HOLDS for c in XS]


def dist(c):  # closeness to current, for tie-breaks (ties never pick a change)
    return (abs(c[0] - CUR[0]) / 0.003 + abs(c[1] - CUR[1]) / 10 + abs(c[2] - CUR[2]) / 0.0005)


def key(c):
    return f"{c[0]*100:.1f}/{c[1]}/{c[2]*100:.2f}"


def run_fold(markets, is_range, oos_range):
    rows = []
    full = {}
    for c in grid:
        tis = simulate(data, *c, t_start=is_range[0], t_end=is_range[1], markets=markets, stop_mode=stop_mode)
        tfull = simulate(data, *c, markets=markets, stop_mode=stop_mode)   # continuous run (state carries over)
        full[c] = tfull
        too = tfull[(tfull.entry_t >= oos_range[0]) & (tfull.entry_t < oos_range[1])] if len(tfull) else tfull
        rows.append(dict(cfg=key(c), stop=c[0], hold=c[1], X=c[2], is_n=len(tis), is_sum=round(tis.net_pct.sum(), 3) if len(tis) else 0.0,
                         oos_n=len(too), oos_sum=round(too.net_pct.sum(), 3) if len(too) else 0.0,
                         oos_mean=round(too.net_pct.mean(), 3) if len(too) else None, d=dist(c)))
    g = pd.DataFrame(rows)
    best = g.sort_values(["is_sum", "d"], ascending=[False, True]).iloc[0]
    chosen = (best.stop, int(best.hold), best.X)
    g["oos_rank"] = g.oos_sum.rank(ascending=False, method="min")
    return g, chosen, full


def paired_boot(a: pd.DataFrame, b: pd.DataFrame, n=20000, seed=11):
    """diff of summed net% (a - b), resampling (market, entry minute) keys; missing trade counts 0."""
    ka = a.set_index(["symbol", "entry_t"]).net_pct
    kb = b.set_index(["symbol", "entry_t"]).net_pct
    keys = ka.index.union(kb.index)
    d = ka.reindex(keys).fillna(0).to_numpy() - kb.reindex(keys).fillna(0).to_numpy()
    rng = np.random.default_rng(seed)
    bs = d[rng.integers(0, len(d), (n, len(d)))].sum(1)
    return dict(diff=round(d.sum(), 2), ci90=[round(np.percentile(bs, 5), 2), round(np.percentile(bs, 95), 2)],
                p_gt0=round(float((bs > 0).mean()), 3), keys=int(len(d)), nonzero=int((np.abs(d) > 1e-9).sum()))


def cluster_boot(a, b, n=20000, seed=12, hours=6):
    """cluster bootstrap on (market, 6h block): robust to trades that shift entry minute."""
    def agg(x):
        if not len(x):
            return pd.Series(dtype=float)
        return x.groupby([x.symbol, x.entry_t // (hours * 3600)]).net_pct.sum()
    A, B = agg(a), agg(b)
    keys = A.index.union(B.index)
    d = A.reindex(keys).fillna(0).to_numpy() - B.reindex(keys).fillna(0).to_numpy()
    rng = np.random.default_rng(seed)
    bs = d[rng.integers(0, len(d), (n, len(d)))].sum(1)
    return dict(diff=round(d.sum(), 2), ci90=[round(np.percentile(bs, 5), 2), round(np.percentile(bs, 95), 2)],
                p_gt0=round(float((bs > 0).mean()), 3), clusters=int(len(d)))


def permarket(tr):
    out = {}
    for s in MKTS:
        x = tr[tr.symbol == s] if len(tr) else tr
        out[s] = summ(x)
    return out


res = dict(tag=tag, spread_mult=spm, min_periods=minp, usdt_mode=umode, split=split_iso, stop_mode=stop_mode,
           spreads_pct={k: round(v * 100, 4) for k, v in spreads.items()},
           data_from=str(pd.Timestamp(t0, unit="s", tz="UTC")), data_to=str(pd.Timestamp(t1, unit="s", tz="UTC")))
for label, mk in (("8m", MKTS), ("7m_noBNB", [m for m in MKTS if m != "BNBTMN"])):
    for fold, isr, oor in (("A", (t0, SPLIT), (SPLIT, t1 + 60)), ("B_reverse", (SPLIT, t1 + 60), (t0, SPLIT))):
        g, ch, full = run_fold(mk, isr, oor)
        cur = full[CUR]
        chf = full[ch]
        sel = lambda x: x[(x.entry_t >= oor[0]) & (x.entry_t < oor[1])] if len(x) else x  # noqa: E731
        oc, ocur = sel(chf), sel(cur)
        # also: OOS from a fresh start at the split (no carried state)
        fresh_ch = simulate(data, *ch, t_start=oor[0], t_end=None if oor[1] > t1 else oor[1], markets=mk, stop_mode=stop_mode)
        fresh_cur = simulate(data, *CUR, t_start=oor[0], t_end=None if oor[1] > t1 else oor[1], markets=mk, stop_mode=stop_mode)
        r = dict(chosen=key(ch), is_sum_chosen=float(g[g.cfg == key(ch)].is_sum.iloc[0]),
                 is_sum_current=float(g[g.cfg == key(CUR)].is_sum.iloc[0]),
                 oos_chosen=summ(oc), oos_current=summ(ocur),
                 paired=paired_boot(oc, ocur), cluster6h=cluster_boot(oc, ocur),
                 oos_rank_of_chosen=int(g[g.cfg == key(ch)].oos_rank.iloc[0]), n_cfg=len(g),
                 oos_best=g.sort_values("oos_sum", ascending=False).head(5)[["cfg", "oos_n", "oos_sum"]].to_dict("records"),
                 is_top5=g.sort_values(["is_sum", "d"], ascending=[False, True]).head(5)[["cfg", "is_n", "is_sum", "oos_sum"]].to_dict("records"),
                 fresh_oos_chosen=summ(fresh_ch), fresh_oos_current=summ(fresh_cur),
                 per_market_oos_current=permarket(ocur), per_market_oos_chosen=permarket(oc),
                 halts=int(cur.attrs.get("halts", 0)))
        if fold == "A":
            g.to_csv(OUT / f"grid_{tag}_{label}.csv", index=False)
            ocur.to_csv(OUT / f"trades_oos_current_{tag}_{label}.csv", index=False)
            oc.to_csv(OUT / f"trades_oos_chosen_{tag}_{label}.csv", index=False)
            r["full_current"] = summ(cur)
            r["full_by_reason_mean"] = cur.groupby("why").net_pct.mean().round(3).to_dict()
        res[f"{label}_{fold}"] = r
        print(label, fold, "chosen", key(ch), "OOS chosen", summ(oc), "OOS current", summ(ocur), "paired", r["paired"], "cluster", r["cluster6h"], flush=True)

(OUT / f"wf_{tag}.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
