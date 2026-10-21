"""Post-hoc (data-snooped) candidate 0.9/45/0.10: cluster-bootstrap CI of full-period diff vs current. Independent code."""
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from vsim import MKTS, build, simulate, tick_spreads
sp, _ = tick_spreads(); spreads = {s: sp[s] for s in MKTS}
data = build(spreads, 1.0, 30)
for label, mk in (("8m", MKTS), ("7m", [m for m in MKTS if m != "BNBTMN"])):
    cur = simulate(data, 0.009, 25, 0.0005, markets=mk)
    for c in [(0.009, 45, 0.001), (0.009, 45, 0.0)]:
        tr = simulate(data, *c, markets=mk)
        cl = lambda x: x.groupby([x.symbol, x.entry_t // (6 * 3600)]).net_pct.sum()
        A, B = cl(tr), cl(cur); k = A.index.union(B.index)
        d = A.reindex(k).fillna(0).to_numpy() - B.reindex(k).fillna(0).to_numpy()
        bs = d[np.random.default_rng(3).integers(0, len(d), (20000, len(d)))].sum(1)
        print(label, c, f"diff {d.sum():+.2f} ci90 [{np.percentile(bs,5):+.2f},{np.percentile(bs,95):+.2f}] P>0 {(bs>0).mean():.2f} clusters {len(d)} nonzero {(np.abs(d)>1e-9).sum()}")
        print('   per market', {s: round(tr[tr.symbol==s].net_pct.sum()-cur[cur.symbol==s].net_pct.sum(),2) for s in mk})
