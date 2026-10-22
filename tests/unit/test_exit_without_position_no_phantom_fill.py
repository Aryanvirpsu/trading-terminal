"""An exit order must never record a fill for a position that is already closed (no phantom sale),
and a position must never have more than one working exit order per intent.

Mechanism (2026-10-16 `ledger_inconsistent` incident, docs/LEDGER_INCONSISTENT_DIAGNOSTIC_20261018.md): the tracker feeds exit
logic the CURRENT DAILY BAR, so `high` is a running session high.  `manage_open_positions` triggers a target exit when
high >= target, but a LIMIT sell fills only on strict trade-through (high > target, PAPER_LIMIT_TRADE_THROUGH=True).  While
the session high sat exactly at the target, every tracker cycle placed one more pending LIMIT sell for the full position.
When the high finally exceeded the target they all filled in one pass: the first closed the position, the rest inserted fill
rows with no position behind them, so cash rebuilt from fills exceeded the cash the positions table reports.

Covers: (1) a touch held for several cycles queues ONE order and reconciles; (2) defense in depth: duplicate pending exit
orders created any other way still produce one sale; (3) the nominal single exit is unchanged; (4) a stop still exits while
a target order is pending and the leftover target order cannot fill; (5) entries are unaffected.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in ("src", "dashboard", "lab"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

from paper import broker, db  # noqa: E402
from paper.fills import Quote  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="papertest_exitguard_")
    monkeypatch.setenv("PAPER_DATA_DIR", tmp)
    monkeypatch.setenv("PAPER_LEDGER", "robinhood_500_baseline")
    monkeypatch.setenv("PAPER_INITIAL_CASH", "500")
    monkeypatch.setenv("PAPER_INITIAL_EQUITY", "500")
    monkeypatch.setenv("PAPER_MAX_LOSS_PER_TRADE", "5")
    monkeypatch.setenv("PAPER_MAX_POSITION_NOTIONAL", "125")
    monkeypatch.setenv("PAPER_LIMIT_TRADE_THROUGH", "true")
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


def _entry_quote(symbol, last):
    return Quote(symbol, last=last, bid=last - 0.01, ask=last + 0.01, open=last, high=last, low=last,
                 volume=5_000_000, source_ts=time.time(), provider="yahoo")


def _bar(symbol, last, high, low, open_=None):
    return Quote(symbol, last=last, bid=round(last - 0.01, 4), ask=round(last + 0.01, 4), open=open_ if open_ else last,
                 high=high, low=low, volume=5_000_000, source_ts=time.time(), provider="yahoo")


def _cycle(quotes):
    """The tracker's two steps, in the tracker's order: (1) pending orders, (2) stop/target management."""
    for o in db.query("SELECT order_id, symbol FROM orders WHERE status IN ('pending','partial')"):
        q = quotes.get(o["symbol"])
        if q:
            broker.process_order(o["order_id"], q)
    broker.manage_open_positions(quotes, "2026-10-16")


def _open(symbol="EOG", entry=147.62, stop=142.53, target=157.6):
    out = broker.submit_entry(_result(symbol, entry, stop, target), _entry_quote(symbol, entry), strategy="s", sector="energy")
    assert out["executed"], out
    return db.query_one("SELECT * FROM positions WHERE symbol=? AND status='open'", (symbol,))


def _exit_fills(symbol):
    return db.query("SELECT f.* FROM fills f JOIN orders o ON o.order_id=f.order_id WHERE o.symbol=? AND o.side='SELL'", (symbol,))


def test_target_touched_for_many_cycles_queues_one_order_then_fills_once_and_reconciles():
    pos = _open()
    touch = _bar("EOG", last=157.5, high=157.6, low=152.5)          # session high == target exactly
    for _ in range(13):                                            # 13 tracker cycles with the high sitting at the target
        _cycle({"EOG": touch})
    assert len(db.query("SELECT 1 FROM orders WHERE symbol='EOG' AND side='SELL'")) == 1, "one working exit order per intent"
    _cycle({"EOG": _bar("EOG", last=157.7, high=157.8, low=152.5)})     # now it trades through

    p = db.query_one("SELECT * FROM positions WHERE symbol='EOG'")
    assert p["status"] == "closed" and p["exit_reason"] == "exit_target"
    fl = _exit_fills("EOG")
    assert len(fl) == 1 and fl[0]["quantity"] == pytest.approx(pos["quantity"]) and fl[0]["price"] == 157.6
    rec = broker.reconcile()
    assert rec["reconciled"] and rec["delta"] == 0.0, rec


def test_duplicate_pending_exit_orders_created_any_other_way_still_produce_one_sale():
    """Defense in depth: even if several exit orders exist (legacy rows, another code path), only one may fill."""
    pos = _open()
    for _ in range(3):
        broker.place_order("EOG", "SELL", pos["quantity"], order_type="LIMIT", limit_price=157.6, intent="exit_target",
                           strategy="s", signal_id=pos["signal_id"], session_date="2026-10-16")
    assert len(db.query("SELECT 1 FROM orders WHERE side='SELL' AND status='pending'")) == 3
    _cycle({"EOG": _bar("EOG", last=157.7, high=157.8, low=152.5)})

    assert len(_exit_fills("EOG")) == 1, "exactly one sale may be recorded for one position"
    states = sorted(r["status"] for r in db.query("SELECT status FROM orders WHERE symbol='EOG' AND side='SELL'"))
    assert states == ["cancelled", "cancelled", "filled"]
    assert db.query_one("SELECT COUNT(*) n FROM audit WHERE event='exit_without_position'")["n"] == 2
    rec = broker.reconcile()
    assert rec["reconciled"] and rec["delta"] == 0.0, rec


def test_nominal_single_exit_is_unchanged():
    pos = _open()
    _cycle({"EOG": _bar("EOG", last=157.9, high=158.0, low=152.5)})
    fl = _exit_fills("EOG")
    assert len(fl) == 1 and fl[0]["price"] == 157.6 and fl[0]["slippage"] == 0.0
    p = db.query_one("SELECT * FROM positions WHERE symbol='EOG'")
    assert p["realized_pnl"] == pytest.approx(round((157.6 - pos["avg_entry"]) * pos["quantity"], 4), abs=1e-4)
    assert broker.reconcile()["reconciled"]
    assert db.query_one("SELECT COUNT(*) n FROM audit WHERE event='exit_without_position'")["n"] == 0


def test_stop_still_exits_while_a_target_order_is_pending_and_the_leftover_target_order_cannot_fill():
    _open()
    _cycle({"EOG": _bar("EOG", last=157.5, high=157.6, low=152.5)})               # target touched -> one pending LIMIT
    assert len(db.query("SELECT 1 FROM orders WHERE symbol='EOG' AND intent='exit_target' AND status='pending'")) == 1
    # later the price collapses through the stop: the STOP must still fire (the de-duplication is per intent)
    _cycle({"EOG": _bar("EOG", last=141.0, high=157.6, low=140.0, open_=152.0)})
    p = db.query_one("SELECT * FROM positions WHERE symbol='EOG'")
    assert p["status"] == "closed" and p["exit_reason"] == "exit_stop"
    # the session high then clears the target; the leftover pending LIMIT must not record a second sale
    _cycle({"EOG": _bar("EOG", last=150.0, high=158.0, low=140.0, open_=152.0)})
    assert len(_exit_fills("EOG")) == 1
    assert db.query_one("SELECT status FROM orders WHERE symbol='EOG' AND intent='exit_target'")["status"] == "cancelled"
    assert broker.reconcile()["reconciled"]


def test_entry_orders_are_not_affected():
    out = broker.submit_entry(_result("AAA", 100.0, 97.0, 106.0), _entry_quote("AAA", 100.0), strategy="s", sector="technology")
    assert out["executed"]
    assert db.query_one("SELECT status FROM orders WHERE symbol='AAA' AND intent='entry'")["status"] == "filled"
    assert broker.reconcile()["reconciled"]
