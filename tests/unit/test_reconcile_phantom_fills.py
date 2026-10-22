"""reconcile() and health() around audited phantom fills (the 2026-10-16 `ledger_inconsistent` incident).

Before process_order() was fixed, several pending exit orders for one position could all fill in one pass: the first closed the
position, the others inserted fill rows with no position behind them and the ledger audited `exit_without_position` for each.
Those rows are append-only, so a ledger that already contains them can never reconcile again.  reconcile() therefore recognises
exactly that signature (exit order + audit event for that order + a fill row), leaves it out of the cash rebuild, and LISTS it;
health() reports it as degraded.  Everything else that disagrees with the positions table must still fail closed.

Covers: (1) the incident signature reconciles and is listed; (2) the same missing position effect WITHOUT the audit event
fails closed with the exact delta; (3) an ENTRY fill is never excluded, even with the audit event; (4) health() exposes the
amounts when unhealthy and degrades (not unhealthy) for audited phantoms; (5) a clean ledger reports nothing excluded.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in ("src", "dashboard", "lab"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

from paper import broker, db, fills, runtime as rt  # noqa: E402
from paper.fills import Quote  # noqa: E402

UTC = dt.timezone.utc


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="papertest_phantom_")
    monkeypatch.setenv("PAPER_DATA_DIR", tmp)
    monkeypatch.setenv("PAPER_LEDGER", "robinhood_500_baseline")
    monkeypatch.setenv("PAPER_INITIAL_CASH", "500")
    monkeypatch.setenv("PAPER_INITIAL_EQUITY", "500")
    monkeypatch.setenv("PAPER_MAX_LOSS_PER_TRADE", "5")
    monkeypatch.setenv("PAPER_MAX_POSITION_NOTIONAL", "125")
    db.reset_for_tests(tmp)
    yield
    db.close()


def _result(symbol, entry, stop, target):
    gates = [{"name": n, "passed": True, "blocking": True, "reason": ""}
             for n in ("quality_threshold", "positive_ev", "liquidity")]
    return {"symbol": symbol, "decision": "TRADEABLE", "direction": "LONG", "price": entry,
            "entry_range": [entry, entry * 1.003], "stop": stop, "target": target,
            "suggested_shares": 10, "decision_gates": gates, "failed_gates": [],
            "freshness": {"state": "fresh", "label": "fresh"}, "data_source": "yahoo", "data_state": "fresh",
            "ev_breakdown": {"ev_per_share": 0.8, "expected_r": 0.3}, "confidence_quality": 70,
            "quality_threshold": 45}


def _quote(symbol, last, high=None, low=None):
    return Quote(symbol, last=last, bid=last - 0.01, ask=last + 0.01, open=last, high=high or last, low=low or last,
                 volume=5_000_000, source_ts=time.time(), provider="yahoo")


def _round_trip():
    out = broker.submit_entry(_result("EOG", 147.62, 142.53, 157.6), _quote("EOG", 147.62), strategy="s", sector="energy")
    assert out["executed"], out
    broker.manage_open_positions({"EOG": _quote("EOG", 157.9, high=158.0, low=152.5)}, "2026-10-16")
    assert broker.reconcile()["reconciled"]


def _inject_phantom_sale(qty=0.846323, price=157.6, intent="exit_target", audited=True):
    """Re-create what the old code left behind: a filled exit order + fill row for a position that no longer exists."""
    o = broker.place_order("EOG", "SELL", qty, order_type="LIMIT", limit_price=price, intent=intent, strategy="s",
                           session_date="2026-10-16")
    db.execute("UPDATE orders SET status='filled', filled_qty=?, avg_fill=? WHERE order_id=?", (qty, price, o["order_id"]))
    db.execute("INSERT INTO fills(fill_id, order_id, filled_at, quantity, price, fees, slippage, reference, liquidity, note) "
               "VALUES(?,?,?,?,?,?,?,?,?,?)",
               (fills.fill_id(o["order_id"], 1), o["order_id"], db.utcnow(), qty, price, 0.0, 0.0, price, "normal",
                "limit filled on trade-through"))
    if audited:
        db.audit("order", o["order_id"], "exit_without_position", {"symbol": "EOG"})
    return o["order_id"], round(qty * price, 2)


def test_clean_ledger_reports_no_excluded_fills():
    _round_trip()
    rec = broker.reconcile()
    assert rec["reconciled"] and rec["excluded_phantom_fills"] == []


def test_incident_signature_reconciles_and_is_listed_not_hidden():
    _round_trip()
    order_id, notional = _inject_phantom_sale()
    rec = broker.reconcile()
    assert rec["reconciled"] and rec["delta"] == 0.0, rec
    assert [p["order_id"] for p in rec["excluded_phantom_fills"]] == [order_id]
    assert rec["excluded_phantom_fills"][0]["notional"] == pytest.approx(notional, abs=0.01)


def test_same_missing_effect_without_the_audit_event_still_fails_closed_with_the_exact_delta():
    _round_trip()
    _inject_phantom_sale(audited=False)
    rec = broker.reconcile()
    assert not rec["reconciled"] and rec["excluded_phantom_fills"] == []
    assert rec["delta"] == pytest.approx(133.38, abs=0.01), rec


def test_an_entry_fill_is_never_excluded_even_with_the_audit_event():
    out = broker.submit_entry(_result("AAA", 100.0, 97.0, 106.0), _quote("AAA", 100.0), strategy="s", sector="technology")
    assert out["executed"]
    entry_order = db.query_one("SELECT order_id FROM orders WHERE symbol='AAA' AND intent='entry'")["order_id"]
    db.audit("order", entry_order, "exit_without_position", {"symbol": "AAA"})        # nonsense on an entry order
    # shrink the position row so the books disagree: this must be caught, not excused by the stray audit event
    db.execute("UPDATE positions SET quantity=quantity*0.5 WHERE symbol='AAA'")
    rec = broker.reconcile()
    assert not rec["reconciled"] and rec["excluded_phantom_fills"] == []


def _healthy_runtime_state():
    now = dt.datetime.now(UTC)
    return now, {"heartbeat": now.isoformat(), "restore_ok": True, "counters": {"provider_failures": 0}}


def test_health_degrades_but_stays_healthy_for_audited_phantoms_and_shows_the_amounts():
    _round_trip()
    _inject_phantom_sale()
    lock = rt.runtime_lock()
    assert lock.acquire()
    try:
        now, st = _healthy_runtime_state()
        h = rt.health(now=now, state=st)
        assert "ledger_inconsistent" not in h["unhealthy"]
        assert "phantom_fills_excluded" in h["degraded"]
        assert h["reconcile"]["reconciled"] is True and h["reconcile"]["excluded_phantom_fills"] == 1
    finally:
        lock.release()


def test_health_explains_an_unflagged_mismatch_with_the_cash_amounts():
    _round_trip()
    _inject_phantom_sale(audited=False)
    lock = rt.runtime_lock()
    assert lock.acquire()
    try:
        now, st = _healthy_runtime_state()
        h = rt.health(now=now, state=st)
        assert "ledger_inconsistent" in h["unhealthy"] and not h["healthy"]
        r = h["reconcile"]
        assert r["reconciled"] is False and r["delta"] == pytest.approx(133.38, abs=0.01)
        assert r["cash_from_fills"] == pytest.approx(r["cash_from_state"] + r["delta"], abs=0.01)
    finally:
        lock.release()
