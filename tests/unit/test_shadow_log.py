"""Shadow candidate logger: separate, append-only, non-fatal, and never changes trading."""
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
from paper import db, risk, shadow_log, workflow
from paper.fills import Quote

FINALISTS = [  # scanner order; three energy names compete for one sector slot
    {"symbol": "AAA", "direction": "LONG", "sector": "energy", "strategy": "liquid_momentum", "scanner_rank": 1},
    {"symbol": "BBB", "direction": "LONG", "sector": "energy", "strategy": "liquid_momentum", "scanner_rank": 2},
    {"symbol": "CCC", "direction": "LONG", "sector": "energy", "strategy": "liquid_momentum", "scanner_rank": 3},
    {"symbol": "DDD", "direction": "LONG", "sector": "technology", "strategy": "liquid_momentum", "scanner_rank": 4},
]
QUALITY = {"AAA": 66, "BBB": 75, "CCC": 70, "DDD": 55}
DECISION = {"AAA": "TRADEABLE", "BBB": "TRADEABLE", "CCC": "TRADEABLE", "DDD": "MONITOR"}


def _result(sym):
    ok = DECISION[sym] == "TRADEABLE"
    return {"symbol": sym, "decision": DECISION[sym], "direction": "LONG", "price": 40.0,
            "entry_range": [40.0, 40.1], "stop": 39.6, "target": 42.0, "suggested_shares": 100,
            "decision_gates": [{"name": "quality", "passed": ok},
                               {"name": "liquidity", "passed": True, "value": 0.9}],
            "failed_gates": [] if ok else ["conviction"], "freshness": {"state": "fresh"},
            "data_source": "yahoo", "data_state": "fresh",
            "ev_breakdown": {"ev_per_share": 0.5, "expected_r": 1.0 + QUALITY[sym] / 1000},
            "confidence_quality": QUALITY[sym]}


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
    monkeypatch.setattr(workflow, "quote_for", lambda s: Quote(
        s, last=40, bid=39.95, ask=40.05, source_ts=time.time(), provider="yahoo"))
    yield
    db.close()


def _shadow():
    c = sqlite3.connect(shadow_log.path())
    c.row_factory = sqlite3.Row
    return c


def test_cycle_records_full_finalist_set_picks_and_capacity_reasons():
    pre = workflow.premarket("2026-09-25")
    assert [o["symbol"] for o in pre["orders_placed"]] == ["AAA"]      # Champion: first eligible energy
    c = _shadow()
    cyc = c.execute("SELECT * FROM shadow_cycles").fetchall()
    assert len(cyc) == 1 and cyc[0]["finalists"] == 4 and cyc[0]["eligible"] == 3
    assert cyc[0]["is_choice_event"] == 1
    assert cyc[0]["engine_version"] == "decision_engine/gates-v1.1"
    rows = {r["symbol"]: r for r in c.execute("SELECT * FROM shadow_candidates")}
    assert set(rows) == {"AAA", "BBB", "CCC", "DDD"}
    assert rows["AAA"]["champion_executed"] == 1 and rows["AAA"]["signal_id"]
    assert rows["BBB"]["capacity_reason"] == "sector" and rows["CCC"]["capacity_reason"] == "sector"
    assert rows["DDD"]["capacity_reason"] == "not_eligible:MONITOR"
    assert rows["AAA"]["spread_pct"] and rows["AAA"]["quote_source_ts"]     # decision-time execution data
    picks = {(r["policy"], r["symbol"]) for r in c.execute("SELECT * FROM shadow_picks")}
    assert ("CHAMPION", "AAA") in picks and ("CHAMPION_ACTUAL", "AAA") in picks
    assert ("CH-002A_conviction", "BBB") in picks                            # highest quality
    assert ("CH-002B_expected_r", "BBB") in picks


def test_ledger_untouched_and_trading_identical_with_logger_disabled(monkeypatch, tmp_path):
    pre_on = workflow.premarket("2026-09-25")
    on_positions = [(p["symbol"], p["quantity"], p["avg_entry"]) for p in db.query("SELECT * FROM positions")]
    tables = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not any(t.startswith("shadow_") for t in tables)                # never inside the ledger
    other = tmp_path / "off"
    other.mkdir()
    db.reset_for_tests(str(other))
    monkeypatch.setattr(shadow_log, "capacity_snapshot", lambda d: (_ for _ in ()).throw(RuntimeError("off")))
    pre_off = workflow.premarket("2026-09-25")
    off_positions = [(p["symbol"], p["quantity"], p["avg_entry"]) for p in db.query("SELECT * FROM positions")]
    assert [o["symbol"] for o in pre_on["orders_placed"]] == [o["symbol"] for o in pre_off["orders_placed"]]
    assert on_positions == off_positions


def test_append_only_triggers_block_mutation():
    workflow.premarket("2026-09-25")
    c = _shadow()
    for sql in ("UPDATE shadow_candidates SET quality=0", "DELETE FROM shadow_candidates",
                "UPDATE shadow_cycles SET finalists=0", "DELETE FROM shadow_picks"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            c.execute(sql)


def test_logger_failure_is_non_fatal(monkeypatch):
    monkeypatch.setattr(shadow_log, "candidate_row", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    pre = workflow.premarket("2026-09-25")
    assert [o["symbol"] for o in pre["orders_placed"]] == ["AAA"]


def test_rerun_same_session_adds_no_duplicates():
    workflow.premarket("2026-09-25")
    workflow.premarket("2026-09-25")            # idempotency guard skips already-evaluated symbols
    c = _shadow()
    assert c.execute("SELECT COUNT(*) n FROM shadow_cycles").fetchone()["n"] == 1
    assert c.execute("SELECT COUNT(*) n FROM shadow_candidates").fetchone()["n"] == 4


def test_update_bars_appends_only_complete_bars_and_is_idempotent():
    workflow.premarket("2026-09-25")
    now = dt.datetime.now(dt.timezone.utc)
    done = (now - dt.timedelta(hours=1)).isoformat()
    partial = (now - dt.timedelta(minutes=1)).isoformat()

    def history(sym, rng):
        if rng == "1M":
            return {"state": "ok", "points": [
                {"t": "2026-09-24T00:00:00-04:00", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10},
                {"t": "2026-09-25T00:00:00-04:00", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10}]}
        return {"state": "ok", "points": [{"t": done, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 1},
                                          {"t": partial, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 1}]}

    added = shadow_log.update_bars("2026-09-25", history)
    assert added["daily"] == 4 and added["5m"] == 4          # 4 symbols; today's bar and the partial 5m skipped
    assert shadow_log.update_bars("2026-09-25", history) == {"daily": 0, "5m": 0}
