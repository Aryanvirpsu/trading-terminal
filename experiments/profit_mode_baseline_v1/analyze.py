import sqlite3,json,statistics as st,collections
c=sqlite3.connect('file:orig/robinhood_500_baseline.db?mode=ro',uri=True); c.row_factory=sqlite3.Row
S=[dict(r) for r in c.execute("select * from signals order by created_at")]
BPS=5.0
def netR(s,bps=BPS,gap_bps=None):
    e,sp,t=s['entry'],s['stop'],s['target']; risk=e-sp
    ef=e*(1+bps/1e4)
    if s['outcome']=='target_hit': return (t-ef)/risk
    if s['outcome']=='stop_hit': return (sp*(1-(gap_bps if gap_bps else bps)/1e4)-ef)/risk
    return None
def grossR(s):
    r=s['entry']-s['stop']
    return {'target_hit':(s['target']-s['entry'])/r,'stop_hit':-1.0}.get(s['outcome'])
for s in S:
    r=s['entry']-s['stop']; s['risk']=r; s['rr']=(s['target']-s['entry'])/r
    s['mfeR']=s['mfe']/r; s['maeR']=s['mae']/r
    s['bucket']=('<50' if s['quality']<50 else '50-54' if s['quality']<55 else '55-59' if s['quality']<60 else '60-64' if s['quality']<65 else '65-69' if s['quality']<70 else '70+')
def stats(rows,label):
    res=[x for x in rows if x['outcome'] in('target_hit','stop_hit')]
    tg=sum(x['outcome']=='target_hit' for x in res); sl=len(res)-tg
    ev=collections.OrderedDict()
    # independent events: resolved, by (symbol,outcome_at)
    ind={}
    for x in res: ind.setdefault((x['symbol'],x['outcome_at'][:10],x['outcome']),[]).append(x)
    def m(f,rs): 
        v=[f(x) for x in rs]; return round(sum(v)/len(v),2) if v else None
    row=dict(bucket=label,n=len(rows),resolved=len(res),open=len(rows)-len(res),target=tg,stop=sl,
      target_rate=round(tg/len(res),2) if res else None,
      indep_events=len(ind),indep_target=sum(k[2]=='target_hit' for k in ind),
      meanRR=m(lambda x:x['rr'],rows),
      grossR_sum=round(sum(grossR(x) for x in res),2),
      netR_sum=round(sum(netR(x) for x in res),2),
      netR_mean=m(netR,res),
      netR_mean_indep=(round(sum(netR(v[0]) for v in ind.values())/len(ind),2) if ind else None),
      netR_mean_15bps=m(lambda x:netR(x,15,25),res),
      mfeR=m(lambda x:x['mfeR'],rows),maeR=m(lambda x:x['maeR'],rows),
      open_mfeR=m(lambda x:x['mfeR'],[x for x in rows if x['outcome']=='open']),
      open_maeR=m(lambda x:x['maeR'],[x for x in rows if x['outcome']=='open']))
    return row
out=[]
print("== BY ACTION"); 
for a in ('TRADEABLE','MONITOR','REJECT'):
    print(stats([s for s in S if s['action']==a],a))
print("== BY QUALITY BUCKET")
for b in ('<50','50-54','55-59','60-64','65-69','70+'):
    print(stats([s for s in S if s['bucket']==b],b))
print("== ALL", stats(S,'ALL'))
print("== RESOLVED-ONLY BY ACTION x outcome")
for a in ('TRADEABLE','MONITOR','REJECT'):
    print(a,collections.Counter(s['outcome'] for s in S if s['action']==a))
print("== blocked winners (MONITOR+REJECT target_hit) clusters")
bw=[s for s in S if s['action']!='TRADEABLE' and s['outcome']=='target_hit']
print(len(bw),collections.Counter(s['symbol'] for s in bw))
bl=[s for s in S if s['action']!='TRADEABLE' and s['outcome']=='stop_hit']
print("blocked losers",len(bl),collections.Counter(s['symbol'] for s in bl))
print("blocked resolved n",len(bw)+len(bl))
print("== by symbol resolved, non-TRADEABLE")
for sym in sorted({s['symbol'] for s in S}):
    rs=[s for s in S if s['symbol']==sym and s['outcome']!='open']
    if rs: print(sym,collections.Counter((s['action'][:4],s['outcome']) for s in rs))
json.dump(S,open('signals_enriched.json','w'),default=str,indent=1)
