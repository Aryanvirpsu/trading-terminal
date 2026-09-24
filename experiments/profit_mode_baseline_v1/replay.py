import json,pandas as pd,collections,math,sys
S=json.load(open('signals_enriched.json')); B=pd.read_csv('ohlc_daily.csv',header=[0,1],index_col=0,parse_dates=True)
BPS=float(sys.argv[1]) if len(sys.argv)>1 else 5.0
def replay(s,bps=BPS):
    e,sp,t=s['entry'],s['stop'],s['target']; risk=e-sp; ef=e*(1+bps/1e4)
    bars=B[s['symbol']].dropna(); bars=bars[bars.index>pd.Timestamp(s['session_date'])]
    mfe=mae=0.0
    for d,b in bars.iterrows():
        o,h,l=b['Open'],b['High'],b['Low']
        # gap first
        if o<=sp: return dict(out='stop',R=(o*(1-25/1e4)-ef)/risk,day=str(d.date()),gap=True,mfeR=mfe/risk,maeR=max(mae,(e-l))/risk)
        if l<=sp: return dict(out='stop',R=(sp*(1-bps/1e4)-ef)/risk,day=str(d.date()),gap=False,mfeR=max(mfe,h-e)/risk if False else mfe/risk,maeR=(e-l)/risk)
        if h>=t: return dict(out='target',R=(t-ef)/risk,day=str(d.date()),gap=(o>=t),mfeR=(t-e)/risk,maeR=max(mae,e-l)/risk)
        mfe=max(mfe,h-e); mae=max(mae,e-l)
    return dict(out='open',R=None,day=None,gap=False,mfeR=mfe/risk,maeR=mae/risk)
rows=[]
for s in S:
    r=replay(s); r.update(id=s['signal_id'],sym=s['symbol'],action=s['action'],q=s['quality'],bucket=s['bucket'],date=s['session_date'],ledger=s['outcome'],rr=s['rr'],risk=s['risk'],fg=s['failed_gates']); rows.append(r)
json.dump(rows,open('replay_%sbps.json'%int(BPS),'w'),indent=1,default=lambda o: bool(o) if hasattr(o,'item') else str(o))
def agg(rs,label):
    res=[x for x in rs if x['out']!='open']; tg=sum(x['out']=='target' for x in res)
    ev={}
    for x in res: ev.setdefault((x['sym'],x['day'],x['out']),x)
    m=lambda v: round(sum(v)/len(v),2) if v else None
    return dict(g=label,n=len(rs),res=len(res),open=len(rs)-len(res),tgt=tg,stp=len(res)-tg,
      tgt_rate=round(tg/len(res),2) if res else None,events=len(ev),
      R_sum=round(sum(x['R'] for x in res),2),R_mean=m([x['R'] for x in res]),
      R_mean_evt=m([x['R'] for x in ev.values()]),
      open_mfeR=m([x['mfeR'] for x in rs if x['out']=='open']),open_maeR=m([x['maeR'] for x in rs if x['out']=='open']),
      rr=m([x['rr'] for x in rs]))
print("bps",BPS)
for a in('TRADEABLE','MONITOR','REJECT'): print(agg([x for x in rows if x['action']==a],a))
for a in('MONITOR+REJECT',): print(agg([x for x in rows if x['action']!='TRADEABLE'],a))
print(agg(rows,'ALL'))
for b in('<50','50-54','55-59','60-64','65-69','70+'): print(agg([x for x in rows if x['bucket']==b],b))
# ledger vs replay disagreement
d=[(x['sym'],x['date'],x['action'][:4],x['ledger'],x['out'],x['day']) for x in rows if (x['ledger']=='open')!=(x['out']=='open') or (x['ledger']!='open' and x['ledger'][:4]!=x['out'][:4])]
print("DISAGREE",len(d)); [print(' ',z) for z in d]
