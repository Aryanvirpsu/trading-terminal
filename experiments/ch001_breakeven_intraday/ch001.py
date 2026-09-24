"""CH-001 intraday replay. See PREREG.md (registered before results)."""
import collections
import json
import os

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SIG = json.load(open(os.path.join(HERE, '..', 'profit_mode_baseline_v1', 'signals_enriched.json')))
BARS = pd.read_csv(os.path.join(HERE, 'ohlc_5m.csv'), header=[0, 1], index_col=0)
BARS.index = pd.to_datetime(BARS.index, utc=True)


def replay(s, variant, bps=5.0):
    """variant: 'CHAMPION' or 'BE'. Returns dict(out, R, ambiguous, day, ts)."""
    e, sp, t = s['entry'], s['stop'], s['target']
    risk = e - sp
    ef = e * (1 + bps / 1e4)
    trig = e + risk
    t0 = pd.Timestamp(s['created_at'])
    b = BARS[s['symbol']].dropna()
    b = b[b.index >= t0]                       # first bar starting at/after the signal
    stop, moved, amb = sp, False, False
    prev_day = None
    for ts, r in b.iterrows():
        o, h, l = r['Open'], r['High'], r['Low']
        day = ts.tz_convert('America/New_York').date()
        gap_open = prev_day is not None and day != prev_day        # first bar of a new session
        prev_day = day
        hit_stop = l <= stop
        hit_tgt = h >= t
        if gap_open and o <= stop:
            px = o * (1 - 25 / 1e4)
            return dict(out='stop' if not moved else 'be', R=(px - ef) / risk, ambiguous=amb, day=str(day), ts=str(ts))
        if variant == 'BE' and not moved:
            hit_trig = h >= trig
            if hit_trig and hit_stop:
                return dict(out='AMBIGUOUS', R=None, ambiguous=True, day=str(day), ts=str(ts),
                            why='trigger and original stop in one bar')
            if hit_tgt and hit_stop:
                return dict(out='AMBIGUOUS', R=None, ambiguous=True, day=str(day), ts=str(ts),
                            why='stop and target in one bar')
            if hit_stop:
                return dict(out='stop', R=(stop * (1 - bps / 1e4) - ef) / risk, ambiguous=amb, day=str(day), ts=str(ts))
            if hit_tgt:
                return dict(out='target', R=(t - ef) / risk, ambiguous=amb, day=str(day), ts=str(ts))
            if hit_trig:
                stop, moved = e, True          # effective from the NEXT bar
            continue
        # CHAMPION path, or BE variant after the stop moved
        if hit_stop and hit_tgt:
            if variant == 'CHAMPION':          # repo convention: stop first, but flag it
                return dict(out='stop', R=(stop * (1 - bps / 1e4) - ef) / risk, ambiguous=True,
                            day=str(day), ts=str(ts), why='stop and target in one bar (stop-first)')
            return dict(out='AMBIGUOUS', R=None, ambiguous=True, day=str(day), ts=str(ts),
                        why='moved stop and target in one bar')
        if hit_stop:
            return dict(out='stop' if not moved else 'be', R=(stop * (1 - bps / 1e4) - ef) / risk,
                        ambiguous=amb, day=str(day), ts=str(ts))
        if hit_tgt:
            return dict(out='target', R=(t - ef) / risk, ambiguous=amb, day=str(day), ts=str(ts))
    last = float(b['Close'].iloc[-1]) if len(b) else e
    return dict(out='open', R=(last - ef) / risk, ambiguous=False, day=None, ts=None)


def maxdd(rs):
    peak = cum = dd = 0.0
    for x in rs:
        cum += x
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return dd


def compare(pool, bps, label):
    rows = []
    for s in pool:
        c, v = replay(s, 'CHAMPION', bps), replay(s, 'BE', bps)
        rows.append((s, c, v))
    n = len(rows)
    amb = [r for r in rows if r[2]['out'] == 'AMBIGUOUS' or r[1]['ambiguous']]
    clean = [r for r in rows if r not in amb]
    res = [r for r in clean if r[1]['out'] != 'open']
    def tot(idx, rs):
        return sum(r[idx]['R'] for r in rs)
    order = lambda r: r[1]['ts'] or 'z'
    dd_c = maxdd([r[1]['R'] for r in sorted(res, key=order)])
    dd_v = maxdd([r[2]['R'] for r in sorted(res, key=order)])
    cut = [r for r in clean if r[1]['out'] == 'target' and r[2]['out'] == 'be']
    saved = [r for r in clean if r[1]['out'] == 'stop' and r[2]['out'] == 'be']
    # best/worst bounds for the ambiguous ones: variant best = champion R, worst = BE exit ~0 or stop
    print(f"\n== {label} @ {bps:g} bps: n={n} unambiguous={len(clean)} ambiguous={len(amb)} ({100*len(amb)/n:.0f}%)")
    print(f"   resolved-unambiguous={len(res)}  CHAMPION sumR={tot(1,res):6.2f} mean={tot(1,res)/max(len(res),1):5.2f} maxDD={dd_c:5.2f}")
    print(f"                              BE       sumR={tot(2,res):6.2f} mean={tot(2,res)/max(len(res),1):5.2f} maxDD={dd_v:5.2f}")
    print(f"   winners cut prematurely: {len(cut)} ({[r[0]['symbol'] for r in cut]})  losses reduced: {len(saved)} ({[r[0]['symbol']+r[0]['session_date'][5:] for r in saved]})")
    opens = [r for r in clean if r[1]['out'] == 'open']
    print(f"   still-open (unambiguous): {len(opens)}  champion MTM sumR={tot(1,opens):5.2f}  BE MTM sumR={tot(2,opens):5.2f}")
    if amb:
        print("   ambiguous:", [(r[0]['symbol'], r[0]['session_date'][5:], r[2].get('why') or r[1].get('why')) for r in amb])
    ex = [r for r in res if r[0]['symbol'] != 'META']
    print(f"   leave-META-out: CHAMPION {tot(1,ex):6.2f}  BE {tot(2,ex):6.2f}  (n={len(ex)})")
    ev = {}
    for r in res:
        ev.setdefault((r[0]['symbol'], r[1]['day']), r)
    print(f"   event-level (n={len(ev)}): CHAMPION {sum(r[1]['R'] for r in ev.values()):6.2f} BE {sum(r[2]['R'] for r in ev.values()):6.2f}")
    return rows


if __name__ == '__main__':
    pools = {'ALL 55 signals': SIG,
             'TRADEABLE (8)': [s for s in SIG if s['action'] == 'TRADEABLE'],
             'EXECUTED (2)': [s for s in SIG if s['executed']]}
    out = {}
    for label, pool in pools.items():
        for bps in (5.0, 15.0):
            rows = compare(pool, bps, label)
            out[f'{label}|{bps:g}'] = [dict(sym=r[0]['symbol'], date=r[0]['session_date'], champion=r[1], be=r[2]) for r in rows]
    json.dump(out, open(os.path.join(HERE, 'ch001_results.json'), 'w'), indent=1, default=str)
