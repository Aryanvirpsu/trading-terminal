"""Shadow candidate logger — free Challenger data from every decision cycle.

PURPOSE: CH-002 (slot ranking) was data-starved: real slot competitions are rare. This records, for
every discovery cycle, the COMPLETE finalist set with decision-time features only, what the Champion
actually did, what each hypothetical ranking policy would have picked under the SAME capacity rules,
why each losing candidate lost its slot, and the opportunity funnel. Later the raw daily + 5-minute bars
of those symbols are appended so outcomes (R, MFE/MAE, +1R-before-stop ordering) are derived offline —
outcomes are never stored as derived numbers here, and no hindsight field is ever available to ranking.

OBSERVATIONS vs EVENTS (fake-sample-size protection)
  * observation_id = `<cycle_id>:<symbol>` — one row per symbol per discovery cycle.
  * event_id — the independent setup. Decided at OBSERVATION TIME from prior observations only (no
    hindsight). RULE: an observation joins the latest event for the same (symbol, direction) iff
      (1) that event's last observation was within EVENT_WINDOW_DAYS calendar days,
      (2) the current price is still strictly inside that event's (stop, target) range, and
      (3) no Champion position born from that event has closed.
    Otherwise it starts a NEW event (e.g. the next stop/target crossing, a stale >5-day gap, or a
    closed trade). Count independent evidence by event_id, never by observation.

GUARANTEES
  * Separate SQLite file (`shadow/shadow_candidates.db` beside the ledger dir, never inside the ledger):
    experimental telemetry cannot contaminate the authoritative trading ledger.
  * Append-only: INSERT OR IGNORE only; BEFORE UPDATE/DELETE triggers abort any mutation (shadow_meta is
    a scratch table for fetch markers only).
  * Never changes ranking, selection, sizing, orders or Champion state; read-only over the ledger.
  * Every public entry point swallows its own exceptions (a logging failure must never break trading).
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import os
import sqlite3
from typing import Any, Callable, Dict, List, Optional

from . import config as cfg
from . import db

SHADOW_DB = "shadow_candidates.db"
TRACK_DAYS = 25            # keep appending bars for a candidate this many calendar days
EVENT_WINDOW_DAYS = 5      # an observation continues an event only within this many calendar days
_CAPACITY_CHECKS = {"max_open_positions", "max_positions_per_sector", "max_sector_exposure",
                    "max_entries_per_day"}

_TABLES = """
CREATE TABLE IF NOT EXISTS shadow_meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS shadow_cycles (
    cycle_id TEXT PRIMARY KEY, session_date TEXT NOT NULL, scan_ts TEXT NOT NULL, session_type TEXT,
    created_at TEXT NOT NULL, code_version TEXT, engine_version TEXT, config_version TEXT,
    finalists INTEGER, eligible INTEGER, is_choice_event INTEGER,
    capacity_json TEXT, funnel_json TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS shadow_events (
    event_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, direction TEXT, first_obs_id TEXT,
    first_session TEXT, first_ts TEXT, entry REAL, stop REAL, target REAL, strategy TEXT);
CREATE TABLE IF NOT EXISTS shadow_candidates (
    cand_id TEXT PRIMARY KEY, cycle_id TEXT NOT NULL, event_id TEXT, session_date TEXT NOT NULL,
    scan_ts TEXT, session_type TEXT,
    symbol TEXT NOT NULL, sector TEXT, strategy TEXT, scanner_rank INTEGER, signal_id TEXT,
    decision TEXT, failed_gates TEXT, quality REAL, expected_r REAL, ev_per_share REAL, rr REAL,
    exec_conf REAL, composite REAL, entry REAL, stop REAL, target REAL,
    quote_bid REAL, quote_ask REAL, quote_last REAL, quote_source_ts REAL, quote_provider TEXT,
    spread_pct REAL, eligible INTEGER, champion_executed INTEGER, champion_reason TEXT,
    capacity_reason TEXT, features_json TEXT,
    UNIQUE (cycle_id, symbol));
CREATE INDEX IF NOT EXISTS idx_cand_event ON shadow_candidates(event_id);
CREATE INDEX IF NOT EXISTS idx_cand_sym ON shadow_candidates(symbol, session_date);
CREATE TABLE IF NOT EXISTS shadow_picks (
    cycle_id TEXT NOT NULL, policy TEXT NOT NULL, symbol TEXT NOT NULL, pick_order INTEGER,
    PRIMARY KEY (cycle_id, policy, symbol));
CREATE TABLE IF NOT EXISTS shadow_bars_d (
    symbol TEXT NOT NULL, day TEXT NOT NULL, o REAL, h REAL, l REAL, c REAL, v REAL,
    fetched_at TEXT, PRIMARY KEY (symbol, day));
CREATE TABLE IF NOT EXISTS shadow_bars_5m (
    symbol TEXT NOT NULL, ts TEXT NOT NULL, o REAL, h REAL, l REAL, c REAL, v REAL,
    fetched_at TEXT, PRIMARY KEY (symbol, ts));
CREATE TABLE IF NOT EXISTS shadow_ch001_obs (
    position_id TEXT PRIMARY KEY, event_id TEXT, symbol TEXT, plus1r_ts TEXT, plus1r_price REAL,
    observed_at TEXT);
CREATE TABLE IF NOT EXISTS shadow_ch001_results (
    position_id TEXT PRIMARY KEY, event_id TEXT, symbol TEXT, signal_id TEXT,
    avg_entry REAL, stop REAL, target REAL, risk_per_share REAL,
    opened_at TEXT, closed_at TEXT, original_exit_reason TEXT, original_exit_price REAL, original_R REAL,
    plus1r_occurred INTEGER, plus1r_ts TEXT, be_exit_ts TEXT, be_exit_price REAL, be_R REAL, delta_R REAL,
    ambiguous INTEGER, ambiguity_note TEXT, bars_used INTEGER, computed_at TEXT);
"""
_APPEND_ONLY = ("shadow_cycles", "shadow_events", "shadow_candidates", "shadow_picks", "shadow_bars_d",
                "shadow_bars_5m", "shadow_ch001_obs", "shadow_ch001_results")


def path() -> str:
    # Own subfolder: db.list_ledgers() treats every top-level *.db as a ledger, and telemetry must never look like one.
    return os.path.join(db._DATA_DIR, "shadow", SHADOW_DB)


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path()), exist_ok=True)
    conn = sqlite3.connect(path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(_TABLES)
    for t in _APPEND_ONLY:
        for op in ("UPDATE", "DELETE"):
            conn.execute(f"CREATE TRIGGER IF NOT EXISTS {t}_no_{op.lower()} BEFORE {op} ON {t} "
                         f"BEGIN SELECT RAISE(ABORT, '{t} is append-only'); END")
    return conn


def _f(x: Any) -> Optional[float]:
    try:
        v = float(x)
        return v if v == v and v not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None


# ── decision-time features + policy ranking ───────────────────────────────────

def _gate_value(result: Dict[str, Any], name: str) -> Any:
    for g in result.get("decision_gates") or []:
        if g.get("name") == name:
            return g.get("value")
    return None


def candidate_row(finalist: Dict[str, Any], result: Dict[str, Any], canon: Optional[Dict[str, Any]],
                  quote: Any) -> Dict[str, Any]:
    """Decision-time facts only (nothing observed after the decision)."""
    er = result.get("entry_range") or []
    entry, stop, target = (er[0] if er else None), result.get("stop"), result.get("target")
    ev = result.get("ev_breakdown") or {}
    quality = _f(result.get("confidence_quality"))
    rr = None
    if entry is not None and stop is not None and target is not None and entry != stop:
        rr = (target - entry) / (entry - stop)
    exec_conf = _f(_gate_value(result, "liquidity"))
    composite = (quality / 100.0 * rr * (exec_conf if exec_conf is not None else 1.0)
                 if quality is not None and rr is not None else None)
    bid = ask = last = ts = provider = spread = None
    if quote is not None:
        qr = quote.resolved()
        bid, ask, last, ts, provider = _f(qr.bid), _f(qr.ask), _f(qr.last), _f(quote.source_ts), quote.provider
        if bid and ask and ask >= bid:
            spread = round((ask - bid) / ((ask + bid) / 2) * 100, 4)
    stock_ready = bool(canon and canon.get("instrument_choice") is not None
                       and canon["instrument_choice"].choice.value == "STOCK"
                       and canon["stock_executable"].executable)
    # Eligibility is judged INDEPENDENTLY of capacity: canonical account-fit runs against live state that
    # already reflects the Champion's own earlier entries this cycle, so a candidate that lost its slot
    # would otherwise look "ineligible" and the competition would be invisible. A candidate is eligible if
    # it is stock-executable, or its ONLY stock-fit violations are capacity-type ones.
    fit = canon.get("stock_account_fit") if canon else None
    viol = {v.check for v in (getattr(fit, "violations", None) or ())}
    capacity_only = bool(viol) and viol <= _CAPACITY_CHECKS
    choice = canon["instrument_choice"].choice.value if canon and canon.get("instrument_choice") else None
    precap_ok = stock_ready or (choice in ("STOCK", "NO_TRADE") and capacity_only)
    decision = result.get("decision")
    return {
        "symbol": finalist["symbol"], "sector": finalist.get("sector"), "strategy": finalist.get("strategy"),
        "direction": result.get("direction") or finalist.get("direction") or "LONG",
        "scanner_rank": finalist.get("scanner_rank"), "decision": decision,
        "failed_gates": json.dumps(result.get("failed_gates") or []),
        "quality": quality, "expected_r": _f(ev.get("expected_r")), "ev_per_share": _f(ev.get("ev_per_share")),
        "rr": rr, "exec_conf": exec_conf, "composite": composite,
        "entry": _f(entry), "stop": _f(stop), "target": _f(target),
        "quote_bid": bid, "quote_ask": ask, "quote_last": last, "quote_source_ts": ts,
        "quote_provider": provider, "spread_pct": spread,
        "eligible": int(decision == "TRADEABLE" and precap_ok and quote is not None),
        "features_json": json.dumps({
            "freshness": (result.get("freshness") or {}).get("state"), "data_source": result.get("data_source"),
            "data_state": result.get("data_state"), "direction": result.get("direction"),
            "instrument": choice, "stock_executable_at_decision": stock_ready,
            "stock_fit_violations": sorted(viol)}, default=str),
    }


POLICIES: Dict[str, Callable[[Dict[str, Any]], tuple]] = {
    "CHAMPION": lambda c: (c["scanner_rank"] if c["scanner_rank"] is not None else 99, c["symbol"]),
    "CH-002A_conviction": lambda c: (-(c["quality"] or 0.0), c["symbol"]),
    "CH-002B_expected_r": lambda c: (-(c["expected_r"] or 0.0), -(c["quality"] or 0.0), c["symbol"]),
    "CH-002C_composite": lambda c: (-(c["composite"] or 0.0), c["symbol"]),
}


def capacity_snapshot(session_date: str) -> Dict[str, Any]:
    """Capacity available at the START of the cycle (ledger is only read)."""
    from . import risk as risk_mod
    r = cfg.risk()
    st = risk_mod.account_state(session_date)
    return {"max_open": r.max_open_positions, "max_entries_per_day": r.max_entries_per_day,
            "max_per_sector": r.max_positions_per_sector, "open_positions": st["open_positions"],
            "entries_today": st["entries_today"], "sector_positions": st["sector_positions"],
            "open_symbols": [p["symbol"] for p in db.query("SELECT symbol FROM positions WHERE status='open'")],
            "available_cash": st["available_cash"], "buying_power": st["buying_power"]}


def simulate_picks(cands: List[Dict[str, Any]], key: Callable, cap: Dict[str, Any]) -> Dict[str, Any]:
    """Greedy fill of the fixed capacity by `key` order. Returns picks + the reason each loser lost."""
    open_n, entries = cap["open_positions"], cap["entries_today"]
    sectors = dict(cap["sector_positions"])
    open_syms = set(cap.get("open_symbols") or [])
    picks, lost = [], {}
    for c in sorted([c for c in cands if c["eligible"]], key=key):
        sec = c["sector"] or "unknown"
        if entries >= cap["max_entries_per_day"]:
            lost[c["symbol"]] = "entries_per_day"
        elif open_n >= cap["max_open"]:
            lost[c["symbol"]] = "max_open"
        elif sectors.get(sec, 0) >= cap["max_per_sector"]:
            lost[c["symbol"]] = "sector"
        elif c["symbol"] in open_syms:
            lost[c["symbol"]] = "duplicate_symbol"
        else:
            picks.append(c["symbol"])
            entries += 1
            open_n += 1
            sectors[sec] = sectors.get(sec, 0) + 1
    return {"picks": picks, "lost": lost}


# ── event identity ────────────────────────────────────────────────────────────

def _assign_event(conn: sqlite3.Connection, c: Dict[str, Any], session_date: str, scan_ts: str,
                  obs_id: str, closed_signal_ids: set, session_type: str = "regular") -> str:
    """Decision-time event assignment — uses ONLY observations made before this one (see module docstring).
    Manual/smoke-test cycles (session_type 'manual') neither join nor seed real events."""
    sym, direction = c["symbol"], c["direction"]
    manual = session_type == "manual"
    ev = None if manual else conn.execute(
        "SELECT e.*, (SELECT MAX(session_date) FROM shadow_candidates x WHERE x.event_id=e.event_id) last_sess "
        "FROM shadow_events e WHERE e.symbol=? AND e.direction=? AND e.first_obs_id NOT LIKE '%manual%' "
        "ORDER BY e.first_ts DESC LIMIT 1", (sym, direction)).fetchone()
    last = c.get("quote_last") or c.get("entry")
    if ev is not None and last is not None and ev["stop"] is not None and ev["target"] is not None:
        try:
            gap = (dt.date.fromisoformat(session_date) - dt.date.fromisoformat(ev["last_sess"])).days
        except Exception:
            gap = 10 ** 6
        lo, hi = sorted((ev["stop"], ev["target"]))
        closed = False
        if closed_signal_ids:
            closed = conn.execute(
                "SELECT 1 FROM shadow_candidates WHERE event_id=? AND signal_id IN (%s) LIMIT 1"
                % ",".join("?" * len(closed_signal_ids)), (ev["event_id"], *closed_signal_ids)).fetchone() is not None
        if gap <= EVENT_WINDOW_DAYS and lo < last < hi and not closed:
            return ev["event_id"]
    event_id = f"evt{'m' if manual else ''}_{sym}_{direction}_{scan_ts[:16].replace(':', '').replace('-', '')}"
    conn.execute("INSERT OR IGNORE INTO shadow_events VALUES(?,?,?,?,?,?,?,?,?,?)",
                 (event_id, sym, direction, obs_id, session_date, scan_ts, c.get("entry"), c.get("stop"),
                  c.get("target"), c.get("strategy")))
    return event_id


def _closed_signal_ids() -> set:
    """Signals whose Champion position has closed (event over) — read from the ledger."""
    try:
        return {r["signal_id"] for r in db.query(
            "SELECT signal_id FROM positions WHERE status='closed' AND signal_id IS NOT NULL")}
    except Exception:
        return set()


# ── recording ────────────────────────────────────────────────────────────────

def _funnel(scan: Optional[Dict[str, Any]], rows: List[Dict[str, Any]], evaluated: List[Dict[str, Any]]) -> Dict[str, Any]:
    scan = scan or {}
    decisions = collections.Counter(c["decision"] for c in rows)
    attempted = [e for e in evaluated if e.get("executed") is not None]
    reasons: collections.Counter = collections.Counter()
    for c in rows:
        for g in json.loads(c["failed_gates"] or "[]"):
            reasons[f"gate:{g}"] += 1
        if not c["champion_executed"]:
            reasons[f"champion:{(c['champion_reason'] or 'not executed')[:80]}"] += 1
    feats = [json.loads(c["features_json"] or "{}") for c in rows]
    return {"universe_considered": scan.get("universe_considered"), "bars_available": scan.get("bars_available"),
            "scanner_detections": scan.get("candidate_count"), "finalists": len(rows),
            "validation_pass": decisions.get("TRADEABLE", 0) + decisions.get("MONITOR", 0),
            "validation_fail": decisions.get("REJECT", 0),
            "TRADEABLE": decisions.get("TRADEABLE", 0), "MONITOR": decisions.get("MONITOR", 0),
            "REJECT": decisions.get("REJECT", 0),
            "stock_executable_at_decision": sum(bool(f.get("stock_executable_at_decision")) for f in feats),
            "instrument_stock": sum(f.get("instrument") == "STOCK" for f in feats),
            "instrument_option": sum(f.get("instrument") == "OPTION" for f in feats),
            "instrument_no_trade": sum(f.get("instrument") == "NO_TRADE" for f in feats),
            "capacity_eligible": sum(c["eligible"] for c in rows),
            "orders_attempted": len(attempted), "orders_refused": sum(1 for e in attempted if not e.get("executed")),
            "entry_blocked_pre_broker": sum(
                1 for c in rows if c["decision"] == "TRADEABLE" and not c["champion_executed"]
                and any(k in (c["champion_reason"] or "") for k in
                        ("cap reached", "no executable quote", "not canonically", "shadow-only"))),
            "entries": sum(1 for e in attempted if e.get("executed")),
            "top_reasons": dict(reasons.most_common(12))}


def record_cycle(session_date: str, rows: List[Dict[str, Any]], evaluated: List[Dict[str, Any]],
                 capacity: Dict[str, Any], *, cycle_id: Optional[str] = None, scan_ts: Optional[str] = None,
                 session_type: str = "regular", scan: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Append one decision cycle. `evaluated` is the discovery cycle's own list (signal_id / executed / reasons)."""
    if not rows:
        return None
    try:
        from .journal import ENGINE_VERSION
        scan_ts = scan_ts or db.utcnow()
        cycle_id = cycle_id or f"cyc_{session_date}"
        by_sym = {e.get("symbol"): e for e in evaluated}
        for c in rows:
            e = by_sym.get(c["symbol"], {})
            c["signal_id"] = e.get("signal_id")
            c["champion_executed"] = int(bool(e.get("executed")))
            c["champion_reason"] = "executed" if e.get("executed") else (
                "; ".join(str(x) for x in (e.get("reasons") or [])) or e.get("note") or "not executed")
        sim = {name: simulate_picks(rows, key, capacity) for name, key in POLICIES.items()}
        champ = sim["CHAMPION"]
        eligible_n = sum(c["eligible"] for c in rows)
        is_choice = int(bool(champ["lost"]) or any(sim[n]["picks"] != champ["picks"] for n in sim))
        for c in rows:
            c["capacity_reason"] = (
                champ["lost"].get(c["symbol"]) if c["eligible"] and c["symbol"] in champ["lost"]
                else None if c["symbol"] in champ["picks"]
                else f"not_eligible:{c['decision']}" if not c["eligible"] else None)
        closed = _closed_signal_ids()
        conn = _connect()
        with conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO shadow_cycles(cycle_id,session_date,scan_ts,session_type,created_at,code_version,"
                "engine_version,config_version,finalists,eligible,is_choice_event,capacity_json,funnel_json,note) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cycle_id, session_date, scan_ts, session_type, db.utcnow(), os.environ.get("GITHUB_SHA")
                 or os.environ.get("AVDI_CODE_VERSION", "unknown"), ENGINE_VERSION, db.config_version(),
                 len(rows), eligible_n, is_choice, json.dumps(capacity, default=str),
                 json.dumps(_funnel(scan, rows, evaluated), default=str),
                 "hypothetical picks use capacity at cycle start; Champion actual in shadow_candidates"))
            if cur.rowcount == 0:
                conn.close()
                return cycle_id                         # this cycle was already recorded — never duplicate
            for c in rows:
                obs_id = f"{cycle_id}:{c['symbol']}"
                event_id = _assign_event(conn, c, session_date, scan_ts, obs_id, closed, session_type)
                conn.execute(
                    "INSERT OR IGNORE INTO shadow_candidates(cand_id,cycle_id,event_id,session_date,scan_ts,session_type,"
                    "symbol,sector,strategy,scanner_rank,signal_id,decision,failed_gates,quality,expected_r,ev_per_share,"
                    "rr,exec_conf,composite,entry,stop,target,quote_bid,quote_ask,quote_last,quote_source_ts,quote_provider,"
                    "spread_pct,eligible,champion_executed,champion_reason,capacity_reason,features_json) "
                    "VALUES(" + ",".join("?" * 33) + ")",
                    (obs_id, cycle_id, event_id, session_date, scan_ts, session_type, c["symbol"], c["sector"],
                     c["strategy"], c["scanner_rank"], c["signal_id"], c["decision"], c["failed_gates"], c["quality"],
                     c["expected_r"], c["ev_per_share"], c["rr"], c["exec_conf"], c["composite"], c["entry"],
                     c["stop"], c["target"], c["quote_bid"], c["quote_ask"], c["quote_last"],
                     c["quote_source_ts"], c["quote_provider"], c["spread_pct"], c["eligible"],
                     c["champion_executed"], c["champion_reason"], c["capacity_reason"], c["features_json"]))
            for name, s in sim.items():
                for i, sym in enumerate(s["picks"]):
                    conn.execute("INSERT OR IGNORE INTO shadow_picks(cycle_id,policy,symbol,pick_order) VALUES(?,?,?,?)",
                                 (cycle_id, name, sym, i))
            for c in rows:      # the ACTUAL Champion outcome (may differ from the greedy hypothetical)
                if c["champion_executed"]:
                    conn.execute("INSERT OR IGNORE INTO shadow_picks(cycle_id,policy,symbol,pick_order) VALUES(?,?,?,?)",
                                 (cycle_id, "CHAMPION_ACTUAL", c["symbol"], 0))
        conn.close()
        return cycle_id
    except Exception as e:                         # never break trading
        try:
            db.audit("system", session_date, "shadow_log_failed", {"error": str(e)[:200]})
        except Exception:
            pass
        return None


# ── raw bars ─────────────────────────────────────────────────────────────────

def _complete_5m(ts_iso: str, now: dt.datetime) -> bool:
    try:
        t = dt.datetime.fromisoformat(ts_iso)
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.timezone.utc)
        return t + dt.timedelta(minutes=5) <= now
    except Exception:
        return False


def update_bars(session_date: str, history: Optional[Callable[[str, str], Dict[str, Any]]] = None,
                intraday_every_min: int = 15, now: Optional[dt.datetime] = None) -> Dict[str, int]:
    """Append raw bars for recent shadow candidates: COMPLETE daily bars (date < session_date) at most once per
    symbol/day, and completed 5-minute bars at most once per `intraday_every_min` per symbol. Idempotent.
    Outcomes are derived from these offline, never stored."""
    added = {"daily": 0, "5m": 0}
    try:
        if history is None:
            import research as R
            history = R.price_history
        conn = _connect()
        cutoff = (dt.date.fromisoformat(session_date) - dt.timedelta(days=TRACK_DAYS)).isoformat()
        syms = [r["symbol"] for r in conn.execute(
            "SELECT DISTINCT symbol FROM shadow_candidates WHERE session_date >= ?", (cutoff,))]
        now = now or dt.datetime.now(dt.timezone.utc)
        bucket = int(now.timestamp() // (intraday_every_min * 60))
        stamp = db.utcnow()
        for sym in syms:
            for rng, kind in (("1M", "daily"), ("1D", "5m")):
                marker = (f"bars:daily:{sym}:{session_date}" if kind == "daily"
                          else f"bars:5m:{sym}:{bucket}")           # bounded fetch rate per symbol/kind
                if conn.execute("SELECT 1 FROM shadow_meta WHERE k=?", (marker,)).fetchone():
                    continue
                try:
                    h = history(sym, rng)
                except Exception:
                    continue
                with conn:
                    conn.execute("INSERT OR IGNORE INTO shadow_meta(k,v) VALUES(?,?)", (marker, stamp))
                if h.get("state") != "ok":
                    continue
                with conn:
                    for p in h.get("points") or []:
                        t = str(p.get("t"))
                        if kind == "daily":
                            if t[:10] >= session_date:          # today's bar is incomplete
                                continue
                            cur = conn.execute("INSERT OR IGNORE INTO shadow_bars_d VALUES(?,?,?,?,?,?,?,?)",
                                               (sym, t[:10], p.get("o"), p.get("h"), p.get("l"), p.get("c"), p.get("v"), stamp))
                        else:
                            if not _complete_5m(t, now):
                                continue
                            cur = conn.execute("INSERT OR IGNORE INTO shadow_bars_5m VALUES(?,?,?,?,?,?,?,?)",
                                               (sym, t, p.get("o"), p.get("h"), p.get("l"), p.get("c"), p.get("v"), stamp))
                        added[kind] += cur.rowcount
        conn.close()
    except Exception as e:                         # never break trading
        try:
            db.audit("system", session_date, "shadow_bars_failed", {"error": str(e)[:200]})
        except Exception:
            pass
    return added


# ── CH-001 forward shadow (breakeven after +1R) — never touches the Champion position ────────────────

def _parse_ts(x: Any) -> Optional[dt.datetime]:
    try:
        t = dt.datetime.fromisoformat(str(x).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None


def evaluate_ch001(now: Optional[dt.datetime] = None) -> Dict[str, int]:
    """For every Champion position: record when +1R was genuinely observed (first complete 5-minute bar whose
    HIGH >= entry + 1R, starting at/after the entry) and, for CLOSED positions, what the hypothetical
    breakeven stop (moved effective the bar AFTER the trigger) would have done versus the original exit.
    Ambiguity is never resolved favourably: a bar holding both the trigger and the original stop, or the
    breakeven level and the target, is flagged AMBIGUOUS. Append-only; final rows only once closed."""
    out = {"obs": 0, "results": 0, "ambiguous": 0}
    try:
        conn = _connect()
        bps = cfg.execution().slippage_bps
        for p in db.query("SELECT * FROM positions"):
            pid = p["position_id"]
            opened, closed_at = _parse_ts(p.get("opened_at")), _parse_ts(p.get("closed_at"))
            entry, stop, target = p.get("avg_entry"), p.get("stop"), p.get("target")
            if not opened or entry is None or stop is None or entry <= stop:
                continue
            if conn.execute("SELECT 1 FROM shadow_ch001_results WHERE position_id=?", (pid,)).fetchone():
                continue
            risk = entry - stop
            trig = entry + risk
            end = closed_at or (now or dt.datetime.now(dt.timezone.utc))
            bars = []
            for b in conn.execute("SELECT * FROM shadow_bars_5m WHERE symbol=? ORDER BY ts", (p["symbol"],)):
                t = _parse_ts(b["ts"])
                if t and t >= opened:
                    bars.append(b)
            ev = conn.execute("SELECT event_id FROM shadow_candidates WHERE signal_id=? LIMIT 1",
                              (p.get("signal_id"),)).fetchone()
            event_id = ev["event_id"] if ev else None
            plus_ts = plus_px = None
            moved_idx = None
            be_ts = be_px = None
            amb = False
            note = ""
            for i, b in enumerate(bars):
                t = _parse_ts(b["ts"])
                if t > end:
                    break
                hit_stop = b["l"] <= stop
                if plus_ts is None:
                    if b["h"] >= trig:
                        if hit_stop:
                            amb, note = True, f"trigger and original stop in one bar {b['ts']}"
                            break
                        plus_ts, plus_px, moved_idx = b["ts"], trig, i
                    continue
                if i > moved_idx and b["l"] <= entry:                     # stop moved to entry after the trigger bar
                    if target is not None and b["h"] >= target:
                        amb, note = True, f"breakeven level and target in one bar {b['ts']}"
                        break
                    be_ts, be_px = b["ts"], entry * (1 - bps / 1e4)
                    break
            if plus_ts and not conn.execute("SELECT 1 FROM shadow_ch001_obs WHERE position_id=?", (pid,)).fetchone():
                with conn:
                    conn.execute("INSERT OR IGNORE INTO shadow_ch001_obs VALUES(?,?,?,?,?,?)",
                                 (pid, event_id, p["symbol"], plus_ts, plus_px, db.utcnow()))
                out["obs"] += 1
            if p.get("status") != "closed":
                continue
            planned = p.get("planned_risk") or 0.0
            orig_R = (p.get("realized_pnl") or 0.0) / planned if planned else None
            be_R = ((be_px - entry) / risk) if be_px is not None else None
            # BE only differs from the original if it exited BEFORE the original exit
            be_before = be_ts is not None and (_parse_ts(be_ts) <= (closed_at or end))
            delta = (be_R - orig_R) if (be_before and be_R is not None and orig_R is not None) else 0.0
            with conn:
                conn.execute(
                    "INSERT OR IGNORE INTO shadow_ch001_results VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (pid, event_id, p["symbol"], p.get("signal_id"), entry, stop, target, risk, p.get("opened_at"),
                     p.get("closed_at"), p.get("exit_reason"), p.get("avg_exit"), orig_R, int(plus_ts is not None),
                     plus_ts, be_ts if be_before else None, be_px if be_before else None,
                     be_R if be_before else None, delta, int(amb), note or None, len(bars), db.utcnow()))
            out["results"] += 1
            out["ambiguous"] += int(amb)
        conn.close()
    except Exception as e:                         # never break trading
        try:
            db.audit("system", None, "shadow_ch001_failed", {"error": str(e)[:200]})
        except Exception:
            pass
    return out


def status() -> Dict[str, Any]:
    """Lightweight operational view for health/status."""
    try:
        conn = _connect()
        last = conn.execute("SELECT cycle_id, session_date, scan_ts, finalists, eligible FROM shadow_cycles "
                            "ORDER BY scan_ts DESC LIMIT 1").fetchone()
        n = {t: conn.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"]
             for t in ("shadow_cycles", "shadow_candidates", "shadow_events", "shadow_bars_5m", "shadow_ch001_results")}
        conn.close()
        return {"ok": True, "last_cycle": dict(last) if last else None, "counts": n, "path": path()}
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}
