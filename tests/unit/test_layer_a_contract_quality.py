"""Canonical Option Architecture v1.1 — Layer A dedicated unit tests.

Tests canonical/contract_quality.py in isolation. This is NOT the invariant/
specification file (tests/unit/test_canonical_v11_invariants.py) — that file
tests cross-pipeline invariants against EXISTING production code and is
re-run, not edited, after this step. This file tests the new module's own
correctness directly.

Fully offline and deterministic: every freshness-sensitive case pins an
explicit `now`, so nothing depends on wall-clock at run time.

Run: pytest tests/unit/test_layer_a_contract_quality.py -v
"""
from __future__ import annotations

import inspect
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)  # so `import canonical...` resolves the same way a
                           # real caller running from repo root would find it,
                           # without relying on -m's cwd-insertion behavior.

from canonical.contract_quality import (   # noqa: E402
    ContractQualityPolicy, FreshnessStatus, GreeksProvenance, LiquidityTier,
    evaluate_contract_quality, from_analyse_contract_shape,
    from_grade_contract_shape,
)

NOW = datetime(2026, 6, 15, 15, 30, tzinfo=timezone.utc)  # a fixed instant — arbitrary trading afternoon
POL = ContractQualityPolicy()  # X=50, Y=250, spread cap 12%, etc. — the provisional defaults


def _good(**overrides):
    """A perfectly liquid, tight-spread, ATM, fresh, fully-observed CALL —
    the baseline every single-axis test perturbs exactly one field of."""
    base = dict(
        strike=103.0, underlying=100.0, side="CALL", bid=1.00, ask=1.02,
        volume=300, open_interest=1000, implied_volatility=0.30, delta=0.50,
        dte=14, quote_timestamp=NOW.isoformat(), session_open=True, now=NOW,
    )
    base.update(overrides)
    return evaluate_contract_quality(**base)


# ══════════════════════════════════════════════════════════════════════════
# Structural enforcement of zero account-awareness
# ══════════════════════════════════════════════════════════════════════════

def test_layer_a_has_zero_account_parameters():
    """The signature itself must not admit any account-fit concept. This
    inspects the real signature rather than asserting on behavior, so it
    fails loudly if anyone ever adds `balance=`/`equity=`/etc."""
    sig = inspect.signature(evaluate_contract_quality)
    forbidden = {"balance", "equity", "buying_power", "portfolio",
                 "risk_budget", "max_loss_per_account", "contracts_affordable",
                 "account_policy", "account", "cash"}
    present = set(sig.parameters) & forbidden
    assert not present, f"Layer A signature must never gain an account-fit parameter, found: {present}"
    # No **kwargs escape hatch either — otherwise a caller COULD pass balance=
    # and have it silently swallowed instead of raising TypeError.
    kinds = {p.kind for p in sig.parameters.values()}
    assert inspect.Parameter.VAR_KEYWORD not in kinds, (
        "Layer A must not accept **kwargs — that would let account parameters "
        "through silently instead of raising TypeError."
    )
    with pytest.raises(TypeError):
        evaluate_contract_quality(balance=500.0)  # type: ignore[call-arg]


# ══════════════════════════════════════════════════════════════════════════
# quality_pass is about hard_failures, not score
# ══════════════════════════════════════════════════════════════════════════

def test_perfect_liquid_contract_passes():
    r = _good()
    assert r.quality_pass is True
    assert r.hard_failures == []
    assert r.grade in ("A", "B")
    assert r.liquidity_tier == LiquidityTier.ADEQUATE_PLUS
    assert r.greeks_provenance == GreeksProvenance.OBSERVED
    assert r.missing_observed_delta is False


def test_high_score_does_not_override_a_hard_failure():
    """The exact invariant the task calls out: a contract can score well and
    still have quality_pass=False. A stale quote is the cleanest case — every
    other axis is the good baseline."""
    stale_ts = (NOW - timedelta(hours=72)).isoformat()
    r = _good(quote_timestamp=stale_ts)
    assert r.quality_pass is False
    assert r.score >= 60, f"expected a respectable score despite the hard fail, got {r.score}"
    assert any("stale" in f for f in r.hard_failures)


# ══════════════════════════════════════════════════════════════════════════
# DTE
# ══════════════════════════════════════════════════════════════════════════

def test_0_dte_hard_fails():
    r = _good(dte=0)
    assert r.quality_pass is False
    assert any("0-DTE" in f or "below the 7-day" in f for f in r.hard_failures)


def test_dte_6_hard_fails():
    r = _good(dte=6)
    assert r.quality_pass is False


def test_dte_7_passes_the_dte_rule():
    r = _good(dte=7)
    assert not any("minimum" in f for f in r.hard_failures)
    assert r.quality_pass is True


def test_dte_missing_hard_fails_distinctly_from_dte_zero():
    r_missing = _good(dte=None)
    r_zero = _good(dte=0)
    assert r_missing.quality_pass is False and r_zero.quality_pass is False
    assert r_missing.hard_failures != r_zero.hard_failures
    assert any("missing DTE" in f for f in r_missing.hard_failures)
    assert any("0-DTE" in f for f in r_zero.hard_failures)


def test_dte_above_warn_ceiling_is_a_warning_not_a_hard_fail():
    r = _good(dte=90)
    assert r.quality_pass is True
    assert any("soft ceiling" in w for w in r.warnings)


# ══════════════════════════════════════════════════════════════════════════
# Quote freshness
# ══════════════════════════════════════════════════════════════════════════

def test_stale_over_30h_hard_fails():
    r = _good(quote_timestamp=(NOW - timedelta(hours=31)).isoformat())
    assert r.quality_pass is False
    assert r.freshness_status == FreshnessStatus.STALE


def test_exactly_30h_boundary_passes():
    r = _good(quote_timestamp=(NOW - timedelta(hours=30)).isoformat())
    assert r.quality_pass is True
    assert r.freshness_status == FreshnessStatus.FRESH


def test_missing_timestamp_hard_fails_as_unknown():
    r = _good(quote_timestamp=None)
    assert r.quality_pass is False
    assert r.freshness_status == FreshnessStatus.UNKNOWN
    assert any("missing quote timestamp" in f for f in r.hard_failures)


# ══════════════════════════════════════════════════════════════════════════
# bid/ask
# ══════════════════════════════════════════════════════════════════════════

def test_zero_bid_hard_fails_with_explicit_diagnostic():
    r = _good(bid=0.0)
    assert r.quality_pass is False
    assert any("zero bid" in f for f in r.hard_failures)
    assert r.two_sided is False


def test_missing_ask_hard_fails_as_one_sided():
    r = _good(ask=None)
    assert r.quality_pass is False
    assert any("missing ask" in f and "one-sided" in f for f in r.hard_failures)


def test_one_sided_quote_bid_only_hard_fails():
    r = _good(ask=None)
    assert r.two_sided is False
    assert r.spread_pct is None and r.spread_dollars is None


# ══════════════════════════════════════════════════════════════════════════
# OTM / moneyness — CALL and PUT
# ══════════════════════════════════════════════════════════════════════════

def test_call_exactly_7pct_otm_passes():
    r = _good(strike=107.0, underlying=100.0, side="CALL")
    assert r.moneyness_pct == pytest.approx(7.0, abs=0.01)
    assert not any("OTM" in f for f in r.hard_failures)
    assert r.quality_pass is True


def test_call_above_7pct_otm_hard_fails():
    r = _good(strike=108.0, underlying=100.0, side="CALL")
    assert r.quality_pass is False
    assert any("OTM" in f for f in r.hard_failures)


def test_put_exactly_7pct_otm_passes():
    r = _good(strike=93.0, underlying=100.0, side="PUT")
    assert r.moneyness_pct == pytest.approx(7.0, abs=0.01)
    assert r.quality_pass is True


def test_put_above_7pct_otm_hard_fails():
    r = _good(strike=92.0, underlying=100.0, side="PUT")
    assert r.quality_pass is False
    assert any("OTM" in f for f in r.hard_failures)


# ══════════════════════════════════════════════════════════════════════════
# Greeks provenance — three states, no 100/100 bug
# ══════════════════════════════════════════════════════════════════════════

def test_observed_delta_is_provenance_observed_no_penalty():
    r = _good(delta=0.50)
    assert r.greeks_provenance == GreeksProvenance.OBSERVED
    assert r.missing_observed_delta is False
    assert not any("MODELED" in w for w in r.warnings)


def test_missing_delta_with_iv_models_and_flags_it():
    r = _good(delta=None, implied_volatility=0.30)
    assert r.greeks_provenance == GreeksProvenance.MODEL
    assert r.missing_observed_delta is True
    assert any("MODELED" in w for w in r.warnings), "the substitution must be surfaced, never hidden"
    # the historical bug: missing delta silently scoring 100/100 with zero
    # penalty. A modeled substitution must not reach a perfect score/grade.
    assert not (r.score >= 99.9), (
        f"modeled-delta contract scored {r.score} — must never reach a "
        "perfect score the way the historical grade_contract() bug did"
    )


def test_missing_delta_and_missing_iv_is_unavailable_and_hard_fails():
    r = _good(delta=None, implied_volatility=None)
    assert r.greeks_provenance == GreeksProvenance.UNAVAILABLE
    assert r.quality_pass is False
    assert any("Greeks unavailable" in f for f in r.hard_failures)


def test_missing_delta_never_silently_scores_100_even_when_unavailable():
    r = _good(delta=None, implied_volatility=None)
    assert r.score < 100, "the historical missing-delta 100/100 bug must not recur"


# ══════════════════════════════════════════════════════════════════════════
# IV — soft only
# ══════════════════════════════════════════════════════════════════════════

def test_missing_iv_is_a_warning_not_a_hard_fail():
    r = _good(implied_volatility=None, delta=0.50)  # observed delta so Greeks don't cascade-fail
    assert r.quality_pass is True
    assert any("implied volatility" in w for w in r.warnings)


def test_extreme_iv_is_warning_only():
    r = _good(implied_volatility=1.50, delta=0.50)  # 150% IV, observed delta
    assert r.quality_pass is True
    assert any("extreme IV" in w for w in r.warnings)


# ══════════════════════════════════════════════════════════════════════════
# Session-aware volume, and genuine 0 vs missing
# ══════════════════════════════════════════════════════════════════════════

def test_zero_volume_session_open_hard_fails():
    r = _good(volume=0, session_open=True)
    assert r.quality_pass is False
    assert any("session-open floor" in f for f in r.hard_failures)


def test_zero_volume_session_closed_is_waived():
    r = _good(volume=0, session_open=False)
    assert r.quality_pass is True
    assert any("waived" in w for w in r.warnings)


def test_missing_volume_is_a_warning_not_a_hard_fail():
    r = _good(volume=None)
    assert r.quality_pass is True
    assert any("missing volume" in w for w in r.warnings)


# ══════════════════════════════════════════════════════════════════════════
# OI three-tier model — exhaustive, non-overlapping, X/Y open
# ══════════════════════════════════════════════════════════════════════════

def test_oi_below_x_hard_fails_illiquid():
    r = _good(open_interest=POL.oi_floor_x - 1)
    assert r.quality_pass is False
    assert r.liquidity_tier == LiquidityTier.ILLIQUID


def test_oi_exactly_x_is_thin_not_hard_fail():
    r = _good(open_interest=POL.oi_floor_x)
    assert r.liquidity_tier == LiquidityTier.THIN
    assert r.quality_pass is True
    assert any("thin" in w for w in r.warnings)


def test_oi_between_x_and_y_is_thin():
    mid = (POL.oi_floor_x + POL.oi_floor_y) // 2
    r = _good(open_interest=mid)
    assert r.liquidity_tier == LiquidityTier.THIN
    assert r.quality_pass is True


def test_oi_exactly_y_is_adequate_plus():
    r = _good(open_interest=POL.oi_floor_y)
    assert r.liquidity_tier == LiquidityTier.ADEQUATE_PLUS
    assert r.quality_pass is True


def test_oi_above_y_is_adequate_plus():
    r = _good(open_interest=POL.oi_floor_y + 1000)
    assert r.liquidity_tier == LiquidityTier.ADEQUATE_PLUS


def test_oi_missing_is_a_warning_not_a_hard_fail():
    r = _good(open_interest=None)
    assert r.quality_pass is True
    assert r.liquidity_tier is None
    assert any("missing open interest" in w for w in r.warnings)


def test_oi_tiers_are_exhaustive_and_non_overlapping():
    """A direct structural sweep, not just the five boundary points above —
    every integer OI from 0 to well past Y must land in exactly one tier."""
    for oi in range(0, POL.oi_floor_y + 100):
        r = _good(open_interest=oi)
        assert r.liquidity_tier is not None
        if oi < POL.oi_floor_x:
            assert r.liquidity_tier == LiquidityTier.ILLIQUID
        elif oi < POL.oi_floor_y:
            assert r.liquidity_tier == LiquidityTier.THIN
        else:
            assert r.liquidity_tier == LiquidityTier.ADEQUATE_PLUS


def test_oi_policy_rejects_an_inverted_x_y():
    with pytest.raises(ValueError):
        ContractQualityPolicy(oi_floor_x=250, oi_floor_y=50)


# ══════════════════════════════════════════════════════════════════════════
# Spread — below / exactly-at / above the (open) cap
# ══════════════════════════════════════════════════════════════════════════

def test_spread_below_cap_passes():
    r = _good(bid=1.00, ask=1.05)  # ~4.9%, under the 12% default cap
    assert r.quality_pass is True
    assert not any("spread" in f for f in r.hard_failures)


def test_spread_exactly_at_cap_passes():
    # solve bid/ask so spread_pct lands exactly at pol.max_spread_pct with a
    # $1.00 mid: spread_dollars = mid * cap/100
    cap = POL.max_spread_pct
    bid, ask = 1.00 - (cap / 100.0) / 2, 1.00 + (cap / 100.0) / 2
    r = _good(bid=round(bid, 6), ask=round(ask, 6))
    assert r.spread_pct == pytest.approx(cap, abs=0.05)
    assert r.quality_pass is True


def test_spread_above_cap_hard_fails():
    r = _good(bid=0.50, ask=1.50)  # spread% >> 12%
    assert r.quality_pass is False
    assert any("spread" in f and "exceeds" in f for f in r.hard_failures)


def test_spread_dollars_and_pct_both_reported():
    r = _good(bid=1.00, ask=1.02)
    assert r.spread_dollars == pytest.approx(0.02, abs=1e-6)
    assert r.spread_pct is not None


def test_spread_score_reference_is_not_confused_with_hard_cap():
    """10% spread must NOT hard-fail against the 12% cap, even though it's
    above the (separate, scoring-only) 10% reference point."""
    r = _good(bid=0.95, ask=1.05)  # 10.0% spread on a $1.00 mid
    assert r.spread_pct == pytest.approx(10.0, abs=0.1)
    assert not any("spread" in f for f in r.hard_failures), (
        "the 10% spread-score reference must never act as a hard-fail threshold"
    )


# ══════════════════════════════════════════════════════════════════════════
# Genuine zero vs missing, across every axis the task calls out
# ══════════════════════════════════════════════════════════════════════════

def test_genuine_zero_dte_differs_from_missing_dte():
    assert _good(dte=0).hard_failures != _good(dte=None).hard_failures


def test_genuine_zero_bid_differs_from_missing_bid():
    z, m = _good(bid=0.0), _good(bid=None)
    assert z.hard_failures != m.hard_failures
    assert any("zero bid" in f for f in z.hard_failures)
    assert any("missing bid" in f for f in m.hard_failures)


def test_genuine_zero_volume_differs_from_missing_volume():
    z, m = _good(volume=0), _good(volume=None)
    assert z.quality_pass is False   # session-open floor violated
    assert m.quality_pass is True    # unknown, not zero — a warning only
    assert z.hard_failures != m.hard_failures


def test_genuine_zero_spread_is_evaluated_not_treated_as_missing():
    r = _good(bid=1.00, ask=1.00)  # spread_dollars/pct == 0.0, a real tight market
    assert r.spread_pct == 0.0
    assert r.quality_pass is True
    assert not any("spread" in f for f in r.hard_failures)


# ══════════════════════════════════════════════════════════════════════════
# Adapters — reuse the two existing raw shapes, additive only
# ══════════════════════════════════════════════════════════════════════════

def test_from_grade_contract_shape_matches_direct_call():
    contract = {"strike": 103.0, "bid": 1.00, "ask": 1.02, "volume": 300,
                "open_interest": 1000, "implied_volatility": 0.30, "delta": 0.50}
    via_adapter = from_grade_contract_shape(
        contract, underlying=100.0, side="CALL", dte=14,
        quote_timestamp=NOW.isoformat(), now=NOW)
    direct = _good()
    assert via_adapter.quality_pass == direct.quality_pass
    assert via_adapter.grade == direct.grade


def test_from_analyse_contract_shape_derives_dte_from_expiry():
    expiry = (NOW + timedelta(days=14)).date().isoformat()
    contract = {"strike": 103.0, "bid": 1.00, "ask": 1.02, "volume": 300,
                "open_interest": 1000, "implied_volatility": 0.30, "delta": 0.50,
                "side": "CALL", "expiry": expiry, "quote_timestamp": NOW.isoformat()}
    r = from_analyse_contract_shape(contract, spot=100.0, now=NOW)
    assert r.dte == 14
    assert r.quality_pass is True
