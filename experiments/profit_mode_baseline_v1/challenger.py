import json,collections,datetime as dt,sys
R=json.load(open('replay_5bps.json')); S={s['signal_id']:s for s in json.load(open('signals_enriched.json'))}
for x in R: x['s']=S[x['id']]
def run(T,max_day=2,max_open=3,per_sector=1,label=''):
    acc=[];open_pos=[]  # (sym,sector,exit_day)
    by_day=collections.defaultdict(list)
    for x in R: by_day[x['date']].append(x)
    skipped=collections.Counter()
    for day in sorted(by_day):
        # release positions whose exit is on/before this day
        open_pos=[p for p in open_pos if p['exit'] is None or p['exit']>day]
        n_today=0
        cand=[x for x in by_day[day] if json.loads(x['s']['failed_gates'] or '[]') in ([],['conviction']) and x['q']>=T]
        cand.sort(key=lambda x:(x['s'].get('scanner_rank') or 99,-x['q']))
        for x in cand:
            sec=x['s']['sector']
            if n_today>=max_day: skipped['entries_per_day']+=1; continue
            if len(open_pos)>=max_open: skipped['max_open']+=1; continue
            if sum(p['sec']==sec for p in open_pos)>=per_sector: skipped['sector']+=1; continue
            if any(p['sym']==x['sym'] for p in open_pos): skipped['dup_symbol']+=1; continue
            n_today+=1; open_pos.append(dict(sym=x['sym'],sec=sec,exit=x['day'])); acc.append(x)
    res=[x for x in acc if x['out']!='open']
    ev={}
    for x in res: ev.setdefault((x['sym'],x['day']),x)
    def usd(x):  # $ at $125 notional: qty=125/entry; risk/sh
        e=x['s']['entry']; return x['R']*(125/e)*x['s']['risk']
    return dict(T=T,label=label,accepted=len(acc),resolved=len(res),open=len(acc)-len(res),
        tgt=sum(x['out']=='target' for x in res),R_sum=round(sum(x['R'] for x in res),2),
        R_mean=round(sum(x['R'] for x in res)/len(res),2) if res else None,
        usd=round(sum(usd(x) for x in res),2),skipped=dict(skipped),syms=[(x['sym'],x['date'][5:],x['out']) for x in acc])
for T,l in((60,'CHAMPION (P0-corrected sizing)'),(55,'conviction 55 (threshold probe)'),(50,'conviction 50 (threshold probe)'),(45,'conviction 45 (threshold probe)')):
    r=run(T,label=l); print(json.dumps(r,default=str))
