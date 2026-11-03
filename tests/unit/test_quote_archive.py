"""Quote archive: telemetry only. Append-only, never raises, never changes a quote, a decision or the ledger."""
import datetime as dt
import sqlite3
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for folder in ("src", "dashboard", "lab"):
    sys.path.insert(0, str(ROOT / folder))

import decision_engine as de
from paper import db, risk, runtime as rt, shadow_log, workflow
from paper.fills import Quote

FINALISTS = [{"symbol": "AAA", "direction": "LONG", "sector": "energy", "strategy": "liquid_momentum", "scanner_rank": 1},
             {"symbol": "DDD", "direction": "LONG", "sector": "technology", "strategy": "liquid_momentum", "scanner_rank": 2}]


def _result(sym):
    return {"symbol": sym, "decision": "TRADEABLE", "direction": "LONG", "price": 40.0, "entry_range": [40.0, 40.1],
            "stop": 39.6, "target": 42.0, "suggested_shares": 100,
            "decision_gates": [{"name": "quality", "passed": True}, {"name": "liquidity", "passed": True, "value": 0.9}],
            "failed_gates": [], "freshness": {"state": "fresh"}, "data_source": "yahoo", "data_state": "fresh",
            "ev_breakdown": {"ev_per_share": 0.5, "expected_r": 1.1}, "confidence_quality": 66}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("PAPER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DECISION_ALLOW_STALE", "false")
    db.reset_for_tests(str(tmp_path))
    monkeypatch.setattr(risk, "_live_mark", lambda symbol: 40.0)
    monkeypatch.setattr(de.ss, "_pick_option_idea", lambda *a, **k: None)
    monkeypatch.setattr(workflow, "provider_health", lambda: {"healthy": True})
    monkeypatch.setattr(workflow.strategies, "scan", lambda: {"state": "ok", "finalists": FINALISTS})
    monkeypatch.setattr(de, "evaluate", lambda sym, *a, **k: _result(sym))
    monkeypatch.setattr(de, "_safe_regime", lambda: {})
    monkeypatch.setattr(workflow, "quote_for", lambda s: Quote(s, last=40, source_ts=time.time(), provider="yahoo"))
    shadow_log._QUOTE_STATE.update(ready_path=None, skip_until=0.0, consecutive=0, total=0, skipped=0, last_error=None)
    yield
    db.close()


def _rows(sql="SELECT * FROM shadow_quotes ORDER BY quote_id"):
    c = sqlite3.connect(shadow_log.path())
    c.row_factory = sqlite3.Row
    return c.execute(sql).fetchall()


def test_row_has_receipt_time_provider_stamp_and_synthetic_flag():
    q = Quote("AAA", last=100.0, source_ts=1_700_000_000.5, provider="yahoo")        # no provider bid/ask
    assert shadow_log.log_quote("AAA", q, "tracker_fast") is True
    r = _rows()[0]
    assert r["ok"] == 1 and r["symbol"] == "AAA" and r["context"] == "tracker_fast" and r["provider"] == "yahoo"
    assert r["source_ts"] == 1_700_000_000.5 and r["received_at"] and r["session_date"]
    assert r["bid_ask_synthetic"] == 1 and r["bid"] < 100.0 < r["ask"]
    real = Quote("BBB", last=50.0, bid=49.98, ask=50.02, source_ts=1.0, provider="x")
    shadow_log.log_quote("BBB", real, "tracker_full")
    assert _rows()[1]["bid_ask_synthetic"] == 0 and _rows()[1]["bid"] == 49.98


def test_missing_quote_is_recorded_as_missing():
    shadow_log.log_quote("ZZZ", None, "tracker_fast")
    r = _rows()[0]
    assert r["ok"] == 0 and r["last"] is None and r["source_ts"] is None and r["bid_ask_synthetic"] is None


def test_table_is_append_only():
    shadow_log.log_quote("AAA", Quote("AAA", last=1.0, source_ts=1.0), "t")
    c = sqlite3.connect(shadow_log.path())
    with pytest.raises(sqlite3.DatabaseError):
        c.execute("UPDATE shadow_quotes SET last=2")
    with pytest.raises(sqlite3.DatabaseError):
        c.execute("DELETE FROM shadow_quotes")


def test_never_raises_and_never_mutates_the_quote(monkeypatch):
    q = Quote("AAA", last=100.0, source_ts=1.0, provider="yahoo")
    before = (q.bid, q.ask, q.last)
    monkeypatch.setattr(shadow_log, "_connect", lambda: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    assert shadow_log.log_quote("AAA", q, "tracker_fast") is False
    assert shadow_log.log_quote("AAA", object(), "tracker_fast") is False          # garbage in, still no exception
    assert (q.bid, q.ask, q.last) == before


def test_discovery_and_tracker_log_with_cycle_context_and_trading_is_unchanged(monkeypatch, tmp_path):
    pre_on = workflow.premarket("2026-09-25", cycle_id="2026-09-25T0935")
    workflow.market_hours("2026-09-25", scope="positions")
    workflow.market_hours("2026-09-25", scope="all")
    rows = _rows()
    assert {r["context"] for r in rows} >= {"discovery", "tracker_fast", "tracker_full"}
    assert {r["cycle_id"] for r in rows if r["context"] == "discovery"} == {"2026-09-25T0935"}
    assert {r["symbol"] for r in rows if r["context"] == "discovery"} == {"AAA", "DDD"}
    on = ([(p["symbol"], p["quantity"], p["avg_entry"]) for p in db.query("SELECT * FROM positions")],
          [(o["symbol"], o["quantity"], o["status"]) for o in db.query("SELECT * FROM orders")])
    # same run with the archive disabled: identical decisions, orders and positions
    off_dir = tmp_path / "off"
    off_dir.mkdir()
    db.reset_for_tests(str(off_dir))
    monkeypatch.setattr(shadow_log, "log_quote", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("off")))
    monkeypatch.setattr(workflow.shadow_log, "log_quote", lambda *a, **k: False)
    pre_off = workflow.premarket("2026-09-25", cycle_id="2026-09-25T0935")
    workflow.market_hours("2026-09-25", scope="positions")
    workflow.market_hours("2026-09-25", scope="all")
    off = ([(p["symbol"], p["quantity"], p["avg_entry"]) for p in db.query("SELECT * FROM positions")],
           [(o["symbol"], o["quantity"], o["status"]) for o in db.query("SELECT * FROM orders")])
    assert on == off and on[0]
    assert [o["symbol"] for o in pre_on["orders_placed"]] == [o["symbol"] for o in pre_off["orders_placed"]]
    tables = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "shadow_quotes" not in tables                                           # never inside the ledger


# ── fail-safe: a locked or broken archive must never slow or alter trading ─────────────────────────────────────────
def _hold_shadow_lock():
    shadow_log._connect().close()                                  # make sure the file and schema exist
    holder = sqlite3.connect(shadow_log.path(), timeout=1)
    holder.execute("BEGIN EXCLUSIVE")
    return holder


def test_locked_shadow_db_costs_a_fraction_of_a_second_then_backs_off():
    q = Quote("AAA", last=100.0, source_ts=time.time(), provider="yahoo")
    holder = _hold_shadow_lock()
    try:
        t = time.perf_counter()
        assert shadow_log.log_quote("AAA", q, "tracker_fast") is False
        assert shadow_log.log_quote("AAA", q, "tracker_fast") is False
        assert time.perf_counter() - t < 3.0                       # was ~10 s PER CALL with the default busy timeout
        t = time.perf_counter()
        for _ in range(30):                                        # cooling down: instant, nothing waits on the lock
            assert shadow_log.log_quote("AAA", q, "tracker_fast") is False
        assert time.perf_counter() - t < 0.2
    finally:
        holder.rollback()
        holder.close()
    s = shadow_log.quote_archive_status()
    assert s["consecutive_failures"] == 2 and s["skipped"] == 30 and s["cooling_down"] and s["last_error"]
    shadow_log._QUOTE_STATE["skip_until"] = 0.0                    # cooldown over: the archive resumes by itself
    assert shadow_log.log_quote("AAA", q, "tracker_fast") is True
    assert shadow_log.quote_archive_status()["consecutive_failures"] == 0 and len(_rows()) == 1


def test_tracker_decisions_are_identical_and_prompt_while_the_archive_is_locked(monkeypatch):
    workflow.premarket("2026-09-25", cycle_id="2026-09-25T0935")   # opens a position, archive working
    before = [(p["symbol"], p["quantity"], p["status"]) for p in db.query("SELECT * FROM positions")]
    assert before and before[0][2] == "open"
    holder = _hold_shadow_lock()
    try:
        t = time.perf_counter()
        for _ in range(5):
            out = workflow.market_hours("2026-09-25", scope="positions")     # fast tracker: positions only
        elapsed = time.perf_counter() - t
    finally:
        holder.rollback()
        holder.close()
    assert elapsed < 5.0 and out["symbols_watched"] == ["AAA", "DDD"] and out["quotes_missing"] == []
    assert [(p["symbol"], p["quantity"], p["status"]) for p in db.query("SELECT * FROM positions")] == before
    assert shadow_log.quote_archive_status()["total_failures"] >= 2


def test_status_and_health_expose_a_failing_archive_without_touching_health(monkeypatch):
    st = shadow_log.status()
    assert st["ok"] and st["quote_archive"]["consecutive_failures"] == 0
    now = dt.datetime(2026, 9, 28, 23, tzinfo=dt.timezone.utc)
    state = {"heartbeat": now.isoformat(), "restore_ok": True, "counters": {}}
    lock = rt.runtime_lock()
    assert lock.acquire()
    try:
        assert "quote_archive_failing" not in rt.health(now=now, state=state)["degraded"]
        shadow_log._QUOTE_STATE["consecutive"] = 3
        h = rt.health(now=now, state=state)
        assert "quote_archive_failing" in h["degraded"] and h["healthy"] is True      # degraded, never unhealthy
    finally:
        lock.release()


# ── SR-001: the stored rows must carry the identifiers and times needed to join them to the decision ───────────────
def test_discovery_rows_join_to_candidates_and_keep_exact_times():
    t0 = time.time()
    workflow.premarket("2026-09-25", cycle_id="2026-09-25T0935")
    c = sqlite3.connect(shadow_log.path())
    c.row_factory = sqlite3.Row
    cands = c.execute("SELECT * FROM shadow_candidates").fetchall()
    assert {r["symbol"] for r in cands} == {"AAA", "DDD"}
    for r in cands:
        q = c.execute("SELECT * FROM shadow_quotes WHERE context='discovery' AND cycle_id=? AND symbol=?",
                      (r["cycle_id"], r["symbol"])).fetchall()
        assert len(q) == 1, r["cand_id"]
        assert r["cand_id"] == f"{q[0]['cycle_id']}:{q[0]['symbol']}"                 # stable decision identifier
        assert q[0]["source_ts"] == r["quote_source_ts"]                              # exact float, no rounding
        got = dt.datetime.fromisoformat(q[0]["received_at"])
        assert got.tzinfo is not None and got.utcoffset() == dt.timedelta(0) and got.microsecond is not None
        assert got.timestamp() >= q[0]["source_ts"] - 1e-6                            # receipt never precedes observation
        assert t0 - 1 <= got.timestamp() <= time.time() + 1
        assert (q[0]["provider"], q[0]["ok"], q[0]["bid_ask_synthetic"]) == ("yahoo", 1, 1)
