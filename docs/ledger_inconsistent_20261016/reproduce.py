"""Isolated reproduction of ledger-reconciliation failure modes in the DEPLOYED code (b236d54).

    git archive b236d54 | tar -x -C <deployed_src_dir>
    python docs/ledger_inconsistent_20261016/reproduce.py --src <deployed_src_dir> --ledger <pristine ledger .db> --out reproduce_results.json

Each scenario runs in its own subprocess on a FRESH COPY of the verified 2026-10-15 cloud ledger, with the data directory
(PAPER_DATA_DIR), HOME and USERPROFILE forced into a throw-away sandbox.  The script refuses to run if the resolved ledger
path is not inside that sandbox.  It uses the real deployed functions (broker.process_order, broker.manage_open_positions,
broker.reconcile, risk.account_state) and only supplies synthetic market quotes.  Nothing is sent to a broker or a network:
the live-quote mark is stubbed, and ROBINHOOD_TRADING_ENABLED=false / BROKER_PROVIDER=none are set.

It does NOT claim that any scenario happened on 2026-10-16.  It shows which code paths CAN make `reconcile()` fail.
"""
import argparse, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

SCENARIOS = {
    "A_control_single_exits": "EOG target traded through + META stop hit, one exit order each (nominal path)",
    "B_target_touch_then_through": "EOG high == target (touch) for 2 cycles, then high > target: duplicate pending exit orders",
    "C_tiny_bar_volume_overfill": "META stop hit while the quote's volume is tiny: fractional order filled as >= 1 whole share",
    "D_partial_then_close": "a >1-share position exits partially (small volume) then closes: realized P&L of the partial is lost",
}


def child(scenario, src, ledger_copy, sandbox):
    os.environ.update({"PAPER_DATA_DIR": str(sandbox), "HOME": str(sandbox), "USERPROFILE": str(sandbox),
                       "ROBINHOOD_TRADING_ENABLED": "false", "BROKER_PROVIDER": "none"})
    for p in ("lab", "dashboard", "src"):
        sys.path.insert(0, str(Path(src) / p))
    from paper import db, broker, risk, fills                      # deployed modules
    from paper.fills import Quote
    assert str(Path(db.db_path()).resolve()).startswith(str(Path(sandbox).resolve())), "SAFETY: ledger path escaped the sandbox"
    assert ".tradingview_mcp_data" not in db.db_path(), "SAFETY: would touch the real data folder"
    risk._live_mark_src = lambda s: (None, "sandbox")              # no network; cash is unaffected by marks

    def cycle(quotes, session="2026-10-16"):
        """exactly the tracker's two steps: (1) pending orders, (2) stop/target management"""
        for o in db.query("SELECT order_id, symbol FROM orders WHERE status IN ('pending','partial')"):
            q = quotes.get(o["symbol"])
            if q:
                broker.process_order(o["order_id"], q)
        broker.manage_open_positions(quotes, session)

    Q = lambda s, last, o, h, l, v, spread=0.02: Quote(s, last, round(last - spread / 2, 4), round(last + spread / 2, 4), o, h, l, v)
    if scenario == "A_control_single_exits":
        cycle({"EOG": Q("EOG", 157.9, 153.0, 158.0, 152.5, 250000), "META": Q("META", 700.0, 735.0, 736.0, 699.0, 500000)})
    elif scenario == "B_target_touch_then_through":
        touch = Q("EOG", 157.5, 153.0, 157.6, 152.5, 250000)       # high == target 157.6 exactly
        cycle({"EOG": touch}); cycle({"EOG": touch})
        cycle({"EOG": Q("EOG", 157.7, 153.0, 157.8, 152.5, 250000)})
    elif scenario == "C_tiny_bar_volume_overfill":
        cycle({"META": Q("META", 700.0, 735.0, 736.0, 699.0, 3)})   # 3 shares traded in the bar
    elif scenario == "D_partial_then_close":
        syn = Q("SYN", 20.0, 20.0, 20.2, 19.9, 1_000_000)
        o = broker.place_order("SYN", "BUY", 3.0, order_type="MARKET", intent="entry", strategy="sandbox", session_date="2026-10-16")
        broker.process_order(o["order_id"], syn)
        db.execute("UPDATE positions SET stop=19.0, target=25.0 WHERE symbol='SYN'")
        cycle({"SYN": Q("SYN", 18.9, 19.5, 19.6, 18.8, 100)})       # stop hit, tiny volume -> partial (1 share)
        cycle({"SYN": Q("SYN", 18.9, 19.5, 19.6, 18.8, 1_000_000)})  # next cycle: remainder closes in full

    r = broker.reconcile()
    # independent recomputation straight from SQL (no deployed code), same formula as docs/cloud_evidence_20261018/audit.py
    cash = 500.0
    for f in db.query("SELECT f.quantity q, f.price p, f.fees fee, o.side s FROM fills f JOIN orders o ON o.order_id=f.order_id ORDER BY f.filled_at"):
        cash += (-1 if f["s"] == "BUY" else 1) * f["q"] * f["p"] - (f["fee"] or 0)
    orders = db.query("SELECT symbol, side, order_type, intent, quantity, status, filled_qty FROM orders ORDER BY created_at, order_id")
    new = [o for o in orders if o["intent"] != "entry" or o["symbol"] == "SYN"]
    over = db.query("SELECT o.symbol, o.quantity oq, f.quantity fq FROM fills f JOIN orders o ON o.order_id=f.order_id WHERE f.quantity > o.quantity + 1e-9")
    out = {"scenario": scenario, "description": SCENARIOS[scenario],
           "reconcile": {k: r[k] for k in ("reconciled", "cash_from_fills", "cash_from_state", "delta")},
           "reconcile_tolerance_usd": broker.RECONCILE_TOLERANCE_USD,
           "independent_cash_from_fills": round(cash, 2),
           "exit_orders_created_this_run": [o for o in new if o["side"] == "SELL"][-6:],
           "audit_exit_without_position": db.query_one("SELECT COUNT(*) n FROM audit WHERE event='exit_without_position'")["n"],
           "fills_larger_than_their_order": over,
           "open_positions_after": [p["symbol"] for p in db.query("SELECT symbol FROM positions WHERE status='open'")]}
    print(json.dumps(out, default=str))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src"); ap.add_argument("--ledger"); ap.add_argument("--out")
    ap.add_argument("--child"); ap.add_argument("--sandbox")
    a = ap.parse_args()
    if a.child:
        return child(a.child, a.src, a.ledger, a.sandbox)
    results = {"deployed_source": str(a.src), "ledger_used": Path(a.ledger).name, "scenarios": []}
    for sc in SCENARIOS:
        with tempfile.TemporaryDirectory(prefix="ch_repro_") as sb:
            shutil.copyfile(a.ledger, Path(sb) / "robinhood_500_baseline.db")       # a copy; the pristine file is never opened writable
            p = subprocess.run([sys.executable, "-I", __file__, "--child", sc, "--src", a.src, "--ledger", a.ledger, "--sandbox", sb],
                               capture_output=True, text=True, timeout=120)
            line = [l for l in p.stdout.splitlines() if l.startswith("{")]
            results["scenarios"].append(json.loads(line[-1]) if line else {"scenario": sc, "error": (p.stderr or p.stdout)[-600:]})
    # control on the untouched Oct-15 ledger (no simulated activity)
    with tempfile.TemporaryDirectory(prefix="ch_repro_") as sb:
        shutil.copyfile(a.ledger, Path(sb) / "robinhood_500_baseline.db")
        code = ("import os,sys,json;os.environ.update(PAPER_DATA_DIR=r'%s',HOME=r'%s',USERPROFILE=r'%s');"
                "[sys.path.insert(0,os.path.join(r'%s',p)) for p in ('lab','dashboard','src')];"
                "from paper import broker,risk;risk._live_mark_src=lambda s:(None,'x');r=broker.reconcile();"
                "print(json.dumps({k:r[k] for k in ('reconciled','cash_from_fills','cash_from_state','delta')}))") % (sb, sb, sb, a.src)
        p = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120)
        results["baseline_oct15_ledger_deployed_reconcile"] = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {"error": p.stderr[-400:]}
    Path(a.out).write_text(json.dumps(results, indent=1, sort_keys=True) + "\n", newline="\n")
    print(json.dumps(results, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
