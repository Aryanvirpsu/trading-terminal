"""Offline regressions for execution sizing, quote provenance, and missed entries."""
import datetime as dt
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for folder in ("src", "dashboard", "lab"):
    sys.path.insert(0, str(ROOT / folder))

import decision_engine as de
import providers
import research
from paper import broker, db, report, risk, workflow
from paper.fills import Quote


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("PAPER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DECISION_ALLOW_STALE", "false")
    db.reset_for_tests(str(tmp_path))
    monkeypatch.setattr(risk, "_live_mark", lambda symbol: 40.0)
    monkeypatch.setattr(de.ss, "_pick_option_idea", lambda *a, **k: None)
    yield
    db.close()


def decision():
    return {"symbol": "BAC", "decision": "TRADEABLE", "direction": "LONG",
            "price": 40.0, "entry_range": [40.0, 40.1], "stop": 39.6,
            "target": 42.0, "suggested_shares": 100,
            "decision_gates": [{"name": "quality", "passed": True}],
            "failed_gates": [], "freshness": {"state": "fresh"},
            "data_source": "yahoo", "data_state": "fresh",
            "ev_breakdown": {"ev_per_share": 0.5}, "confidence_quality": 70}


def arm_workflow(monkeypatch, quote):
    monkeypatch.setattr(workflow, "provider_health", lambda: {"healthy": True})
    monkeypatch.setattr(workflow.strategies, "scan", lambda: {
        "state": "ok", "finalists": [{"symbol": "BAC", "direction": "LONG",
        "sector": "financials", "strategy": "liquid_momentum"}]})
    monkeypatch.setattr(de, "evaluate", lambda *a, **k: decision())
    monkeypatch.setattr(de, "_safe_regime", lambda: {})
    monkeypatch.setattr(workflow, "quote_for", lambda symbol: quote)


# ($200 cash is not a valid scenario: the 30% sector cap ($60) binds before the
# reserve can, so reserve-once is asserted directly in the next test instead.)
@pytest.mark.parametrize("cash,expected_cost", [(500, 125)])
def test_workflow_sizes_to_fill_cost_and_subtracts_reserve_once(monkeypatch, cash, expected_cost):
    monkeypatch.setenv("PAPER_INITIAL_CASH", str(cash))
    monkeypatch.setenv("PAPER_INITIAL_EQUITY", str(cash))
    q = Quote("BAC", last=40, bid=39.95, ask=40.05,
              source_ts=time.time(), provider="yahoo")
    arm_workflow(monkeypatch, q)
    pre = workflow.premarket()
    assert len(pre["orders_placed"]) == 1, pre["evaluated"]
    pos = db.query_one("SELECT * FROM positions WHERE status='open'")
    assert round(pos["quantity"] * pos["avg_entry"], 2) == expected_cost
    assert risk.account_state()["cash"] >= 100
    audit = db.query_one("SELECT detail_json FROM audit WHERE event='canonical_evaluated'")
    assert pos["quantity"] == json.loads(audit["detail_json"])["sizing_quantity"]
    assert broker.reconcile()["reconciled"]


def history(bar_date="2020-01-02T00:00:00-05:00"):
    return {"state": "ok", "points": [{"t": bar_date,
        "o": 30, "h": 50, "l": 20, "c": 40, "v": 1000000}]}


def test_history_close_cannot_be_relabelled_as_a_live_quote(monkeypatch):
    monkeypatch.setattr(providers, "price_consensus", lambda s: {"value": None})
    monkeypatch.setattr(research, "price_history", lambda *a: history())
    assert workflow.quote_for("BAC") is None


@pytest.mark.parametrize("timestamp", [None, 0, float("nan"), float("inf"), "invalid"])
def test_quote_without_valid_source_time_is_unavailable(monkeypatch, timestamp):
    monkeypatch.setattr(providers, "price_consensus", lambda s: {
        "value": 40, "provider": "yahoo", "source_timestamp": timestamp})
    monkeypatch.setattr(research, "price_history", lambda *a: history())
    assert workflow.quote_for("BAC") is None


def test_old_bar_cannot_trigger_an_exit_on_a_fresh_quote(monkeypatch):
    ts = time.time()
    monkeypatch.setattr(providers, "price_consensus", lambda s: {
        "value": 40, "provider": "yahoo", "source_timestamp": ts})
    monkeypatch.setattr(research, "price_history", lambda *a: history())
    q = workflow.quote_for("BAC")
    assert q.source_ts == ts
    assert (q.open, q.high, q.low, q.volume) == (None, None, None, None)


def test_current_session_bar_is_preserved(monkeypatch):
    ts = time.time()
    day = dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat()
    monkeypatch.setattr(providers, "price_consensus", lambda s: {
        "value": 40, "provider": "yahoo", "source_timestamp": ts})
    monkeypatch.setattr(research, "price_history", lambda *a: history(day + "T00:00:00-04:00"))
    q = workflow.quote_for("BAC")
    assert (q.open, q.high, q.low, q.volume) == (30, 50, 20, 1000000)


def test_daily_report_explains_a_tradeable_signal_without_a_quote(monkeypatch):
    arm_workflow(monkeypatch, None)
    pre = workflow.premarket()
    assert not pre["orders_placed"]
    rep = report.daily()
    rows = rep["entry_outcomes"]
    assert len(rows) == 1
    assert rows[0]["symbol"] == "BAC"
    assert rows[0]["executed"] is False
    assert "no executable quote" in rows[0]["reasons"]


@pytest.mark.parametrize("timestamp", [None, 0, float("nan"), float("inf")])
def test_broker_rejects_unknown_source_time_even_with_fresh_engine_label(timestamp):
    q = Quote("BAC", last=40, source_ts=timestamp, provider="yahoo")
    out = broker.submit_entry(decision(), q, strategy="liquid_momentum")
    assert not out["executed"]
    assert db.query_one("SELECT COUNT(*) n FROM orders")["n"] == 0


def test_bridge_passes_raw_cash_so_reserve_is_netted_once(monkeypatch):
    from paper import canonical_bridge
    seen = {}
    real = canonical_bridge.stock_account_fit
    def spy(**kw):
        seen.update(kw)
        return real(**kw)
    monkeypatch.setattr(canonical_bridge, "stock_account_fit", spy)
    q = Quote("BAC", last=40, bid=39.95, ask=40.05, source_ts=time.time(), provider="yahoo")
    canonical_bridge.evaluate_canonical(decision(), symbol="BAC", direction="LONG",
                                        sector="financials", quote=q)
    st = risk.account_state()
    assert seen["buying_power"] == st["available_cash"]          # raw, not net of reserve
    assert seen["fill_price"] == pytest.approx(broker._expected_fill_price(decision(), q))
