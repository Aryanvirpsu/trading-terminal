"""Ordering semantics: only an EXECUTED entry consumes a daily slot.

CH-002 replay found the scanner order can rank a MONITOR ahead of a TRADEABLE. This pins the live
behaviour so it is explicit rather than accidental: candidates are evaluated in scanner order, a
non-TRADEABLE candidate never consumes an entry slot or capital, so a TRADEABLE ranked behind it
still trades. (If the policy ever changes to rank by decision label, change this test deliberately.)
"""
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for folder in ("src", "dashboard", "lab"):
    sys.path.insert(0, str(ROOT / folder))

import decision_engine as de
from paper import db, risk, workflow
from paper.fills import Quote


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("PAPER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DECISION_ALLOW_STALE", "false")
    monkeypatch.setenv("PAPER_MAX_ENTRIES_PER_DAY", "1")          # ONE slot: a wasted slot would show
    db.reset_for_tests(str(tmp_path))
    monkeypatch.setattr(risk, "_live_mark", lambda symbol: 40.0)
    monkeypatch.setattr(de.ss, "_pick_option_idea", lambda *a, **k: None)
    yield
    db.close()


def _result(symbol, decision):
    tradeable = decision == "TRADEABLE"
    return {"symbol": symbol, "decision": decision, "direction": "LONG", "price": 40.0,
            "entry_range": [40.0, 40.1], "stop": 39.6, "target": 42.0, "suggested_shares": 100,
            "decision_gates": [{"name": "quality", "passed": tradeable}],
            "failed_gates": [] if tradeable else ["conviction"],
            "freshness": {"state": "fresh"}, "data_source": "yahoo", "data_state": "fresh",
            "ev_breakdown": {"ev_per_share": 0.5}, "confidence_quality": 70 if tradeable else 55}


def test_monitor_ranked_first_does_not_consume_the_only_slot(monkeypatch):
    finalists = [  # scanner order: rank 1 is a MONITOR, rank 2 is TRADEABLE
        {"symbol": "AAA", "direction": "LONG", "sector": "energy", "strategy": "liquid_momentum"},
        {"symbol": "BBB", "direction": "LONG", "sector": "financials", "strategy": "liquid_momentum"}]
    decisions = {"AAA": "MONITOR", "BBB": "TRADEABLE"}
    monkeypatch.setattr(workflow, "provider_health", lambda: {"healthy": True})
    monkeypatch.setattr(workflow.strategies, "scan", lambda: {"state": "ok", "finalists": finalists})
    monkeypatch.setattr(de, "evaluate", lambda sym, *a, **k: _result(sym, decisions[sym]))
    monkeypatch.setattr(de, "_safe_regime", lambda: {})
    monkeypatch.setattr(workflow, "quote_for", lambda s: Quote(
        s, last=40, bid=39.95, ask=40.05, source_ts=time.time(), provider="yahoo"))
    pre = workflow.premarket()
    placed = [o["symbol"] for o in pre["orders_placed"]]
    assert placed == ["BBB"], pre["evaluated"]                 # TRADEABLE traded despite lower rank
    positions = db.query("SELECT symbol FROM positions WHERE status='open'")
    assert [p["symbol"] for p in positions] == ["BBB"]         # the MONITOR never became a position
