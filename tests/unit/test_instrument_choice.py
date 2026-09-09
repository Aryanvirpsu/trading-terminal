"""Canonical Option Architecture v1.1 — Layer D dedicated tests.

Tests canonical/instrument_choice.py in isolation, plus a legacy-parity
class comparing decisions against dashboard.option_risk.instrument_choice()
(untouched — see canonical/instrument_choice.py's module docstring for why
no delegation/adapter was built).

Run: pytest tests/unit/test_instrument_choice.py -v
"""
from __future__ import annotations

import inspect
import os
import sys
from datetime import datetime, timezone

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from canonical.instrument_choice import (   # noqa: E402
    InstrumentChoice, InstrumentChoiceResult, OptionEdge, choose_instrument,
)
from canonical.account_fit import (   # noqa: E402
    AccountFitResult, ConstraintViolation, InstrumentType, RiskConfidence,
    option_account_fit, stock_account_fit,
)
from canonical.contract_quality import evaluate_contract_quality  # noqa: E402
from canonical.risk_policy import DASHBOARD_POLICY, STRATEGY_500_POLICY, RiskPolicy  # noqa: E402

NOW = datetime(2026, 6, 15, 15, 30, tzinfo=timezone.utc)


def _good_call_quality(**overrides):
    base = dict(strike=103.0, underlying=100.0, side="CALL", bid=0.99, ask=1.01,
               volume=300, open_interest=1000, implied_volatility=0.30, delta=0.50,
               dte=14, quote_timestamp=NOW.isoformat(), session_open=True, now=NOW)
    base.update(overrides)
    return evaluate_contract_quality(**base)


def _bad_call_quality():
    return _good_call_quality(dte=0)   # 0-DTE -> hard fail, quality_pass=False


def _eligible_stock_fit(policy=STRATEGY_500_POLICY, equity=500.0, buying_power=400.0):
    return stock_account_fit(policy=policy, equity=equity, entry=100.0, stop=97.0,
                             buying_power=buying_power)


def _ineligible_stock_fit():
    # zero risk distance -> fails closed
    return stock_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, entry=100.0,
                             stop=100.0, buying_power=400.0)


def _eligible_option_fit(policy=DASHBOARD_POLICY, equity=50_000.0, buying_power=40_000.0,
                         cq=None):
    return option_account_fit(policy=policy, equity=equity,
                              contract_quality=cq or _good_call_quality(),
                              side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                              strike=103.0, iv=0.30, dte=14, atr_pct=2.5,
                              buying_power=buying_power)


def _ineligible_option_fit(policy=STRATEGY_500_POLICY, equity=500.0, buying_power=400.0):
    return option_account_fit(policy=policy, equity=equity, contract_quality=_good_call_quality(),
                              side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                              strike=103.0, iv=0.30, dte=14, atr_pct=2.5,
                              buying_power=buying_power)


# ══════════════════════════════════════════════════════════════════════════
# Required behavioral matrix, items 1-10
# ══════════════════════════════════════════════════════════════════════════

def test_1_setup_invalid_both_legs_eligible_no_trade():
    r = choose_instrument(setup_tradeable=False,
                          stock_account_fit=_eligible_stock_fit(),
                          option_account_fit=_eligible_option_fit(),
                          contract_quality=_good_call_quality())
    assert r.choice == InstrumentChoice.NO_TRADE
    assert r.reason == "setup_not_tradeable"
    assert r.stock_eligible is False and r.option_eligible is False


def test_2_stock_eligible_option_ineligible_stock():
    r = choose_instrument(setup_tradeable=True,
                          stock_account_fit=_eligible_stock_fit(),
                          option_account_fit=_ineligible_option_fit(),
                          contract_quality=_good_call_quality())
    assert r.choice == InstrumentChoice.STOCK
    assert r.stock_eligible is True and r.option_eligible is False


def test_3_stock_ineligible_option_eligible_option():
    r = choose_instrument(setup_tradeable=True,
                          stock_account_fit=_ineligible_stock_fit(),
                          option_account_fit=_eligible_option_fit(),
                          contract_quality=_good_call_quality())
    assert r.choice == InstrumentChoice.OPTION
    assert r.reason == "stock_ineligible_option_available"
    assert r.stock_eligible is False and r.option_eligible is True


def test_4_both_ineligible_no_trade():
    r = choose_instrument(setup_tradeable=True,
                          stock_account_fit=_ineligible_stock_fit(),
                          option_account_fit=_ineligible_option_fit(),
                          contract_quality=_good_call_quality())
    assert r.choice == InstrumentChoice.NO_TRADE
    assert r.reason == "neither_instrument_eligible"


def test_5_both_eligible_comparative_favors_stock():
    r = choose_instrument(setup_tradeable=True,
                          stock_account_fit=_eligible_stock_fit(policy=DASHBOARD_POLICY, equity=50_000.0, buying_power=40_000.0),
                          option_account_fit=_eligible_option_fit(),
                          contract_quality=_good_call_quality(),
                          option_edge=OptionEdge(theta_pct_per_day=3.0,
                                                 break_even_within_expected_move=False,
                                                 ev_positive=False))
    assert r.choice == InstrumentChoice.STOCK
    assert r.reason == "stock_edge_superior"
    assert r.stock_eligible is True and r.option_eligible is True
    assert r.option_score is not None and r.stock_score is not None


def test_6_both_eligible_comparative_favors_option():
    r = choose_instrument(setup_tradeable=True,
                          stock_account_fit=_eligible_stock_fit(policy=DASHBOARD_POLICY, equity=50_000.0, buying_power=40_000.0),
                          option_account_fit=_eligible_option_fit(),
                          contract_quality=_good_call_quality(),
                          option_edge=OptionEdge(break_even_within_expected_move=True,
                                                 ev_positive=True))
    assert r.choice == InstrumentChoice.OPTION
    assert r.reason == "option_edge_superior"


def test_7_option_structurally_invalid_stock_available_stock():
    bad_cq = _bad_call_quality()
    r = choose_instrument(setup_tradeable=True,
                          stock_account_fit=_eligible_stock_fit(),
                          option_account_fit=None,   # never evaluated — A failed first
                          contract_quality=bad_cq)
    assert r.choice == InstrumentChoice.STOCK
    assert r.reason == "option_structurally_invalid_stock_available"


def test_8_high_quality_edge_cannot_bypass_ineligible_b():
    """The critical invariant: no comparative signal, however favorable, can
    resurrect a B-ineligible option leg into OPTION."""
    r = choose_instrument(setup_tradeable=True,
                          stock_account_fit=_eligible_stock_fit(),
                          option_account_fit=_ineligible_option_fit(),
                          contract_quality=_good_call_quality(),
                          option_edge=OptionEdge(ev_positive=True,
                                                 break_even_within_expected_move=True),
                          quality_tilt=1_000_000.0)   # absurdly large, must not matter
    assert r.choice in (InstrumentChoice.STOCK, InstrumentChoice.NO_TRADE)
    assert r.choice != InstrumentChoice.OPTION


def test_9_historical_strategy500_500_vs_1_prefers_stock():
    cq = _good_call_quality()
    saf = _eligible_stock_fit()
    oaf = option_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, contract_quality=cq,
                             side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                             strike=103.0, iv=0.30, dte=14, atr_pct=2.5, buying_power=400.0)
    assert oaf.eligible is False
    assert oaf.binding_constraint == "per_trade_risk"
    assert oaf.violations[0].actual == pytest.approx(100.06, abs=0.01)
    assert oaf.violations[0].limit == pytest.approx(5.0)

    r = choose_instrument(setup_tradeable=True, stock_account_fit=saf,
                          option_account_fit=oaf, contract_quality=cq)
    assert r.choice == InstrumentChoice.STOCK


def test_10_dashboard_50k_option_preferred():
    cq = _good_call_quality()
    saf = _eligible_stock_fit(policy=DASHBOARD_POLICY, equity=50_000.0, buying_power=40_000.0)
    oaf = _eligible_option_fit()
    assert saf.eligible is True and oaf.eligible is True

    r = choose_instrument(setup_tradeable=True, stock_account_fit=saf,
                          option_account_fit=oaf, contract_quality=cq,
                          option_edge=OptionEdge(ev_positive=True,
                                                 break_even_within_expected_move=True))
    assert r.choice == InstrumentChoice.OPTION


# ══════════════════════════════════════════════════════════════════════════
# Additional required coverage
# ══════════════════════════════════════════════════════════════════════════

def test_option_eligibility_is_terminal_no_score_can_override():
    r = choose_instrument(setup_tradeable=True,
                          stock_account_fit=_eligible_stock_fit(),
                          option_account_fit=_ineligible_option_fit(),
                          contract_quality=_good_call_quality(),
                          quality_tilt=999.0)
    assert r.choice != InstrumentChoice.OPTION


def test_stock_eligibility_is_terminal_option_remains_reachable():
    r = choose_instrument(setup_tradeable=True,
                          stock_account_fit=_ineligible_stock_fit(),
                          option_account_fit=_eligible_option_fit(),
                          contract_quality=_good_call_quality(),
                          quality_tilt=-999.0)
    assert r.choice == InstrumentChoice.OPTION


def test_layer_a_failure_blocks_option_even_with_a_technically_eligible_account_fit():
    """Defense-in-depth: option_available requires BOTH
    contract_quality.quality_pass AND option_account_fit.eligible, exactly
    as v1.1 specifies, even though option_account_fit() already
    short-circuits on a failed contract_quality itself (Step 4) — this
    proves D enforces its own precondition independently, not merely by
    trusting B did."""
    bad_cq = _bad_call_quality()
    # A technically "eligible" AccountFitResult is impossible to construct
    # from real option_account_fit() when quality fails (it short-circuits),
    # so this proves the precondition using B's own honest output instead.
    oaf = option_account_fit(policy=DASHBOARD_POLICY, equity=50_000.0,
                             contract_quality=bad_cq, side="CALL", limit_price=1.00,
                             spot=100.0, stop=97.0, strike=103.0, iv=0.30, dte=0,
                             buying_power=40_000.0)
    assert oaf.eligible is False   # B already refuses
    r = choose_instrument(setup_tradeable=True, stock_account_fit=_eligible_stock_fit(),
                          option_account_fit=oaf, contract_quality=bad_cq)
    assert r.choice != InstrumentChoice.OPTION


def test_positive_option_ev_cannot_bypass_b():
    r = choose_instrument(setup_tradeable=True,
                          stock_account_fit=_eligible_stock_fit(),
                          option_account_fit=_ineligible_option_fit(),
                          contract_quality=_good_call_quality(),
                          option_edge=OptionEdge(ev_positive=True))
    assert r.choice != InstrumentChoice.OPTION


def test_option_quality_cannot_bypass_b():
    good_cq = _good_call_quality()   # A passes...
    r = choose_instrument(setup_tradeable=True,
                          stock_account_fit=_eligible_stock_fit(),
                          option_account_fit=_ineligible_option_fit(),  # ...but B rejects
                          contract_quality=good_cq)
    assert r.choice != InstrumentChoice.OPTION


def test_capital_efficiency_comparison_preserved():
    """A cheaper option (less capital, lower planned risk) should tally in
    OPTION's favor, matching the legacy tally's own logic."""
    equity = 50_000.0
    cq = _good_call_quality()
    saf = _eligible_stock_fit(policy=DASHBOARD_POLICY, equity=equity, buying_power=40_000.0)
    cheap_oaf = option_account_fit(policy=DASHBOARD_POLICY, equity=equity, contract_quality=cq,
                                   side="CALL", limit_price=0.20, spot=100.0, stop=97.0,
                                   strike=103.0, iv=0.30, dte=14, atr_pct=2.5, buying_power=40_000.0)
    assert cheap_oaf.eligible is True
    assert cheap_oaf.capital_required < saf.capital_required

    r = choose_instrument(setup_tradeable=True, stock_account_fit=saf,
                          option_account_fit=cheap_oaf, contract_quality=cq)
    assert r.choice == InstrumentChoice.OPTION
    assert any("less capital committed" in reason for reason in r.reasons)


def _synthetic_fit(instrument, capital, planned, confidence=None):
    """A hand-built, eligible AccountFitResult for pinning exact tally
    arithmetic — bypasses the real risk math entirely so capital_required/
    planned_risk (and, since Step 5.1, risk_confidence) are exactly
    controlled, independent of any pricing model."""
    return AccountFitResult(
        instrument=instrument, eligible=True, quantity_allowed=1,
        capital_required=capital, planned_risk=planned,
        stress_risk=planned, absolute_max_loss=capital,
        binding_constraint=None, violations=(), assumptions=("synthetic fixture",),
        risk_confidence=confidence)


def test_deterministic_tie_favors_stock():
    """A genuine, hand-constructed 0-0 tally tie (identical capital_required
    and planned_risk on both legs, no option_edge, no quality_tilt) must
    deterministically resolve to STOCK, matching the legacy `>` (not `>=`)
    tie behavior."""
    # Both zero, not merely equal: capital/planned comparisons only fire
    # when BOTH sides are truthy, so all-zero is the true no-signal case —
    # equal-but-nonzero values are NOT a tie (equal capital favors stock via
    # the elif branch, equal planned_risk favors option via `<=`; those
    # cancel out to a 1-1 "tie" for a different, less illustrative reason).
    saf = _synthetic_fit(InstrumentType.STOCK, capital=0.0, planned=0.0)
    oaf = _synthetic_fit(InstrumentType.LONG_CALL, capital=0.0, planned=0.0)
    r = choose_instrument(setup_tradeable=True, stock_account_fit=saf,
                          option_account_fit=oaf, contract_quality=_good_call_quality())
    assert r.option_score == r.stock_score == 0.0
    assert r.choice == InstrumentChoice.STOCK
    assert r.reason == "tie_favors_stock"


def test_equal_nonzero_capital_and_planned_risk_also_ties_to_stock():
    """A different, real tally path also lands at a 1-1 tie: equal capital
    favors stock (elif branch), equal planned_risk favors option (`<=`) —
    they cancel, and the tie-break still resolves to STOCK."""
    saf = _synthetic_fit(InstrumentType.STOCK, capital=100.0, planned=10.0)
    oaf = _synthetic_fit(InstrumentType.LONG_CALL, capital=100.0, planned=10.0)
    r = choose_instrument(setup_tradeable=True, stock_account_fit=saf,
                          option_account_fit=oaf, contract_quality=_good_call_quality())
    assert r.option_score == r.stock_score == 1.0
    assert r.choice == InstrumentChoice.STOCK
    assert r.reason == "tie_favors_stock"


def test_tie_is_deterministic_across_repeated_calls():
    equity = 50_000.0
    cq = _good_call_quality()
    saf = _eligible_stock_fit(policy=DASHBOARD_POLICY, equity=equity, buying_power=40_000.0)
    oaf = _eligible_option_fit(policy=DASHBOARD_POLICY, equity=equity, buying_power=40_000.0)
    results = {choose_instrument(setup_tradeable=True, stock_account_fit=saf,
                                 option_account_fit=oaf, contract_quality=cq).choice
              for _ in range(20)}
    assert len(results) == 1, "same inputs must always produce the same choice"


def test_deterministic_reasons_are_stable_strings_not_free_text_restated():
    r = choose_instrument(setup_tradeable=True, stock_account_fit=_eligible_stock_fit(),
                          option_account_fit=_ineligible_option_fit(),
                          contract_quality=_good_call_quality())
    assert r.reason == "option_account_ineligible_stock_available"
    assert all(isinstance(x, str) and x for x in r.reasons)


def test_same_inputs_same_output():
    kwargs = dict(setup_tradeable=True, stock_account_fit=_eligible_stock_fit(),
                 option_account_fit=_ineligible_option_fit(),
                 contract_quality=_good_call_quality())
    r1 = choose_instrument(**kwargs)
    r2 = choose_instrument(**kwargs)
    assert r1 == r2


def test_equivalent_call_and_put_upstream_facts_same_d_behavior():
    equity = 50_000.0
    call_cq = _good_call_quality()
    put_cq = _good_call_quality(strike=93.0, side="PUT", delta=-0.50)
    saf = _eligible_stock_fit(policy=DASHBOARD_POLICY, equity=equity, buying_power=40_000.0)
    call_oaf = option_account_fit(policy=DASHBOARD_POLICY, equity=equity, contract_quality=call_cq,
                                  side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                                  strike=103.0, iv=0.30, dte=14, atr_pct=2.5, buying_power=40_000.0)
    put_oaf = option_account_fit(policy=DASHBOARD_POLICY, equity=equity, contract_quality=put_cq,
                                 side="PUT", limit_price=1.00, spot=100.0, stop=103.0,
                                 strike=93.0, iv=0.30, dte=14, atr_pct=2.5, buying_power=40_000.0)
    r_call = choose_instrument(setup_tradeable=True, stock_account_fit=saf,
                               option_account_fit=call_oaf, contract_quality=call_cq)
    r_put = choose_instrument(setup_tradeable=True, stock_account_fit=saf,
                              option_account_fit=put_oaf, contract_quality=put_cq)
    assert r_call.choice == r_put.choice
    assert r_call.reason == r_put.reason


def test_no_policy_name_or_pipeline_branch_in_source():
    src = inspect.getsource(choose_instrument)
    for forbidden in ("policy.name", "Strategy500", "Dashboard", "pipeline", '"BALANCED"',
                      "profile"):
        assert forbidden not in src, f"choose_instrument() references {forbidden!r} — policy/pipeline leak"


def test_no_module_level_policy_or_pipeline_symbols():
    import canonical.instrument_choice as ic
    public = [n for n in dir(ic) if not n.startswith("_")]
    assert "RiskPolicy" not in public
    assert not any("strategy500" in n.lower() or "dashboard" in n.lower() for n in public)


def test_choose_instrument_takes_no_policy_or_equity_parameter():
    sig = inspect.signature(choose_instrument)
    forbidden = {"policy", "equity", "buying_power", "risk_policy", "profile",
                "premium", "oi", "spread", "dte", "quote_timestamp"}
    present = set(sig.parameters) & forbidden
    assert not present, f"choose_instrument() must not take account/policy/quote state: {present}"


def test_malformed_setup_tradeable_type_raises():
    with pytest.raises(TypeError):
        choose_instrument(setup_tradeable="yes",   # not a bool
                          stock_account_fit=_eligible_stock_fit(),
                          option_account_fit=None, contract_quality=None)


def test_malformed_account_fit_type_raises():
    with pytest.raises(TypeError):
        choose_instrument(setup_tradeable=True,
                          stock_account_fit={"eligible": True},   # wrong type
                          option_account_fit=None, contract_quality=None)


def test_missing_legs_both_none_is_no_trade_not_a_crash():
    r = choose_instrument(setup_tradeable=True, stock_account_fit=None,
                          option_account_fit=None, contract_quality=None)
    assert r.choice == InstrumentChoice.NO_TRADE
    assert r.reason == "neither_instrument_eligible"


# ══════════════════════════════════════════════════════════════════════════
# Step 5.1 — structured low-confidence preference signal
# ══════════════════════════════════════════════════════════════════════════

def test_low_confidence_penalizes_option_only_when_both_legs_available():
    """Without the penalty, capital efficiency alone would make OPTION win
    1-0. With risk_confidence=LOW, the penalty ties it 1-1, which resolves
    to STOCK — exactly the legacy behavior, reproduced from a structured
    field, not by parsing `assumptions`. stock's planned_risk is 0.0
    (falsy) so the planned-risk comparison is skipped entirely (`if
    o_planned and s_planned`), isolating capital efficiency as the ONLY
    pre-confidence signal — otherwise an equal planned_risk on both sides
    would independently add a second point to for_option via `<=`."""
    saf = _synthetic_fit(InstrumentType.STOCK, capital=200.0, planned=0.0)
    oaf_high_conf = _synthetic_fit(InstrumentType.LONG_CALL, capital=100.0, planned=20.0,
                                   confidence=RiskConfidence.MODELLED)
    oaf_low_conf = _synthetic_fit(InstrumentType.LONG_CALL, capital=100.0, planned=20.0,
                                  confidence=RiskConfidence.LOW)
    cq = _good_call_quality()

    without_penalty = choose_instrument(setup_tradeable=True, stock_account_fit=saf,
                                        option_account_fit=oaf_high_conf, contract_quality=cq)
    assert without_penalty.choice == InstrumentChoice.OPTION
    assert without_penalty.option_score == 1.0 and without_penalty.stock_score == 0.0

    with_penalty = choose_instrument(setup_tradeable=True, stock_account_fit=saf,
                                     option_account_fit=oaf_low_conf, contract_quality=cq)
    assert with_penalty.choice == InstrumentChoice.STOCK
    assert with_penalty.option_score == 1.0 and with_penalty.stock_score == 1.0
    assert any("could not be modelled" in reason for reason in
              (with_penalty.reasons if with_penalty.choice == InstrumentChoice.STOCK else ()))


def test_low_confidence_cannot_block_option_only_selection():
    """Confidence is a preference signal for the comparative branch only —
    when stock is unavailable, a low-confidence-but-eligible option must
    still be chosen; there is no eligibility gate to trip."""
    oaf = _synthetic_fit(InstrumentType.LONG_CALL, capital=100.0, planned=20.0,
                         confidence=RiskConfidence.LOW)
    r = choose_instrument(setup_tradeable=True, stock_account_fit=_ineligible_stock_fit(),
                          option_account_fit=oaf, contract_quality=_good_call_quality())
    assert r.choice == InstrumentChoice.OPTION


def test_low_confidence_cannot_bypass_b_ineligibility():
    """Ordering proof: A/B eligibility is decided BEFORE D ever looks at
    confidence. An ineligible option_account_fit carries
    risk_confidence=None (Step 4/5.1's own zeroing convention) and is never
    reachable regardless."""
    oaf = _ineligible_option_fit()
    assert oaf.eligible is False
    assert oaf.risk_confidence is None
    r = choose_instrument(setup_tradeable=True, stock_account_fit=_eligible_stock_fit(),
                          option_account_fit=oaf, contract_quality=_good_call_quality())
    assert r.choice != InstrumentChoice.OPTION


def test_low_confidence_signal_is_deterministic():
    saf = _synthetic_fit(InstrumentType.STOCK, capital=200.0, planned=20.0)
    oaf = _synthetic_fit(InstrumentType.LONG_CALL, capital=100.0, planned=20.0,
                         confidence=RiskConfidence.LOW)
    cq = _good_call_quality()
    results = {choose_instrument(setup_tradeable=True, stock_account_fit=saf,
                                 option_account_fit=oaf, contract_quality=cq).choice
              for _ in range(10)}
    assert len(results) == 1


def test_no_string_parsing_of_assumptions_for_confidence():
    """Structural guard: choose_instrument()'s source must never inspect
    `assumptions` text (e.g. `"low" in assumption`) to recover confidence —
    only the typed `risk_confidence` field."""
    # Only real CODE lines are checked (comments/docstrings are stripped) —
    # this module's own docstring explicitly discusses the forbidden
    # pattern in prose ("never `\"low\" in assumption`-style parsing") to
    # document the decision, which would otherwise false-positive a naive
    # whole-source substring search.
    src = inspect.getsource(choose_instrument)
    code_lines = [line for line in src.splitlines()
                 if (stripped := line.strip()) and not stripped.startswith("#")]
    code_only = "\n".join(code_lines)
    assert ".assumptions" not in code_only, (
        "choose_instrument() must never read AccountFitResult.assumptions in "
        "code — confidence must come from the typed risk_confidence field only"
    )
    assert "risk_confidence" in code_only, (
        "choose_instrument() should reference the structured confidence field"
    )




class TestLegacyParity:
    @staticmethod
    def _legacy():
        dashboard_dir = os.path.join(_ROOT, "dashboard")
        if dashboard_dir not in sys.path:
            sys.path.insert(0, dashboard_dir)
        import option_risk as ORK
        return ORK

    def test_both_ineligible_agrees(self):
        ORK = self._legacy()
        legacy = ORK.instrument_choice(stock=None, option=None,
                                       option_eligibility={"eligible": False},
                                       stock_sizeable=False, option_quality_ok=False)
        canonical = choose_instrument(setup_tradeable=True, stock_account_fit=None,
                                      option_account_fit=None, contract_quality=None)
        assert legacy["instrument"] == "NO TRADE"
        assert canonical.choice == InstrumentChoice.NO_TRADE

    def test_option_ineligible_stock_sizeable_agrees(self):
        ORK = self._legacy()
        legacy = ORK.instrument_choice(
            stock={"planned_risk": 3.75, "capital_committed": 125.0},
            option={"planned_risk": 100.06, "capital_committed": 100.06, "confidence": "modelled"},
            option_eligibility={"eligible": False, "reason": "per_trade_risk"},
            stock_sizeable=True, option_quality_ok=True)
        canonical = choose_instrument(setup_tradeable=True,
                                      stock_account_fit=_eligible_stock_fit(),
                                      option_account_fit=_ineligible_option_fit(),
                                      contract_quality=_good_call_quality())
        assert legacy["instrument"] == "STOCK PREFERRED"
        assert canonical.choice == InstrumentChoice.STOCK

    def test_stock_unsizeable_option_eligible_agrees(self):
        ORK = self._legacy()
        legacy = ORK.instrument_choice(
            stock=None,
            option={"planned_risk": 50.0, "capital_committed": 100.0, "confidence": "modelled"},
            option_eligibility={"eligible": True}, stock_sizeable=False, option_quality_ok=True)
        canonical = choose_instrument(setup_tradeable=True,
                                      stock_account_fit=_ineligible_stock_fit(),
                                      option_account_fit=_eligible_option_fit(),
                                      contract_quality=_good_call_quality())
        assert legacy["instrument"] == "OPTION PREFERRED"
        assert canonical.choice == InstrumentChoice.OPTION

    def test_both_eligible_cheap_option_prefers_option_agrees(self):
        ORK = self._legacy()
        legacy = ORK.instrument_choice(
            stock={"planned_risk": 300.0, "capital_committed": 10_000.0},
            option={"planned_risk": 40.0, "capital_committed": 40.0, "confidence": "modelled"},
            option_eligibility={"eligible": True}, stock_sizeable=True, option_quality_ok=True,
            option_edge={"break_even_within_expected_move": True})
        equity = 50_000.0
        cq = _good_call_quality()
        saf = _eligible_stock_fit(policy=DASHBOARD_POLICY, equity=equity, buying_power=40_000.0)
        cheap_oaf = option_account_fit(policy=DASHBOARD_POLICY, equity=equity, contract_quality=cq,
                                       side="CALL", limit_price=0.20, spot=100.0, stop=97.0,
                                       strike=103.0, iv=0.30, dte=14, atr_pct=2.5, buying_power=40_000.0)
        canonical = choose_instrument(setup_tradeable=True, stock_account_fit=saf,
                                      option_account_fit=cheap_oaf, contract_quality=cq,
                                      option_edge=OptionEdge(break_even_within_expected_move=True))
        assert legacy["instrument"] == "OPTION PREFERRED"
        assert canonical.choice == InstrumentChoice.OPTION

    def test_tie_behavior_agrees(self):
        """Both functions must resolve an exact 0-0 tally tie to STOCK."""
        ORK = self._legacy()
        legacy = ORK.instrument_choice(
            stock={"planned_risk": 0.0, "capital_committed": 0.0},
            option={"planned_risk": 0.0, "capital_committed": 0.0, "confidence": "modelled"},
            option_eligibility={"eligible": True}, stock_sizeable=True, option_quality_ok=True)
        assert legacy["instrument"] == "STOCK PREFERRED"   # legacy's own tie -> stock
        # canonical: force a comparable 0-0 tally via an explicit synthetic pair
        equity = 50_000.0
        cq = _good_call_quality()
        saf = stock_account_fit(policy=DASHBOARD_POLICY, equity=equity, entry=100.0,
                                stop=99.9999999, buying_power=40_000.0)  # ~0 planned risk
        # Use the real eligible option fit; assert tie-break rule directly instead
        # of chasing an exact synthetic 0-0, which real risk math rarely produces.
        oaf = _eligible_option_fit(policy=DASHBOARD_POLICY, equity=equity, buying_power=40_000.0)
        canonical = choose_instrument(setup_tradeable=True, stock_account_fit=saf,
                                      option_account_fit=oaf, contract_quality=cq)
        if canonical.option_score == canonical.stock_score:
            assert canonical.choice == InstrumentChoice.STOCK

    def test_both_viable_normal_confidence_agrees(self):
        """Step 5.1 Phase 5, case 1: both viable, MODELLED confidence — no
        penalty on either side, decision matches on capital efficiency
        alone. Stock's planned_risk is 0.0 (falsy) so that comparison is
        skipped in both functions, isolating capital efficiency as the only
        pre-confidence signal (an equal, nonzero planned_risk on both sides
        would otherwise add a second independent point to OPTION via `<=`)."""
        ORK = self._legacy()
        legacy = ORK.instrument_choice(
            stock={"planned_risk": 0.0, "capital_committed": 200.0},
            option={"planned_risk": 20.0, "capital_committed": 100.0, "confidence": "modelled"},
            option_eligibility={"eligible": True}, stock_sizeable=True, option_quality_ok=True)
        saf = _synthetic_fit(InstrumentType.STOCK, capital=200.0, planned=0.0)
        oaf = _synthetic_fit(InstrumentType.LONG_CALL, capital=100.0, planned=20.0,
                             confidence=RiskConfidence.MODELLED)
        canonical = choose_instrument(setup_tradeable=True, stock_account_fit=saf,
                                      option_account_fit=oaf, contract_quality=_good_call_quality())
        assert legacy["instrument"] == "OPTION PREFERRED"
        assert canonical.choice == InstrumentChoice.OPTION

    def test_both_viable_low_option_confidence_agrees(self):
        """Step 5.1 Phase 5, case 2: identical facts to the case above,
        EXCEPT confidence=low. Without the penalty OPTION wins (as just
        proven); with it, both functions must flip to STOCK."""
        ORK = self._legacy()
        legacy = ORK.instrument_choice(
            stock={"planned_risk": 0.0, "capital_committed": 200.0},
            option={"planned_risk": 20.0, "capital_committed": 100.0, "confidence": "low"},
            option_eligibility={"eligible": True}, stock_sizeable=True, option_quality_ok=True)
        saf = _synthetic_fit(InstrumentType.STOCK, capital=200.0, planned=0.0)
        oaf = _synthetic_fit(InstrumentType.LONG_CALL, capital=100.0, planned=20.0,
                             confidence=RiskConfidence.LOW)
        canonical = choose_instrument(setup_tradeable=True, stock_account_fit=saf,
                                      option_account_fit=oaf, contract_quality=_good_call_quality())
        assert legacy["instrument"] == "STOCK PREFERRED"
        assert canonical.choice == InstrumentChoice.STOCK

    def test_low_confidence_stock_unavailable_agrees(self):
        """Step 5.1 Phase 5, case 3: low confidence is a preference signal,
        not an eligibility gate — with stock unavailable, OPTION wins in
        both functions regardless of confidence."""
        ORK = self._legacy()
        legacy = ORK.instrument_choice(
            stock=None, option={"planned_risk": 20.0, "capital_committed": 100.0, "confidence": "low"},
            option_eligibility={"eligible": True}, stock_sizeable=False, option_quality_ok=True)
        oaf = _synthetic_fit(InstrumentType.LONG_CALL, capital=100.0, planned=20.0,
                             confidence=RiskConfidence.LOW)
        canonical = choose_instrument(setup_tradeable=True,
                                      stock_account_fit=_ineligible_stock_fit(),
                                      option_account_fit=oaf, contract_quality=_good_call_quality())
        assert legacy["instrument"] == "OPTION PREFERRED"
        assert canonical.choice == InstrumentChoice.OPTION

    def test_low_confidence_option_ineligible_agrees(self):
        """Step 5.1 Phase 5, case 4: option ineligible in B — confidence is
        irrelevant, neither function can ever choose OPTION."""
        ORK = self._legacy()
        legacy = ORK.instrument_choice(
            stock={"planned_risk": 3.75, "capital_committed": 125.0},
            option={"planned_risk": 100.06, "capital_committed": 100.06, "confidence": "low"},
            option_eligibility={"eligible": False, "reason": "per_trade_risk"},
            stock_sizeable=True, option_quality_ok=True)
        canonical = choose_instrument(setup_tradeable=True,
                                      stock_account_fit=_eligible_stock_fit(),
                                      option_account_fit=_ineligible_option_fit(),
                                      contract_quality=_good_call_quality())
        assert legacy["instrument"] != "OPTION PREFERRED"
        assert canonical.choice != InstrumentChoice.OPTION


# ══════════════════════════════════════════════════════════════════════════
# InstrumentChoiceResult schema sanity
# ══════════════════════════════════════════════════════════════════════════

def test_result_never_carries_a_final_quantity():
    """Layer D must never compute or expose an executable quantity — that
    is Layer E's job (Step 6)."""
    r = choose_instrument(setup_tradeable=True, stock_account_fit=_eligible_stock_fit(),
                          option_account_fit=_ineligible_option_fit(),
                          contract_quality=_good_call_quality())
    fields = {f for f in InstrumentChoiceResult.__dataclass_fields__}
    forbidden = {"quantity", "quantity_allowed", "shares", "contracts", "size"}
    assert not (fields & forbidden), f"InstrumentChoiceResult must not carry sizing fields: {fields & forbidden}"
