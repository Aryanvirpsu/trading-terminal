"""Quote-only stops: a fresh quote that crosses the stop must close the position, a bad quote must not, and the
bar-based stop behaviour must stay exactly as it was.

Failure fixed: with no bar range `simulate_stop` returned "not triggered" even though `manage_open_positions` had just
decided the stop was hit (it falls back to `last`), so the exit order stayed pending and the position stayed open.
"""
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
from paper import broker, db, fills, risk, runtime as rt, workflow
from paper.fills import STOP_BLOCKED, Quote

NOW = 1_800_000_000.0
STOP = 39.6
SLIP = 5 / 10000          # config default slippage
BUY_SLIP = SLIP


def fresh(age=5.0, **kw):
    return Quote("BAC", source_ts=NOW - age, provider="t", **kw)


def stop(q, qty=10.0, triggered=False, now=NOW):
    return fills.simulate_stop("SELL", qty, STOP, q, triggered=triggered, now=now)


# ── crossing, gap-through, not crossed ───────────────────────────────────────────────────────────────────────────
def test_quote_only_crossing_fills_at_the_executable_book_not_at_the_stop():
    r = stop(fresh(last=39.59))
    bid = round(39.59 * (1 - 5 / 10000), 4)                 # synthesised bid = last - half of the 10 bps spread
    assert r.status == "filled" and r.liquidity == "normal"
    assert r.price == pytest.approx(round(bid * (1 - SLIP), 4)) and r.price < STOP
    assert r.reference == STOP and r.slippage == pytest.approx(abs(r.price - STOP), abs=1e-6)
    assert "quote only" in r.note


def test_last_equal_to_stop_triggers():
    assert stop(fresh(last=STOP)).status == "filled"


def test_not_crossed_stays_pending_and_is_not_reported_as_unmanaged():
    r = stop(fresh(last=39.61))
    assert r.status == "pending" and not r.reason.startswith(STOP_BLOCKED)


def test_quote_only_gap_through_fills_at_the_market_and_is_counted_as_a_gap():
    r = stop(fresh(last=38.0))
    assert r.status == "filled" and r.liquidity == "gap"           # report.py counts liquidity == 'gap'
    assert r.price == pytest.approx(37.962, abs=2e-3) and r.price < 38.0


# ── stale / missing / contradictory evidence keeps the order pending, visibly ────────────────────────────────────
def test_stale_quote_keeps_pending_with_a_visible_reason_and_the_boundary_is_inclusive():
    r = stop(fresh(age=181, last=39.0))
    assert r.status == "pending" and r.reason.startswith(STOP_BLOCKED) and "stale" in r.reason
    assert stop(fresh(age=180, last=39.0)).status == "filled"


@pytest.mark.parametrize("ts", [None, 0, -1, float("nan"), float("inf")])
def test_quote_without_a_valid_source_time_is_not_executable(ts):
    r = stop(Quote("BAC", last=39.0, source_ts=ts, provider="t"))
    assert r.status == "pending" and r.reason.startswith(STOP_BLOCKED) and "source time" in r.reason


def test_future_dated_quote_is_not_executable():
    r = stop(fresh(age=-3600, last=39.0))
    assert r.status == "pending" and "future" in r.reason


def test_missing_last_cannot_trigger_and_is_not_a_fill():
    r = stop(fresh(last=None))
    assert r.status == "pending"


def test_conflicting_quote_last_below_stop_but_bid_above_it_is_not_a_trigger_and_is_not_clamped():
    q = fresh(last=39.5, bid=39.7, ask=39.8)
    r = stop(q)
    assert r.status == "pending" and r.reason.startswith(STOP_BLOCKED) and "conflicting" in r.reason
    assert r.price is None                                        # nothing is filled at the stop (or anywhere)


def test_crossed_book_is_contradictory_and_not_silently_repaired():
    r = stop(fresh(last=39.5, bid=39.6, ask=39.4))                # Quote.resolved() would hide this
    assert r.status == "pending" and "crossed book" in r.reason


def test_genuine_rebound_after_a_validated_trigger_executes_at_the_book_even_above_the_stop():
    q = fresh(last=39.5, bid=39.7, ask=39.8)
    r = stop(q, triggered=True)                                   # the order already has fills => trigger was validated
    assert r.status == "filled" and r.price == pytest.approx(round(39.7 * (1 - SLIP), 4))
    assert r.price > STOP                                         # an executable price, not the stop
    assert "trigger validated earlier" in r.note and r.liquidity == "normal"


def test_validated_trigger_still_refuses_stale_data():
    r = stop(fresh(age=600, last=40.5), triggered=True)
    assert r.status == "pending" and r.reason.startswith(STOP_BLOCKED)


def test_partial_fill_on_thin_volume():
    r = stop(fresh(last=39.0, volume=1000), qty=100)              # 2 % of volume = 20 -> fills 10
    assert r.status == "partial" and r.quantity == 10.0 and r.price < STOP


# ── the bar-based behaviour is pinned (any change here is a separate decision) ───────────────────────────────────
@pytest.mark.parametrize("name,q,price,liq", [
    ("intrabar touch, price rebounded", Quote("BAC", last=39.9, open=40, high=40.2, low=39.5), 39.5802, "normal"),
    ("bar opened through the stop (gap)", Quote("BAC", last=39.1, open=39.0, high=39.2, low=38.8), 38.9025, "gap"),
    ("daily low far below the stop still fills AT the stop", Quote("BAC", last=36.5, open=40, high=40.2, low=36.0),
     39.5802, "normal"),
    ("no age gate on the bar path (old source time)", Quote("BAC", last=39.9, open=40, high=40.2, low=39.5,
                                                              source_ts=1.0), 39.5802, "normal"),
])
def test_bar_based_stop_fills_are_unchanged(name, q, price, liq):
    r = stop(q)
    assert (r.status, r.price, r.liquidity) == ("filled", price, liq), name


def test_bar_present_and_low_above_stop_stays_pending_regardless_of_last():
    r = stop(Quote("BAC", last=39.0, open=40, high=40.2, low=39.7, source_ts=NOW - 5))
    assert r.status == "pending" and r.reason == f"stop {STOP} not triggered"


# ── broker / tracker integration: retry without duplicates ───────────────────────────────────────────────────────
@pytest.fixture()
def book(monkeypatch, tmp_path):
    monkeypatch.setenv("PAPER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DECISION_ALLOW_STALE", "false")
    db.reset_for_tests(str(tmp_path))
    monkeypatch.setattr(risk, "_live_mark", lambda symbol: 40.0)
    monkeypatch.setattr(de.ss, "_pick_option_idea", lambda *a, **k: None)
    d = {"symbol": "BAC", "decision": "TRADEABLE", "direction": "LONG", "price": 40.0, "entry_range": [40.0, 40.1],
         "stop": STOP, "target": 42.0, "suggested_shares": 100, "decision_gates": [{"name": "quality", "passed": True}],
         "failed_gates": [], "freshness": {"state": "fresh"}, "data_source": "yahoo", "data_state": "fresh",
         "ev_breakdown": {"ev_per_share": 0.5}, "confidence_quality": 70}
    out = broker.submit_entry(d, Quote("BAC", last=40, bid=39.95, ask=40.05, source_ts=time.time(), provider="t"),
                              strategy="liquid_momentum")
    assert out["executed"]
    yield db.query_one("SELECT * FROM positions WHERE status='open'")
    db.close()


def tick(monkeypatch, quote):
    monkeypatch.setattr(workflow, "quote_for", lambda symbol: quote)
    return workflow.market_hours("2026-09-28", scope="positions")


def live(age=5.0, **kw):
    return Quote("BAC", source_ts=time.time() - age, provider="t", **kw)


def n_orders():
    return db.query_one("SELECT COUNT(*) n FROM orders WHERE intent='exit_stop'")["n"]


def test_stale_crossing_stays_open_visible_then_a_valid_quote_resolves_the_same_order(book, monkeypatch):
    for _ in range(3):                                            # three tracker minutes on a stale quote
        out = tick(monkeypatch, live(age=900, last=39.0))
        assert out["positions_unmanaged"] and out["positions_unmanaged"][0]["symbol"] == "BAC"
        assert STOP_BLOCKED in out["positions_unmanaged"][0]["reason"]
    assert db.query_one("SELECT status FROM positions WHERE position_id=?", (book["position_id"],))["status"] == "open"
    assert n_orders() == 1                                         # no duplicate exit orders
    audits = db.query("SELECT detail_json FROM audit WHERE event='exit_unmanaged'")
    assert len(audits) == 1 and "stale" in json.loads(audits[0]["detail_json"])["reason"]   # one row per change of reason

    out = tick(monkeypatch, live(last=39.0))                       # a valid quote arrives
    assert out["positions_unmanaged"] == []
    closed = db.query_one("SELECT * FROM positions WHERE position_id=?", (book["position_id"],))
    assert closed["status"] == "closed" and closed["exit_reason"] == "exit_stop"
    assert n_orders() == 1 and db.query_one("SELECT status FROM orders WHERE intent='exit_stop'")["status"] == "filled"
    assert closed["avg_exit"] < STOP and broker.reconcile()["reconciled"]


def test_conflicting_quote_is_visible_and_never_closes_the_position(book, monkeypatch):
    out = tick(monkeypatch, live(last=39.5, bid=39.7, ask=39.8))
    assert "conflicting" in out["positions_unmanaged"][0]["reason"]
    assert db.query_one("SELECT status FROM positions WHERE position_id=?", (book["position_id"],))["status"] == "open"
    assert db.query_one("SELECT COUNT(*) n FROM fills WHERE order_id IN (SELECT order_id FROM orders "
                        "WHERE intent='exit_stop')")["n"] == 0


def test_missing_quote_is_reported_as_unmanaged_not_silently_skipped(book, monkeypatch):
    out = tick(monkeypatch, None)
    assert out["positions_unmanaged"] == [{"symbol": "BAC", "reason": "no quote for an open position",
                                           "order_id": None}]
    assert n_orders() == 0


def test_partial_fill_is_retried_to_completion_even_if_the_price_rebounds(book, monkeypatch):
    qty = book["quantity"]
    out = tick(monkeypatch, live(last=39.5, volume=100))           # 2 % of 100 shares caps the first fill at 1
    assert db.query_one("SELECT status FROM orders WHERE intent='exit_stop'")["status"] == "partial"
    assert db.query_one("SELECT quantity FROM positions WHERE status='open'")["quantity"] == pytest.approx(qty - 1.0)
    for _ in range(3):                                             # price has rebounded above the stop
        tick(monkeypatch, live(last=40.5, volume=100))
        if db.query_one("SELECT status FROM orders WHERE intent='exit_stop'")["status"] == "filled":
            break
    o = db.query_one("SELECT * FROM orders WHERE intent='exit_stop'")
    assert o["status"] == "filled" and o["filled_qty"] == pytest.approx(qty) and n_orders() == 1
    assert db.query_one("SELECT status FROM positions WHERE position_id=?", (book["position_id"],))["status"] == "closed"
    assert broker.reconcile()["reconciled"]


def test_bar_based_stop_through_the_tracker_is_unchanged(book, monkeypatch):
    # source time is ancient and last is above the stop: only the bar can trigger, and no age gate applies to it.
    tick(monkeypatch, Quote("BAC", last=39.9, open=40, high=40.2, low=39.5, source_ts=1.0, provider="t"))
    closed = db.query_one("SELECT * FROM positions WHERE position_id=?", (book["position_id"],))
    assert closed["status"] == "closed" and closed["avg_exit"] == round(STOP * (1 - SLIP), 4)
    assert n_orders() == 1


def test_health_degrades_while_a_position_is_unmanaged(book, monkeypatch, tmp_path):
    now = dt.datetime(2026, 9, 28, 14, 40, tzinfo=dt.timezone.utc)      # Monday 10:40 ET, in session
    lock = rt.runtime_lock()
    assert lock.acquire()
    try:
        def st(unmanaged):
            return {"heartbeat": now.isoformat(), "restore_ok": True, "counters": {},
                    "last_discovery": {"at": now.isoformat()},
                    "last_tracker": {"at": now.isoformat(), "positions_unmanaged": unmanaged}}
        bad = [{"symbol": "BAC", "reason": STOP_BLOCKED + "quote is stale"}]
        assert "position_exit_unmanaged" in rt.health(now=now, state=st(bad))["degraded"]
        assert "position_exit_unmanaged" not in rt.health(now=now, state=st([]))["degraded"]
    finally:
        lock.release()
