"""Compare the REAL cloud ledger (host backup taken after the 2026-10-16 failure) with the suspected duplicate-exit sequence.

    python -I docs/ledger_inconsistent_20261016/host_evidence_analysis.py --mirror ~/.avdi_cloud_mirror/20261021T202501Z \
        --deployed <dir with `git archive b236d54`> --fixed <fix-branch checkout> --out host_evidence_results.json

* Opens the pristine ledger `mode=ro&immutable=1` (the backup is an online-backup snapshot: journal_mode=delete, no WAL to replay).
* Reads ONLY the ledger database (orders, fills, positions, equity, audit, and COUNTS of signals).  It does not open the shadow DB
  and never reads signal/candidate outcome columns, so no challenger outcome is seen.
* The deployed and the fixed `reconcile()` run in sandboxed subprocesses on COPIES (data dir / HOME / USERPROFILE forced to a temp
  directory, ledger path asserted inside it); table hashes before and after prove nothing was modified.
"""
import argparse, datetime as dt, hashlib, json, os, shutil, sqlite3, statistics, subprocess, sys, tempfile
from pathlib import Path

INITIAL_CASH = 500.0


def ro(p):
    c = sqlite3.connect(f"file:{Path(p).as_posix()}?mode=ro&immutable=1", uri=True)
    c.row_factory = sqlite3.Row
    return c


def t(x):
    return dt.datetime.fromisoformat(x)


CHILD = r'''
import os, sys, json, hashlib
sb, src = sys.argv[1], sys.argv[2]
os.environ.update(PAPER_DATA_DIR=sb, HOME=sb, USERPROFILE=sb, ROBINHOOD_TRADING_ENABLED="false", BROKER_PROVIDER="none")
for p in ("lab", "dashboard", "src"): sys.path.insert(0, os.path.join(src, p))
from paper import db, broker, risk
assert os.path.realpath(db.db_path()).startswith(os.path.realpath(sb)) and ".tradingview_mcp_data" not in db.db_path()
risk._live_mark_src = lambda s: (None, "sandbox")
def tables():
    out = {}
    for tb in ("signals", "orders", "fills", "positions", "equity", "audit"):
        rows = db.query(f"SELECT * FROM {tb} ORDER BY 1")
        out[tb] = [len(rows), hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()[:16]]
    return out
before = tables()
r1 = broker.reconcile(); r2 = broker.reconcile()
after = tables()
slim = lambda r: {k: r[k] for k in ("reconciled", "delta", "cash_from_fills", "cash_from_state")} | (
    {"excluded": [(p["fill_id"], p["order_id"], p["notional"]) for p in r.get("excluded_phantom_fills", [])]} if "excluded_phantom_fills" in r else {})
print(json.dumps({"reconcile_run_1": slim(r1), "reconcile_run_2": slim(r2), "identical_runs": slim(r1) == slim(r2),
                  "tables_unchanged": before == after, "tables": after, "tolerance": getattr(broker, "RECONCILE_TOLERANCE_USD", None)}))
'''


def sandbox_reconcile(src, ledger):
    with tempfile.TemporaryDirectory(prefix="ch_host_") as sb:
        shutil.copyfile(ledger, Path(sb) / "robinhood_500_baseline.db")
        p = subprocess.run([sys.executable, "-I", "-c", CHILD, sb, str(src)], capture_output=True, text=True, timeout=300)
        if p.returncode:
            raise SystemExit(p.stderr[-1500:])
        return json.loads([l for l in p.stdout.splitlines() if l.startswith("{")][-1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mirror", required=True); ap.add_argument("--deployed", required=True)
    ap.add_argument("--fixed", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args(); mir = Path(a.mirror).expanduser()
    man = json.loads((mir / "MANIFEST.json").read_text())
    ledger = mir / "robinhood_500_baseline.db"
    res = {"backup": mir.name, "backup_created_utc": man["created_iso"], "runtime_version": man["runtime_version"],
           "sha256_matches_manifest": hashlib.sha256(ledger.read_bytes()).hexdigest() == man["files"][ledger.name]["sha256"]}
    L = ro(ledger)
    res["integrity_check"] = L.execute("PRAGMA integrity_check").fetchone()[0]
    res["journal_mode"] = L.execute("PRAGMA journal_mode").fetchone()[0]

    orders = [dict(r) for r in L.execute("SELECT * FROM orders ORDER BY created_at, order_id")]
    fills = [dict(r) for r in L.execute("SELECT f.*, o.symbol, o.side, o.intent, o.order_type, o.quantity oq, o.limit_price FROM fills f JOIN orders o ON o.order_id=f.order_id ORDER BY f.filled_at, f.fill_id")]
    pos = [dict(r) for r in L.execute("SELECT * FROM positions ORDER BY opened_at")]
    audit = [dict(r) for r in L.execute("SELECT * FROM audit ORDER BY audit_id")]
    eq = [dict(r) for r in L.execute("SELECT * FROM equity ORDER BY recorded_at")]
    phantom_audit = {r["entity_id"]: r for r in audit if r["entity"] == "order" and r["event"] == "exit_without_position"}

    # ---- 1. which fills are phantom, from the host's own rows -------------------------------------------------------------
    def position_open_at(sym, ts):
        return [p["position_id"] for p in pos if p["symbol"] == sym and t(p["opened_at"]) <= t(ts) and (p["closed_at"] is None or t(ts) <= t(p["closed_at"]))]
    ph = []
    for f in fills:
        if f["order_id"] in phantom_audit:
            ph.append({"fill_id": f["fill_id"], "order_id": f["order_id"], "symbol": f["symbol"], "intent": f["intent"], "filled_at": f["filled_at"],
                       "qty": f["quantity"], "price": f["price"], "notional": round(f["quantity"] * f["price"], 4),
                       "audit_at": phantom_audit[f["order_id"]]["at"], "positions_open_at_fill": position_open_at(f["symbol"], f["filled_at"])})
    res["phantom_candidates"] = {"count": len(ph), "symbols": sorted({p["symbol"] for p in ph}), "intents": sorted({p["intent"] for p in ph}),
                                 "total_notional": round(sum(p["notional"] for p in ph), 4),
                                 "all_have_no_open_position_at_fill_time": all(not p["positions_open_at_fill"] for p in ph),
                                 "orders_with_exactly_one_fill_and_one_audit": all(sum(1 for f in fills if f["order_id"] == p["order_id"]) == 1 and
                                     sum(1 for r in audit if r["entity_id"] == p["order_id"] and r["event"] == "exit_without_position") == 1 for p in ph),
                                 "audit_written_after_its_fill": all(t(p["audit_at"]) >= t(p["filled_at"]) for p in ph)}

    # ---- 2. the EOG sequence ---------------------------------------------------------------------------------------------
    eog_orders = [o for o in orders if o["symbol"] == "EOG" and o["intent"] == "exit_target"]
    created = [t(o["created_at"]) for o in eog_orders]
    gaps = [round((b - a).total_seconds(), 1) for a, b in zip(created, created[1:])]
    eog_fills = [f for f in fills if f["symbol"] == "EOG" and f["side"] == "SELL"]
    eog_pos = next(p for p in pos if p["symbol"] == "EOG")
    first_fill = min(t(f["filled_at"]) for f in eog_fills); last_fill = max(t(f["filled_at"]) for f in eog_fills)
    res["eog_sequence"] = {
        "exit_target_orders": len(eog_orders), "same_quantity": sorted({o["quantity"] for o in eog_orders}), "same_limit": sorted({o["limit_price"] for o in eog_orders}),
        "first_created": eog_orders[0]["created_at"], "last_created": eog_orders[-1]["created_at"],
        "creation_gap_seconds": {"min": min(gaps), "median": statistics.median(gaps), "max": max(gaps)},
        "all_orders_status": sorted({o["status"] for o in eog_orders}), "sell_fills": len(eog_fills),
        "fill_window_ms": round((last_fill - first_fill).total_seconds() * 1000, 1), "first_fill_at": first_fill.isoformat(), "last_fill_at": last_fill.isoformat(),
        "position_closed_at": eog_pos["closed_at"], "position_closed_by_first_fill": first_fill <= t(eog_pos["closed_at"]) and
            all(t(f["filled_at"]) > t(eog_pos["closed_at"]) for f in eog_fills if t(f["filled_at"]) != first_fill),
        "position_qty_equals_order_qty": abs(eog_pos["quantity"] - eog_orders[0]["quantity"]) < 1e-9,
        "unflagged_eog_fills": [f["fill_id"] for f in eog_fills if f["order_id"] not in phantom_audit],
        "flagged_eog_fills": sum(1 for f in eog_fills if f["order_id"] in phantom_audit)}
    res["meta_sequence"] = {"orders": [(o["order_type"], o["intent"], o["status"]) for o in orders if o["symbol"] == "META" and o["side"] == "SELL"],
                            "flagged": any(o["order_id"] in phantom_audit for o in orders if o["symbol"] == "META")}
    # activity after the failure other than the EOG sequence
    res["other_orders_after_2026-10-15"] = [(o["created_at"][:19], o["symbol"], o["intent"]) for o in orders
                                            if o["created_at"] >= "2026-10-15T20:30" and not (o["symbol"] == "EOG" and o["intent"] == "exit_target")]

    # ---- 3. cash movements ------------------------------------------------------------------------------------------------
    def cash_from(fs):
        c = INITIAL_CASH
        for f in fs:
            c += (-1 if f["side"] == "BUY" else 1) * f["quantity"] * f["price"] - (f["fees"] or 0)
        return round(c, 2)
    realized_closed = sum(p["realized_pnl"] or 0 for p in pos if p["status"] == "closed")
    invested_open = sum(p["quantity"] * p["avg_entry"] for p in pos if p["status"] == "open")
    cash_state = round(INITIAL_CASH + realized_closed - invested_open, 2)
    phantom_ids = {p["fill_id"] for p in ph}
    res["cash"] = {"cash_from_all_fills": cash_from(fills), "cash_from_fills_without_flagged": cash_from([f for f in fills if f["fill_id"] not in phantom_ids]),
                   "cash_from_positions_state": cash_state, "last_equity_row_cash": eq[-1]["cash"],
                   "delta_all_fills_vs_state": round(cash_from(fills) - cash_state, 4),
                   "delta_without_flagged_vs_state": round(cash_from([f for f in fills if f["fill_id"] not in phantom_ids]) - cash_state, 4),
                   "flagged_notional": res["phantom_candidates"]["total_notional"], "n_flagged": len(ph),
                   "expected_delta_if_only_these_are_phantom": round(sum(p["notional"] for p in ph), 2)}
    # cash by day: the equity table (state-based) vs fills cumulative to that moment, with and without the flagged fills
    daily = []
    for e in eq:
        up = [f for f in fills if f["filled_at"] <= e["recorded_at"]]
        daily.append({"session": e["session_date"], "equity_row_cash": e["cash"], "fills_cash_all": cash_from(up),
                      "fills_cash_unflagged": cash_from([f for f in up if f["fill_id"] not in phantom_ids])})
    res["cash_by_session_after_2026-10-14"] = [d for d in daily if d["session"] >= "2026-10-14"]
    res["cash_unflagged_matches_equity_rows_every_session"] = all(abs(d["equity_row_cash"] - d["fills_cash_unflagged"]) < 0.015 for d in daily)
    res["cash_all_fills_matches_equity_rows_through"] = max((d["session"] for d in daily if abs(d["equity_row_cash"] - d["fills_cash_all"]) < 0.015), default=None)

    # ---- 4. realized P&L and position state ----------------------------------------------------------------------------
    def fifo_pnl(fs):
        lots, pnl = {}, 0.0
        for f in fs:
            if f["side"] == "BUY":
                lots.setdefault(f["symbol"], []).append([f["quantity"], f["price"]])
            else:
                q = f["quantity"]
                while q > 1e-9 and lots.get(f["symbol"]):
                    lot = lots[f["symbol"]][0]; take = min(q, lot[0])
                    pnl += (f["price"] - lot[1]) * take; lot[0] -= take; q -= take
                    if lot[0] <= 1e-9: lots[f["symbol"]].pop(0)
                if q > 1e-9:
                    pnl += (f["price"] - 0.0) * q * 0  # selling shares never held: counted as unmatched below
                    lots.setdefault("_unmatched_" + f["symbol"], []).append(q)
        unmatched = round(sum(sum(v) for k, v in lots.items() if k.startswith("_unmatched_")), 6)
        return round(pnl, 4), unmatched
    pnl_all, unm_all = fifo_pnl(fills)
    pnl_unf, unm_unf = fifo_pnl([f for f in fills if f["fill_id"] not in phantom_ids])
    res["realized_pnl"] = {"positions_table_closed": round(realized_closed, 4), "fills_fifo_all": pnl_all, "fills_fifo_all_unmatched_sell_qty": unm_all,
                           "fills_fifo_without_flagged": pnl_unf, "fills_fifo_without_flagged_unmatched_sell_qty": unm_unf,
                           "positions_vs_fifo_without_flagged": round(realized_closed - pnl_unf, 4),
                           "equity_row_last_realized": eq[-1]["realized_pnl"], "oct16_realized_by_position": {p["symbol"]: p["realized_pnl"] for p in pos if p["symbol"] in ("EOG", "META")}}
    held = {}
    for f in fills:
        if f["fill_id"] in phantom_ids: continue
        held[f["symbol"]] = round(held.get(f["symbol"], 0) + (1 if f["side"] == "BUY" else -1) * f["quantity"], 6)
    res["position_state"] = {"open_positions": {p["symbol"]: p["quantity"] for p in pos if p["status"] == "open"},
                             "shares_held_by_unflagged_fills": {k: v for k, v in held.items() if abs(v) > 1e-9},
                             "match": {p["symbol"]: p["quantity"] for p in pos if p["status"] == "open"} == {k: v for k, v in held.items() if abs(v) > 1e-9},
                             "shares_if_phantoms_were_real": {k: round(v, 6) for k, v in {s: sum((1 if f["side"] == "BUY" else -1) * f["quantity"] for f in fills if f["symbol"] == s) for s in {f["symbol"] for f in fills}}.items() if abs(v) > 1e-9}}

    # ---- 5. what the host itself recorded at each close ----------------------------------------------------------------
    res["host_postmarket_reconciled_flags"] = [(r["at"][:10], json.loads(r["detail_json"]).get("reconciled")) for r in audit
                                               if r["entity"] == "system" and r["event"] == "postmarket" and r["at"] >= "2026-10-14"]
    res["signals_executed_by_engine_version_counts_only"] = {r[0]: r[1] for r in L.execute("SELECT engine_version, SUM(executed) FROM signals GROUP BY 1")}
    res["entries_after_2026-10-15"] = [r[0] for r in L.execute("SELECT created_at FROM orders WHERE intent='entry' AND created_at >= '2026-10-15T20:30'")]
    res["unflagged_fills_without_position_effect"] = [f["fill_id"] for f in fills if f["fill_id"] not in phantom_ids and f["intent"] != "entry"
                                                       and not position_open_at(f["symbol"], f["filled_at"])]
    L.close()

    # ---- 6. the real code on a copy of this ledger ------------------------------------------------------------------------
    res["deployed_b236d54_reconcile"] = sandbox_reconcile(a.deployed, ledger)
    res["fixed_branch_reconcile"] = sandbox_reconcile(a.fixed, ledger)
    Path(a.out).write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n", newline="\n")
    print(json.dumps(res, indent=1, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
