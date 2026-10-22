"""Prove that a LATER ledger snapshot only appended to an EARLIER one (no historical record was rewritten or deleted).

    python -I verify_append_only.py --old ~/.avdi_cloud_mirror/20261021T202501Z --new ~/.avdi_cloud_mirror/<later> [--out result.json]

For each table, every row of the old snapshot (by primary key) must exist in the new one with identical values.  Expected, allowed
changes are reported separately and explicitly, never silently:
  * positions: an OPEN position may change status/closed_at/avg_exit/realized_pnl/exit_reason/quantity/fees (it was closed or reduced),
    and mfe/mae (excursion tracking).  A CLOSED position may not change at all.
  * orders: a 'pending'/'partial' order may change status/filled_qty/avg_fill/fees/updated_at/reject_reason.  A terminal order may not.
  * equity: the row for a session may be re-written only while that session was the last one in the old snapshot.
  * signals: the TRACKER-OWNED columns (outcome, outcome_at, outcome_pnl_ps, mfe, mae, tracked_until) may be updated while the
    signal was still 'open'.  Their VALUES ARE NEVER READ OR PRINTED by this tool (only the column names that changed), so it can be
    run without looking at any signal outcome.  Every decision-time column of a signal must be identical.
Everything else (fills, audit, and all closed/terminal rows) must be byte-identical.  Reads both databases mode=ro&immutable=1.
Exit code 0 = append-only, 1 = a historical record changed or vanished.
"""
import argparse, json, sqlite3, sys
from pathlib import Path

PK = {"signals": "signal_id", "orders": "order_id", "fills": "fill_id", "positions": "position_id", "audit": "audit_id", "equity": "session_date"}
OPEN_POS_MAY_CHANGE = {"status", "closed_at", "avg_exit", "realized_pnl", "exit_reason", "quantity", "fees", "mfe", "mae"}
LIVE_ORDER_MAY_CHANGE = {"status", "filled_qty", "avg_fill", "fees", "updated_at", "reject_reason"}
SIGNAL_TRACKER_COLS = {"outcome", "outcome_at", "outcome_pnl_ps", "mfe", "mae", "tracked_until"}


def ro(p):
    c = sqlite3.connect(f"file:{Path(p).as_posix()}?mode=ro&immutable=1", uri=True)
    c.row_factory = sqlite3.Row
    return c


def rows(c, t):
    return {r[PK[t]]: dict(r) for r in c.execute(f"SELECT * FROM {t}")}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--old", required=True); ap.add_argument("--new", required=True); ap.add_argument("--out")
    a = ap.parse_args()
    old = ro(Path(a.old).expanduser() / "robinhood_500_baseline.db"); new = ro(Path(a.new).expanduser() / "robinhood_500_baseline.db")
    res = {"old": Path(a.old).name, "new": Path(a.new).name, "tables": {}, "violations": []}
    for t in PK:
        o, n = rows(old, t), rows(new, t)
        last_equity = max(o) if t == "equity" and o else None
        allowed, appended = [], len(set(n) - set(o))
        for k, orow in o.items():
            if k not in n:
                res["violations"].append({"table": t, "key": k, "problem": "row deleted"}); continue
            diff = {c: (orow[c], n[k][c]) for c in orow if orow[c] != n[k].get(c)}
            if not diff:
                continue
            ok = False
            if t == "positions" and orow["status"] == "open" and set(diff) <= OPEN_POS_MAY_CHANGE: ok = True
            if t == "orders" and orow["status"] in ("pending", "partial") and set(diff) <= LIVE_ORDER_MAY_CHANGE: ok = True
            if t == "equity" and k == last_equity: ok = True
            if t == "signals":
                decision_time = {c for c in diff if c not in SIGNAL_TRACKER_COLS}
                ok = not decision_time and orow.get("outcome") == "open"
                diff = {c: v for c, v in diff.items() if c in decision_time} if decision_time else diff
                # never carry outcome values anywhere: column names only
                (allowed if ok else res["violations"]).append({"table": t, "key": k, "changed": sorted(diff), **({} if ok else {"problem": "historical record changed (values withheld)"})})
                continue
            (allowed if ok else res["violations"]).append({"table": t, "key": k, "changed": sorted(diff), **({} if ok else {"problem": "historical record changed", "values": {c: [str(x)[:40] for x in v] for c, v in diff.items()}})})
        res["tables"][t] = {"old_rows": len(o), "new_rows": len(n), "appended": appended, "allowed_state_changes": allowed}
    res["append_only"] = not res["violations"]
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n", newline="\n")
    print(json.dumps({"append_only": res["append_only"], "violations": res["violations"][:10],
                      "per_table": {t: {k: (v if k != "allowed_state_changes" else len(v)) for k, v in s.items()} for t, s in res["tables"].items()}}, indent=1, default=str))
    return 0 if res["append_only"] else 1


if __name__ == "__main__":
    sys.exit(main())
