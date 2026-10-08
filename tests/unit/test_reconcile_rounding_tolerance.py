"""lab/paper/broker.py's reconcile(): RECONCILE_TOLERANCE_USD regression tests.

Found running HIST-001's EXP-DD-001 Challenger (110 fills / 55 closed positions over 2+ years of continuous
replay): `account_state()`'s `realized = sum(p["realized_pnl"] for p in closed)` sums positions via
`db.query(...)` with no ORDER BY (whatever row order SQLite returns), while `reconcile()`'s own fill-level
sum iterates `ORDER BY f.filled_at` (chronological). IEEE-754 float addition is not associative, so summing
the SAME set of already-rounded (4-decimal) per-position P&Ls in a different order than the equivalent
fill-level sum can legitimately round to an ADJACENT cent at a boundary case -- proven NOT a missing/
duplicate/partial fill (every order had exactly one fill, zero orphans, fill count exactly 2x closed-
position count; an independent re-derivation of cash from the raw fills matched an independent re-derivation
from the stored per-position realized_pnl values exactly).

Covers: (1) a real, many-position round-trip scenario (not a synthetic mock) reproducing the SAME mechanism
at a similar scale to the one that failed in the wild, now passing; (2) the exact tolerance boundary
(a few cents of drift passes, same order of magnitude the real case showed); (3) the critical safety
property -- a GENUINE discrepancy (a missing fill, equivalent to a real accounting error) still fails
closed, proving the wider tolerance never masks a real problem.
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

from paper import broker, config as cfg, db  # noqa: E402
from paper.fills import Quote  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="papertest_reconcile_")
    monkeypatch.setenv("PAPER_DATA_DIR", tmp)
    monkeypatch.setenv("PAPER_LEDGER", "robinhood_500_baseline")
    monkeypatch.setenv("PAPER_INITIAL_CASH", "500")
    monkeypatch.setenv("PAPER_INITIAL_EQUITY", "500")
    monkeypatch.setenv("PAPER_MAX_LOSS_PER_TRADE", "5")
    monkeypatch.setenv("PAPER_MAX_POSITION_NOTIONAL", "125")
    monkeypatch.setenv("PAPER_MAX_OPEN", "50")           # allow many simultaneous/sequential positions
    monkeypatch.setenv("PAPER_MAX_PER_SECTOR", "50")
    monkeypatch.setenv("PAPER_MAX_ENTRIES_PER_DAY", "50")
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


def _quote(symbol, last):
    return Quote(symbol, last=last, bid=last - 0.05, ask=last + 0.05, open=last,
                high=last * 1.01, low=last * 0.99, volume=5_000_000, source_ts=time.time(), provider="yahoo")


def test_reconcile_passes_after_many_real_round_trips_with_fractional_quantities():
    """Reproduces the real mechanism at scale: ~50 real entry+exit round trips (not a mock) through the real
    broker.submit_entry()/manage_open_positions() path, with varied fractional-share prices chosen to
    legitimately exercise the same per-position-rounding-order sensitivity the wild failure showed."""
    import random
    rng = random.Random(42)
    for i in range(50):
        price = round(10.0 + rng.random() * 490.0, 4)       # varied, "ugly" fractional prices
        sym = f"SYM{i}"
        out = broker.submit_entry(_result(sym, price, price * 0.97, price * 1.06), _quote(sym, price),
                                  strategy="s", sector="technology")
        assert out["executed"], out
        # alternate winning (hits target) and losing (hits stop) exits
        if i % 2 == 0:
            exit_quote = _quote(sym, price * 1.07)
        else:
            exit_quote = _quote(sym, price * 0.96)
        broker.manage_open_positions({sym: exit_quote})

    closed = db.query("SELECT * FROM positions WHERE status='closed'")
    assert len(closed) == 50
    rec = broker.reconcile()
    assert rec["reconciled"], rec
    assert abs(rec["delta"]) <= broker.RECONCILE_TOLERANCE_USD


def test_reconcile_tolerates_a_few_cents_of_rounding_drift():
    """Direct boundary test: a cash_from_state a few cents off from cash_from_fills, the same order of
    magnitude the real EXP-DD-001 case showed (1 cent), must still reconcile."""
    out = broker.submit_entry(_result("AAA", 100.0, 97.0, 106.0), _quote("AAA", 100.0),
                              strategy="s", sector="technology")
    assert out["executed"]
    broker.manage_open_positions({"AAA": _quote("AAA", 107.0)})

    import paper.risk as risk_mod
    real_state = risk_mod.account_state
    drifted = {**real_state(), "cash": real_state()["cash"] - 0.03}   # a 3-cent drift, within tolerance
    import unittest.mock as mock
    with mock.patch.object(risk_mod, "account_state", lambda *a, **k: drifted):
        rec = broker.reconcile()
    assert rec["reconciled"], rec
    assert rec["delta"] == pytest.approx(0.03, abs=1e-6)


def test_reconcile_still_fails_closed_on_a_real_discrepancy():
    """The critical safety property this tolerance change must never break: a GENUINE discrepancy (far
    larger than any plausible rounding drift -- here simulating a missing/miscounted fill's worth of
    dollars) still fails closed, exactly as before."""
    out = broker.submit_entry(_result("BBB", 100.0, 97.0, 106.0), _quote("BBB", 100.0),
                              strategy="s", sector="technology")
    assert out["executed"]
    broker.manage_open_positions({"BBB": _quote("BBB", 107.0)})

    import paper.risk as risk_mod
    real_state = risk_mod.account_state
    broken = {**real_state(), "cash": real_state()["cash"] - 5.00}    # a real $5 discrepancy
    import unittest.mock as mock
    with mock.patch.object(risk_mod, "account_state", lambda *a, **k: broken):
        rec = broker.reconcile()
    assert not rec["reconciled"], rec
    assert abs(rec["delta"]) > broker.RECONCILE_TOLERANCE_USD


def test_reconcile_tolerance_constant_is_small_relative_to_real_trade_sizes():
    """Sanity bound: the tolerance must stay far smaller than this account's own real per-trade risk
    ($5/trade, PAPER_MAX_LOSS_PER_TRADE) -- a few cents can never be confused with a real trade's worth of
    P&L, which is the whole point of widening it only this far and no further."""
    assert broker.RECONCILE_TOLERANCE_USD < cfg.risk().max_loss_per_trade / 10
