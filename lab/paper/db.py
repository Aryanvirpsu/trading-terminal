"""Paper-trading store — SQLite with versioned migrations.

Separate from the $500 `strategy-500` ledger and from the MCP `paper_trade` tools:
this is the evidence-collection vehicle for the paper-trading launch, and it needs
guarantees those don't provide — deterministic order IDs, an append-only audit trail,
and a journal that keeps REJECTED and MONITOR signals so we can later measure whether
the decision gates block winners.

The DB lives OUTSIDE the repository (`~/.tradingview_mcp_data/paper/paper.db`) so no
account state is ever tracked by git.

Schema (v1):
  signals    — every TRADEABLE / MONITOR / REJECT evaluation, with full evidence
  orders     — order lifecycle (deterministic id, status, audit history)
  fills      — individual (possibly partial) fills
  positions  — open + closed positions with realised P&L
  equity     — daily equity/cash snapshots for the curve and drawdown
  audit      — append-only event log (never updated, never deleted)
  meta       — schema version + config version
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1

_DATA_DIR = os.path.expanduser(os.environ.get(
    "PAPER_DATA_DIR", "~/.tradingview_mcp_data/paper"))
_LOCK = threading.RLock()
_CONN: Optional[sqlite3.Connection] = None

# Ledgers are NAMED. The active one models the real Robinhood account being
# simulated ($500 cash). The old $10,000 default is demo/test data only and must
# never be mixed into real statistics — a strategy that works on $10k can be
# completely unreachable on $500, so the balance is part of the experiment.
DEFAULT_LEDGER = "robinhood_500_baseline"
DEMO_LEDGER = "demo_10k"


def ledger_name() -> str:
    return os.environ.get("PAPER_LEDGER", DEFAULT_LEDGER).strip() or DEFAULT_LEDGER


def db_path(ledger: Optional[str] = None) -> str:
    return os.path.join(_DATA_DIR, f"{ledger or ledger_name()}.db")


def archive_ledger(name: str, reason: str = "superseded") -> Optional[str]:
    """Move a ledger file into `archive/` so it can never be read as live data."""
    src = db_path(name)
    if not os.path.exists(src):
        return None
    adir = os.path.join(_DATA_DIR, "archive")
    os.makedirs(adir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dst = os.path.join(adir, f"{name}.{stamp}.{reason}.db")
    close()
    os.replace(src, dst)
    for suf in ("-wal", "-shm"):
        if os.path.exists(src + suf):
            try:
                os.replace(src + suf, dst + suf)
            except OSError:
                os.remove(src + suf)
    return dst


def list_ledgers() -> Dict[str, Any]:
    os.makedirs(_DATA_DIR, exist_ok=True)
    live = [f[:-3] for f in os.listdir(_DATA_DIR) if f.endswith(".db")]
    adir = os.path.join(_DATA_DIR, "archive")
    archived = sorted(os.listdir(adir)) if os.path.isdir(adir) else []
    return {"active": ledger_name(), "live": sorted(live), "archived": archived,
            "data_dir": _DATA_DIR}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    """Process-wide connection. WAL so a reader (dashboard) never blocks the writer."""
    global _CONN
    with _LOCK:
        if _CONN is not None:
            return _CONN
        os.makedirs(_DATA_DIR, exist_ok=True)
        conn = sqlite3.connect(db_path(), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        _CONN = conn
        migrate(conn)
        return conn


def close() -> None:
    global _CONN
    with _LOCK:
        if _CONN is not None:
            _CONN.close()
            _CONN = None


# ── Migrations ────────────────────────────────────────────────────────────────

_MIGRATIONS: Dict[int, List[str]] = {
    1: [
        """CREATE TABLE IF NOT EXISTS meta (
              key TEXT PRIMARY KEY, value TEXT)""",

        # Every decision the engine produced — including the ones we did NOT trade.
        """CREATE TABLE IF NOT EXISTS signals (
              signal_id      TEXT PRIMARY KEY,
              created_at     TEXT NOT NULL,
              session_date   TEXT NOT NULL,
              symbol         TEXT NOT NULL,
              strategy       TEXT NOT NULL,
              action         TEXT NOT NULL,          -- TRADEABLE | MONITOR | REJECT
              sector         TEXT,
              industry       TEXT,
              market_regime  TEXT,
              quality        REAL,
              quality_threshold REAL,
              confidence     REAL,
              data_quality_json TEXT,
              freshness_state   TEXT,
              freshness_json    TEXT,
              provenance_json   TEXT,
              gates_json        TEXT,
              failed_gates      TEXT,
              entry REAL, stop REAL, target REAL, quantity REAL,
              planned_risk   REAL,
              expected_value REAL,
              expected_r     REAL,
              supporting_json TEXT,
              conflicting_json TEXT,
              scenarios_json  TEXT,
              scanner_rank    INTEGER,
              config_version  TEXT,
              engine_version  TEXT,
              executed        INTEGER DEFAULT 0,     -- did this become a paper order?
              order_id        TEXT,
              -- shadow outcome tracking (filled in later for ALL actions)
              tracked_until   TEXT,
              mfe             REAL,                  -- max favourable excursion (per share)
              mae             REAL,                  -- max adverse excursion (per share)
              outcome         TEXT,                  -- target_hit | stop_hit | open | expired
              outcome_pnl_ps  REAL,
              outcome_at      TEXT
           )""",
        "CREATE INDEX IF NOT EXISTS idx_sig_date ON signals(session_date)",
        "CREATE INDEX IF NOT EXISTS idx_sig_sym ON signals(symbol)",
        "CREATE INDEX IF NOT EXISTS idx_sig_action ON signals(action)",
        "CREATE INDEX IF NOT EXISTS idx_sig_strategy ON signals(strategy)",

        """CREATE TABLE IF NOT EXISTS orders (
              order_id     TEXT PRIMARY KEY,
              signal_id    TEXT,
              created_at   TEXT NOT NULL,
              session_date TEXT NOT NULL,
              symbol       TEXT NOT NULL,
              side         TEXT NOT NULL,            -- BUY | SELL
              order_type   TEXT NOT NULL,            -- MARKET | LIMIT | STOP
              quantity     REAL NOT NULL,
              limit_price  REAL,
              stop_price   REAL,
              tif          TEXT DEFAULT 'DAY',
              status       TEXT NOT NULL,            -- pending|partial|filled|cancelled|rejected|expired
              reject_reason TEXT,
              filled_qty   REAL DEFAULT 0,
              avg_fill     REAL,
              fees         REAL DEFAULT 0,
              intent       TEXT,                     -- entry | exit_stop | exit_target | exit_manual
              strategy     TEXT,
              updated_at   TEXT,
              FOREIGN KEY(signal_id) REFERENCES signals(signal_id)
           )""",
        "CREATE INDEX IF NOT EXISTS idx_ord_date ON orders(session_date)",
        "CREATE INDEX IF NOT EXISTS idx_ord_status ON orders(status)",

        """CREATE TABLE IF NOT EXISTS fills (
              fill_id    TEXT PRIMARY KEY,
              order_id   TEXT NOT NULL,
              filled_at  TEXT NOT NULL,
              quantity   REAL NOT NULL,
              price      REAL NOT NULL,
              fees       REAL DEFAULT 0,
              slippage   REAL,                       -- vs the reference price
              reference  REAL,                       -- ask (buy) / bid (sell) used
              liquidity  TEXT,                       -- normal | gap | partial
              note       TEXT,
              FOREIGN KEY(order_id) REFERENCES orders(order_id)
           )""",
        "CREATE INDEX IF NOT EXISTS idx_fill_order ON fills(order_id)",

        """CREATE TABLE IF NOT EXISTS positions (
              position_id  TEXT PRIMARY KEY,
              signal_id    TEXT,
              symbol       TEXT NOT NULL,
              strategy     TEXT,
              sector       TEXT,
              opened_at    TEXT NOT NULL,
              closed_at    TEXT,
              quantity     REAL NOT NULL,
              avg_entry    REAL NOT NULL,
              stop         REAL, target REAL,
              avg_exit     REAL,
              status       TEXT NOT NULL,            -- open | closed
              realized_pnl REAL,
              fees         REAL DEFAULT 0,
              exit_reason  TEXT,
              mfe REAL, mae REAL,
              planned_risk REAL,
              FOREIGN KEY(signal_id) REFERENCES signals(signal_id)
           )""",
        "CREATE INDEX IF NOT EXISTS idx_pos_status ON positions(status)",
        "CREATE INDEX IF NOT EXISTS idx_pos_sym ON positions(symbol)",

        """CREATE TABLE IF NOT EXISTS equity (
              session_date TEXT PRIMARY KEY,
              recorded_at  TEXT NOT NULL,
              cash         REAL NOT NULL,
              positions_value REAL NOT NULL,
              equity       REAL NOT NULL,
              realized_pnl REAL DEFAULT 0,
              unrealized_pnl REAL DEFAULT 0,
              peak_equity  REAL,
              drawdown_pct REAL
           )""",

        # Append-only. Nothing in the codebase may UPDATE or DELETE from this table.
        """CREATE TABLE IF NOT EXISTS audit (
              audit_id  INTEGER PRIMARY KEY AUTOINCREMENT,
              at        TEXT NOT NULL,
              entity    TEXT NOT NULL,               -- order | position | signal | risk | system
              entity_id TEXT,
              event     TEXT NOT NULL,
              detail_json TEXT
           )""",
        "CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit(entity, entity_id)",

        # Options shadow mode — recorded, never placed into the ledger.
        """CREATE TABLE IF NOT EXISTS options_shadow (
              shadow_id    TEXT PRIMARY KEY,
              created_at   TEXT NOT NULL,
              session_date TEXT NOT NULL,
              signal_id    TEXT,
              symbol       TEXT NOT NULL,
              contract     TEXT,
              expiry       TEXT, strike REAL, option_type TEXT,
              bid REAL, ask REAL, assumed_fill REAL, spread_pct REAL,
              volume REAL, open_interest REAL,
              delta REAL, gamma REAL, theta REAL, vega REAL, iv REAL,
              iv_context TEXT, expected_move REAL, break_even REAL,
              max_loss REAL, ev_after_costs REAL,
              preference   TEXT,                     -- prefer-stock|prefer-option|avoid-both
              gradeable    INTEGER DEFAULT 0,
              missing_json TEXT,
              outcome      TEXT, outcome_at TEXT, outcome_pnl REAL,
              FOREIGN KEY(signal_id) REFERENCES signals(signal_id)
           )""",
        "CREATE INDEX IF NOT EXISTS idx_shadow_date ON options_shadow(session_date)",
    ],
}


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations. Idempotent and safe to call on every connect."""
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    current = int(row["value"]) if row else 0
    for v in sorted(_MIGRATIONS):
        if v > current:
            for stmt in _MIGRATIONS[v]:
                conn.execute(stmt)
            conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)",
                         (str(v),))
            current = v
    conn.commit()
    return current


def schema_version(conn: Optional[sqlite3.Connection] = None) -> int:
    conn = conn or connect()
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(row["value"]) if row else 0


# ── Config version (recorded on every signal for reproducibility) ─────────────

def config_version() -> str:
    """A short, stable fingerprint of the knobs that change engine behaviour, so a
    journal row can be tied to the configuration that produced it."""
    import hashlib
    keys = [
        # decision engine
        "TRADINGVIEW_ENABLED", "DECISION_ALLOW_STALE", "DECISION_MIN_DATA_QUALITY",
        "DECISION_CONVICTION_MIN", "DECISION_ALLOW_FALLBACK_TRADEABLE",
        "DECISION_ALLOW_HIGH_DISAGREEMENT", "SENTIMENT_MODEL_ENABLED",
        # simulated account — the balance IS part of the experiment
        "PAPER_LEDGER", "PAPER_INITIAL_CASH", "PAPER_INITIAL_EQUITY",
        "PAPER_BUYING_POWER", "PAPER_MARGIN_ENABLED", "PAPER_ALLOW_SHORTING",
        "PAPER_ALLOW_NAKED_OPTIONS", "PAPER_FRACTIONAL_SHARES",
        # risk
        "PAPER_MAX_LOSS_PER_TRADE", "PAPER_MAX_POSITION_NOTIONAL",
        "PAPER_MIN_CASH_RESERVE_USD", "PAPER_MAX_DAILY_LOSS_USD",
        "PAPER_MAX_DRAWDOWN_USD", "PAPER_MAX_ENTRIES_PER_DAY", "PAPER_MAX_OPEN",
        "PAPER_MAX_PER_SECTOR", "PAPER_MAX_CORRELATED", "PAPER_RISK_PER_TRADE",
        # options
        "PAPER_MAX_OPTION_PREMIUM", "PAPER_OPTIONS_SHADOW_ONLY",
        # execution
        "PAPER_SLIPPAGE_BPS", "PAPER_GAP_SLIPPAGE_BPS", "PAPER_FEE_PER_SHARE",
        "PAPER_FEE_PCT", "PAPER_STRATEGIES",
    ]
    blob = ";".join(f"{k}={os.environ.get(k,'')}" for k in keys)
    return f"cfg-{hashlib.sha1(blob.encode()).hexdigest()[:10]}"


def set_meta(key: str, value: str) -> None:
    conn = connect()
    with _LOCK:
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value))
        conn.commit()


def get_meta(key: str, default: Optional[str] = None) -> Optional[str]:
    row = connect().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


# ── Audit (append-only) ───────────────────────────────────────────────────────

def audit(entity: str, entity_id: Optional[str], event: str,
          detail: Optional[Dict[str, Any]] = None) -> None:
    conn = connect()
    with _LOCK:
        conn.execute(
            "INSERT INTO audit(at,entity,entity_id,event,detail_json) VALUES(?,?,?,?,?)",
            (utcnow(), entity, entity_id, event,
             json.dumps(detail, default=str) if detail is not None else None))
        conn.commit()


def audit_trail(entity_id: str) -> List[Dict[str, Any]]:
    rows = connect().execute(
        "SELECT * FROM audit WHERE entity_id=? ORDER BY audit_id", (entity_id,)).fetchall()
    return [dict(r) for r in rows]


# ── Small helpers ─────────────────────────────────────────────────────────────

def execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    conn = connect()
    with _LOCK:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur


def query(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    return [dict(r) for r in connect().execute(sql, params).fetchall()]


def query_one(sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    row = connect().execute(sql, params).fetchone()
    return dict(row) if row else None


def reset_for_tests(path: Optional[str] = None) -> None:
    """Point the store at a throwaway location (tests only)."""
    global _DATA_DIR
    close()
    if path:
        _DATA_DIR = path
    os.makedirs(_DATA_DIR, exist_ok=True)
    p = db_path()
    if os.path.exists(p):
        os.remove(p)
    for suf in ("-wal", "-shm"):
        if os.path.exists(p + suf):
            os.remove(p + suf)
    connect()
