"""lab/paper/corporate_actions.py -- live split detection/adjustment for the real paper-trading runtime.

Covers: (1) pure detection/classification (2:1, 10:1, reverse-split, and the ordinary-move case that must
NEVER be treated as a suspected split), (2) the fail-closed SplitGuard hook against a real hermetic DB
(confirmed -> adjust, plausible-but-unconfirmed -> pause without adjusting, ordinary move -> untouched),
(3) the P&L-neutrality invariant an adjustment must satisfy, and (4) an AVGO regression reproducing the
exact real-world numbers the Historical Lab found (2024-07-15, 10-for-1, ~$1621.83 entry).

Fully offline and hermetic, same fixture pattern as tests/unit/test_paper_trading.py: every test gets a
throwaway SQLite file, no provider is ever contacted.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in ("src", "dashboard", "lab"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

from paper import db  # noqa: E402
from paper.corporate_actions import (  # noqa: E402
    SplitGuard, apply_split_to_position, detect_suspected_split, is_confirmed, is_plausible_but_unconfirmed,
)


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="papertest_ca_")
    monkeypatch.setenv("PAPER_DATA_DIR", tmp)
    monkeypatch.setenv("PAPER_LEDGER", "robinhood_500_baseline")
    monkeypatch.setenv("PAPER_INITIAL_CASH", "500")
    monkeypatch.setenv("PAPER_INITIAL_EQUITY", "500")
    db.reset_for_tests(tmp)
    yield
    db.close()


def _open_position(position_id="pos_1", symbol="ZZZ", quantity=1.0, avg_entry=100.0, stop=95.0, target=110.0,
                   mfe=2.0, mae=-1.0, planned_risk=5.0):
    db.execute(
        "INSERT INTO positions(position_id, symbol, opened_at, quantity, avg_entry, stop, target, status, "
        "mfe, mae, planned_risk) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (position_id, symbol, db.utcnow(), quantity, avg_entry, stop, target, "open", mfe, mae, planned_risk))
    return db.query_one("SELECT * FROM positions WHERE position_id=?", (position_id,))


class _Q:
    """Minimal stand-in for paper.fills.Quote -- only .last and .volume are read by SplitGuard."""
    def __init__(self, last, volume=None):
        self.last = last
        self.volume = volume


# ── Pure detection/classification ──────────────────────────────────────────────────────────────────────

def test_detects_a_clean_10_for_1_forward_split_with_volume_confirmation():
    ev = detect_suspected_split("AVGO", prior_close=1700.0, current_price=170.0,
                                prior_volume=2_000_000.0, current_volume=20_000_000.0)
    assert ev is not None
    assert ev.inferred_split_ratio == pytest.approx(10.0)
    assert ev.confidence == "high"
    assert ev.volume_confirms is True
    assert is_confirmed(ev)


def test_detects_a_clean_3_for_1_forward_split():
    # ratio 0.333, comfortably outside SUSPECT_RATIO_LOW=0.6.
    ev = detect_suspected_split("AAA", prior_close=300.0, current_price=100.0,
                                prior_volume=1_000_000.0, current_volume=3_000_000.0)
    assert ev.inferred_split_ratio == pytest.approx(3.0)
    assert is_confirmed(ev)


def test_detects_a_clean_1_for_3_reverse_split():
    # a 1-for-3 reverse split: price TRIPLES, share count drops to a third -- apply_split()'s convention is
    # ratio < 1 for a reverse split (shares multiply by ratio -> shrink). Ratio 3.0, comfortably outside
    # SUSPECT_RATIO_HIGH=1/0.6=1.667.
    ev = detect_suspected_split("BBB", prior_close=10.0, current_price=30.0,
                                prior_volume=6_000_000.0, current_volume=2_000_000.0)
    assert ev.inferred_split_ratio == pytest.approx(1.0 / 3.0)
    assert is_confirmed(ev)


def test_detects_a_clean_2_for_1_forward_split_with_volume_confirmation():
    """The real blocker this PR exists to resolve: a clean 2:1 split (ratio 0.5) previously fell inside the
    original [0.4, 2.5] band and was never even considered. Now correctly detected and confirmed."""
    ev = detect_suspected_split("FFF", prior_close=200.0, current_price=100.0,
                                prior_volume=1_000_000.0, current_volume=2_000_000.0)
    assert ev is not None
    assert ev.inferred_split_ratio == pytest.approx(2.0)
    assert ev.confidence == "high"
    assert ev.volume_confirms is True
    assert is_confirmed(ev)


def test_detects_a_clean_1_for_2_reverse_split_with_volume_confirmation():
    """The symmetric case: a clean 1-for-2 reverse split (ratio 2.0) previously fell inside the original
    [0.4, 2.5] band too. Now correctly detected and confirmed."""
    ev = detect_suspected_split("GGG", prior_close=50.0, current_price=100.0,
                                prior_volume=2_000_000.0, current_volume=1_000_000.0)
    assert ev is not None
    assert ev.inferred_split_ratio == pytest.approx(0.5)
    assert ev.confidence == "high"
    assert ev.volume_confirms is True
    assert is_confirmed(ev)


def test_2_for_1_without_volume_data_is_paused_not_silently_applied():
    """Same fail-closed discipline as the 10:1 case: a clean 2:1 ratio with no volume data to corroborate
    it is plausible-but-unconfirmed, never auto-applied."""
    ev = detect_suspected_split("HHH", prior_close=200.0, current_price=100.0)
    assert ev is not None
    assert ev.confidence == "low"
    assert not is_confirmed(ev)
    assert is_plausible_but_unconfirmed(ev)


def test_an_ordinary_38_percent_move_still_never_gets_suspected():
    """The false-positive guard the narrowed band is specifically designed to preserve: a real, ordinary
    (if large) single-day move that stays under the new SUSPECT_RATIO_LOW=0.6 threshold is never even
    considered for split detection -- confirms narrowing [0.4, 2.5] to [0.6, 1.667] did not sacrifice the
    generous allowance for genuine volatility the original design was built around."""
    ev = detect_suspected_split("III", prior_close=100.0, current_price=62.0)   # a 38% down day
    assert ev is None


def test_a_45_percent_crash_that_is_not_a_clean_split_ratio_is_never_paused():
    """Beyond the SUSPECT band (ratio 0.55 < 0.6) but NOT close to any clean split factor (nearest is 2.0,
    off by ~10%, past the 8% tolerance) -- a genuine, if severe, real crash. Must resolve to case 3
    (ordinary move) and never pause real risk management, even though the narrowed band now means more
    large moves enter the detection pipeline at all than before this fix."""
    ev = detect_suspected_split("JJJ", prior_close=100.0, current_price=55.0)
    assert ev is not None                           # now inside the (narrower) suspect band...
    assert not is_confirmed(ev)
    assert not is_plausible_but_unconfirmed(ev)      # ...but never flagged as plausibly a split either


def test_plausible_ratio_without_volume_data_is_unconfirmed_not_silently_applied():
    ev = detect_suspected_split("CCC", prior_close=500.0, current_price=50.0)   # no volume supplied
    assert ev is not None
    assert ev.confidence == "low"                 # confidence collapses without volume corroboration
    assert not is_confirmed(ev)
    assert is_plausible_but_unconfirmed(ev)        # but the ratio itself still looks like a clean 10x


def test_a_genuine_crash_that_does_not_match_any_split_ratio_is_not_flagged_as_plausible():
    # a messy 63% single-day drop (ratio 0.37, magnitude 2.70) -- outside the ordinary band but nowhere near
    # a clean split factor (nearest common factor is 3.0, off by ~10%, past the 8% tolerance). Must NOT be
    # treated as a suspected split at all (case 3 in the module's own docstring): real risk management must
    # proceed untouched.
    ev = detect_suspected_split("DDD", prior_close=100.0, current_price=37.0)
    assert ev is not None                          # it IS outside the normal day-over-day band...
    assert not is_confirmed(ev)
    assert not is_plausible_but_unconfirmed(ev)     # ...but it never gets the fail-closed pause


def test_an_ordinary_days_move_is_not_flagged_at_all():
    ev = detect_suspected_split("EEE", prior_close=100.0, current_price=94.0)   # a normal down day
    assert ev is None
    ev2 = detect_suspected_split("EEE", prior_close=100.0, current_price=115.0)  # a normal up day
    assert ev2 is None


# ── apply_split_to_position: P&L-neutrality invariant ───────────────────────────────────────────────────

def test_apply_split_to_position_preserves_economic_exposure_and_books_no_pnl():
    pos = {"position_id": "p1", "symbol": "AVGO", "quantity": 1.0, "avg_entry": 1621.8269, "stop": 1536.54,
          "target": 1800.0, "mfe": 50.0, "mae": -20.0, "realized_pnl": None, "status": "open"}
    adjusted = apply_split_to_position(pos, 10.0)

    assert adjusted is not pos                                              # never mutates the input
    assert adjusted["quantity"] == pytest.approx(10.0)
    assert adjusted["avg_entry"] == pytest.approx(162.18269, rel=1e-4)
    assert adjusted["stop"] == pytest.approx(153.654, rel=1e-4)
    assert adjusted["target"] == pytest.approx(180.0, rel=1e-4)

    # economic exposure (quantity * avg_entry) and dollar risk (quantity * (avg_entry - stop)) are invariant
    before_exposure = pos["quantity"] * pos["avg_entry"]
    after_exposure = adjusted["quantity"] * adjusted["avg_entry"]
    assert after_exposure == pytest.approx(before_exposure, rel=1e-6)
    before_risk = pos["quantity"] * (pos["avg_entry"] - pos["stop"])
    after_risk = adjusted["quantity"] * (adjusted["avg_entry"] - adjusted["stop"])
    assert after_risk == pytest.approx(before_risk, rel=1e-6)

    # the adjustment itself generates no realized P&L
    assert adjusted["realized_pnl"] is None


def test_apply_split_to_position_is_a_noop_on_fields_not_present():
    pos = {"position_id": "p1", "symbol": "AAA", "quantity": 2.0, "avg_entry": 50.0}   # no stop/target/mfe/mae
    adjusted = apply_split_to_position(pos, 2.0)
    assert adjusted["quantity"] == pytest.approx(4.0)
    assert adjusted["avg_entry"] == pytest.approx(25.0)
    assert "stop" not in adjusted


# ── SplitGuard: the fail-closed per-cycle hook against a real DB ───────────────────────────────────────

def test_split_guard_adjusts_a_confirmed_split_and_audits_it():
    pos = _open_position(symbol="AAA", quantity=1.0, avg_entry=300.0, stop=285.0, target=330.0)
    guard = SplitGuard()
    paused = guard.check_and_adjust(
        [pos], {"AAA": _Q(100.0, volume=3_000_000.0)}, {"AAA": (300.0, 1_000_000.0)})

    assert paused == set()                          # confirmed and adjusted, never paused
    updated = db.query_one("SELECT * FROM positions WHERE position_id=?", (pos["position_id"],))
    assert updated["quantity"] == pytest.approx(3.0)
    assert updated["avg_entry"] == pytest.approx(100.0)
    assert updated["stop"] == pytest.approx(95.0)
    assert updated["target"] == pytest.approx(110.0)
    trail = db.audit_trail(pos["position_id"])
    assert any(e["event"] == "split_adjusted_live" for e in trail)


def test_split_guard_pauses_a_plausible_but_unconfirmed_split_without_adjusting():
    pos = _open_position(symbol="BBB", quantity=1.0, avg_entry=500.0, stop=480.0, target=560.0)
    guard = SplitGuard()
    paused = guard.check_and_adjust(
        [pos], {"BBB": _Q(50.0)}, {"BBB": (500.0, None)})            # no volume data available

    assert paused == {"BBB"}
    unchanged = db.query_one("SELECT * FROM positions WHERE position_id=?", (pos["position_id"],))
    assert unchanged["quantity"] == pytest.approx(1.0)                # untouched -- fail closed
    assert unchanged["avg_entry"] == pytest.approx(500.0)
    trail = db.audit_trail(pos["position_id"])
    assert any(e["event"] == "split_suspected_unconfirmed_paused" for e in trail)
    assert not any(e["event"] == "split_adjusted_live" for e in trail)


def test_split_guard_never_pauses_an_ordinary_large_move():
    """The paper-runtime safety property this module exists to preserve: a genuine large adverse move
    (not shaped like a split) must NEVER have its stop/target management paused."""
    pos = _open_position(symbol="CCC", quantity=1.0, avg_entry=100.0, stop=90.0, target=120.0)
    guard = SplitGuard()
    paused = guard.check_and_adjust(
        [pos], {"CCC": _Q(53.0)}, {"CCC": (100.0, None)})             # a messy 47% real crash, no split shape

    assert paused == set()
    unchanged = db.query_one("SELECT * FROM positions WHERE position_id=?", (pos["position_id"],))
    assert unchanged["quantity"] == pytest.approx(1.0)                 # SplitGuard didn't touch it either way
    assert db.audit_trail(pos["position_id"]) == []                    # nothing logged -- not its concern


def test_split_guard_untouched_when_no_prior_reference_or_quote_available():
    pos = _open_position(symbol="DDD")
    guard = SplitGuard()
    paused = guard.check_and_adjust([pos], {}, {})                    # no quote, no reference data at all
    assert paused == set()


# ── AVGO regression: the exact real-world numbers the Historical Lab found ────────────────────────────

def test_avgo_regression_10_for_1_split_no_longer_produces_an_artificial_liquidation():
    """Reproduces research/historical/hist001/split_aware_diagnostic.py's real finding
    (entry $1621.8269, 2024-07-15 10-for-1 split) against THIS production module: the position survives the
    split with a correctly-rescaled stop instead of an artificial ~17R stop trigger."""
    pos = _open_position(symbol="AVGO", quantity=0.039368, avg_entry=1621.8269, stop=1536.54,
                         target=1800.0, planned_risk=3.4)
    guard = SplitGuard()
    paused = guard.check_and_adjust(
        [pos], {"AVGO": _Q(162.18269, volume=20_000_000.0)},
        {"AVGO": (1621.8269, 2_000_000.0)})

    assert paused == set()
    updated = db.query_one("SELECT * FROM positions WHERE position_id=?", (pos["position_id"],))
    assert updated["quantity"] == pytest.approx(0.39368, rel=1e-4)     # 10x shares
    assert updated["avg_entry"] == pytest.approx(162.18269, rel=1e-4)   # entry / 10
    assert updated["stop"] == pytest.approx(153.654, rel=1e-4)          # stop / 10 -- NOT compared raw vs 1536.54

    # the artificial trigger this module exists to prevent: the raw post-split price (162.18) sits ABOVE
    # the correctly-adjusted stop (153.654), so a subsequent manage_open_positions() call would NOT fire --
    # whereas comparing the same raw price against the UNADJUSTED stop (1536.54) would have looked like a
    # ~90% "loss" and fired instantly. This assertion is the actual regression guard.
    assert updated["stop"] < 162.18269
