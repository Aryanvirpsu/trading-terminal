"""Shadow candidate logger — free Challenger data from every decision cycle.

PURPOSE: CH-002 (slot ranking) was data-starved: real slot competitions are rare. This records, for
every premarket cycle, the COMPLETE finalist set with decision-time features only, what the Champion
actually did, what each hypothetical ranking policy would have picked under the SAME capacity rules,
and why each losing candidate lost its slot. Later the raw daily + 5-minute bars of those symbols are
appended so outcomes (R, MFE/MAE, +1R-before-stop ordering) can be derived offline — outcomes are
never stored as derived numbers here, and no hindsight field is ever available to ranking.

GUARANTEES
  * Separate SQLite file (`shadow/shadow_candidates.db` beside the ledger dir, never inside the ledger): experimental
    telemetry cannot contaminate the authoritative trading ledger.
  * Append-only: INSERT OR IGNORE only; BEFORE UPDATE/DELETE triggers abort any mutation.
  * Never changes ranking, selection, sizing, orders or Champion state; read-only over the ledger.
  * Every public entry point swallows its own exceptions (a logging failure must never break trading).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
from typing import Any, Callable, Dict, List, Optional

from . import config as cfg
from . import db

SHADOW_DB = "shadow_candidates.db"
_CAPACITY_CHECKS = {"max_open_positions", "max_positions_per_sector", "max_sector_exposure",
                    "max_entries_per_day"}
TRACK_DAYS = 25        # keep appending bars for a candidate this many calendar days

_TABLES = """
CREATE TABLE IF NOT EXISTS shadow_meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS shadow_cycles (
    cycle_id TEXT PRIMARY KEY, session_date TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL,
    code_version TEXT, engine_version TEXT, config_version TEXT,
    finalists INTEGER, eligible INTEGER, is_choice_event INTEGER,
    capacity_json TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS shadow_candidates (
    cand_id TEXT PRIMARY KEY, cycle_id TEXT NOT NULL, session_date TEXT NOT NULL,
    symbol TEXT NOT NULL, sector TEXT, strategy TEXT, scanner_rank INTEGER, signal_id TEXT,
    decision TEXT, failed_gates TEXT, quality REAL, expected_r REAL, ev_per_share REAL, rr REAL,
    exec_conf REAL, composite REAL, entry REAL, stop REAL, target REAL,
    quote_bid REAL, quote_ask REAL, quote_last REAL, quote_source_ts REAL, quote_provider TEXT,
    spread_pct REAL, eligible INTEGER, champion_executed INTEGER, champion_reason TEXT,
    capacity_reason TEXT, features_json TEXT,
    UNIQUE (session_date, symbol));
CREATE TABLE IF NOT EXISTS shadow_picks (
    cycle_id TEXT NOT NULL, policy TEXT NOT NULL, symbol TEXT NOT NULL, pick_order INTEGER,
    PRIMARY KEY (cycle_id, policy, symbol));
CREATE TABLE IF NOT EXISTS shadow_bars_d (
    symbol TEXT NOT NULL, day TEXT NOT NULL, o REAL, h REAL, l REAL, c REAL, v REAL,
    fetched_at TEXT, PRIMARY KEY (symbol, day));
CREATE TABLE IF NOT EXISTS shadow_bars_5m (
    symbol TEXT NOT NULL, ts TEXT NOT NULL, o REAL, h REAL, l REAL, c REAL, v REAL,
    fetched_at TEXT, PRIMARY KEY (symbol, ts));
"""
_APPEND_ONLY = ("shadow_cycles", "shadow_candidates", "shadow_picks", "shadow_bars_d", "shadow_bars_5m")


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
            "instrument": (canon["instrument_choice"].choice.value if canon and canon.get("instrument_choice") else None),
            "stock_executable_at_decision": stock_ready, "stock_fit_violations": sorted(viol)}, default=str),
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
            picks.append(c["symbol"]); entries += 1; open_n += 1
            sectors[sec] = sectors.get(sec, 0) + 1
    return {"picks": picks, "lost": lost}


# ── recording ────────────────────────────────────────────────────────────────

def record_cycle(session_date: str, rows: List[Dict[str, Any]], evaluated: List[Dict[str, Any]],
                 capacity: Dict[str, Any]) -> Optional[str]:
    """Append one decision cycle. `evaluated` is premarket()'s own list (signal_id / executed / reasons)."""
    if not rows:
        return None
    try:
        from .journal import ENGINE_VERSION
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
        is_choice = int(bool(champ["lost"]) or any(
            sim[n]["picks"] != champ["picks"] for n in sim))
        for c in rows:
            c["capacity_reason"] = (
                champ["lost"].get(c["symbol"]) if c["eligible"] and c["symbol"] in champ["lost"]
                else None if c["symbol"] in champ["picks"]
                else f"not_eligible:{c['decision']}" if not c["eligible"] else None)
        cycle_id = f"cyc_{session_date}"
        conn = _connect()
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO shadow_cycles(cycle_id,session_date,created_at,code_version,engine_version,"
                "config_version,finalists,eligible,is_choice_event,capacity_json,note) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (cycle_id, session_date, db.utcnow(), os.environ.get("GITHUB_SHA", "unknown"), ENGINE_VERSION,
                 db.config_version(), len(rows), eligible_n, is_choice, json.dumps(capacity, default=str),
                 "hypothetical picks use capacity at cycle start; Champion actual in shadow_candidates"))
            for c in rows:
                conn.execute(
                    "INSERT OR IGNORE INTO shadow_candidates(cand_id,cycle_id,session_date,symbol,sector,strategy,"
                    "scanner_rank,signal_id,decision,failed_gates,quality,expected_r,ev_per_share,rr,exec_conf,"
                    "composite,entry,stop,target,quote_bid,quote_ask,quote_last,quote_source_ts,quote_provider,"
                    "spread_pct,eligible,champion_executed,champion_reason,capacity_reason,features_json) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"{cycle_id}:{c['symbol']}", cycle_id, session_date, c["symbol"], c["sector"], c["strategy"],
                     c["scanner_rank"], c["signal_id"], c["decision"], c["failed_gates"], c["quality"],
                     c["expected_r"], c["ev_per_share"], c["rr"], c["exec_conf"], c["composite"], c["entry"],
                     c["stop"], c["target"], c["quote_bid"], c["quote_ask"], c["quote_last"],
                     c["quote_source_ts"], c["quote_provider"], c["spread_pct"], c["eligible"],
                     c["champion_executed"], c["champion_reason"], c["capacity_reason"], c["features_json"]))
            for name, s in sim.items():
                for i, sym in enumerate(s["picks"]):
                    conn.execute("INSERT OR IGNORE INTO shadow_picks(cycle_id,policy,symbol,pick_order) VALUES(?,?,?,?)",
                                 (cycle_id, name, sym, i))
            # the ACTUAL Champion outcome (may differ from the greedy hypothetical, e.g. affordability)
            for c in rows:
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


def _complete_5m(ts_iso: str, now: dt.datetime) -> bool:
    try:
        t = dt.datetime.fromisoformat(ts_iso)
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.timezone.utc)
        return t + dt.timedelta(minutes=5) <= now
    except Exception:
        return False


def update_bars(session_date: str, history: Optional[Callable[[str, str], Dict[str, Any]]] = None) -> Dict[str, int]:
    """Append raw bars for recent shadow candidates: COMPLETE daily bars (date < session_date) and
    completed 5-minute bars. Idempotent. Outcomes are derived from these offline, never stored."""
    added = {"daily": 0, "5m": 0}
    try:
        if history is None:
            import research as R
            history = R.price_history
        conn = _connect()
        cutoff = (dt.date.fromisoformat(session_date) - dt.timedelta(days=TRACK_DAYS)).isoformat()
        syms = [r["symbol"] for r in conn.execute(
            "SELECT DISTINCT symbol FROM shadow_candidates WHERE session_date >= ?", (cutoff,))]
        now = dt.datetime.now(dt.timezone.utc)
        stamp = db.utcnow()
        for sym in syms:
            for rng, kind in (("1M", "daily"), ("1D", "5m")):
                marker = f"bars:{kind}:{sym}:{session_date}"     # at most one fetch per symbol/kind/day
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
