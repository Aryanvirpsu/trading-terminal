"""Scan cohort memory — the Top-5 OBSERVABILITY layer.

This is deliberately a SEPARATE system from `tracker.py` / `setup_tracker.db`.
`tracker.py` answers "should I get an alert on this name" (idempotent per
symbol/setup/day — one row that lives forever and accumulates status). This module
answers a different question: "what did THIS scan pick, and what happened to it" —
so every real scan gets its own identity and its own five rows, none of which are
ever merged, deduped across days, or deleted because nothing triggered.

    RAW SCAN OBSERVATIONS  →  OUTCOME DATA  →  AGGREGATED STATISTICS  →  DERIVED INSIGHTS

Each layer is a plain function over the row below it — nothing here is an LLM-authored
blob. `capture_scan` and the thesis it writes are built ONLY from fields the scanner
itself already computed (score components, setups, levels, catalyst/sentiment if
present). Aggregate functions (`rank_performance`, `score_bucket_performance`,
`setup_type_performance`, `repetition_stats`) always report a sample size alongside
the number, and refuse to be summarized down to a single conclusion — that's a UI/
reporting concern, not something this module manufactures.

Forward returns (d1/d3/d5/d10, MFE/MAE) are computed on demand from
`research.bars` — the SAME OHLCV pooled fetch the scanner and the historical
validator (`history.py`) already use — and memoized into `observation_snapshots` so
repeat reads don't refetch. There is no separate price-snapshot cron: a snapshot is
"what would forward_returns compute right now", which is always correct because it
reads the actual historical bar at that offset, not a value captured live and then
trusted forever.

Status is an EVENT, not a filter. `update_observations` re-checks live quotes and
writes a status_change event — it never deletes or hides a row. A candidate that
never triggers an entry is exactly as visible in the cohort as one that hit target 2;
that is the whole point (see CLAUDE.md discipline: measure repetition/outcomes
first, do not silently prune the record to make the page tidier).

Storage: SQLite in `~/.tradingview_mcp_data/scan_cohort.db` (WAL), same pattern as
`tracker.py`. `reset_for_tests(dir)` repoints DB_PATH for hermetic tests.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in ("..", os.path.join("..", "lab"), os.path.join("..", "src")):
    sys.path.insert(0, os.path.join(_HERE, _p))
sys.path.insert(0, _HERE)

import research as _R

_DATA_DIR = os.path.expanduser(os.environ.get("TVMCP_DATA_DIR", "~/.tradingview_mcp_data"))
DB_PATH = os.environ.get("SCAN_COHORT_DB", os.path.join(_DATA_DIR, "scan_cohort.db"))
SCHEMA_VERSION = 1

TOP_N = int(os.environ.get("SCAN_COHORT_TOP_N", "5"))
HORIZON_NAMES = ("d0", "d1", "d3", "d5", "d10")
HORIZON_DAYS = {"d0": 0, "d1": 1, "d3": 3, "d5": 5, "d10": 10}

STATUSES = ("selected", "watching", "approaching_entry", "entry_triggered",
            "target_1_hit", "target_2_hit", "invalidated", "thesis_broken",
            "expired", "horizon_complete", "manually_closed")
# Terminal = will never be re-checked again. entry_triggered/target_1_hit/
# horizon_complete are NOT terminal — a name can still move from target_1 to
# target_2, and horizon_complete is an outcome label, not a reason to stop watching.
TERMINAL = ("target_2_hit", "invalidated", "expired", "manually_closed")

_LOCK = threading.Lock()
_BENCH_CACHE: Dict[str, Dict[str, Any]] = {}


def reset_for_tests(data_dir: str) -> None:
    """Repoint DB_PATH at a throwaway directory and re-init. Mirrors
    `lab/paper/db.reset_for_tests` — used by the test suite only."""
    global DB_PATH
    DB_PATH = os.path.join(data_dir, "scan_cohort.db")
    _BENCH_CACHE.clear()
    init_db()


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
        c.execute("""CREATE TABLE IF NOT EXISTS scan_runs (
            scan_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            trading_date TEXT NOT NULL,
            preset TEXT NOT NULL,
            universe_count INTEGER,
            eligible_count INTEGER,
            deep_count INTEGER,
            regime TEXT,
            config TEXT,
            scanner_version TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS scan_observations (
            obs_id TEXT PRIMARY KEY,
            scan_id TEXT NOT NULL REFERENCES scan_runs(scan_id),
            rank INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            setup_type TEXT,
            score REAL,
            score_components TEXT,
            scan_price REAL,
            entry_low REAL,
            entry_high REAL,
            invalidation REAL,
            target_1 REAL,
            target_2 REAL,
            horizon_days INTEGER,
            features TEXT,
            thesis TEXT,
            thesis_json TEXT,
            verdict TEXT,
            status TEXT NOT NULL DEFAULT 'selected',
            status_price REAL,
            source_timestamp TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(scan_id, symbol)
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS observation_events (
            event_id TEXT PRIMARY KEY,
            obs_id TEXT NOT NULL REFERENCES scan_observations(obs_id),
            at TEXT NOT NULL,
            kind TEXT NOT NULL,
            from_status TEXT,
            to_status TEXT,
            price REAL,
            reason TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS observation_snapshots (
            obs_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            at TEXT,
            price REAL,
            pct_return REAL,
            computed_at TEXT NOT NULL,
            PRIMARY KEY (obs_id, kind)
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_obs_symbol ON scan_observations(symbol)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_obs_scan ON scan_observations(scan_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_runs_date ON scan_runs(trading_date)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_events_obs ON observation_events(obs_id)")


# ── Thesis (deterministic — built ONLY from fields the scanner produced) ──────

def _thesis(cand: Dict[str, Any], rank: int) -> Tuple[str, Dict[str, Any]]:
    ind = cand.get("indicators") or {}
    sc = cand.get("score") or {}
    lv = cand.get("levels") or {}
    setups = cand.get("setups") or []
    primary = setups[0] if setups else {}

    why = [c.get("note") for c in sorted(sc.get("components") or [], key=lambda x: -(x.get("score") or 0))
           if (c.get("score") or 0) > 0][:5]
    cat = cand.get("catalyst") or {}
    if cat.get("lean"):
        why.append(f"catalyst lean {cat['lean']} ({len(cat.get('items') or [])} item(s))")

    expected = (f"{primary.get('type', 'setup').replace('_', ' ')} over a "
                f"{primary.get('horizon', 'unspecified')} horizon" if primary
                else "no specific setup matched")
    confirm, invalidate = [], []
    if lv.get("state") == "ok":
        confirm.append(f"price sustaining a move through target 1 ({lv.get('target_1')}, "
                        f"{lv.get('target_1_basis')})")
        invalidate.append(f"price breaking {lv.get('invalidation')} ({lv.get('invalidation_basis')})")

    struct = {
        "rank": rank, "symbol": cand.get("symbol"), "score": sc.get("total"),
        "primary_setup": primary.get("type"), "setup_strength": primary.get("strength"),
        "setup_evidence": primary.get("evidence"),
        "why_selected": why, "expected": expected,
        "would_confirm": confirm, "would_invalidate": invalidate,
        "score_components": sc.get("components"),
    }
    human = (f"{cand.get('symbol')} — Rank {rank} / {sc.get('total')}\n"
             f"Why selected: " + ("; ".join(why) + "." if why else "no scored component above zero.") +
             f"\nExpected setup: {expected}." +
             (f"\nWould confirm: " + "; ".join(confirm) + "." if confirm else "") +
             (f"\nWould invalidate: " + "; ".join(invalidate) + "." if invalidate else ""))
    return human, struct


def _stage_count(scan_result: Dict[str, Any], stage: str) -> Optional[int]:
    for s in (scan_result.get("stages") or []):
        if s.get("stage") == stage:
            return s.get("count")
    return None


# ── Capture ────────────────────────────────────────────────────────────────────

def capture_scan(scan_result: Dict[str, Any], *, top_n: Optional[int] = None) -> Dict[str, Any]:
    """Persist a NEW scan_run + up to top_n observations from an ALREADY-COMPLETED
    scan's top10 (already score-sorted, post-enrichment). Call this exactly once per
    real scanner execution — never on a cache hit — so re-rendering the same scan
    never creates duplicate cohorts. Never raises on bad input; the caller (scanner.
    scan) is still responsible for not letting a tracking failure fail the scan."""
    init_db()
    n = top_n or TOP_N
    cands = (scan_result.get("top10") or [])[:n]
    if not cands:
        return {"state": "skipped", "reason": "no candidates in top10"}

    scan_id = uuid.uuid4().hex[:20]
    now = _now()
    trading_date = now[:10]
    regime = scan_result.get("regime") or {}
    observations = []
    with _LOCK, _conn() as c:
        c.execute("""INSERT INTO scan_runs (scan_id, created_at, trading_date, preset,
                     universe_count, eligible_count, deep_count, regime, config, scanner_version)
                     VALUES (?,?,?,?,?,?,?,?,?,?)""",
                  (scan_id, now, trading_date, scan_result.get("preset", "liquid"),
                   _stage_count(scan_result, "universe"), _stage_count(scan_result, "eligible"),
                   _stage_count(scan_result, "deep"), json.dumps(regime, default=str),
                   json.dumps(scan_result.get("config") or {}, default=str), "phase3-5"))
        for i, cand in enumerate(cands, 1):
            ind = cand.get("indicators") or {}
            lv = cand.get("levels") or {}
            sc = cand.get("score") or {}
            setups = cand.get("setups") or []
            human, struct = _thesis(cand, i)
            obs_id = uuid.uuid4().hex[:20]
            ez = lv.get("entry_zone") or [None, None]
            c.execute("""INSERT INTO scan_observations (obs_id, scan_id, rank, symbol,
                         setup_type, score, score_components, scan_price, entry_low, entry_high,
                         invalidation, target_1, target_2, horizon_days, features, thesis,
                         thesis_json, verdict, status, status_price, source_timestamp, created_at)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (obs_id, scan_id, i, cand.get("symbol"),
                       (setups[0]["type"] if setups else cand.get("primary_setup")),
                       sc.get("total"), json.dumps(sc.get("components"), default=str),
                       ind.get("price"), ez[0], ez[1],
                       lv.get("invalidation"), lv.get("target_1"), lv.get("target_2"),
                       (setups[0].get("horizon") if setups else None),
                       json.dumps({"indicators": ind, "setups": setups, "levels": lv,
                                   "sector": cand.get("sector"), "sector_rank": cand.get("sector_rank"),
                                   "regime": regime, "catalyst": cand.get("catalyst"),
                                   "sentiment": cand.get("sentiment")}, default=str),
                       human, json.dumps(struct, default=str),
                       json.dumps(cand.get("verdict"), default=str) if cand.get("verdict") else None,
                       "selected", ind.get("price"), ind.get("source_timestamp"), now))
            c.execute("""INSERT INTO observation_events (event_id, obs_id, at, kind,
                         from_status, to_status, price, reason) VALUES (?,?,?,?,?,?,?,?)""",
                      (uuid.uuid4().hex[:16], obs_id, now, "created", None, "selected",
                       ind.get("price"), f"rank {i} of scan {scan_id}"))
            observations.append({"obs_id": obs_id, "symbol": cand.get("symbol"), "rank": i})
    return {"state": "ok", "scan_id": scan_id, "trading_date": trading_date,
            "count": len(observations), "observations": observations}


# ── Reading ──────────────────────────────────────────────────────────────────

def _json_field(d: Dict[str, Any], *keys: str) -> Dict[str, Any]:
    for k in keys:
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except (TypeError, ValueError):
                pass
    return d


def _row_to_obs(r: sqlite3.Row) -> Dict[str, Any]:
    return _json_field(dict(r), "score_components", "features", "thesis_json", "verdict")


def latest_scan_id(preset: Optional[str] = None) -> Optional[str]:
    init_db()
    with _conn() as c:
        if preset:
            row = c.execute("SELECT scan_id FROM scan_runs WHERE preset=? ORDER BY created_at DESC LIMIT 1",
                            (preset,)).fetchone()
        else:
            row = c.execute("SELECT scan_id FROM scan_runs ORDER BY created_at DESC LIMIT 1").fetchone()
    return row["scan_id"] if row else None


def get_scan_run(scan_id: str) -> Optional[Dict[str, Any]]:
    init_db()
    with _conn() as c:
        row = c.execute("SELECT * FROM scan_runs WHERE scan_id=?", (scan_id,)).fetchone()
    return _json_field(dict(row), "regime", "config") if row else None


def cohort(scan_id: Optional[str] = None, *, preset: Optional[str] = None, live: bool = True) -> Dict[str, Any]:
    """The Top-N for ONE scan (default: the most recent). `live=True` enriches each
    observation with a fresh quote and the return since scan. Read-only — never
    mutates status; use `update_observations` for that."""
    init_db()
    sid = scan_id or latest_scan_id(preset)
    if not sid:
        return {"state": "empty", "reason": "no scan has been captured yet", "observations": []}
    run = get_scan_run(sid)
    with _conn() as c:
        rows = c.execute("SELECT * FROM scan_observations WHERE scan_id=? ORDER BY rank", (sid,)).fetchall()
    obs = [_row_to_obs(r) for r in rows]
    if live and obs:
        quotes = _R.quotes([o["symbol"] for o in obs], ttl=30, timeout=30.0)
        for o in obs:
            q = quotes.get(o["symbol"]) or {}
            px = q.get("price")
            o["current_price"] = px
            o["return_since_scan_pct"] = (round(100 * (px - o["scan_price"]) / o["scan_price"], 2)
                                          if (px and o.get("scan_price")) else None)
            o["quote_state"] = q.get("state")
    return {"state": "ok", "scan": run, "observations": obs, "count": len(obs)}


def list_scan_runs(limit: int = 30) -> Dict[str, Any]:
    """History picker: every past scan_run with its symbol/rank/score/status summary,
    newest first. Nothing here is ever destroyed by a later scan."""
    init_db()
    with _conn() as c:
        rows = c.execute("SELECT * FROM scan_runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        runs = []
        for r in rows:
            d = _json_field(dict(r), "regime", "config")
            obs = c.execute("SELECT symbol, rank, score, setup_type, status FROM scan_observations "
                            "WHERE scan_id=? ORDER BY rank", (d["scan_id"],)).fetchall()
            d["observations"] = [dict(o) for o in obs]
            runs.append(d)
    return {"state": "ok", "runs": runs, "count": len(runs)}


# ── Status updates (event layer — never removes a row) ───────────────────────

def _transition(c: sqlite3.Connection, row: sqlite3.Row, new_status: str,
                reason: str, price: Optional[float]) -> bool:
    if row["status"] == new_status:
        return False
    c.execute("UPDATE scan_observations SET status=?, status_price=? WHERE obs_id=?",
              (new_status, price, row["obs_id"]))
    c.execute("""INSERT INTO observation_events (event_id, obs_id, at, kind, from_status,
                 to_status, price, reason) VALUES (?,?,?,?,?,?,?,?)""",
              (uuid.uuid4().hex[:16], row["obs_id"], _now(), "status_change",
               row["status"], new_status, price, reason))
    return True


def update_observations(scan_id: Optional[str] = None) -> Dict[str, Any]:
    """Re-check every non-terminal observation against a fresh quote. Entry trigger,
    target hits and invalidation are EVENTS recorded on the row — the row itself is
    never deleted just because no entry occurred. Mirrors tracker.update_all's
    transition style but writes to observation_events."""
    init_db()
    with _conn() as c:
        q = "SELECT * FROM scan_observations WHERE status NOT IN ({})".format(",".join("?" * len(TERMINAL)))
        params: List[Any] = list(TERMINAL)
        if scan_id:
            q += " AND scan_id=?"
            params.append(scan_id)
        rows = c.execute(q, params).fetchall()
    if not rows:
        return {"state": "ok", "checked": 0, "updated": 0, "changes": []}
    syms = sorted({r["symbol"] for r in rows})
    quotes = _R.quotes(syms, ttl=30, timeout=40.0)
    updated, changes = 0, []
    with _LOCK, _conn() as c:
        for r in rows:
            qd = quotes.get(r["symbol"]) or {}
            px = qd.get("price")
            if qd.get("state") != "ok" or px is None:
                continue
            elo, ehi = r["entry_low"], r["entry_high"]
            inval, t1, t2 = r["invalidation"], r["target_1"], r["target_2"]
            new_status, reason = None, None
            if inval is not None and px <= inval:
                new_status, reason = "invalidated", f"price {px:.2f} broke invalidation {inval:.2f}"
            elif t2 is not None and px >= t2:
                new_status, reason = "target_2_hit", f"price {px:.2f} reached target 2 {t2:.2f}"
            elif t1 is not None and px >= t1 and r["status"] != "target_1_hit":
                new_status, reason = "target_1_hit", f"price {px:.2f} reached target 1 {t1:.2f}"
            elif (elo is not None and ehi is not None and elo <= px <= ehi
                  and r["status"] in ("selected", "watching", "approaching_entry")):
                new_status, reason = "entry_triggered", f"price {px:.2f} inside entry zone {elo:.2f}-{ehi:.2f}"
            elif r["status"] == "selected":
                new_status, reason = "watching", "first re-check after selection"
            if new_status is None:
                try:
                    days = (datetime.now(timezone.utc) - datetime.fromisoformat(r["created_at"])).days
                except ValueError:
                    days = 0
                hz = r["horizon_days"] if isinstance(r["horizon_days"], int) else 10
                if days >= max(10, hz):
                    if r["status"] in ("selected", "watching", "approaching_entry"):
                        new_status = "expired"
                        reason = f"{days}d without an entry trigger — past its horizon"
                    elif r["status"] == "entry_triggered":
                        new_status = "horizon_complete"
                        reason = f"{days}d since selection — horizon elapsed without hitting a target or stop"
            if new_status and _transition(c, r, new_status, reason, px):
                updated += 1
                changes.append({"symbol": r["symbol"], "scan_id": r["scan_id"], "obs_id": r["obs_id"],
                                "from": r["status"], "to": new_status, "reason": reason})
    return {"state": "ok", "checked": len(rows), "updated": updated, "changes": changes}


def close_observation(obs_id: str, reason: str = "closed manually",
                      status: str = "manually_closed") -> Dict[str, Any]:
    init_db()
    with _LOCK, _conn() as c:
        row = c.execute("SELECT * FROM scan_observations WHERE obs_id=?", (obs_id,)).fetchone()
        if not row:
            return {"state": "not_found"}
        _transition(c, row, status, reason, row["status_price"])
    return {"state": "ok", "obs_id": obs_id, "status": status}


# ── Forward returns — computed from research.bars, never a separate provider ──

def _trading_day_index(bars: List[dict], anchor: datetime) -> Optional[int]:
    """Index of the last bar at/before `anchor` — the 'day 0' the forward window
    is measured from."""
    idx = None
    for i, b in enumerate(bars):
        t = b.get("t")
        try:
            bt = datetime.fromisoformat(t.replace("Z", "+00:00")) if isinstance(t, str) else None
        except ValueError:
            bt = None
        if bt is None:
            continue
        if bt <= anchor:
            idx = i
        else:
            break
    return idx


def _snapshots_to_dict(rows: List[sqlite3.Row], row: sqlite3.Row) -> Dict[str, Any]:
    by_kind = {r["kind"]: r for r in rows}
    out: Dict[str, Any] = {"state": "ok", "symbol": row["symbol"],
                           "scan_price": row["scan_price"], "cached": True}
    for name in HORIZON_NAMES:
        r = by_kind.get(name)
        out[name] = ({"price": r["price"], "pct_return": r["pct_return"], "date": r["at"]}
                     if r else None)
    for k in ("mfe_pct", "mae_pct"):
        r = by_kind.get(k)
        out[k] = r["pct_return"] if r else None
    return out


def forward_returns(obs_id: str, *, use_cache: bool = True) -> Dict[str, Any]:
    """d0/d1/d3/d5/d10 close + pct return, plus MFE/MAE over the available window —
    all computed from `research.bars` (the same OHLCV pull the live scanner uses),
    anchored on the observation's own scan_price/created_at. A horizon that hasn't
    happened yet is None, never guessed. Memoized once d10 is available (it will
    never change); recomputed otherwise, since more bars may exist by the next call."""
    init_db()
    with _conn() as c:
        row = c.execute("SELECT * FROM scan_observations WHERE obs_id=?", (obs_id,)).fetchone()
    if not row:
        return {"state": "not_found"}
    if use_cache:
        with _conn() as c:
            cached_rows = c.execute("SELECT * FROM observation_snapshots WHERE obs_id=?", (obs_id,)).fetchall()
        if cached_rows and any(r["kind"] == "d10" for r in cached_rows):
            return _snapshots_to_dict(cached_rows, row)

    scan_price = row["scan_price"]
    symbol = row["symbol"]
    if not scan_price:
        return {"state": "no_scan_price"}
    b = _R.bars(symbol, rng="6mo", interval="1d")
    bars = b.get("bars") or []
    if b.get("state") != "ok" or not bars:
        return {"state": "no_data"}
    try:
        anchor = datetime.fromisoformat(row["created_at"])
    except ValueError:
        return {"state": "bad_scan_date"}
    i0 = _trading_day_index(bars, anchor)
    if i0 is None:
        return {"state": "no_anchor_bar"}

    out: Dict[str, Any] = {"state": "ok", "symbol": symbol, "scan_price": scan_price}
    for name, n in HORIZON_DAYS.items():
        j = i0 + n
        if j < len(bars) and bars[j].get("c") is not None:
            px = bars[j]["c"]
            out[name] = {"price": px, "pct_return": round(100 * (px - scan_price) / scan_price, 2),
                        "date": bars[j]["t"][:10]}
        else:
            out[name] = None
    window = bars[i0:min(i0 + 11, len(bars))]
    highs = [x["h"] for x in window if x.get("h") is not None]
    lows = [x["l"] for x in window if x.get("l") is not None]
    out["mfe_pct"] = round(100 * (max(highs) - scan_price) / scan_price, 2) if highs else None
    out["mae_pct"] = round(100 * (min(lows) - scan_price) / scan_price, 2) if lows else None
    out["bars_available_after_scan"] = max(0, len(bars) - i0 - 1)

    with _LOCK, _conn() as c:
        c.execute("DELETE FROM observation_snapshots WHERE obs_id=?", (obs_id,))
        now = _now()
        for name in HORIZON_NAMES:
            v = out.get(name)
            if v:
                c.execute("""INSERT INTO observation_snapshots (obs_id, kind, at, price, pct_return, computed_at)
                             VALUES (?,?,?,?,?,?)""",
                          (obs_id, name, v.get("date"), v.get("price"), v.get("pct_return"), now))
        for k in ("mfe_pct", "mae_pct"):
            if out.get(k) is not None:
                c.execute("""INSERT INTO observation_snapshots (obs_id, kind, at, price, pct_return, computed_at)
                             VALUES (?,?,?,?,?,?)""", (obs_id, k, None, None, out[k], now))
    return out


def _benchmark_forward(scan_id: str, created_at: str, symbol: str = "SPY") -> Dict[str, Any]:
    """SPY's own forward return from the same anchor bar — so a Top-5 candidate's
    return can be judged against what the market did, not in isolation. Cached
    per-process by (symbol, scan_id) since research.bars is itself TTL-cached."""
    key = f"{symbol}:{scan_id}"
    if key in _BENCH_CACHE:
        return _BENCH_CACHE[key]
    result: Dict[str, Any] = {"state": "no_data"}
    b = _R.bars(symbol, rng="6mo", interval="1d")
    bars = b.get("bars") or []
    if b.get("state") == "ok" and bars:
        try:
            anchor = datetime.fromisoformat(created_at)
        except ValueError:
            anchor = None
        if anchor:
            i0 = _trading_day_index(bars, anchor)
            if i0 is not None:
                base = bars[i0]["c"]
                result = {"state": "ok", "base_price": base}
                for name, n in HORIZON_DAYS.items():
                    j = i0 + n
                    result[name] = (round(100 * (bars[j]["c"] - base) / base, 2)
                                    if (j < len(bars) and base and bars[j].get("c") is not None) else None)
    _BENCH_CACHE[key] = result
    return result


# ── Scanner Intelligence: aggregated stats over PERSISTED observations only ───

def _all_observations() -> List[sqlite3.Row]:
    init_db()
    with _conn() as c:
        return c.execute("SELECT * FROM scan_observations ORDER BY created_at").fetchall()


def performance_by(group_fn: Callable[[sqlite3.Row], Any], horizon: str = "d5") -> Dict[str, Any]:
    """Shared grouped-performance aggregator — the ONE place the rank/score-bucket/
    setup-type breakdowns compute their numbers, so they can't drift apart. Every
    group reports its sample size; nothing is a verdict below that context."""
    groups: Dict[Any, List[float]] = {}
    alphas: Dict[Any, List[float]] = {}
    for r in _all_observations():
        fr = forward_returns(r["obs_id"])
        v = (fr.get(horizon) or {}).get("pct_return") if fr.get("state") == "ok" else None
        if v is None:
            continue
        g = group_fn(r)
        if g is None:
            continue
        groups.setdefault(g, []).append(v)
        bench = _benchmark_forward(r["scan_id"], r["created_at"])
        bv = bench.get(horizon) if bench.get("state") == "ok" else None
        if bv is not None:
            alphas.setdefault(g, []).append(v - bv)
    out = {}
    for g, vals in groups.items():
        out[str(g)] = {
            "n": len(vals),
            "avg_return_pct": round(sum(vals) / len(vals), 2),
            "hit_rate_pct": round(100 * sum(1 for x in vals if x > 0) / len(vals), 1),
            "avg_alpha_pct": round(sum(alphas[g]) / len(alphas[g]), 2) if alphas.get(g) else None,
            "alpha_sample": len(alphas.get(g, [])),
        }
    return {"state": "ok", "horizon": horizon, "groups": out,
            "note": "sample sizes below ~12 are indicative only, not a verdict"}


def rank_performance(horizon: str = "d5") -> Dict[str, Any]:
    return performance_by(lambda r: r["rank"], horizon)


def score_bucket_performance(horizon: str = "d5",
                             buckets: Tuple[Tuple[int, int], ...] = ((0, 60), (60, 70), (70, 80), (80, 101))
                             ) -> Dict[str, Any]:
    def bucket(r: sqlite3.Row) -> Optional[str]:
        s = r["score"]
        if s is None:
            return None
        for lo, hi in buckets:
            if lo <= s < hi:
                return f"{lo}-{hi - 1}"
        return None
    return performance_by(bucket, horizon)


def setup_type_performance(horizon: str = "d5") -> Dict[str, Any]:
    return performance_by(lambda r: r["setup_type"] or "unspecified", horizon)


def repetition_stats(lookback_scans: int = 20, *, warn_threshold: float = 0.5) -> Dict[str, Any]:
    """Per-symbol appearance stats across the last N scan_runs — why the same names
    keep showing up, measured rather than assumed. Never removes/limits a symbol;
    this is diagnostics only."""
    init_db()
    with _conn() as c:
        run_rows = c.execute("SELECT scan_id, created_at FROM scan_runs ORDER BY created_at DESC LIMIT ?",
                             (lookback_scans,)).fetchall()
        scan_ids = [r["scan_id"] for r in run_rows]
        if not scan_ids:
            return {"state": "empty", "scans_considered": 0, "symbols": []}
        scan_order = {sid: i for i, sid in enumerate(scan_ids)}  # 0 = most recent
        qmarks = ",".join("?" * len(scan_ids))
        obs_rows = c.execute(f"SELECT * FROM scan_observations WHERE scan_id IN ({qmarks})", scan_ids).fetchall()

    by_symbol: Dict[str, List[sqlite3.Row]] = {}
    for r in obs_rows:
        by_symbol.setdefault(r["symbol"], []).append(r)

    total_scans = len(scan_ids)
    out = []
    for sym, rows in by_symbol.items():
        rows_sorted = sorted(rows, key=lambda r: scan_order.get(r["scan_id"], 10 ** 9))
        appearances = len(rows_sorted)
        avg_rank = round(sum(r["rank"] for r in rows_sorted) / appearances, 2)
        avg_score = round(sum((r["score"] or 0) for r in rows_sorted) / appearances, 2)
        streak = 0
        for sid in scan_ids:  # most-recent-first
            if any(r["scan_id"] == sid for r in rows_sorted):
                streak += 1
            else:
                break
        fwd = []
        for r in rows_sorted:
            fr = forward_returns(r["obs_id"])
            d5 = (fr.get("d5") or {}).get("pct_return") if fr.get("state") == "ok" else None
            if d5 is not None:
                fwd.append(d5)
        rate = appearances / total_scans
        out.append({
            "symbol": sym, "appearances": appearances, "appearance_rate": round(rate, 2),
            "avg_rank": avg_rank, "avg_score": avg_score, "consecutive_streak": streak,
            "first_seen": min(r["created_at"] for r in rows_sorted),
            "last_seen": max(r["created_at"] for r in rows_sorted),
            "avg_5d_return_pct": round(sum(fwd) / len(fwd), 2) if fwd else None,
            "sample_size_5d": len(fwd),
            "repeated_candidate_warning": bool(rate >= warn_threshold and appearances >= 3),
        })
    out.sort(key=lambda x: -x["appearances"])
    return {"state": "ok", "scans_considered": total_scans, "symbols": out}


# ── Daily review — deterministic, numbers only, no LLM narrative ─────────────

def daily_review(trading_date: Optional[str] = None) -> Dict[str, Any]:
    """A same-day (d1) read of that date's cohort(s) — every field is a computed
    number from persisted rows. Deliberately does NOT synthesize "what worked" prose:
    that phrasing belongs to whoever consumes this (dashboard/report), and only after
    enough days exist to say more than "one day of noise" (see rank_performance for
    the accumulated read across all days)."""
    init_db()
    td = trading_date or datetime.now(timezone.utc).date().isoformat()
    with _conn() as c:
        runs = c.execute("SELECT * FROM scan_runs WHERE trading_date=? ORDER BY created_at", (td,)).fetchall()
    if not runs:
        return {"state": "empty", "trading_date": td, "reason": "no scan captured that day"}

    items = []
    for run in runs:
        with _conn() as c:
            obs = c.execute("SELECT * FROM scan_observations WHERE scan_id=? ORDER BY rank",
                            (run["scan_id"],)).fetchall()
        bench = _benchmark_forward(run["scan_id"], run["created_at"])
        b1 = bench.get("d1") if bench.get("state") == "ok" else None
        for o in obs:
            fr = forward_returns(o["obs_id"])
            d1 = (fr.get("d1") or {}).get("pct_return") if fr.get("state") == "ok" else None
            items.append({"symbol": o["symbol"], "rank": o["rank"], "score": o["score"],
                          "setup_type": o["setup_type"], "status": o["status"],
                          "return_d1_pct": d1,
                          "alpha_d1_pct": round(d1 - b1, 2) if (d1 is not None and b1 is not None) else None})

    returns = [i["return_d1_pct"] for i in items if i["return_d1_pct"] is not None]
    cohort_avg = round(sum(returns) / len(returns), 2) if returns else None
    bench0 = _benchmark_forward(runs[0]["scan_id"], runs[0]["created_at"])
    bench_avg = bench0.get("d1") if bench0.get("state") == "ok" else None
    scored = [i for i in items if i["return_d1_pct"] is not None]
    best = max(scored, key=lambda i: i["return_d1_pct"], default=None)
    worst = min(scored, key=lambda i: i["return_d1_pct"], default=None)
    top = [i for i in scored if i["rank"] <= 2]
    bottom = [i for i in scored if i["rank"] >= 4]
    spread = (round((sum(i["return_d1_pct"] for i in top) / len(top)) -
                    (sum(i["return_d1_pct"] for i in bottom) / len(bottom)), 2)
             if (top and bottom) else None)

    return {
        "state": "ok", "trading_date": td, "scans": len(runs), "items": items,
        "cohort_avg_return_pct": cohort_avg, "benchmark_return_pct": bench_avg,
        "cohort_alpha_pct": (round(cohort_avg - bench_avg, 2)
                             if (cohort_avg is not None and bench_avg is not None) else None),
        "best": {"symbol": best["symbol"], "return_pct": best["return_d1_pct"]} if best else None,
        "worst": {"symbol": worst["symbol"], "return_pct": worst["return_d1_pct"]} if worst else None,
        "top_vs_bottom_rank_spread_pct": spread,
        "note": "single-day numbers are one observation, not a verdict — see rank_performance / "
                "score_bucket_performance for the accumulated read across every scan",
    }
