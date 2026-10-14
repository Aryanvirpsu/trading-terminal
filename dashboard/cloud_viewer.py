"""Read-only viewer for the mirrored CLOUD paper ledger (Case 1) — a separate process on its own port.

    python dashboard/cloud_viewer.py            # http://127.0.0.1:5058 ; the main dashboard proxies /api/cloud/* here

Why a separate process: the paper modules (lab/paper/*) keep a process-wide ledger connection chosen at import
time. Running them against the mirror inside the main dashboard process would share that state with the LOCAL
ledger (Case 2). Here the whole process is pointed at the mirror, so the exact same accounting/report code answers
for the cloud data (identical numbers to what the cloud computes) while the local ledger cannot be reached:

  * PAPER_DATA_DIR  -> a throw-away COPY of the verified cloud ledger (the pristine file is never opened for write)
  * HOME/USERPROFILE/TVMCP_DATA_DIR -> a folder inside the mirror, so any stray "~/.tradingview_mcp_data" write
    lands there, never in the real data directory
  * ROBINHOOD_TRADING_ENABLED=false, BROKER_PROVIDER=none (no broker client can be built)
  * startup REFUSES if the resolved ledger directory is the real local ledger directory

The mirror is produced by scripts/sync_cloud_data.py. When a newer mirror is installed this process switches to it
on the next request — no restart needed. The shadow evidence DB is opened with SQLite `mode=ro&immutable=1`.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
_REAL_HOME_DATA = Path(os.environ.get("TVMCP_DATA_DIR", "~/.tradingview_mcp_data")).expanduser()
MIRROR = Path(os.environ.get("AVDI_MIRROR_DIR", "~/.avdi_cloud_mirror")).expanduser()   # outside the local data dir, on purpose
REAL_LOCAL_LEDGER_DIR = (_REAL_HOME_DATA / "paper").resolve()

for _p in (ROOT / "lab", ROOT / "src", HERE):
    sys.path.insert(0, str(_p))

from flask import Flask, jsonify  # noqa: E402

app = Flask(__name__)
_LOCK = threading.RLock()
_STATE: dict = {"name": None, "ptr": None, "db": None}


def _pointer() -> dict | None:
    try:
        return json.loads((MIRROR / "current.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _mirror_mark_for(ptr: dict):
    """A mark function that prices open positions AS OF THE BACKUP (the last 5-minute bar at or before it, from the
    backup's own shadow bars), never at today's live price. A mirror is a snapshot: mixing yesterday's ledger with
    today's quotes would show an equity the cloud never had. Falls back to the last daily bar, then the last fill."""
    shadow = Path(ptr["path"]) / ptr["shadow_file"] if ptr.get("shadow_file") else None
    asof = ptr.get("backup_created_at")
    try:
        asof_dt = datetime.fromisoformat(str(asof).replace("Z", "+00:00")) if asof else None
    except ValueError:
        asof_dt = None

    def mark(symbol: str):
        if shadow and shadow.exists():
            con = sqlite3.connect(f"file:{shadow.as_posix()}?mode=ro&immutable=1", uri=True)
            try:
                for ts, c in con.execute("SELECT ts, c FROM shadow_bars_5m WHERE symbol=? ORDER BY ts DESC LIMIT 400", (symbol,)):
                    try:
                        if asof_dt is None or datetime.fromisoformat(ts) <= asof_dt:
                            return float(c), "backup_5m_bar"
                    except ValueError:
                        continue
                row = con.execute("SELECT c FROM shadow_bars_d WHERE symbol=? ORDER BY day DESC LIMIT 1", (symbol,)).fetchone()
                if row:
                    return float(row[0]), "backup_daily_bar"
            except sqlite3.Error:
                pass
            finally:
                con.close()
        from paper import risk
        return risk._last_mark(symbol), "last_fill"
    return mark


def _activate(ptr: dict) -> None:
    """Point this process (env + the paper db module) at the mirror's working copy."""
    work = Path(ptr["work_dir"]).resolve()
    if work == REAL_LOCAL_LEDGER_DIR or REAL_LOCAL_LEDGER_DIR in work.parents:
        raise SystemExit(f"refusing to run: the mirror work dir {work} is inside the local ledger directory")
    home = work.parent / "home"
    home.mkdir(exist_ok=True)
    os.environ.update({
        "PAPER_DATA_DIR": str(work), "PAPER_LEDGER": ptr["ledger_name"],
        "TVMCP_DATA_DIR": str(home / ".tradingview_mcp_data"), "HOME": str(home), "USERPROFILE": str(home),
        "ROBINHOOD_TRADING_ENABLED": "false", "BROKER_PROVIDER": "none",
    })
    from paper import db, risk  # noqa: PLC0415 — must be imported only AFTER the env above is set
    db.close()
    db._DATA_DIR = str(work)
    risk._live_mark_src = _mirror_mark_for(ptr)   # account_state / account() both price through this: snapshot marks, not live
    _STATE.update(name=ptr["name"], ptr=ptr, db=db)


def _ensure() -> dict | None:
    """Follow current.json: the first mirror, or a newer one installed while we run."""
    ptr = _pointer()
    if not ptr:
        return None
    with _LOCK:
        if ptr["name"] != _STATE["name"]:
            _activate(ptr)
    return ptr


def _clean(o):
    if isinstance(o, float):
        return None if (math.isnan(o) or math.isinf(o)) else o
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


def _no_mirror():
    resp = jsonify({"state": "no_mirror", "reason": "No cloud mirror installed yet. Run: python scripts/sync_cloud_data.py"})
    resp.status_code = 503
    return resp


def _age(iso: str | None):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(str(iso).replace("Z", "+00:00"))).total_seconds()
    except (ValueError, TypeError):
        return None


@app.route("/api/cloud/meta")
def meta():
    ptr = _ensure()
    if not ptr:
        return jsonify({"state": "no_mirror", "mirror_dir": str(MIRROR),
                        "reason": "No cloud mirror installed yet. Run: python scripts/sync_cloud_data.py"})
    snap = None
    try:
        con = sqlite3.connect(f"file:{(Path(ptr['path']) / ptr['ledger_file']).as_posix()}?mode=ro&immutable=1", uri=True)
        con.row_factory = sqlite3.Row
        r = con.execute("SELECT session_date, recorded_at, cash, positions_value, equity, realized_pnl, unrealized_pnl, drawdown_pct FROM equity ORDER BY session_date DESC LIMIT 1").fetchone()
        snap = dict(r) if r else None
        con.close()
    except sqlite3.Error:
        pass
    return jsonify(_clean({
        "equity_snapshot": snap,
        "state": "ok", "name": ptr["name"], "ledger": ptr["ledger_name"], "has_shadow": bool(ptr.get("shadow_file")),
        "backup_created_at": ptr.get("backup_created_at"), "backup_age_seconds": _age(ptr.get("backup_created_at")),
        "synced_at": ptr.get("synced_at"), "synced_age_seconds": _age(ptr.get("synced_at")),
        "source": ptr.get("source"), "verified": ptr.get("verified"), "files": ptr.get("files"),
        "engine_version": ptr.get("engine_version"), "runtime_version": ptr.get("runtime_version"),
        "host_evidence": ptr.get("host_evidence"), "ledger_summary": ptr.get("ledger_summary"),
        "read_only": True, "separate_from_local_ledger": True,
    }))


# ── the same paper endpoints the local dashboard serves, answered from the mirrored ledger ─────────────────────
@app.route("/api/cloud/paper/account")
def paper_account():
    if not _ensure():
        return _no_mirror()
    from paper import broker, config, db
    return jsonify(_clean({
        "account_type": "paper", "is_simulated": True, "is_real_money": False, "label": "Cloud paper ledger (read-only mirror)",
        "as_of": (_STATE["ptr"] or {}).get("backup_created_at"), "mark_basis": "backup snapshot — last 5-minute bar at or before the backup, not live prices",
        **broker.account(), "config": config.snapshot(), "reconciliation": broker.reconcile(),
        "schema_version": db.schema_version(), "config_version": db.config_version(),
    }))


@app.route("/api/cloud/paper/report")
def paper_report():
    from flask import request
    if not _ensure():
        return _no_mirror()
    from paper import report
    d = request.args.get("date")
    return jsonify(_clean(report.daily(d) if d else report.daily()))


@app.route("/api/cloud/paper/performance")
def paper_performance():
    if not _ensure():
        return _no_mirror()
    from paper import report
    return jsonify(_clean(report.performance()))


@app.route("/api/cloud/paper/signals")
def paper_signals():
    from flask import request
    if not _ensure():
        return _no_mirror()
    from paper import journal
    return jsonify(_clean({
        "signals": journal.signals(session_date=request.args.get("date"), action=request.args.get("action"),
                                   limit=int(request.args.get("limit", 100))),
        "gate_outcomes": journal.gate_outcomes(), "blocked_winners": journal.blocked_winners(),
    }))


@app.route("/api/cloud/paper/options_shadow")
def paper_options_shadow():
    if not _ensure():
        return _no_mirror()
    from paper import options_shadow
    return jsonify(_clean({"summary": options_shadow.summary(), "graduation": options_shadow.graduation_readiness()}))


# ── what the cloud runtime has been doing: the append-only shadow evidence DB ──────────────────────────────────
def _rows(con: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    try:
        cur = con.execute(sql, params)
    except sqlite3.OperationalError:
        return []
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _json_field(row: dict, key: str) -> None:
    try:
        row[key] = json.loads(row[key]) if row.get(key) else None
    except (ValueError, TypeError):
        pass


@app.route("/api/cloud/runtime")
def runtime():
    ptr = _ensure()
    if not ptr:
        return _no_mirror()
    if not ptr.get("shadow_file"):
        return jsonify({"state": "unavailable", "reason": "this backup has no shadow evidence database"})
    path = Path(ptr["path"]) / ptr["shadow_file"]
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True)
    try:
        boundary = _rows(con, "SELECT * FROM shadow_evidence_boundary ORDER BY at")
        sessions = _rows(con, "SELECT session_date, session_type, COUNT(*) AS cycles, MAX(finalists) AS max_finalists, "
                              "MAX(eligible) AS max_eligible FROM shadow_cycles GROUP BY session_date, session_type "
                              "ORDER BY session_date DESC, session_type LIMIT 60")
        cycles = _rows(con, "SELECT cycle_id, session_date, scan_ts, session_type, finalists, eligible, is_choice_event, "
                            "funnel_json, capacity_json, note FROM shadow_cycles ORDER BY scan_ts DESC LIMIT 30")
        for c in cycles:
            _json_field(c, "funnel_json"); _json_field(c, "capacity_json")
        latest = _rows(con, "SELECT cycle_id FROM shadow_cycles WHERE session_type='regular' ORDER BY scan_ts DESC LIMIT 1") \
            or _rows(con, "SELECT cycle_id FROM shadow_cycles ORDER BY scan_ts DESC LIMIT 1")
        cands = _rows(con, "SELECT symbol, sector, strategy, scanner_rank, decision, failed_gates, quality, expected_r, rr, entry, stop, "
                           "target, spread_pct, eligible, champion_executed, champion_reason, capacity_reason, scan_ts "
                           "FROM shadow_candidates WHERE cycle_id=? ORDER BY scanner_rank LIMIT 40",
                      (latest[0]["cycle_id"],)) if latest else []
        ch001 = _rows(con, "SELECT symbol, avg_entry, stop, target, opened_at, closed_at, original_exit_reason, original_R, "
                           "plus1r_occurred, plus1r_ts, be_exit_price, be_R, delta_R, ambiguous, ambiguity_note FROM shadow_ch001_results "
                           "ORDER BY closed_at DESC LIMIT 50")
        counts = {t: (_rows(con, f"SELECT COUNT(*) AS n FROM {t}") or [{"n": None}])[0]["n"]
                  for t in ("shadow_cycles", "shadow_events", "shadow_candidates", "shadow_picks", "shadow_bars_d", "shadow_bars_5m",
                            "shadow_ch001_obs", "shadow_ch001_results")}
        maintenance = _rows(con, "SELECT at, action, detail_json FROM shadow_maintenance_log ORDER BY at DESC LIMIT 10")
    finally:
        con.close()

    v11 = next((b for b in boundary if b.get("version") == "v1.1"), None)
    pnl = None
    if v11 and v11.get("equity") is not None:
        from paper import broker
        pnl = {"boundary_equity": v11["equity"], "boundary_at": v11.get("at"), "equity_now": broker.account().get("equity")}
        pnl["v11_pnl_recomputed"] = (pnl["equity_now"] - v11["equity"]) if pnl["equity_now"] is not None else None
        host = ((ptr.get("host_evidence") or {}).get("v11_pnl"))
        pnl["v11_pnl_host"] = host
        pnl["v11_pnl"] = host if host is not None else pnl["v11_pnl_recomputed"]
    return jsonify(_clean({
        "state": "ok", "as_of_backup": ptr.get("backup_created_at"), "evidence_boundary": boundary, "v11": pnl,
        "sessions": sessions, "latest_cycle_id": latest[0]["cycle_id"] if latest else None, "latest_candidates": cands,
        "recent_cycles": cycles, "ch001_results": ch001, "counts": counts, "maintenance": maintenance,
    }))


@app.route("/api/cloud/health")
def health():
    return jsonify({"ok": True, "mirror": bool(_pointer())})


if __name__ == "__main__":
    port = int(os.environ.get("CLOUD_VIEWER_PORT", "5058"))
    _ensure()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
