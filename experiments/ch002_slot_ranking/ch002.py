"""CH-002: capacity-aware slot ranking replay. See PREREG.md (registered before results)."""
import collections
import json
import os
import random
import statistics as st

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(HERE, '..', 'profit_mode_baseline_v1')
SIG = json.load(open(os.path.join(BASE, 'signals_enriched.json')))
BARS = pd.read_csv(os.path.join(BASE, 'ohlc_daily.csv'), header=[0, 1], index_col=0, parse_dates=True)
MAX_OPEN, MAX_DAY, PER_SECTOR, NOTIONAL, RISK_USD = 3, 2, 1, 125.0, 5.0


def replay(s, bps):
    """Bar-by-bar from the session AFTER the signal. Resolved: exit day + R. Open: MTM R at last close."""
    e, sp, t = s['entry'], s['stop'], s['target']
    risk = e - sp
    ef = e * (1 + bps / 1e4)
    bars = BARS[s['symbol']].dropna()
    bars = bars[bars.index > pd.Timestamp(s['session_date'])]
    for d, b in bars.iterrows():
        o, h, l = b['Open'], b['High'], b['Low']
        if o <= sp:
            return dict(res=True, R=(o * (1 - 25 / 1e4) - ef) / risk, day=str(d.date()))
        if l <= sp:
            return dict(res=True, R=(sp * (1 - bps / 1e4) - ef) / risk, day=str(d.date()))
        if h >= t:
            return dict(res=True, R=(t - ef) / risk, day=str(d.date()))
    last = float(bars['Close'].iloc[-1]) if len(bars) else e
    return dict(res=False, R=(last - ef) / risk, day=None)


def features(s):
    """Decision-time fields only."""
    g = {x['name']: x['value'] for x in json.loads(s['gates_json'] or '[]')}
    ex = g.get('liquidity')
    ex = float(ex) if isinstance(ex, (int, float)) else 1.0
    rr = (s['target'] - s['entry']) / (s['entry'] - s['stop'])
    return dict(scanner_rank=s['scanner_rank'], quality=s['quality'],
                expected_r=float(s['expected_r']) if s['expected_r'] is not None else 0.0,
                composite=(s['quality'] / 100.0) * rr * ex)


POLICIES = {   # ascending sort key; negate for "highest first"; symbol = deterministic tiebreak
    'CHAMPION': lambda f, s: (f['scanner_rank'], s['symbol']),
    'CH-002A conviction': lambda f, s: (-f['quality'], s['symbol']),
    'CH-002B expected_R': lambda f, s: (-f['expected_r'], -f['quality'], s['symbol']),
    'CH-002C composite': lambda f, s: (-f['composite'], s['symbol']),
}


def simulate(pool, keyfn, R):
    """A slot frees only the day AFTER its exit (conservative)."""
    by_day = collections.defaultdict(list)
    for s in pool:
        by_day[s['session_date']].append(s)
    open_pos, acc, constrained = [], [], 0
    for day in sorted(by_day):
        open_pos = [p for p in open_pos if p['exit'] is None or p['exit'] >= day]
        n_today = 0
        skipped_cap = 0
        for s in sorted(by_day[day], key=lambda x: keyfn(features(x), x)):
            if (n_today >= MAX_DAY or len(open_pos) >= MAX_OPEN
                    or sum(p['sec'] == s['sector'] for p in open_pos) >= PER_SECTOR
                    or any(p['sym'] == s['symbol'] for p in open_pos)):
                skipped_cap += 1
                continue
            r = R[s['signal_id']]
            n_today += 1
            open_pos.append(dict(sym=s['symbol'], sec=s['sector'], exit=r['day']))
            acc.append(s)
        constrained += (skipped_cap > 0)

    def usd(s):
        r = R[s['signal_id']]
        e = s['entry']
        risk = e - s['stop']
        q = min(NOTIONAL / e, RISK_USD / risk)
        return r['R'] * q * risk

    return dict(n=len(acc), resolved=sum(R[s['signal_id']]['res'] for s in acc),
                realized_R=sum(R[s['signal_id']]['R'] for s in acc if R[s['signal_id']]['res']),
                total_R=sum(R[s['signal_id']]['R'] for s in acc),
                usd=sum(usd(s) for s in acc), constrained_days=constrained,
                trades=[(s['symbol'], s['session_date'][5:]) for s in acc])


def pools(exclude=()):
    p1 = [s for s in SIG if s['action'] == 'TRADEABLE' and s['symbol'] not in exclude]
    p2 = [s for s in SIG if s['action'] in ('TRADEABLE', 'MONITOR') and s['symbol'] not in exclude]
    return {'P1 TRADEABLE (primary)': p1, 'P2 TRADEABLE+MONITOR (ranking probe)': p2}


def run(bps, exclude=(), n_random=2000):
    R = {s['signal_id']: replay(s, bps) for s in SIG}
    out = {}
    for pname, pool in pools(exclude).items():
        row = {k: simulate(pool, fn, R) for k, fn in POLICIES.items()}
        rnd = random.Random(7)
        tots, reals = [], []
        for _ in range(n_random):
            keys = {s['signal_id']: rnd.random() for s in pool}
            r = simulate(pool, lambda f, s: keys[s['signal_id']], R)
            tots.append(r['total_R'])
            reals.append(r['realized_R'])
        for k in POLICIES:
            row[k]['pctile_vs_random'] = sum(t < row[k]['total_R'] for t in tots) / len(tots)
            row[k]['differs_from_champion'] = sorted(
                set(map(tuple, row[k]['trades'])) ^ set(map(tuple, row['CHAMPION']['trades'])))
        row['RANDOM'] = dict(total_mean=st.mean(tots), total_sd=st.pstdev(tots),
                             realized_mean=st.mean(reals), total_min=min(tots), total_max=max(tots))
        out[pname] = row
    return out


def show(title, res):
    print('\n=====', title)
    for pname, row in res.items():
        print('--', pname)
        for k in POLICIES:
            r = row[k]
            print(f"  {k:20s} n={r['n']:2d} resolved={r['resolved']:2d} realizedR={r['realized_R']:6.2f} "
                  f"totalR={r['total_R']:6.2f} ${r['usd']:7.2f} constrained_days={r['constrained_days']} "
                  f"pctile_vs_random={r['pctile_vs_random']:.2f} differs={len(r['differs_from_champion'])}")
        rd = row['RANDOM']
        print(f"  RANDOM  totalR mean={rd['total_mean']:.2f} sd={rd['total_sd']:.2f} "
              f"[{rd['total_min']:.2f},{rd['total_max']:.2f}] realized mean={rd['realized_mean']:.2f}")


if __name__ == '__main__':
    results = {}
    for bps in (5.0, 15.0):
        results[f'{bps:g}bps'] = run(bps)
        show(f'ALL EVENTS, {bps:g} bps', results[f'{bps:g}bps'])
    results['ex_META_5bps'] = run(5.0, exclude=('META',))
    show('LEAVE META OUT, 5 bps', results['ex_META_5bps'])
    loo = collections.defaultdict(list)
    for sym in sorted({s['symbol'] for s in SIG}):
        r = run(5.0, exclude=(sym,), n_random=1)
        for pname, row in r.items():
            for k in list(POLICIES)[1:]:
                loo[(pname, k)].append((sym, round(row[k]['total_R'] - row['CHAMPION']['total_R'], 2)))
    print('\n===== LEAVE-ONE-SYMBOL-OUT: policy minus CHAMPION total R (5 bps)')
    for (pname, k), v in loo.items():
        d = [x[1] for x in v]
        print(f"  {pname[:2]} {k:20s} min={min(d):6.2f} median={st.median(d):6.2f} max={max(d):6.2f}  "
              f"#>0={sum(x > 0 for x in d)}/{len(d)}  #<0={sum(x < 0 for x in d)}")
    json.dump(dict(results=results, loo={f'{a}|{b}': v for (a, b), v in loo.items()}),
              open(os.path.join(HERE, 'ch002_results.json'), 'w'), indent=1, default=str)
