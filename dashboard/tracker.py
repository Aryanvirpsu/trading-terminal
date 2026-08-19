"""Phase 10 — persistent setup tracker.

Tracks selected setups forward through the session and into the next one. It
records, it never trades: there is no order path in this module, and the alert
types deliberately stop at "entry zone reached" rather than "entered".

Storage is SQLite in `~/.tradingview_mcp_data/` (outside the repo, like every
other ledger here), with a versioned schema and WAL. Every status change writes
a row to `setup_events` with the reason and the data timestamps behind it, so the
history of a setup is auditable rather than just its current state.

Alerts are deduplicated and rate-limited: the same alert type for the same setup
will not fire again inside its cooldown, which is what stops a price oscillating
across the entry zone from producing forty notifications.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in ("..", os.path.join("..", "lab"), os.path.join("..", "src")):
    sys.path.insert(0, os.path.join(_HERE, _p))
sys.path.insert(0, _HERE)

import research as _R
import market_regime as _MR

_DATA_DIR = os.path.expanduser(os.environ.get("TVMCP_DATA_DIR", "~/.tradingview_mcp_data"))
DB_PATH = os.environ.get("TRACKER_DB", os.path.join(_DATA_DIR, "setup_tracker.db"))
SCHEMA_VERSION = 1

STATUSES = ("scanning", "watch", "approaching_entry", "entry_triggered", "invalidated",
            "target_1_reached", "target_2_reached", "expired", "data_unavailable",
            "manually_closed")
TERMINAL = ("invalidated", "target_2_reached", "expired", "manually_closed")

ALERT_TYPES = ("entry_zone_reached", "breakout_confirmed", "invalidation_breached",
               "target_1_reached", "target_2_reached", "score_deterioration",
               "new_catalyst", "abnormal_volume", "option_spread_deterioration",
               "contract_unusable", "data_source_failure")

_LOCK = threading.Lock()


def _cfg() -> Dict[str, Any]:
    def _f(n, d):
        try:
            return float(os.environ.get(n, d))
        except (TypeError, ValueError):
            return float(d)
    return {
        "alert_cooldown_seconds": _f("TRACK_ALERT_COOLDOWN_S", 1800),
        "approach_atr": _f("TRACK_APPROACH_ATR", 0.5),
        "score_drop_alert": _f("TRACK_SCORE_DROP", 12),
        "abnormal_rvol": _f("TRACK_ABNORMAL_RVOL", 2.5),
        "expire_after_days": _f("TRACK_EXPIRE_DAYS", 5),
        "stale_quote_hours": _f("TRACK_STALE_QUOTE_H", 30),
        "max_tracked": int(_f("TRACK_MAX_ACTIVE", 40)),
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init_db() -> None:
    with _LOCK, _conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS schema_meta (k TEXT PRIMARY KEY, v TEXT)")
        c.execute("""CREATE TABLE IF NOT EXISTS setups (
            setup_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            setup_type TEXT,
            created_at TEXT NOT NULL,
            original_price REAL,
            entry_low REAL, entry_high REAL,
            invalidation REAL,
            target_1 REAL, target_2 REAL,
            catalyst TEXT,
            expected_holding_period TEXT,
            verdict TEXT,
            contract TEXT,
            initial_score REAL,
            current_score REAL,
            status TEXT NOT NULL,
            last_checked TEXT,
            last_price REAL,
            data_source_timestamp TEXT,
            source_timestamps TEXT,
            payload TEXT,
            closed_at TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS setup_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            setup_id TEXT NOT NULL REFERENCES setups(setup_id) ON DELETE CASCADE,
            at TEXT NOT NULL,
            kind TEXT NOT NULL,
            from_status TEXT, to_status TEXT,
            reason TEXT,
            price REAL,
            score REAL,
            data_source_timestamp TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            setup_id TEXT NOT NULL REFERENCES setups(setup_id) ON DELETE CASCADE,
            at TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            message TEXT,
            price REAL,
            acknowledged INTEGER NOT NULL DEFAULT 0
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS ix_setups_status ON setups(status)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_events_setup ON setup_events(setup_id, at)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_alerts_setup ON alerts(setup_id, alert_type, at)")
        c.execute("INSERT OR REPLACE INTO schema_meta (k, v) VALUES ('version', ?)",
                  (str(SCHEMA_VERSION),))


def _event(c: sqlite3.Connection, setup_id: str, kind: str, *, reason: str,
           from_status: Optional[str] = None, to_status: Optional[str] = None,
           price: Optional[float] = None, score: Optional[float] = None,
           ts: Optional[str] = None) -> None:
    c.execute("""INSERT INTO setup_events (setup_id, at, kind, from_status, to_status,
                 reason, price, score, data_source_timestamp)
                 VALUES (?,?,?,?,?,?,?,?,?)""",
              (setup_id, _now(), kind, from_status, to_status, reason, price, score, ts))


def _alert(c: sqlite3.Connection, setup_id: str, alert_type: str, message: str,
           price: Optional[float], cooldown: float) -> bool:
    """Record an alert unless an identical one fired inside the cooldown.
    Returns True if it was actually recorded."""
    if alert_type not in ALERT_TYPES:
        return False
    row = c.execute("""SELECT at FROM alerts WHERE setup_id=? AND alert_type=?
                       ORDER BY at DESC LIMIT 1""", (setup_id, alert_type)).fetchone()
    if row:
        try:
            last = datetime.fromisoformat(row["at"])
            if (datetime.now(timezone.utc) - last).total_seconds() < cooldown:
                return False           # duplicate inside the cooldown — suppressed
        except ValueError:
            pass
    c.execute("INSERT INTO alerts (setup_id, at, alert_type, message, price) VALUES (?,?,?,?,?)",
              (setup_id, _now(), alert_type, message, price))
    return True


# ── Creating setups ──────────────────────────────────────────────────────────

def add_setup(candidate: Dict[str, Any], *, verdict: Optional[Dict[str, Any]] = None,
              contract: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Track a scanner candidate. Idempotent per (symbol, setup type, day): re-running
    a scan does not create a second row for the same idea."""
    init_db()
    lv = candidate.get("levels") or {}
    ind = candidate.get("indicators") or {}
    if lv.get("state") != "ok":
        return {"state": "rejected", "reason": "candidate has no tradeable levels"}
    sym = candidate["symbol"]
    stype = candidate.get("primary_setup") or "unspecified"
    day = datetime.now(timezone.utc).date().isoformat()
    with _LOCK, _conn() as c:
        dupe = c.execute("""SELECT setup_id, status FROM setups
                            WHERE symbol=? AND setup_type=? AND substr(created_at,1,10)=?""",
                         (sym, stype, day)).fetchone()
        if dupe:
            return {"state": "exists", "setup_id": dupe["setup_id"], "status": dupe["status"]}
        n_active = c.execute("SELECT COUNT(*) n FROM setups WHERE status NOT IN "
                             "('invalidated','target_2_reached','expired','manually_closed')"
                             ).fetchone()["n"]
        if n_active >= _cfg()["max_tracked"]:
            return {"state": "rejected", "reason": f"tracker at its {_cfg()['max_tracked']}-setup limit"}
        sid = uuid.uuid4().hex[:16]
        ez = lv.get("entry_zone") or [None, None]
        sc = (candidate.get("score") or {}).get("total")
        cat = candidate.get("catalyst") or {}
        c.execute("""INSERT INTO setups (setup_id, symbol, setup_type, created_at, original_price,
                     entry_low, entry_high, invalidation, target_1, target_2, catalyst,
                     expected_holding_period, verdict, contract, initial_score, current_score,
                     status, last_checked, last_price, data_source_timestamp, source_timestamps, payload)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (sid, sym, stype, _now(), ind.get("price"), ez[0], ez[1],
                   lv.get("invalidation"), lv.get("target_1"), lv.get("target_2"),
                   json.dumps({"lean": cat.get("lean"),
                               "items": [i.get("title") for i in (cat.get("items") or [])[:3]],
                               "state": cat.get("state")}),
                   (candidate.get("setups") or [{}])[0].get("horizon"),
                   (verdict or {}).get("verdict"),
                   json.dumps({k: (contract or {}).get(k) for k in
                               ("contract_id", "expiry", "strike", "side", "limit_price",
                                "max_loss", "break_even", "spread_pct", "open_interest",
                                "delta", "theta", "quote_timestamp")} if contract else None),
                   sc, sc, "watch", _now(), ind.get("price"),
                   ind.get("source_timestamp"),
                   json.dumps({"bars": ind.get("source_timestamp"),
                               "quote": (candidate.get("quote") or {}).get("source_timestamp"),
                               "contract": (contract or {}).get("quote_timestamp")}),
                   json.dumps({"setups": candidate.get("setups"),
                               "score": candidate.get("score"),
                               "sector": candidate.get("sector"),
                               "levels": lv})))
        _event(c, sid, "created", reason=f"added from scan as {stype}", to_status="watch",
               price=ind.get("price"), score=sc, ts=ind.get("source_timestamp"))
    return {"state": "added", "setup_id": sid, "symbol": sym, "status": "watch"}


# ── Updating ─────────────────────────────────────────────────────────────────

def _transition(c, row, new_status: str, reason: str, price: Optional[float],
                score: Optional[float], ts: Optional[str]) -> bool:
    """Returns True only if the status ACTUALLY changed, so callers can report the
    number of real changes rather than the number of re-checks."""
    if row["status"] == new_status:
        return False
    c.execute("UPDATE setups SET status=?, closed_at=? WHERE setup_id=?",
              (new_status, _now() if new_status in TERMINAL else None, row["setup_id"]))
    _event(c, row["setup_id"], "status_change", reason=reason, from_status=row["status"],
           to_status=new_status, price=price, score=score, ts=ts)
    return True


def update_all(*, rescore: bool = False) -> Dict[str, Any]:
    """Re-check every active setup against a fresh quote. NEVER places an order."""
    init_db()
    cfg = _cfg()
    session = _MR.session_state()
    with _LOCK, _conn() as c:
        rows = c.execute("SELECT * FROM setups WHERE status NOT IN "
                         "('invalidated','target_2_reached','expired','manually_closed')"
                         ).fetchall()
    if not rows:
        return {"state": "ok", "checked": 0, "updated": 0, "alerts": 0, "session": session,
                "changes": [], "checked_at": _now(), "note": "no active setups",
                "order_placement": "disabled — this service has no order path"}

    syms = sorted({r["symbol"] for r in rows})
    quotes = _R.quotes(syms, ttl=30, timeout=40.0)
    import freshness as _fr

    updated = alerts_fired = 0
    details: List[Dict[str, Any]] = []
    with _LOCK, _conn() as c:
        for r in rows:
            q = quotes.get(r["symbol"]) or {}
            sid = r["setup_id"]
            px = q.get("price")
            ts = q.get("source_timestamp")
            age = _fr.bar_age_seconds(ts) if ts else None

            if q.get("state") != "ok" or px is None:
                if _transition(c, r, "data_unavailable",
                               f"quote unavailable ({q.get('state')}: {str(q.get('reason'))[:60]})",
                               None, r["current_score"], ts):
                    updated += 1
                if _alert(c, sid, "data_source_failure",
                          f"{r['symbol']}: no usable quote — tracking paused", None,
                          cfg["alert_cooldown_seconds"]):
                    alerts_fired += 1
                continue
            if age is not None and age > cfg["stale_quote_hours"] * 3600:
                if _transition(c, r, "data_unavailable",
                               f"quote is {age/3600:.1f}h old — too stale to judge the setup",
                               px, r["current_score"], ts):
                    updated += 1
                continue

            c.execute("UPDATE setups SET last_checked=?, last_price=?, data_source_timestamp=? "
                      "WHERE setup_id=?", (_now(), px, ts, sid))

            # age-out
            try:
                created = datetime.fromisoformat(r["created_at"])
                days = (datetime.now(timezone.utc) - created).days
            except ValueError:
                days = 0
            if days >= cfg["expire_after_days"] and r["status"] in ("watch", "approaching_entry"):
                if _transition(c, r, "expired",
                               f"{days} days without triggering — the setup has aged out",
                               px, r["current_score"], ts):
                    updated += 1
                    details.append({"symbol": r["symbol"], "status": "expired"})
                continue

            inval, t1, t2 = r["invalidation"], r["target_1"], r["target_2"]
            elo, ehi = r["entry_low"], r["entry_high"]
            new_status, reason, alert = None, None, None

            if inval is not None and px <= inval:
                new_status = "invalidated"
                reason = f"price {px:.2f} broke the invalidation at {inval:.2f}"
                alert = ("invalidation_breached", f"{r['symbol']} invalidated: {px:.2f} below {inval:.2f}")
            elif t2 is not None and px >= t2:
                new_status = "target_2_reached"
                reason = f"price {px:.2f} reached target 2 at {t2:.2f}"
                alert = ("target_2_reached", f"{r['symbol']} hit target 2 at {t2:.2f} (now {px:.2f})")
            elif t1 is not None and px >= t1:
                new_status = "target_1_reached"
                reason = f"price {px:.2f} reached target 1 at {t1:.2f}"
                alert = ("target_1_reached", f"{r['symbol']} hit target 1 at {t1:.2f} (now {px:.2f})")
            elif elo is not None and ehi is not None and elo <= px <= ehi:
                new_status = "entry_triggered"
                reason = f"price {px:.2f} is inside the entry zone {elo:.2f}-{ehi:.2f}"
                alert = ("entry_zone_reached",
                         f"{r['symbol']} is in its entry zone {elo:.2f}-{ehi:.2f} at {px:.2f} "
                         f"— review manually; nothing has been ordered")
            elif elo is not None and r["status"] == "watch":
                payload = json.loads(r["payload"] or "{}")
                atr = ((payload.get("levels") or {}).get("risk_per_share") or 0) / 1.5 or None
                near = atr * cfg["approach_atr"] if atr else abs(elo) * 0.01
                if abs(px - elo) <= near:
                    new_status = "approaching_entry"
                    reason = f"price {px:.2f} is within {near:.2f} of the entry zone low {elo:.2f}"

            if new_status and _transition(c, r, new_status, reason, px, r["current_score"], ts):
                updated += 1
                details.append({"symbol": r["symbol"], "status": new_status, "reason": reason})
            if alert and _alert(c, sid, alert[0], alert[1], px, cfg["alert_cooldown_seconds"]):
                alerts_fired += 1

    return {"state": "ok", "checked": len(rows), "updated": updated,
            "alerts": alerts_fired, "changes": details, "session": session,
            "checked_at": _now(),
            "order_placement": "disabled — this service has no order path"}


def close_setup(setup_id: str, reason: str = "closed manually") -> Dict[str, Any]:
    init_db()
    with _LOCK, _conn() as c:
        row = c.execute("SELECT * FROM setups WHERE setup_id=?", (setup_id,)).fetchone()
        if not row:
            return {"state": "not_found"}
        _transition(c, row, "manually_closed", reason, row["last_price"], row["current_score"], None)
    return {"state": "ok", "setup_id": setup_id, "status": "manually_closed"}


def acknowledge_alerts(setup_id: Optional[str] = None) -> Dict[str, Any]:
    init_db()
    with _LOCK, _conn() as c:
        if setup_id:
            c.execute("UPDATE alerts SET acknowledged=1 WHERE setup_id=?", (setup_id,))
        else:
            c.execute("UPDATE alerts SET acknowledged=1")
    return {"state": "ok"}


# ── Reading ──────────────────────────────────────────────────────────────────

def _row_to_setup(r: sqlite3.Row) -> Dict[str, Any]:
    d = dict(r)
    for k in ("catalyst", "contract", "payload", "source_timestamps"):
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except (TypeError, ValueError):
                pass
    d["score_change"] = (round(d["current_score"] - d["initial_score"], 1)
                         if (d.get("current_score") is not None and d.get("initial_score") is not None)
                         else None)
    d["is_active"] = d.get("status") not in TERMINAL
    return d


def list_setups(include_closed: bool = False, limit: int = 200) -> Dict[str, Any]:
    init_db()
    with _LOCK, _conn() as c:
        if include_closed:
            rows = c.execute("SELECT * FROM setups ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM setups WHERE status NOT IN "
                             "('invalidated','target_2_reached','expired','manually_closed') "
                             "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        setups = [_row_to_setup(r) for r in rows]
        ids = [s["setup_id"] for s in setups]
        ev_by: Dict[str, List[Dict[str, Any]]] = {i: [] for i in ids}
        al_by: Dict[str, List[Dict[str, Any]]] = {i: [] for i in ids}
        if ids:
            qmarks = ",".join("?" * len(ids))
            for e in c.execute(f"SELECT * FROM setup_events WHERE setup_id IN ({qmarks}) "
                               f"ORDER BY at", ids).fetchall():
                ev_by[e["setup_id"]].append(dict(e))
            for a in c.execute(f"SELECT * FROM alerts WHERE setup_id IN ({qmarks}) "
                               f"ORDER BY at DESC", ids).fetchall():
                al_by[a["setup_id"]].append(dict(a))
        counts = {r["status"]: r["n"] for r in
                  c.execute("SELECT status, COUNT(*) n FROM setups GROUP BY status").fetchall()}
    for s in setups:
        s["timeline"] = ev_by.get(s["setup_id"], [])
        s["alerts"] = al_by.get(s["setup_id"], [])
    return {"state": "ok", "setups": setups, "count": len(setups),
            "status_counts": counts, "db": DB_PATH,
            "order_placement": "disabled", "generated_at": _now()}


def recent_alerts(limit: int = 50, unacknowledged_only: bool = False) -> Dict[str, Any]:
    init_db()
    q = ("SELECT a.*, s.symbol FROM alerts a JOIN setups s ON s.setup_id=a.setup_id "
         + ("WHERE a.acknowledged=0 " if unacknowledged_only else "")
         + "ORDER BY a.at DESC LIMIT ?")
    with _LOCK, _conn() as c:
        rows = [dict(r) for r in c.execute(q, (limit,)).fetchall()]
    return {"state": "ok", "alerts": rows, "count": len(rows)}


def stats() -> Dict[str, Any]:
    init_db()
    with _LOCK, _conn() as c:
        counts = {r["status"]: r["n"] for r in
                  c.execute("SELECT status, COUNT(*) n FROM setups GROUP BY status").fetchall()}
        n_alerts = c.execute("SELECT COUNT(*) n FROM alerts").fetchone()["n"]
        last = c.execute("SELECT MAX(last_checked) m FROM setups").fetchone()["m"]
    return {"state": "ok", "status_counts": counts, "total_alerts": n_alerts,
            "last_checked": last, "db": DB_PATH, "schema_version": SCHEMA_VERSION,
            "config": _cfg(), "order_placement": "disabled"}


# ── Cadence ──────────────────────────────────────────────────────────────────
# Times are ET and market-calendar aware: a run scheduled on a holiday or weekend
# is reported as skipped with the reason rather than silently doing nothing.

CADENCE = [
    ("premarket_scan", "08:30", "full scan + refresh tracked setups"),
    ("premarket_final", "09:20", "final pre-open refresh"),
    ("open_stabilise", "09:40", "post-open stabilisation scan"),
    ("midday", "12:30", "midday refresh"),
    ("power_hour", "15:15", "power-hour scan"),
    ("close_review", "16:10", "closing review"),
    ("evening_plan", "18:00", "next-session plan"),
]


def next_runs(now: Optional[datetime] = None) -> Dict[str, Any]:
    """The upcoming scheduled runs, skipping non-trading days."""
    from datetime import datetime as _dt, time as _t, timedelta as _td
    now_et = (now or datetime.now(timezone.utc)).astimezone(_MR.ET)
    out = []
    for offset in range(0, 7):
        day = (now_et + _td(days=offset)).date()
        if not _MR.is_trading_day(day):
            if offset == 0:
                out.append({"date": day.isoformat(), "skipped": True,
                            "reason": ("weekend" if day.weekday() >= 5 else
                                       f"market holiday — {_MR.market_holidays(day.year).get(day)}")})
            continue
        for name, hhmm, note in CADENCE:
            h, m = (int(x) for x in hhmm.split(":"))
            when = _dt.combine(day, _t(h, m), tzinfo=_MR.ET)
            if when > now_et:
                out.append({"run": name, "at_et": when.isoformat(timespec="minutes"),
                            "at_ist": when.astimezone(_MR.IST).isoformat(timespec="minutes"),
                            "in_minutes": round((when - now_et).total_seconds() / 60),
                            "note": note})
        if len([o for o in out if o.get("run")]) >= 4:
            break
    return {"state": "ok", "now_et": now_et.isoformat(timespec="seconds"),
            "cadence": [{"run": n, "et": t, "note": d} for n, t, d in CADENCE],
            "upcoming": out[:8],
            "order_placement": "disabled — the scheduler can only scan, track and alert"}
