"""Collect everything into results.json: selected configs per fill model with order-management stats."""
import os as _os
from pathlib import Path as _P
HERE = str(_P(__file__).resolve().parent)          # this study's folder (outputs)
REPO = str(_P(__file__).resolve().parents[2])      # repo root (research/candles_30d, research/premium)
MAIN = _os.environ.get("WALLEX_MAIN", str(_P(REPO).parent / "wallex-bot"))   # live bot folder (ticks.csv, team only)
import json, numpy as np, pandas as pd
from ticksim import load_ticks, sim, summarize, CANDLE_END, CLOCK_OFFSET, WIDE
OUT=rf"{HERE}"
df=load_ticks(); CORE=sorted(set(df.symbol)-WIDE)
grid=json.load(open(OUT+r"\tick_grid.json")); adv=json.load(open(OUT+r"\adverse.json")); cw=json.load(open(OUT+r"\candle_wf.json"))
def parse(lab):
    t=lab.split(); v=t[0]; p={}
    for x in t[1:]:
        k,val=x.split('=')
        p[k]= (val=='on') if k=='guard' else (int(val) if k=='T2' else float(val.rstrip('%'))/100)
    return v,p
def halves(model):
    d=df if model!='trade' else df[df.t+CLOCK_OFFSET<CANDLE_END-60]
    mid=(d.t.min()+d.t.max())/2
    return {'H1':d[d.t<mid],'H2':d[d.t>=mid]}
sel={}
for model in ['tick','trade','strict']:
    hv=halves(model)
    for v in ['a','b']:
        lab=grid['fill_models'][model]['select_'+v]['label']; var,p=parse(lab)
        rec={'label':lab}
        for h,d in hv.items():
            tr,st=sim(d,var,fill=model,**p)
            core=[x for x in tr if x['sym'] in CORE]
            s=summarize(core,st,700000)
            nb_core=None
            s['buy_orders_all_markets']=st['buy_orders']; s['buy_fills_all_markets']=len(tr)
            s['buy_fill_rate']=round(len(tr)/max(st['buy_orders'],1),3)
            s['cancels_per_min_all_markets']=round(st['cancels']/st['hours']/60,2)
            s['orders_per_min_all_markets']=round(st['placements']/st['hours']/60,2)
            s['mean_gross_pct']=round(float(np.mean([x['gross'] for x in core])*100),4) if core else None
            rec[h+'_core']=s
            w=[x for x in tr if x['sym'] in WIDE]
            rec[h+'_wide_flagged']=summarize(w,st,700000) if w else {'entries':0}
        sel[f'{model}|{v}']=rec
        print(model,v,lab,{h:{k:rec[h+'_core'].get(k) for k in ['entries','mean_net_pct','mean_gross_pct','win_rate','timestop_share','worst_pct','toman','per_hour','buy_fill_rate','cancels_per_min_all_markets']} for h in ['H1','H2']})
res={
 'task':'Maker (resting limit) strategies on Wallex TMN spot, long-only. Variant a = passive dip-buy, b = fair-value market making.',
 'fees':{'maker':0.0025,'taker':0.003},
 'size_toman_per_trade':700000,
 'tick_data':{'rows':int(len(df)),'from_utc':str(pd.to_datetime(df.t.min(),unit='s')),'to_utc':str(pd.to_datetime(df.t.max(),unit='s')),
   'core_markets':CORE,'wide_markets_flagged':sorted(WIDE),'wide_note':'POL/SAND only exist from 06:12 UTC 10-22 (second half only); excluded from selection.',
   'median_spread_pct':{s:round(float(((g.ask/g.bid-1).median())*100),3) for s,g in df.groupby('symbol')}},
 'fill_models':{
   'tick':'Specified rule. Live one tick after decision; cancels effective one tick later. BUY at P fills if ask<=P, or (joined at bid) bid<P, or (improved above bid b0) bid<b0, or (rested below bid) bid reaches P then a later tick goes below that level. Sell mirrored. Turned out GENEROUS: bid/ask re-quote flicker (70-200 bid drops/h) triggers fills far more often than real trades occur (Wallex trades in only 11-70% of minutes).',
   'trade':'tick rule, but a book trade-through counts only if the Wallex 1m candle for that exchange minute shows a real trade strictly through our price (cross still counts). Only possible to 07:00 UTC 10-22 (candle end), so halves are inside 16:45-07:00 and POL/SAND are effectively absent.',
   'strict':'Crosses only (ask<=P / bid>=Q). Pessimistic bound; maximal adverse selection.',
   'candles':'30 d, 1m: BUY at P fills if a later minute low<P (vol>0), SELL if high>Q; time-stop exit = last trade x (1 - median half spread), taker.'},
 'grid':{'a':'d1 {0.2,0.3,0.5}% x m {0.1,0.2,0.4}% x T2 {10,30,60} min x guard on/off; T1 fixed 60 s (pre-declared, not tuned)',
         'b':'s {0.2,0.3,0.4,0.6}% x T2 {10,30,60} x guard on/off; reprice when fair-adjusted moves >0.1%; sell floor entry x 1.0055',
         'guard':"no buy (resting buy cancelled) when the coin's fair (Binance x USDT/TMN) fell >0.3% over the last 60 s; triggers on 0.38% of ticks"},
 'selection_rule':'best total toman on FIRST half over the 8 core markets, needing >=10 completed round trips; report SECOND half',
 'tick_selected':sel,
 'tick_configs_positive_core':{m:grid['fill_models'][m]['configs_positive'] for m in grid['fill_models']},
 'tick_splits_utc':{m:grid['fill_models'][m]['split_utc'] for m in grid['fill_models']},
 'tick_hindsight_best_H2':{m:grid['fill_models'][m]['hindsight_best_H2_core'] for m in grid['fill_models']},
 'adverse_selection':adv,
 'candle_walk_forward':{k:cw[k] for k in cw if k!='table'},
 'files':{'grid_tables':'tick_grid.json, candle_wf.json','scripts':'ticksim.py, run_ticks.py, adverse.py, candlesim.py, final.py'},
}
json.dump(res,open(OUT+r"\results.json","w"),indent=1,default=str)
