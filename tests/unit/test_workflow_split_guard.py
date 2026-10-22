"""Paper-runtime safety tests: `lab/paper/workflow.py`'s `market_hours()` wired to `corporate_actions.
SplitGuard`, exercised through the REAL workflow function (not the guard in isolation -- see
tests/unit/test_corporate_actions.py for that). Confirms three things together, against the real
`manage_open_positions()`:

  1. A confirmed split adjusts the position and does NOT produce an artificial stop trigger.
  2. A plausible-but-unconfirmed split pauses that position's stop/target check for the cycle -- it stays
     open, untouched, rather than being liquidated against a raw, unadjusted stop.
  3. A genuine ordinary stop-loss (no split shape at all) still fires normally -- the paper-runtime safety
     property this whole module exists to preserve: SplitGuard must never swallow a real stop.

Fully offline and hermetic: `quote_for` and `_prior_close_and_volume` are monkeypatched directly (no
provider network access), same fixture pattern as tests/unit/test_paper_trading.py.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in ("src", "dashboard", "lab"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

from paper import db, workflow as wf  # noqa: E402
from paper.fills import Quote  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="papertest_wfsg_")
    monkeypatch.setenv("PAPER_DATA_DIR", tmp)
    monkeypatch.setenv("PAPER_LEDGER", "robinhood_500_baseline")
    monkeypatch.setenv("PAPER_INITIAL_CASH", "500")
    monkeypatch.setenv("PAPER_INITIAL_EQUITY", "500")
    db.reset_for_tests(tmp)
    yield
    db.close()


def _open_position(symbol="ZZZ", quantity=1.0, avg_entry=100.0, stop=95.0, target=110.0):
    pid = f"pos_{symbol}"
    db.execute(
        "INSERT INTO positions(position_id, symbol, opened_at, quantity, avg_entry, stop, target, status) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (pid, symbol, db.utcnow(), quantity, avg_entry, stop, target, "open"))
    return pid


def test_market_hours_confirms_a_split_and_the_position_survives_instead_of_artificially_stopping(monkeypatch):
    pid = _open_position(symbol="AVGO", quantity=1.0, avg_entry=1621.8269, stop=1536.54, target=1800.0)
    monkeypatch.setattr(wf, "quote_for", lambda sym: Quote(
        sym, last=162.18269, high=163.0, low=161.0, volume=20_000_000.0, source_ts=1_700_000_000.0))
    monkeypatch.setattr(wf, "_prior_close_and_volume", lambda sym, ts: (1621.8269, 2_000_000.0))

    result = wf.market_hours(session_date="2024-07-16", scope="positions")

    pos = db.query_one("SELECT * FROM positions WHERE position_id=?", (pid,))
    assert pos["status"] == "open"                              # NOT artificially stopped out
    assert pos["quantity"] == pytest.approx(10.0)                # split-adjusted
    assert pos["stop"] == pytest.approx(153.654, rel=1e-4)       # stop rescaled, not compared raw
    assert result["exits"] == 0                       # no exit fired this cycle


def test_market_hours_pauses_a_plausible_unconfirmed_split_instead_of_liquidating_blindly(monkeypatch):
    pid = _open_position(symbol="CCC", quantity=1.0, avg_entry=500.0, stop=480.0, target=560.0)
    # ratio looks like a clean 10x split, but no volume data is available to corroborate it.
    monkeypatch.setattr(wf, "quote_for", lambda sym: Quote(
        sym, last=50.0, high=51.0, low=49.0, volume=None, source_ts=1_700_000_000.0))
    monkeypatch.setattr(wf, "_prior_close_and_volume", lambda sym, ts: (500.0, None))

    wf.market_hours(session_date="2024-07-16", scope="positions")

    pos = db.query_one("SELECT * FROM positions WHERE position_id=?", (pid,))
    assert pos["status"] == "open"
    assert pos["quantity"] == pytest.approx(1.0)                 # untouched -- fail closed, not adjusted
    assert pos["avg_entry"] == pytest.approx(500.0)
    trail = db.audit_trail(pid)
    assert any(e["event"] == "split_suspected_unconfirmed_paused" for e in trail)


def test_market_hours_still_fires_a_genuine_stop_loss_when_nothing_looks_like_a_split(monkeypatch):
    """The critical safety property: SplitGuard must never swallow a real stop-loss."""
    pid = _open_position(symbol="DDD", quantity=1.0, avg_entry=100.0, stop=95.0, target=120.0)
    # an ordinary (if unpleasant) down day -- price drops through the real stop, no split shape at all.
    monkeypatch.setattr(wf, "quote_for", lambda sym: Quote(
        sym, last=90.0, high=94.0, low=88.0, volume=1_100_000.0, source_ts=1_700_000_000.0))
    monkeypatch.setattr(wf, "_prior_close_and_volume", lambda sym, ts: (98.0, 1_000_000.0))

    result = wf.market_hours(session_date="2024-07-16", scope="positions")

    pos = db.query_one("SELECT * FROM positions WHERE position_id=?", (pid,))
    assert pos["status"] == "closed"                              # the real stop fired, exactly as it should
    assert pos["exit_reason"] == "exit_stop"
    assert result["exits"] == 1
