import os as _os
from pathlib import Path as _P
HERE = str(_P(__file__).resolve().parent)          # this study's folder (outputs)
REPO = str(_P(__file__).resolve().parents[2])      # repo root (research/candles_30d, research/premium)
MAIN = _os.environ.get("WALLEX_MAIN", str(_P(REPO).parent / "wallex-bot"))   # live bot folder (ticks.csv, team only)
import pandas as pd, numpy as np, io
p=rf"{MAIN}\runs\live_main\ticks.csv"
raw=open(p,'rb').read().decode('utf-8',errors='replace')
lines=raw.split('\n')
if not raw.endswith('\n'): lines=lines[:-1]
df=pd.read_csv(io.StringIO('\n'.join(lines)))
print(df.shape, df.dtypes)
df=df.dropna(subset=['bid','ask'])
print(pd.to_datetime(df.t.min(),unit='s'), pd.to_datetime(df.t.max(),unit='s'))
for s,g in df.groupby('symbol'):
    g=g.sort_values('t')
    sp=(g.ask/g.bid-1)
    dt=g.t.diff().median()
    # price steps
    px=np.concatenate([g.bid.values,g.ask.values])
    u=np.unique(px)
    d=np.diff(u); d=d[d>0]
    bidch=(g.bid.diff()!=0).mean(); askch=(g.ask.diff()!=0).mean()
    print(s,len(g),'dt',dt,'spread med %.4f mean %.4f'%(sp.median(),sp.mean()),'minstep',d.min() if len(d) else None,'med px',g.bid.median(),'bidchg %.2f askchg %.2f'%(bidch,askch), 'fair0', (g.fair.isna()|(g.fair==0)).sum())
