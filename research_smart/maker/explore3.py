import os as _os
from pathlib import Path as _P
HERE = str(_P(__file__).resolve().parent)          # this study's folder (outputs)
REPO = str(_P(__file__).resolve().parents[2])      # repo root (research/candles_30d, research/premium)
MAIN = _os.environ.get("WALLEX_MAIN", str(_P(REPO).parent / "wallex-bot"))   # live bot folder (ticks.csv, team only)
import pandas as pd, numpy as np
from ticksim import load_ticks
df=load_ticks()
C=rf"{REPO}\research\candles_30d\{{}}_1.csv"
for s in sorted(df.symbol.unique()):
    c=pd.read_csv(C.format(s))
    c30=c
    ov=c[(c.ts>=df.t.min()+40)&(c.ts<=c.ts.max())]
    g=df[df.symbol==s]
    # bid qty in toman
    bq=(g.bid*g.bid_qty).median(); aq=(g.ask*g.ask_qty).median()
    # bid decreases per hour
    b=g.bid.values; dec=(np.diff(b)<0).sum()/((g.t.max()-g.t.min())/3600)
    print(s,'30d trade-min share %.2f'%(c30.volume>0).mean(),'overlap rows',len(ov),'trade-min share %.2f'%((ov.volume>0).mean() if len(ov) else np.nan),
          'med bid toman %.0f ask toman %.0f'%(bq,aq),'bid drops/h %.0f'%dec)
