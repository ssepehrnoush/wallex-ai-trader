"""Adverse selection on tick fills: markouts after our passive BUY fills, vs. an unconditional baseline,
plus guard on/off paired comparison."""
import os as _os
from pathlib import Path as _P
HERE = str(_P(__file__).resolve().parent)          # this study's folder (outputs)
REPO = str(_P(__file__).resolve().parents[2])      # repo root (research/candles_30d, research/premium)
MAIN = _os.environ.get("WALLEX_MAIN", str(_P(REPO).parent / "wallex-bot"))   # live bot folder (ticks.csv, team only)
import json, numpy as np, pandas as pd
from ticksim import load_ticks, WIDE
OUT=rf"{HERE}"
df=load_ticks(); df['mid']=(df.bid+df.ask)/2
alltr=pd.read_pickle(OUT+r"\tick_trades.pkl")
ser={s:(g.t.values,g.mid.values,g.fa.values,g.bid.values) for s,g in df.groupby('symbol')}
H=[10,60,300,1800]
def mk(trs):
    rows=[]
    for x in trs:
        t,mid,fa,bid=ser[x['sym']]
        i0=np.searchsorted(t,x['t_in'])
        r={'sym':x['sym'],'P_vs_fa':x['P']/fa[i0]-1,'P_vs_mid':x['P']/mid[i0]-1}
        for h in H:
            j=np.searchsorted(t,x['t_in']+h)
            if j>=len(t): r[f'mo{h}']=np.nan; r[f'fa{h}']=np.nan; continue
            r[f'mo{h}']=mid[j]/x['P']-1            # mid markout vs our fill price
            r[f'fa{h}']=fa[j]/fa[i0]-1             # how fair moved after our fill
            r[f'mm{h}']=mid[j]/mid[i0]-1           # how the Wallex mid moved after our fill
        # how fair moved in the 60 s BEFORE the fill
        jb=np.searchsorted(t,x['t_in']-60); r['fa_pre60']=fa[i0]/fa[jb]-1
        rows.append(r)
    return pd.DataFrame(rows)
# unconditional baseline: fair drift and mid change over same horizons at all ticks (core)
base={}
for h in H:
    v=[]
    for s,(t,mid,fa,bid) in ser.items():
        if s in WIDE: continue
        j=np.searchsorted(t,t+h); ok=j<len(t)
        v.append(fa[j[ok]]/fa[ok]-1)
    base[f'fa{h}']=float(np.concatenate(v).mean()*100)
    w=[]
    for s,(t,mid,fa,bid) in ser.items():
        if s in WIDE: continue
        j=np.searchsorted(t,t+h); ok=j<len(t)
        w.append(mid[j[ok]]/mid[ok]-1)
    base[f'mid{h}']=float(np.concatenate(w).mean()*100)
res={'unconditional_drift_pct':{k:round(v,4) for k,v in base.items()}}
sel={'tick':['b s=0.6% T2=30 guard=off','b s=0.3% T2=30 guard=off','a d1=0.3% m=0.4% T2=30 guard=on','b s=0.2% T2=30 guard=off'],
     'trade':['b s=0.4% T2=30 guard=off','b s=0.3% T2=30 guard=off','a d1=0.2% m=0.1% T2=60 guard=off'],
     'strict':['b s=0.3% T2=60 guard=off','a d1=0.2% m=0.1% T2=10 guard=on']}
for model,labs in sel.items():
    for lab in labs:
        trs=alltr[(model,lab,'H1')]+alltr[(model,lab,'H2')]
        core=[x for x in trs if x['sym'] not in WIDE]
        m=mk(core)
        if m.empty: continue
        d={'n':len(m),'fill_price_vs_fair_pct':round(m.P_vs_fa.mean()*100,3),'fill_price_vs_mid_pct':round(m.P_vs_mid.mean()*100,3),
           'fair_move_60s_before_fill_pct':round(m.fa_pre60.mean()*100,3)}
        for h in H:
            d[f'mid_markout_{h}s_pct']=round(m[f'mo{h}'].mean()*100,3)
            d[f'fair_move_{h}s_after_pct']=round(m[f'fa{h}'].mean()*100,3)
            d[f'mid_move_{h}s_after_pct']=round(m[f'mm{h}'].mean()*100,3)
            d[f'share_mid_below_fill_{h}s']=round(float((m[f'mo{h}']<0).mean()),2)
        res[f'{model} | {lab}']=d
        print(model,lab,d)
# guard paired comparison (core, both halves)
gp={}
for model in ['tick','trade','strict']:
    diffs=[];cnt=[0,0]
    for key in alltr:
        if key[0]!=model or 'guard=off' not in key[1]: continue
        on=key[1].replace('guard=off','guard=on')
        a=[x['net'] for x in alltr[key] if x['sym'] not in WIDE]; b=[x['net'] for x in alltr[(model,on,key[2])] if x['sym'] not in WIDE]
        diffs.append((sum(b)-sum(a))*700000); cnt[0]+=len(a); cnt[1]+=len(b)
    gp[model]={'mean_toman_change_per_config_half_guard_on_minus_off':round(float(np.mean(diffs))),'share_configs_improved':round(float(np.mean(np.array(diffs)>0)),2),'trades_off':cnt[0],'trades_on':cnt[1]}
res['guard_paired']=gp
print(gp)
# guard trigger frequency
res['guard_trigger_share_of_ticks']=round(float((df.ret60<-0.003).mean()),4)
json.dump(res,open(OUT+r"\adverse.json","w"),indent=1)
