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
df['ts']=pd.to_datetime(df.t,unit='s')
for s,g in df.groupby('symbol'):
    print(s,g.ts.min(),g.ts.max())
b=df[df.symbol=='BTCTMN'].sort_values('t')
d=b.t.diff()
print(d.describe()); print(b[d>10][['ts','t']].assign(gap=d[d>10]))
# fair staleness
fc=(b.fair.diff()==0)
# runs of unchanged fair
run=(fc!=fc.shift()).cumsum()
r=b.groupby(run).agg(n=('fair','size'),st=('ts','first'),stale=('fair',lambda x: (x.diff().fillna(1)==0).all()))
print(b[fc].shape)
x=b.assign(fc=fc)
x['grp']=(~x.fc).cumsum()
gr=x.groupby('grp').agg(n=('t','size'),st=('ts','first'),en=('ts','last'))
print(gr.sort_values('n').tail(5))
print(b[(b.ts>'2026-10-22 09:55')&(b.ts<'2026-10-22 10:30')].iloc[::20][['ts','bid','fair','baseline','buy_dev']])
print(df.isna().sum())
