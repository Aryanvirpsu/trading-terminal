"""Partial fills must keep the ledger reconcilable.

Two latent defects found while diagnosing the 2026-10-16 `ledger_inconsistent` failure (both need a bar volume so small
that the order exceeds 2 % of it, so they are unlikely on liquid names, but each breaks `reconcile()` when it happens):

* C. `_partial` returned `max(1.0, ...)` shares, so a FRACTIONAL order (< 1 share) with a tiny bar volume was filled as one
  whole share: a fill larger than the order (and, on an entry, a position larger than the risk budget allowed).
* D. A partial exit left the position open with its realized P&L stored on the open row; `account_state` only summed
  realized P&L of CLOSED positions and the final close overwrote the stored amount with the last chunk's P&L alone, so the
  partial's cash vanished from the account.

Fills, prices, triggers and slippage are untouched; the nominal (full-size) fill path is identical.
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

from paper import broker, db, fills  # noqa: E402
from paper.fills import Quote  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="papertest_partial_")
    monkeypatch.setenv("PAPER_DATA_DIR", tmp)
    monkeypatch.setenv("PAPER_LEDGER", "robinhood_500_baseline")
    monkeypatch.setenv("PAPER_INITIAL_CASH", "500")
    monkeypatch.setenv("PAPER_INITIAL_EQUITY", "500")
    monkeypatch.setenv("PAPER_ALLOW_PARTIAL", "true")
    db.reset_for_tests(tmp)
    yield
    db.close()


def _q(symbol, last, high, low, volume, open_=None):
    return Quote(symbol, last=last, bid=round(last - 0.01, 4), ask=round(last + 0.01, 4), open=open_ if open_ else last,
                 high=high, low=low, volume=volume, source_ts=time.time(), provider="yahoo")


def _buy(symbol, qty, price, volume=5_000_000):
    o = broker.place_order(symbol, "BUY", qty, order_type="MARKET", intent="entry", strategy="s", session_date="2026-10-16")
    broker.process_order(o["order_id"], _q(symbol, price, price, price, volume))
    return db.query_one("SELECT * FROM positions WHERE symbol=? AND status='open'", (symbol,))


def test_partial_helper_never_returns_more_than_the_order():
    q = _q("X", 100, 100, 100, volume=3)
    assert fills._partial(0.1186, q) == (0.1186, False)             # fractional order, tiny volume: fills in full
    assert fills._partial(1.0, q) == (1.0, False)                   # equal to the one-share floor: fills in full
    assert fills._partial(3.0, _q("X", 100, 100, 100, volume=100)) == (1.0, True)     # still a real partial
    assert fills._partial(500, _q("X", 100, 100, 100, volume=10_000)) == (100.0, True)  # large order: unchanged
    assert fills._partial(10, _q("X", 100, 100, 100, volume=5_000_000)) == (10, False)  # normal liquidity: unchanged


def test_fractional_stop_order_with_tiny_volume_fills_exactly_the_ordered_quantity_and_reconciles():
    pos = _buy("META", 0.118596, 750.5)
    db.execute("UPDATE positions SET stop=706.2, target=839.43 WHERE symbol='META'")
    broker.manage_open_positions({"META": _q("META", 700.0, 736.0, 699.0, volume=3, open_=735.0)}, "2026-10-16")

    f = db.query("SELECT f.quantity fq, o.quantity oq FROM fills f JOIN orders o ON o.order_id=f.order_id WHERE o.side='SELL'")
    assert len(f) == 1 and f[0]["fq"] == pytest.approx(f[0]["oq"]) == pytest.approx(pos["quantity"])
    assert db.query_one("SELECT status FROM positions WHERE symbol='META'")["status"] == "closed"
    rec = broker.reconcile()
    assert rec["reconciled"] and rec["delta"] == 0.0, rec


def test_partial_exit_then_close_keeps_the_ledger_reconciled_at_every_step():
    pos = _buy("SYN", 3.0, 20.0)
    db.execute("UPDATE positions SET stop=19.0, target=25.0 WHERE symbol='SYN'")

    # stop hit on a thin bar: only 1 of 3 shares fills -> the position stays open with 2 left
    broker.manage_open_positions({"SYN": _q("SYN", 18.9, 19.6, 18.8, volume=100, open_=19.5)}, "2026-10-16")
    p = db.query_one("SELECT * FROM positions WHERE symbol='SYN'")
    assert p["status"] == "open" and p["quantity"] == pytest.approx(2.0)
    assert p["realized_pnl"] == pytest.approx((19.0 - 0.0005 * 19.0 - pos["avg_entry"]) * 1.0, abs=0.01)
    mid = broker.reconcile()
    assert mid["reconciled"] and mid["delta"] == 0.0, mid          # open + partially realized

    # the next cycle closes the remainder on a liquid bar (the working partial order is processed first)
    for o in db.query("SELECT order_id FROM orders WHERE status IN ('pending','partial')"):
        broker.process_order(o["order_id"], _q("SYN", 18.9, 19.6, 18.8, volume=5_000_000, open_=19.5))
    p = db.query_one("SELECT * FROM positions WHERE symbol='SYN'")
    assert p["status"] == "closed"

    sells = db.query("SELECT f.quantity q, f.price px FROM fills f JOIN orders o ON o.order_id=f.order_id WHERE o.side='SELL'")
    assert sum(s["q"] for s in sells) == pytest.approx(3.0)
    expected = sum((s["px"] - pos["avg_entry"]) * s["q"] for s in sells)
    assert p["realized_pnl"] == pytest.approx(expected, abs=1e-3), "closed P&L must include the earlier partial exit"
    end = broker.reconcile()
    assert end["reconciled"] and end["delta"] == 0.0, end


def test_normal_full_size_round_trip_is_unchanged():
    pos = _buy("AAA", 1.5, 100.0)
    db.execute("UPDATE positions SET stop=97.0, target=106.0 WHERE symbol='AAA'")
    broker.manage_open_positions({"AAA": _q("AAA", 107.0, 107.5, 105.0, volume=5_000_000)}, "2026-10-16")
    p = db.query_one("SELECT * FROM positions WHERE symbol='AAA'")
    assert p["status"] == "closed" and p["realized_pnl"] == pytest.approx((106.0 - pos["avg_entry"]) * 1.5, abs=1e-3)
    assert broker.reconcile()["reconciled"]
