"""End-to-end proof that the fix branch restores reconciliation on a ledger damaged by the UNFIXED deployed code.

    python -I docs/ledger_inconsistent_20261016/verify_fix_on_damaged_ledger.py \
        --deployed <dir with `git archive b236d54`> --fixed <fix-branch checkout> \
        --ledger ~/.avdi_cloud_mirror/20261015T202501Z/robinhood_500_baseline.db --out verify_fix_results.json

1. copy the verified Oct-15 ledger into a throw-away sandbox (data dir, HOME and USERPROFILE forced there; guarded);
2. with the DEPLOYED source, replay the suspected Oct-16 sequence (EOG running high sits at the target for several cycles,
   then clears it) and confirm the deployed reconcile() fails;
3. open the SAME damaged database with the FIXED source: reconcile() must pass, list the phantom fills, and a further
   tracker cycle must not create any new one; no existing row may change (row counts and hashes of every table are compared).
4. repeat the replay from scratch with the FIXED source: it must never create a phantom fill at all.
"""
import argparse, hashlib, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

PRELUDE = """
import os, sys, json, time
sb, src = sys.argv[1], sys.argv[2]
os.environ.update(PAPER_DATA_DIR=sb, HOME=sb, USERPROFILE=sb, ROBINHOOD_TRADING_ENABLED='false', BROKER_PROVIDER='none')
for p in ('lab', 'dashboard', 'src'): sys.path.insert(0, os.path.join(src, p))
from paper import db, broker, risk
from paper.fills import Quote
assert os.path.realpath(db.db_path()).startswith(os.path.realpath(sb)) and '.tradingview_mcp_data' not in db.db_path()
risk._live_mark_src = lambda s: (None, 'sandbox')
Q = lambda last, hi, lo: Quote('EOG', last, round(last-.01,4), round(last+.01,4), 153.0, hi, lo, 250000, time.time(), 'sandbox')
def cycle(q):
    for o in db.query("SELECT order_id, symbol FROM orders WHERE status IN ('pending','partial')"):
        broker.process_order(o['order_id'], q)
    broker.manage_open_positions({'EOG': q}, '2026-10-16')
def tables():
    out = {}
    for t in ('signals','orders','fills','positions','equity','audit'):
        rows = db.query(f'SELECT * FROM {t} ORDER BY 1')
        out[t] = [len(rows), __import__('hashlib').sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()[:16]]
    return out
"""
REPLAY = """
# the Oct-16 ledger after META's stop: EOG still open (target 157.6); replay the 13-minute touch, then the trade-through
db.execute("UPDATE positions SET stop=142.53, target=157.6 WHERE symbol='EOG'")
for _ in range(13): cycle(Q(157.5, 157.6, 152.5))
cycle(Q(157.7, 157.8, 152.5))
"""


def run(code, *args):
    p = subprocess.run([sys.executable, "-I", "-c", code, *map(str, args)], capture_output=True, text=True, timeout=300)
    if p.returncode:
        raise SystemExit(p.stderr[-1500:])
    return json.loads([l for l in p.stdout.splitlines() if l.startswith("{")][-1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deployed", required=True); ap.add_argument("--fixed", required=True)
    ap.add_argument("--ledger", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    res = {"deployed_src": a.deployed, "fixed_src": a.fixed}
    with tempfile.TemporaryDirectory(prefix="ch_verify_") as sb:
        shutil.copyfile(a.ledger, Path(sb) / "robinhood_500_baseline.db")
        # step 2: damage with the deployed code
        res["deployed_after_replay"] = run(PRELUDE + REPLAY + """
r = broker.reconcile(); n = db.query_one("SELECT COUNT(*) n FROM orders WHERE symbol='EOG' AND side='SELL'")['n']
print(json.dumps({'reconciled': r['reconciled'], 'delta': r['delta'], 'eog_sell_orders': n,
   'exit_without_position_audits': db.query_one("SELECT COUNT(*) n FROM audit WHERE event='exit_without_position'")['n']}))""", sb, a.deployed)
        # step 3: open the damaged database with the fixed code (a snapshot of every table is taken before and after)
        res["fixed_on_damaged_ledger"] = run(PRELUDE + """
before = tables(); r = broker.reconcile()
cycle(Q(157.7, 158.0, 152.5))            # a further tracker cycle on the damaged ledger
after = tables()
print(json.dumps({'reconciled': r['reconciled'], 'delta': r['delta'], 'excluded_phantom_fills': len(r['excluded_phantom_fills']),
   'excluded_notional': round(sum(p['notional'] for p in r['excluded_phantom_fills']), 2),
   'after_more_cycles_reconciled': broker.reconcile()['reconciled'],
   'rows_before': before, 'rows_after': after, 'no_existing_row_changed': before == after}))""", sb, a.fixed)
    # step 4: the same replay from scratch with the fixed code
    with tempfile.TemporaryDirectory(prefix="ch_verify_") as sb:
        shutil.copyfile(a.ledger, Path(sb) / "robinhood_500_baseline.db")
        res["fixed_replay_from_scratch"] = run(PRELUDE + REPLAY + """
r = broker.reconcile()
print(json.dumps({'reconciled': r['reconciled'], 'delta': r['delta'], 'excluded_phantom_fills': len(r['excluded_phantom_fills']),
   'eog_sell_orders': db.query_one("SELECT COUNT(*) n FROM orders WHERE symbol='EOG' AND side='SELL'")['n'],
   'eog_sell_fills': db.query_one("SELECT COUNT(*) n FROM fills f JOIN orders o ON o.order_id=f.order_id WHERE o.symbol='EOG' AND o.side='SELL'")['n']}))""", sb, a.fixed)
    Path(a.out).write_text(json.dumps(res, indent=1, sort_keys=True) + "\n", newline="\n")
    print(json.dumps(res, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
