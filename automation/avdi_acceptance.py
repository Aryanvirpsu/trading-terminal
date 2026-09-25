"""AVDI live-acceptance extraction: one JSON document describing a session, read-only.

    docker exec avdi-runtime python automation/avdi_acceptance.py [YYYY-MM-DD]

Reads the paper ledger, the shadow log and the runtime state (never writes). Used for UBUNTU_LIVE_ACCEPTANCE_01.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("lab", "dashboard", "src"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

from paper import db, runtime as rt, shadow_log  # noqa: E402
from paper import market_calendar as cal  # noqa: E402


def _ro(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def main(argv=None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    d = dt.date.fromisoformat(a[0]) if a else dt.datetime.now(cal.ET).date()
    day = d.isoformat()
    out = {"date": day, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    state = rt.load_state()
    slots = rt.build_slots(d)
    done = {k.split(":", 1)[1]: v for k, v in state.get("done", {}).items() if k.startswith(day)}
    missed = {k.split(":", 1)[1]: v for k, v in state.get("missed", {}).items() if k.startswith(day)}
    disc = [s for s in slots if s.kind == "discovery"]
    out["schedule"] = {
        "expected_discovery_cycles": len(disc), "expected_entry_cycles": sum(s.allow_entries for s in disc),
        "slot_status": {s.id: (done.get(s.id, {}).get("status") or ("missed" if s.id in missed else "pending"))
                        for s in slots}}
    log = [c for c in state.get("cycle_log", []) if c["cycle_id"].startswith(day)]
    secs = [c["seconds"] for c in log]
    out["discovery_cycles"] = {
        "completed": len([c for c in log if c.get("state") == "ok"]), "logged": len(log),
        "avg_seconds": round(sum(secs) / len(secs), 1) if secs else None, "max_seconds": max(secs) if secs else None,
        "max_late_s": max((c["late_s"] for c in log), default=None), "cycles": log}
    out["runtime_state"] = {k: state.get(k) for k in ("started_at", "restore_ok", "counters", "last_tracker",
                                                     "last_discovery", "last_backup", "last_exception", "prep")}
    out["runtime_state"]["errors_today"] = [e for e in state.get("errors", []) if e["at"].startswith(day)]
    out["slots_missed"] = missed

    sc = _ro(shadow_log.path())
    cyc = [dict(r) for r in sc.execute("SELECT * FROM shadow_cycles WHERE session_date=? ORDER BY scan_ts", (day,))]
    for c in cyc:
        c["funnel"] = json.loads(c.pop("funnel_json") or "{}")
        c["capacity"] = json.loads(c.pop("capacity_json") or "{}")
    out["shadow_cycles"] = cyc
    obs = [dict(r) for r in sc.execute(
        "SELECT cand_id, cycle_id, event_id, scan_ts, session_type, symbol, sector, decision, failed_gates, quality, "
        "expected_r, eligible, champion_executed, champion_reason, capacity_reason, quote_provider, quote_source_ts, "
        "spread_pct, entry, stop, target, signal_id FROM shadow_candidates WHERE session_date=? ORDER BY scan_ts, symbol",
        (day,))]
    for o in obs:
        try:
            ts = dt.datetime.fromisoformat(o["scan_ts"])
            o["quote_age_s_at_scan"] = round(ts.timestamp() - o["quote_source_ts"], 1) if o["quote_source_ts"] else None
        except Exception:
            o["quote_age_s_at_scan"] = None
    out["observations"] = obs
    real = [o for o in obs if o["session_type"] not in ("manual",)]
    out["evidence_counts"] = {
        "raw_observations": len(real), "unique_events": len({o["event_id"] for o in real}),
        "unique_symbols": len({o["symbol"] for o in real}),
        "by_decision": {k: sum(1 for o in real if o["decision"] == k) for k in ("TRADEABLE", "MONITOR", "REJECT")}}
    trans = {}
    for o in real:
        trans.setdefault(o["symbol"], []).append((o["scan_ts"][11:16], o["decision"]))
    out["state_transitions"] = {s: [f"{t}:{x}" for t, x in v] for s, v in trans.items()
                                if len({x for _, x in v}) > 1}
    out["provider_freshness"] = {
        "max_quote_age_s_at_scan": max((o["quote_age_s_at_scan"] for o in real if o["quote_age_s_at_scan"] is not None), default=None),
        "providers": sorted({o["quote_provider"] for o in real if o["quote_provider"]})}
    out["bars"] = {"daily": sc.execute("SELECT COUNT(*) n FROM shadow_bars_d").fetchone()["n"],
                   "5m_total": sc.execute("SELECT COUNT(*) n FROM shadow_bars_5m").fetchone()["n"],
                   "5m_latest": sc.execute("SELECT MAX(ts) t FROM shadow_bars_5m").fetchone()["t"]}
    out["ch001"] = {
        "observations": [dict(r) for r in sc.execute("SELECT * FROM shadow_ch001_obs")],
        "results": [dict(r) for r in sc.execute("SELECT * FROM shadow_ch001_results")]}
    sc.close()

    lc = _ro(db.db_path())
    out["ledger"] = {
        "signals_today": [dict(r) for r in lc.execute(
            "SELECT signal_id, symbol, action, quality, executed, order_id, engine_version, created_at FROM signals "
            "WHERE session_date=? ORDER BY created_at", (day,))],
        "orders_today": [dict(r) for r in lc.execute(
            "SELECT order_id, symbol, side, order_type, quantity, status, avg_fill, intent, created_at FROM orders "
            "WHERE session_date=? ORDER BY created_at", (day,))],
        "fills_today": [dict(r) for r in lc.execute(
            "SELECT f.* FROM fills f JOIN orders o ON o.order_id=f.order_id WHERE o.session_date=?", (day,))],
        "open_positions": [dict(r) for r in lc.execute("SELECT * FROM positions WHERE status='open'")],
        "counts": {t: lc.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"]
                   for t in ("signals", "orders", "fills", "positions", "audit")},
        "duplicate_entries": [dict(r) for r in lc.execute(
            "SELECT symbol, COUNT(*) n FROM orders WHERE session_date=? AND intent='entry' GROUP BY symbol HAVING n>1", (day,))],
        "refusals": [{"signal_id": r["entity_id"], "event": r["event"], "detail": (r["detail_json"] or "")[:300]}
                     for r in lc.execute(
                         "SELECT entity_id, event, detail_json FROM audit WHERE substr(at,1,10)>=? AND event IN "
                         "('not_executed','risk_blocked','unaffordable','entry_refused','not_executable','entries_disabled')",
                         (day,))],
        "audit_failures": [dict(r) for r in lc.execute(
            "SELECT at, event, detail_json FROM audit WHERE event LIKE '%failed%' ORDER BY audit_id DESC LIMIT 20")]}
    lc.close()
    out["evidence_boundary"] = shadow_log.evidence_summary("v1.1")
    out["health"] = rt.health()
    bdir = rt.backup_dir()
    out["backups"] = sorted(os.listdir(bdir))[-6:] if os.path.isdir(bdir) else []
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
