import json,pandas as pd
S=json.load(open('signals_enriched.json')); B=pd.read_csv('ohlc_daily.csv',header=[0,1],index_col=0,parse_dates=True)
def rep(s,bps=5.0,be_after=None,tgt_mult=None):
    e,sp,t0=s['entry'],s['stop'],s['target']; risk=e-sp; ef=e*(1+bps/1e4)
    t=e+tgt_mult*risk if tgt_mult else t0; stop=sp; moved=False
    bars=B[s['symbol']].dropna(); bars=bars[bars.index>pd.Timestamp(s['session_date'])]
    for d,b in bars.iterrows():
        o,h,l=b['Open'],b['High'],b['Low']
        if o<=stop: return (o*(1-25/1e4)-ef)/risk,str(d.date())
        if l<=stop: return (stop*(1-bps/1e4)-ef)/risk,str(d.date())
        if h>=t: return (t-ef)/risk,str(d.date())
        if be_after and not moved and h>=e+be_after*risk: stop=e; moved=True   # effective from NEXT bar (conservative)
    return None,None
def run(label,**kw):
    rs=[(s,)+rep(s,**kw) for s in S]; res=[r for r in rs if r[1] is not None]
    ev={}
    for s,R,d in res: ev.setdefault((s['symbol'],d),R)
    tg=sum(1 for s,R,d in res if R>0)
    print(f"{label:34s} resolved {len(res):2d} win {tg:2d} R_sum {sum(r[1] for r in res):6.2f} mean {sum(r[1] for r in res)/len(res):5.2f} events {len(ev)} mean/event {sum(ev.values())/len(ev):5.2f}")
    exm=[r for r in res if r[0]['symbol']!='META']
    print(f"{'   ex-META':34s} resolved {len(exm):2d} R_sum {sum(r[1] for r in exm):6.2f} mean {sum(r[1] for r in exm)/len(exm):5.2f}")
run("BASE (stop 1R / target ~2.2R)")
run("E1 stop->breakeven after +1R",be_after=1.0)
run("E2 target 1.5R",tgt_mult=1.5)
run("E3 target 1.5R + BE after +1R",tgt_mult=1.5,be_after=1.0)
print("--- same-set comparison: signals resolved under BASE (n=22) ---")
base=[s for s in S if rep(s)[0] is not None]
for label,kw in (("BASE",{}),("E1 BE after +1R",dict(be_after=1.0)),("E2 target 1.5R",dict(tgt_mult=1.5)),("E3 1.5R + BE",dict(tgt_mult=1.5,be_after=1.0))):
    v=[rep(s,**kw)[0] for s in base]; nm=[rep(s,**kw)[0] for s in base if s['symbol']!='META']
    print(f"{label:18s} n={len(v)} unresolved_now={sum(x is None for x in v)} R_sum={sum(x for x in v if x is not None):6.2f}  exMETA R_sum={sum(x for x in nm if x is not None):6.2f} (n={len(nm)})")
