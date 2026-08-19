"""The terminal's OWN empirical dataset.

`history.py` reconstructs the past from OHLCV, which is honest but limited: it can only
see what daily bars can express. It cannot recover the news signal, the catalyst set,
the option chain or the account state that existed at a historical moment, because none
of those were ever recorded.

So record them from now on. Every assessment writes one row — the features, the regime,
the news signal, the catalysts, the levels and the decision as they stood at that
instant — and `attach_outcomes()` later fills in what actually happened. Over time this
becomes a dataset with the very fields the reconstruction cannot supply, and the
validation stops being technical-only.

Two rules:
  * The snapshot is written BEFORE the outcome is knowable, and the outcome is attached
    by a separate pass. There is no code path that can write both at once, which is
    what makes the stored record safe to learn from.
  * One row per symbol/strategy/session. A widget re-rendering is not a new signal.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in ("..", os.path.join("..", "lab"), os.path.join("..", "src")):
    sys.path.insert(0, os.path.join(_HERE, _p))
sys.path.insert(0, _HERE)

DATA_DIR = os.path.join(os.path.expanduser("~"), ".tradingview_mcp_data")
DB_PATH = os.environ.get("STRATEGY_STORE_DB", os.path.join(DATA_DIR, "strategy_snapshots.db"))
SCHEMA_VERSION = 1
_LOCK = threading.Lock()

_DDL = """
CREATE TABLE IF NOT EXISTS snapshots (
  snapshot_id      INTEGER PRIMARY KEY AUTOINCREMENT,
  at               TEXT NOT NULL,
  session_date     TEXT NOT NULL,
  symbol           TEXT NOT NULL,
  strategy         TEXT,
  strategy_score   REAL,
  decision         TEXT,
  instrument       TEXT,
  structural_score REAL,
  context_score    REAL,
  -- structural + context, exactly. NOT the scanner's 0-100 setup score, which is
  -- stored separately as `strategy_score`.
  decision_score   REAL,
  entry            REAL,
  stop             REAL,
  target_1         REAL,
  target_2         REAL,
  rr_target_1      REAL,
  features_json    TEXT,
  regime_json      TEXT,
  news_json        TEXT,
  catalysts_json   TEXT,
  components_json  TEXT,
  hard_failures_json TEXT,
  history_class    TEXT,
  history_fit      REAL,
  history_n        INTEGER,
  engine_version   TEXT,
  -- filled later, by a separate pass, never at write time
  outcome          TEXT,
  outcome_at       TEXT,
  outcome_return_pct REAL,
  outcome_exit     TEXT,
  outcome_held_days INTEGER,
  outcome_mfe_pct  REAL,
  outcome_mae_pct  REAL,
  UNIQUE(session_date, symbol, strategy)
);
CREATE INDEX IF NOT EXISTS ix_snap_symbol ON snapshots(symbol);
CREATE INDEX IF NOT EXISTS ix_snap_open   ON snapshots(outcome) WHERE outcome IS NULL;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _migrate(c: sqlite3.Connection) -> None:
    """`total_score` was renamed to `decision_score` when the +20 display constant was
    removed — the column held the inflated number, so keeping the old name would
    silently mix two scales in one column."""
    try:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(snapshots)")}
        if cols and "total_score" in cols and "decision_score" not in cols:
            c.execute("ALTER TABLE snapshots RENAME COLUMN total_score TO decision_score")
            # Rows written before the fix hold `structural + context + 20`.
            c.execute("UPDATE snapshots SET decision_score = decision_score - 20.0 "
                      "WHERE decision_score IS NOT NULL")
            c.commit()
    except sqlite3.Error:
        pass


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    _migrate(c)
    c.executescript(_DDL)
    c.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version',?)",
              (str(SCHEMA_VERSION),))
    c.commit()
    return c


def _j(v: Any) -> Optional[str]:
    try:
        return json.dumps(v, default=str) if v is not None else None
    except Exception:  # noqa: BLE001
        return None


def record(*, symbol: str, assessment: Dict[str, Any],
           candidate: Optional[Dict[str, Any]] = None,
           news: Optional[Dict[str, Any]] = None,
           history: Optional[Dict[str, Any]] = None,
           regime: Optional[Dict[str, Any]] = None,
           features: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Write ONE snapshot for this symbol/strategy/session. Idempotent within a day.

    Never raises into the caller: a telemetry failure must not break a decision.
    """
    try:
        cand = candidate or {}
        lv = (cand.get("levels") or assessment.get("levels") or {})
        setups = cand.get("setups") or assessment.get("setups") or []
        strategy = (cand.get("primary_setup")
                    or (setups[0].get("type") if setups else None))
        now = datetime.now(timezone.utc)
        row = {
            "at": now.isoformat(), "session_date": now.date().isoformat(),
            "symbol": (symbol or "").upper(), "strategy": strategy,
            "strategy_score": ((cand.get("score") or assessment.get("scanner_score") or {})
                               .get("total")),
            "decision": assessment.get("label"),
            "instrument": assessment.get("instrument"),
            "structural_score": assessment.get("structural_score"),
            "context_score": assessment.get("context_score"),
            "decision_score": assessment.get("decision_score"),
            "entry": lv.get("reference_price"), "stop": lv.get("invalidation"),
            "target_1": lv.get("target_1"), "target_2": lv.get("target_2"),
            "rr_target_1": lv.get("rr_target_1"),
            "features_json": _j(features),
            "regime_json": _j(regime),
            "news_json": _j({k: (news or {}).get(k) for k in
                             ("direction", "sentiment_score", "confidence",
                              "relevant_count", "direct_count", "counts",
                              "tier_breakdown", "source_diversity")}),
            "catalysts_json": _j((news or {}).get("catalysts")),
            "components_json": _j(assessment.get("components")),
            "hard_failures_json": _j(assessment.get("hard_failures")),
            "history_class": (history or {}).get("classification"),
            "history_fit": (history or {}).get("fit_pct"),
            "history_n": (history or {}).get("sample_size"),
            "engine_version": os.environ.get("ENGINE_VERSION", "terminal-2"),
        }
        cols = ",".join(row)
        marks = ",".join("?" * len(row))
        with _LOCK, _conn() as c:
            cur = c.execute(
                f"INSERT INTO snapshots({cols}) VALUES({marks}) "
                f"ON CONFLICT(session_date,symbol,strategy) DO NOTHING",
                list(row.values()))
            c.commit()
            return {"state": "ok", "written": cur.rowcount == 1,
                    "symbol": row["symbol"], "strategy": strategy,
                    "session_date": row["session_date"]}
    except Exception as e:  # noqa: BLE001 — telemetry must never break a decision
        return {"state": "error", "reason": f"{type(e).__name__}: {str(e)[:120]}"}


def open_snapshots(limit: int = 500) -> List[Dict[str, Any]]:
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM snapshots WHERE outcome IS NULL ORDER BY at LIMIT ?", (limit,))]


def attach_outcomes(horizon_days: int = 20) -> Dict[str, Any]:
    """Fill in what happened, for snapshots old enough to have an answer.

    Runs the SAME outcome measurement the historical engine uses, so a recorded
    signal and a reconstructed one are scored identically.
    """
    import research as _R
    import history as _H

    rows = open_snapshots()
    if not rows:
        return {"state": "ok", "checked": 0, "resolved": 0}
    resolved = 0
    by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_symbol.setdefault(r["symbol"], []).append(r)

    with _LOCK, _conn() as c:
        for sym, srows in by_symbol.items():
            b = _R.bars(sym, rng="1y", interval="1d", ttl=6 * 3600)
            bars = [x for x in (b.get("bars") or []) if x.get("c") is not None]
            if not bars:
                continue
            index = {str(x.get("t", ""))[:10]: k for k, x in enumerate(bars)}
            for r in srows:
                i = index.get(r["session_date"])
                if i is None or r["entry"] is None or r["stop"] is None:
                    continue
                if i + horizon_days >= len(bars):
                    continue                    # not enough forward data yet
                out = _H._outcome(bars, i, {"reference_price": r["entry"],
                                            "invalidation": r["stop"],
                                            "target_1": r["target_1"],
                                            "target_2": r["target_2"]},
                                  horizon_days)
                if out is None:
                    continue
                c.execute(
                    "UPDATE snapshots SET outcome=?, outcome_at=?, outcome_return_pct=?, "
                    "outcome_exit=?, outcome_held_days=?, outcome_mfe_pct=?, "
                    "outcome_mae_pct=? WHERE snapshot_id=?",
                    ("resolved", datetime.now(timezone.utc).isoformat(),
                     out["return_pct"], out["exit_reason"], out["held_days"],
                     out["mfe_pct"], out["mae_pct"], r["snapshot_id"]))
                resolved += 1
        c.commit()
    return {"state": "ok", "checked": len(rows), "resolved": resolved,
            "horizon_days": horizon_days}


def stats() -> Dict[str, Any]:
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) n FROM snapshots").fetchone()["n"]
        done = c.execute("SELECT COUNT(*) n FROM snapshots WHERE outcome IS NOT NULL"
                         ).fetchone()["n"]
        by = [dict(r) for r in c.execute(
            "SELECT strategy, COUNT(*) n, "
            "       SUM(CASE WHEN outcome IS NOT NULL THEN 1 ELSE 0 END) resolved, "
            "       ROUND(AVG(outcome_return_pct),2) avg_return "
            "FROM snapshots GROUP BY strategy ORDER BY n DESC")]
        first = c.execute("SELECT MIN(at) a FROM snapshots").fetchone()["a"]
    return {"state": "ok", "db": DB_PATH, "total": total, "resolved": done,
            "pending": total - done, "by_strategy": by, "since": first,
            "note": ("Recorded live and resolved by a later pass — the snapshot is "
                     "always written before its outcome can be known.")}
