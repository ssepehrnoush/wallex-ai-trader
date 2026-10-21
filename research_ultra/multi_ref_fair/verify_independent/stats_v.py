"""Noise check on my own trade lists: per-day paired differences, SE of the changed-set net, bootstrap."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
T0 = 1791986700
tag = sys.argv[1] if len(sys.argv) > 1 else ""
rng = np.random.default_rng(7)
for e in ("bt", "live"):
    t0 = pd.read_csv(HERE / f"trades_A0_{e}{tag}.csv")
    for v in ("A1", "B1", "AB"):
        t1 = pd.read_csv(HERE / f"trades_{v}_{e}{tag}.csv")
        k0 = set(zip(t0.symbol, t0.ts)); k1 = set(zip(t1.symbol, t1.ts))
        gone = t0[[(s, x) not in k1 for s, x in zip(t0.symbol, t0.ts)]].ret.to_numpy()
        new = t1[[(s, x) not in k0 for s, x in zip(t1.symbol, t1.ts)]].ret.to_numpy()
        net = new.sum() - gone.sum()
        allr = np.concatenate([t0.ret.to_numpy(), t1.ret.to_numpy()])
        sd = allr.std(ddof=1)
        se = sd * np.sqrt(len(new) + len(gone))
        # bootstrap of the changed set (resample gone and new independently)
        bs = np.array([rng.choice(new, len(new)).sum() - rng.choice(gone, len(gone)).sum() for _ in range(5000)]) \
            if len(new) and len(gone) else np.array([net])
        d0 = t0.groupby((t0.ts - T0) // 86400).ret.sum()
        d1 = t1.groupby((t1.ts - T0) // 86400).ret.sum()
        dd = d1.reindex(range(7), fill_value=0) - d0.reindex(range(7), fill_value=0)
        print(f"{e:4s} A0->{v}: net={net:+.2f} SE~{se:.2f} (z={net / se:+.2f}) boot95=[{np.percentile(bs, 2.5):+.2f},{np.percentile(bs, 97.5):+.2f}] "
              f"P(net>0)={np.mean(bs > 0):.2f} | daily diffs {dd.round(2).tolist()} (+days {int((dd > 0).sum())}, -days {int((dd < 0).sum())})")
