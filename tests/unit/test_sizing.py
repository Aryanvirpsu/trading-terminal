"""Canonical Option Architecture v1.1 — Layer E dedicated tests.

Tests canonical/sizing.py in isolation. Fully offline and deterministic.

Run: pytest tests/unit/test_sizing.py -v
"""
from __future__ import annotations

import inspect
import os
import sys
from datetime import datetime, timezone

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from canonical.sizing import (   # noqa: E402
    ExecutionConstraints, SizingError, SizingResult, size_instrument,
)
from canonical.instrument_choice import (   # noqa: E402
    InstrumentChoice, InstrumentChoiceResult, choose_instrument,
)
from canonical.account_fit import (   # noqa: E402
    AccountFitResult, InstrumentType, option_account_fit, stock_account_fit,
)
from canonical.contract_quality import evaluate_contract_quality  # noqa: E402
from canonical.risk_policy import DASHBOARD_POLICY, STRATEGY_500_POLICY  # noqa: E402

NOW = datetime(2026, 6, 15, 15, 30, tzinfo=timezone.utc)


def _good_call_quality(**overrides):
    base = dict(strike=103.0, underlying=100.0, side="CALL", bid=0.99, ask=1.01,
               volume=300, open_interest=1000, implied_volatility=0.30, delta=0.50,
               dte=14, quote_timestamp=NOW.isoformat(), session_open=True, now=NOW)
    base.update(overrides)
    return evaluate_contract_quality(**base)


def _strategy500_stock_fit(equity=500.0, buying_power=400.0):
    return stock_account_fit(policy=STRATEGY_500_POLICY, equity=equity, entry=100.0,
                             stop=97.0, buying_power=buying_power)


def _ineligible_stock_fit():
    return stock_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, entry=100.0,
                             stop=100.0, buying_power=400.0)   # zero risk distance


def _strategy500_option_fit(equity=500.0, buying_power=400.0, cq=None):
    return option_account_fit(policy=STRATEGY_500_POLICY, equity=equity,
                              contract_quality=cq or _good_call_quality(),
                              side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                              strike=103.0, iv=0.30, dte=14, atr_pct=2.5,
                              buying_power=buying_power)


def _dashboard_option_fit(equity=50_000.0, buying_power=40_000.0, contracts=1, cq=None):
    return option_account_fit(policy=DASHBOARD_POLICY, equity=equity,
                              contract_quality=cq or _good_call_quality(),
                              side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                              strike=103.0, iv=0.30, dte=14, atr_pct=2.5,
                              buying_power=buying_power, contracts_candidate=contracts)


def _dashboard_stock_fit(equity=50_000.0, buying_power=40_000.0):
    return stock_account_fit(policy=DASHBOARD_POLICY, equity=equity, entry=100.0,
                             stop=97.0, buying_power=buying_power)


def _choice(choice, stock_eligible=True, option_eligible=True):
    return InstrumentChoiceResult(choice=choice, reason="fixture", reasons=("fixture",),
                                  stock_eligible=stock_eligible, option_eligible=option_eligible)


# ══════════════════════════════════════════════════════════════════════════
# Core
# ══════════════════════════════════════════════════════════════════════════

def test_no_trade_sizes_zero_no_math():
    r = size_instrument(choice=_choice(InstrumentChoice.NO_TRADE, False, False),
                        stock_account_fit=_strategy500_stock_fit(),
                        option_account_fit=_strategy500_option_fit())
    assert r.quantity == 0.0
    assert r.sizeable is False
    assert r.reasons == ("no_trade_selected",)


def test_stock_eligible_sizes_positive_quantity():
    saf = _strategy500_stock_fit()
    r = size_instrument(choice=_choice(InstrumentChoice.STOCK), stock_account_fit=saf,
                        option_account_fit=None)
    assert r.sizeable is True
    assert r.quantity == saf.quantity_allowed > 0


def test_option_eligible_sizes_integer_contracts():
    oaf = _dashboard_option_fit()
    r = size_instrument(choice=_choice(InstrumentChoice.OPTION), stock_account_fit=None,
                        option_account_fit=oaf)
    assert r.sizeable is True
    assert r.quantity == 1
    assert isinstance(r.quantity, int)


def test_selected_instrument_b_ineligible_sizes_zero():
    r = size_instrument(choice=_choice(InstrumentChoice.STOCK),
                        stock_account_fit=_ineligible_stock_fit(), option_account_fit=None)
    assert r.quantity == 0.0
    assert r.sizeable is False
    assert r.reasons == ("stock_account_fit_missing_or_ineligible",)


def test_selected_instrument_b_missing_sizes_zero():
    r = size_instrument(choice=_choice(InstrumentChoice.OPTION), stock_account_fit=None,
                        option_account_fit=None)
    assert r.quantity == 0
    assert r.sizeable is False


# ══════════════════════════════════════════════════════════════════════════
# Ceiling
# ══════════════════════════════════════════════════════════════════════════

def test_target_below_b_ceiling_wins():
    saf = _strategy500_stock_fit()   # ceiling 1.25
    r = size_instrument(choice=_choice(InstrumentChoice.STOCK), stock_account_fit=saf,
                        option_account_fit=None, target_quantity=0.5)
    assert r.quantity == 0.5
    assert r.binding_constraint == "target_quantity"


def test_target_equal_b_ceiling():
    saf = _strategy500_stock_fit()
    r = size_instrument(choice=_choice(InstrumentChoice.STOCK), stock_account_fit=saf,
                        option_account_fit=None, target_quantity=saf.quantity_allowed)
    assert r.quantity == saf.quantity_allowed
    # equal, not strictly below -> the B binder wins, not "target_quantity"
    assert r.binding_constraint == f"account_fit:{saf.binding_constraint}"


def test_target_above_b_ceiling_clamped():
    saf = _strategy500_stock_fit()
    r = size_instrument(choice=_choice(InstrumentChoice.STOCK), stock_account_fit=saf,
                        option_account_fit=None, target_quantity=10.0)
    assert r.quantity == saf.quantity_allowed
    assert r.quantity < 10.0
    assert r.binding_constraint == f"account_fit:{saf.binding_constraint}"


def test_no_target_uses_full_b_ceiling():
    saf = _strategy500_stock_fit()
    r = size_instrument(choice=_choice(InstrumentChoice.STOCK), stock_account_fit=saf,
                        option_account_fit=None)
    assert r.quantity == saf.quantity_allowed
    assert r.requested_quantity is None


def test_e_can_never_exceed_b_quantity_property():
    """Sweep a range of targets, including absurdly large ones, and confirm
    the invariant holds unconditionally."""
    saf = _strategy500_stock_fit()
    for target in (0.01, 1.0, 1.25, 1.2500001, 2.0, 100.0, 1_000_000.0):
        r = size_instrument(choice=_choice(InstrumentChoice.STOCK), stock_account_fit=saf,
                            option_account_fit=None, target_quantity=target)
        assert r.quantity <= saf.quantity_allowed + 1e-9


def test_negative_target_rejected():
    saf = _strategy500_stock_fit()
    r = size_instrument(choice=_choice(InstrumentChoice.STOCK), stock_account_fit=saf,
                        option_account_fit=None, target_quantity=-1.0)
    assert r.sizeable is False
    assert r.quantity == 0.0
    assert r.reasons == ("negative_target_quantity",)


def test_zero_target_sizes_zero():
    saf = _strategy500_stock_fit()
    r = size_instrument(choice=_choice(InstrumentChoice.STOCK), stock_account_fit=saf,
                        option_account_fit=None, target_quantity=0)
    assert r.quantity == 0.0
    assert r.sizeable is False
    assert r.reasons == ("zero_target_quantity",)


def test_option_fractional_target_raises():
    # ceiling must exceed 1.7 so the clamp doesn't itself floor the target to
    # a whole number before the fractional check ever runs (min(1.7, 1) = 1,
    # which would never trigger it) — use a 3-contract ceiling.
    oaf = _dashboard_option_fit(contracts=3)
    with pytest.raises(SizingError):
        size_instrument(choice=_choice(InstrumentChoice.OPTION), stock_account_fit=None,
                        option_account_fit=oaf, target_quantity=1.7)


# ══════════════════════════════════════════════════════════════════════════
# Stock
# ══════════════════════════════════════════════════════════════════════════

def test_stock_fractional_b_allowance_preserved():
    saf = _strategy500_stock_fit()   # 1.25, genuinely fractional
    assert saf.quantity_allowed != int(saf.quantity_allowed)
    r = size_instrument(choice=_choice(InstrumentChoice.STOCK), stock_account_fit=saf,
                        option_account_fit=None)
    assert r.quantity == saf.quantity_allowed


def test_stock_integer_only_upstream_allowance_preserved():
    """When B already floored to whole shares (fractional_shares=False
    upstream), E must not invent fractional capacity."""
    from canonical.risk_policy import RiskPolicy
    whole_policy = RiskPolicy(
        name="whole", source="test",
        max_loss_per_trade=5.0, risk_per_trade_pct=0.01,
        max_position_notional=125.0, max_position_notional_pct=None,
        max_portfolio_exposure_pct=None, max_sector_exposure_pct=None,
        max_open_positions=None, max_positions_per_sector=None,
        max_daily_loss=None, max_daily_loss_pct=None,
        max_drawdown=None, max_drawdown_pct=None, min_cash_reserve=None,
        option_planned_risk_pct=None, max_long_option_premium_pct=None,
        max_total_option_premium_pct=None, max_portfolio_planned_risk_pct=None,
        max_portfolio_option_stress_risk_pct=None,
        max_portfolio_theoretical_option_loss_pct=None, max_open_option_positions=None,
        fractional_shares=False,
    )
    saf = stock_account_fit(policy=whole_policy, equity=500.0, entry=100.0, stop=97.0,
                            buying_power=400.0)
    assert saf.quantity_allowed == float(int(saf.quantity_allowed))
    r = size_instrument(choice=_choice(InstrumentChoice.STOCK), stock_account_fit=saf,
                        option_account_fit=None, target_quantity=1.9)
    assert r.quantity == saf.quantity_allowed   # clamped, not 1.9 and not re-floored differently


def test_small_account_stock_fixture():
    saf = _strategy500_stock_fit()
    r = size_instrument(choice=_choice(InstrumentChoice.STOCK), stock_account_fit=saf,
                        option_account_fit=None)
    assert r.quantity == pytest.approx(1.25, abs=1e-6)


def test_stock_binding_provenance_retained():
    saf = _strategy500_stock_fit()
    r = size_instrument(choice=_choice(InstrumentChoice.STOCK), stock_account_fit=saf,
                        option_account_fit=None)
    assert r.binding_constraint == f"account_fit:{saf.binding_constraint}"
    assert saf.binding_constraint in r.binding_constraint


# ══════════════════════════════════════════════════════════════════════════
# Options
# ══════════════════════════════════════════════════════════════════════════

def test_one_option_contract_allowed():
    oaf = _dashboard_option_fit(contracts=1)
    r = size_instrument(choice=_choice(InstrumentChoice.OPTION), stock_account_fit=None,
                        option_account_fit=oaf)
    assert r.quantity == 1


def test_multiple_option_contracts_allowed():
    oaf = _dashboard_option_fit(contracts=3)
    r = size_instrument(choice=_choice(InstrumentChoice.OPTION), stock_account_fit=None,
                        option_account_fit=oaf)
    assert r.quantity == 3
    assert isinstance(r.quantity, int)


def test_zero_option_contracts():
    r = size_instrument(choice=_choice(InstrumentChoice.OPTION), stock_account_fit=None,
                        option_account_fit=_strategy500_option_fit())   # ineligible -> 0
    assert r.quantity == 0
    assert r.sizeable is False


def test_fractional_option_contracts_impossible():
    oaf = _dashboard_option_fit(contracts=2)
    r = size_instrument(choice=_choice(InstrumentChoice.OPTION), stock_account_fit=None,
                        option_account_fit=oaf, target_quantity=2)
    assert r.quantity == 2 and isinstance(r.quantity, int)
    with pytest.raises(SizingError):
        size_instrument(choice=_choice(InstrumentChoice.OPTION), stock_account_fit=None,
                        option_account_fit=oaf, target_quantity=1.5)


def test_strategy500_500_vs_1_never_sizes_option():
    cq = _good_call_quality()
    saf = _strategy500_stock_fit()
    oaf = _strategy500_option_fit(cq=cq)
    assert oaf.eligible is False and oaf.quantity_allowed == 0
    d = choose_instrument(setup_tradeable=True, stock_account_fit=saf,
                          option_account_fit=oaf, contract_quality=cq)
    assert d.choice == InstrumentChoice.STOCK

    e = size_instrument(choice=d, stock_account_fit=saf, option_account_fit=oaf)
    assert e.instrument == InstrumentChoice.STOCK
    assert e.quantity == saf.quantity_allowed
    assert e.quantity != 1   # never "1 option contract"

    # even a target of "1" (conceivably meant by a confused caller as "1
    # option") cannot resurrect OPTION — D's choice already routed sizing
    # to the stock leg entirely, so it is correctly interpreted as 1 SHARE
    # (clamped normally against the stock ceiling), never as a contract.
    e_forced = size_instrument(choice=d, stock_account_fit=saf, option_account_fit=oaf,
                               target_quantity=1)
    assert e_forced.instrument == InstrumentChoice.STOCK
    assert e_forced.quantity == min(1, saf.quantity_allowed) == 1


def test_dashboard_50k_sizes_one_option_when_d_chose_option():
    from canonical.instrument_choice import OptionEdge
    cq = _good_call_quality()
    saf = _dashboard_stock_fit()
    oaf = _dashboard_option_fit()
    assert saf.eligible and oaf.eligible
    d = choose_instrument(setup_tradeable=True, stock_account_fit=saf,
                          option_account_fit=oaf, contract_quality=cq,
                          option_edge=OptionEdge(ev_positive=True,
                                                 break_even_within_expected_move=True))
    assert d.choice == InstrumentChoice.OPTION
    e = size_instrument(choice=d, stock_account_fit=saf, option_account_fit=oaf)
    assert e.instrument == InstrumentChoice.OPTION
    assert e.quantity == 1
    assert e.sizeable is True


# ══════════════════════════════════════════════════════════════════════════
# Conviction / target-size clamping
# ══════════════════════════════════════════════════════════════════════════

def test_conviction_target_above_b_ceiling_is_clamped():
    """The historical audit's own numbers: decision_engine's 'strong
    conviction' inline formula suggested ~3.82 shares (~$12.04 planned
    risk) on a $500 account whose real B ceiling is 1.25 shares. E must
    clamp to 1.25, never trust 3.82."""
    saf = _strategy500_stock_fit()
    r = size_instrument(choice=_choice(InstrumentChoice.STOCK), stock_account_fit=saf,
                        option_account_fit=None, target_quantity=3.82)
    assert r.quantity == saf.quantity_allowed
    assert r.quantity == pytest.approx(1.25, abs=1e-6)
    assert r.quantity < 3.82


# ══════════════════════════════════════════════════════════════════════════
# Preview / execute
# ══════════════════════════════════════════════════════════════════════════

def test_same_inputs_byte_identical_result():
    saf = _strategy500_stock_fit()
    kwargs = dict(choice=_choice(InstrumentChoice.STOCK), stock_account_fit=saf,
                 option_account_fit=None, target_quantity=1.0)
    assert size_instrument(**kwargs) == size_instrument(**kwargs)


def test_different_account_state_enters_through_new_b_result_not_a_mode_branch():
    """'Preview' and 'execute' differ only in which AccountFitResult was
    supplied upstream (e.g. a different equity snapshot) — never a `mode`
    parameter inside E."""
    saf_preview = _strategy500_stock_fit(equity=500.0)
    saf_execute = _strategy500_stock_fit(equity=480.0)   # a lower, "live" equity
    r_preview = size_instrument(choice=_choice(InstrumentChoice.STOCK),
                                stock_account_fit=saf_preview, option_account_fit=None)
    r_execute = size_instrument(choice=_choice(InstrumentChoice.STOCK),
                                stock_account_fit=saf_execute, option_account_fit=None)
    assert r_preview.quantity == saf_preview.quantity_allowed
    assert r_execute.quantity == saf_execute.quantity_allowed
    # same function, same formula — only the upstream AccountFitResult differs
    assert r_preview.quantity != r_execute.quantity or saf_preview.quantity_allowed == saf_execute.quantity_allowed


def test_no_mode_parameter_or_preview_execute_branch_in_source():
    sig = inspect.signature(size_instrument)
    assert "mode" not in sig.parameters
    assert "preview" not in sig.parameters and "execute" not in sig.parameters
    src = inspect.getsource(size_instrument)
    code_lines = [line for line in src.splitlines()
                 if (stripped := line.strip()) and not stripped.startswith("#")]
    code_only = "\n".join(code_lines)
    assert '"preview"' not in code_only and '"execute"' not in code_only
    assert "mode ==" not in code_only and "mode is" not in code_only


# ══════════════════════════════════════════════════════════════════════════
# Safety
# ══════════════════════════════════════════════════════════════════════════

def test_direct_invalid_state_option_selected_but_b_option_ineligible():
    r = size_instrument(choice=_choice(InstrumentChoice.OPTION), stock_account_fit=None,
                        option_account_fit=_strategy500_option_fit())
    assert r.quantity == 0
    assert r.sizeable is False


def test_direct_invalid_state_stock_selected_but_b_stock_ineligible():
    r = size_instrument(choice=_choice(InstrumentChoice.STOCK),
                        stock_account_fit=_ineligible_stock_fit(), option_account_fit=None)
    assert r.quantity == 0.0
    assert r.sizeable is False


def test_direct_invalid_state_option_zero_quantity_allowed():
    r = size_instrument(choice=_choice(InstrumentChoice.OPTION), stock_account_fit=None,
                        option_account_fit=_strategy500_option_fit())
    assert r.quantity == 0


def test_direct_invalid_state_no_trade_but_both_b_eligible():
    r = size_instrument(choice=_choice(InstrumentChoice.NO_TRADE, True, True),
                        stock_account_fit=_strategy500_stock_fit(),
                        option_account_fit=_dashboard_option_fit())
    assert r.quantity == 0.0
    assert r.sizeable is False


def test_malformed_b_quantity_option_non_integer_raises():
    bad = AccountFitResult(
        instrument=InstrumentType.LONG_CALL, eligible=True, quantity_allowed=1.7,
        capital_required=100.0, planned_risk=50.0, stress_risk=50.0, absolute_max_loss=100.0,
        binding_constraint=None, violations=(), assumptions=("corrupted fixture",))
    with pytest.raises(SizingError):
        size_instrument(choice=_choice(InstrumentChoice.OPTION), stock_account_fit=None,
                        option_account_fit=bad)


def test_mismatched_instrument_type_raises():
    stock_shaped_but_wrong_type = AccountFitResult(
        instrument=InstrumentType.LONG_CALL, eligible=True, quantity_allowed=1.0,
        capital_required=100.0, planned_risk=10.0, stress_risk=None, absolute_max_loss=None,
        binding_constraint=None, violations=(), assumptions=())
    with pytest.raises(SizingError):
        size_instrument(choice=_choice(InstrumentChoice.STOCK),
                        stock_account_fit=stock_shaped_but_wrong_type, option_account_fit=None)


def test_d_cannot_be_bypassed_stock_fit_ignored_when_d_is_no_trade():
    r = size_instrument(choice=_choice(InstrumentChoice.NO_TRADE, True, True),
                        stock_account_fit=_strategy500_stock_fit(), option_account_fit=None)
    assert r.quantity == 0.0


def test_target_quantity_cannot_bypass_b_even_when_huge():
    saf = _strategy500_stock_fit()
    r = size_instrument(choice=_choice(InstrumentChoice.STOCK), stock_account_fit=saf,
                        option_account_fit=None, target_quantity=1e9)
    assert r.quantity == saf.quantity_allowed


def test_no_risk_policy_parameter():
    sig = inspect.signature(size_instrument)
    assert "policy" not in sig.parameters and "risk_policy" not in sig.parameters


def test_no_equity_or_buying_power_parameter():
    sig = inspect.signature(size_instrument)
    forbidden = {"equity", "buying_power", "entry", "stop", "premium", "oi", "spread", "dte"}
    assert not (set(sig.parameters) & forbidden)


def test_no_profile_or_pipeline_name_branching_in_source():
    src = inspect.getsource(size_instrument)
    for forbidden in ("policy.name", "Strategy500", "Dashboard", "pipeline"):
        assert forbidden not in src


# ══════════════════════════════════════════════════════════════════════════
# ExecutionConstraints
# ══════════════════════════════════════════════════════════════════════════

def test_execution_constraint_min_quantity_zeroes_out_below_minimum():
    saf = _strategy500_stock_fit()   # 1.25
    r = size_instrument(choice=_choice(InstrumentChoice.STOCK), stock_account_fit=saf,
                        option_account_fit=None, target_quantity=0.1,
                        execution_constraints=ExecutionConstraints(min_quantity=1.0))
    assert r.quantity == 0.0
    assert r.sizeable is False
    assert r.binding_constraint == "execution_constraint:min_quantity"


def test_execution_constraint_increment_rounds_down_never_up():
    saf = _strategy500_stock_fit()   # 1.25
    r = size_instrument(choice=_choice(InstrumentChoice.STOCK), stock_account_fit=saf,
                        option_account_fit=None,
                        execution_constraints=ExecutionConstraints(quantity_increment=0.5))
    assert r.quantity == 1.0   # floor(1.25 / 0.5) * 0.5 = 1.0, never 1.5
    assert r.quantity <= saf.quantity_allowed
    assert r.rounding_applied is True


def test_execution_constraint_whole_units_only_for_stock():
    saf = _strategy500_stock_fit()   # 1.25
    r = size_instrument(choice=_choice(InstrumentChoice.STOCK), stock_account_fit=saf,
                        option_account_fit=None,
                        execution_constraints=ExecutionConstraints(whole_units_only=True))
    assert r.quantity == 1.0
    assert r.rounding_applied is True


def test_malformed_execution_constraints_type_raises():
    with pytest.raises(SizingError):
        size_instrument(choice=_choice(InstrumentChoice.STOCK),
                        stock_account_fit=_strategy500_stock_fit(), option_account_fit=None,
                        execution_constraints={"min_quantity": 1.0})   # wrong type
