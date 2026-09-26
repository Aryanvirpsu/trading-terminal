import hashlib
import json
import os
import sqlite3
import sys

sys.path[:0] = ["/app/lab", "/app/dashboard", "/app/src"]

LEDGER = "/data/case1/paper/robinhood_500_baseline.db"
SHADOW = "/data/case1/paper/shadow/shadow_candidates.db"


def rows_hash(conn, table, order):
    rows = conn.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall()
    h = hashlib.sha256()
    for r in rows:
        h.update(repr(tuple(r)).encode())
    return {"n": len(rows), "sha256": h.hexdigest()}


led = sqlite3.connect("file:" + LEDGER + "?mode=ro", uri=True)
led.row_factory = sqlite3.Row
sh = sqlite3.connect("file:" + SHADOW + "?mode=ro", uri=True)
sh.row_factory = sqlite3.Row

snap = {}
snap["ledger"] = {t: rows_hash(led, t, o) for t, o in (("signals", "signal_id"), ("orders", "order_id"), ("fills", "fill_id"),
                                                         ("positions", "position_id"), ("equity", "session_date"))}
snap["ledger"]["cash_from_fills"] = led.execute("SELECT cash FROM equity ORDER BY session_date DESC LIMIT 1").fetchone()["cash"]
snap["last_equity_row"] = dict(led.execute("SELECT * FROM equity ORDER BY session_date DESC LIMIT 1").fetchone())
snap["open_positions"] = [dict(r) for r in led.execute(
    "SELECT symbol, quantity, avg_entry, stop, target, status, opened_at, signal_id FROM positions WHERE status='open' ORDER BY symbol")]
snap["orders_today"] = [r["order_id"] for r in led.execute("SELECT order_id FROM orders WHERE session_date='2026-09-25' ORDER BY order_id")]
snap["entries_2026_09_25"] = led.execute(
    "SELECT COUNT(*) n FROM orders WHERE session_date='2026-09-25' AND intent='entry' AND status IN ('filled','partial')").fetchone()["n"]
snap["shadow"] = {t: rows_hash(sh, t, o) for t, o in (
    ("shadow_cycles", "cycle_id"), ("shadow_candidates", "cand_id"), ("shadow_events", "event_id"),
    ("shadow_evidence_boundary", "version"), ("shadow_ch001_quarantine", "quarantined_at, source_table"),
    ("shadow_maintenance_log", "at"))}
snap["shadow"]["bars_5m"] = sh.execute("SELECT COUNT(*) n FROM shadow_bars_5m").fetchone()["n"]
snap["boundary"] = dict(sh.execute("SELECT * FROM shadow_evidence_boundary WHERE version='v1.1'").fetchone())
st = json.load(open("/data/case1/paper/runtime/runtime_state.json"))
snap["runtime_state"] = {"done": len(st["done"]), "missed": len(st["missed"]), "cycle_log": len(st.get("cycle_log", [])),
                        "discovery_runs": st["counters"]["discovery_runs"], "last_close": (st.get("last_close") or {}).get("at"),
                        "last_backup": (st.get("last_backup") or {}).get("at")}
snap["env"] = {k: os.environ.get(k) for k in ("ROBINHOOD_TRADING_ENABLED", "BROKER_PROVIDER", "AVDI_CODE_VERSION", "TZ", "PAPER_DATA_DIR")}
from paper import broker, db
snap["reconciled"] = broker.reconcile()
snap["config_version"] = db.config_version()
print(json.dumps(snap, default=str))
