"""Canonical Option Architecture v1.1 — Step 7 dedicated tests.

Tests canonical/executable.py in isolation. Fully offline and deterministic.

Run: pytest tests/unit/test_executable.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from canonical.executable import (   # noqa: E402
    ExecutableResult, evaluate_option_executable, evaluate_stock_executable,
)
from canonical.sizing import SizingResult, size_instrument              # noqa: E402
from canonical.instrument_choice import (                               # noqa: E402
    InstrumentChoice, InstrumentChoiceResult, choose_instrument,
)
from canonical.account_fit import option_account_fit, stock_account_fit  # noqa: E402
from canonical.contract_quality import evaluate_contract_quality         # noqa: E402
from canonical.risk_policy import DASHBOARD_POLICY, STRATEGY_500_POLICY  # noqa: E402

NOW = datetime(2026, 6, 15, 15, 30, tzinfo=timezone.utc)


# ══════════════════════════════════════════════════════════════════════════
# Fixture builders — real canonical objects, same pattern as test_sizing.py
# ══════════════════════════════════════════════════════════════════════════

def _good_call_quality(**overrides):
    base = dict(strike=103.0, underlying=100.0, side="CALL", bid=0.99, ask=1.01,
               volume=300, open_interest=1000, implied_volatility=0.30, delta=0.50,
               dte=14, quote_timestamp=NOW.isoformat(), session_open=True, now=NOW)
    base.update(overrides)
    return evaluate_contract_quality(**base)


def _bad_quality():
    """A quote nobody is making — a genuine Layer-A hard failure."""
    return evaluate_contract_quality(strike=103.0, underlying=100.0, side="CALL",
                                     bid=None, ask=None, volume=300, open_interest=1000,
                                     implied_volatility=0.30, delta=0.50, dte=14,
                                     quote_timestamp=NOW.isoformat(), session_open=True, now=NOW)


def _strategy500_stock_fit(equity=500.0, buying_power=400.0):
    return stock_account_fit(policy=STRATEGY_500_POLICY, equity=equity, entry=100.0,
                             stop=97.0, buying_power=buying_power)


def _strategy500_option_fit(equity=500.0, buying_power=400.0, cq=None):
    return option_account_fit(policy=STRATEGY_500_POLICY, equity=equity,
                              contract_quality=cq or _good_call_quality(),
                              side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                              strike=103.0, iv=0.30, dte=14, atr_pct=2.5,
                              buying_power=buying_power)


def _dashboard_stock_fit(equity=50_000.0, buying_power=40_000.0):
    return stock_account_fit(policy=DASHBOARD_POLICY, equity=equity, entry=100.0,
                             stop=97.0, buying_power=buying_power)


def _dashboard_option_fit(equity=50_000.0, buying_power=40_000.0, contracts=1, cq=None):
    return option_account_fit(policy=DASHBOARD_POLICY, equity=equity,
                              contract_quality=cq or _good_call_quality(),
                              side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                              strike=103.0, iv=0.30, dte=14, atr_pct=2.5,
                              buying_power=buying_power, contracts_candidate=contracts)


def _choice(choice, stock_eligible=True, option_eligible=True):
    return InstrumentChoiceResult(choice=choice, reason="fixture", reasons=("fixture",),
                                  stock_eligible=stock_eligible, option_eligible=option_eligible)


def _fake_sizing(instrument, quantity=1, sizeable=True, binding_constraint="fixture"):
    """A hand-built SizingResult, bypassing size_instrument()'s own
    consistency checks entirely — deliberately used to feed
    evaluate_*_executable() an otherwise-valid-looking but single-axis-wrong
    E object, isolating exactly one blocker per test."""
    return SizingResult(instrument=instrument, quantity=quantity,
                        max_quantity_allowed=quantity if quantity else 1,
                        requested_quantity=None, binding_constraint=binding_constraint,
                        sizeable=sizeable, reasons=(binding_constraint,))


def _full_stock_pipeline(equity=50_000.0, buying_power=40_000.0):
    fit = _dashboard_stock_fit(equity, buying_power)
    choice = _choice(InstrumentChoice.STOCK)
    sizing = size_instrument(choice=choice, stock_account_fit=fit, option_account_fit=None)
    return fit, choice, sizing


def _full_option_pipeline(equity=50_000.0, buying_power=40_000.0):
    cq = _good_call_quality()
    fit = _dashboard_option_fit(equity, buying_power, cq=cq)
    choice = _choice(InstrumentChoice.OPTION)
    sizing = size_instrument(choice=choice, stock_account_fit=None, option_account_fit=fit)
    return cq, fit, choice, sizing


# ══════════════════════════════════════════════════════════════════════════
# Stock
# ══════════════════════════════════════════════════════════════════════════

def test_fully_valid_stock_is_executable():
    fit, choice, sizing = _full_stock_pipeline()
    r = evaluate_stock_executable(setup_tradeable=True, account_fit=fit, choice=choice, sizing=sizing)
    assert r == ExecutableResult(executable=True, instrument=InstrumentChoice.STOCK, blockers=())


def test_stock_setup_not_tradeable():
    fit, choice, sizing = _full_stock_pipeline()
    r = evaluate_stock_executable(setup_tradeable=False, account_fit=fit, choice=choice, sizing=sizing)
    assert r.executable is False
    assert r.blockers == ("setup_not_tradeable",)


def test_stock_account_fit_ineligible():
    fit = stock_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, entry=100.0,
                            stop=100.0, buying_power=400.0)   # zero risk distance -> ineligible
    assert fit.eligible is False   # fixture sanity
    choice = _choice(InstrumentChoice.STOCK)
    sizing = _fake_sizing(InstrumentChoice.STOCK)   # otherwise-valid E, isolating B
    r = evaluate_stock_executable(setup_tradeable=True, account_fit=fit, choice=choice, sizing=sizing)
    assert r.executable is False
    assert r.blockers == ("account_ineligible",)


def test_stock_D_not_stock():
    fit, _choice_unused, sizing = _full_stock_pipeline()
    choice = _choice(InstrumentChoice.OPTION)   # D picked OPTION, not STOCK
    r = evaluate_stock_executable(setup_tradeable=True, account_fit=fit, choice=choice, sizing=sizing)
    assert r.executable is False
    assert "instrument_not_selected" in r.blockers


def test_stock_E_wrong_instrument():
    fit, choice, _sizing_unused = _full_stock_pipeline()
    sizing = _fake_sizing(InstrumentChoice.OPTION)   # E says OPTION for a STOCK D-result
    r = evaluate_stock_executable(setup_tradeable=True, account_fit=fit, choice=choice, sizing=sizing)
    assert r.executable is False
    assert r.blockers == ("structurally_invalid",)


def test_stock_E_zero_quantity():
    fit, choice, _sizing_unused = _full_stock_pipeline()
    sizing = _fake_sizing(InstrumentChoice.STOCK, quantity=0, sizeable=True)
    r = evaluate_stock_executable(setup_tradeable=True, account_fit=fit, choice=choice, sizing=sizing)
    assert r.executable is False
    assert r.blockers == ("zero_quantity",)


def test_stock_E_not_sizeable():
    fit, choice, _sizing_unused = _full_stock_pipeline()
    sizing = _fake_sizing(InstrumentChoice.STOCK, quantity=1, sizeable=False)
    r = evaluate_stock_executable(setup_tradeable=True, account_fit=fit, choice=choice, sizing=sizing)
    assert r.executable is False
    assert r.blockers == ("not_sizeable",)


def test_stock_execution_policy_blocked():
    fit, choice, sizing = _full_stock_pipeline()
    r = evaluate_stock_executable(setup_tradeable=True, account_fit=fit, choice=choice, sizing=sizing,
                                  execution_policy_allows=False)
    assert r.executable is False
    assert r.blockers == ("execution_policy_blocked",)


# ══════════════════════════════════════════════════════════════════════════
# Option
# ══════════════════════════════════════════════════════════════════════════

def test_fully_valid_option_is_executable():
    cq, fit, choice, sizing = _full_option_pipeline()
    r = evaluate_option_executable(setup_tradeable=True, contract_quality=cq, account_fit=fit,
                                   choice=choice, sizing=sizing)
    assert r == ExecutableResult(executable=True, instrument=InstrumentChoice.OPTION, blockers=())


def test_option_setup_not_tradeable():
    cq, fit, choice, sizing = _full_option_pipeline()
    r = evaluate_option_executable(setup_tradeable=False, contract_quality=cq, account_fit=fit,
                                   choice=choice, sizing=sizing)
    assert r.executable is False
    assert r.blockers == ("setup_not_tradeable",)


def test_option_quality_fail_blocks():
    bad_cq = _bad_quality()
    assert bad_cq.quality_pass is False   # fixture sanity
    fit = _dashboard_option_fit()   # eligible, computed against a GOOD contract elsewhere —
                                     # the predicate must still fail closed on A alone
    choice = _choice(InstrumentChoice.OPTION)
    sizing = _fake_sizing(InstrumentChoice.OPTION)
    r = evaluate_option_executable(setup_tradeable=True, contract_quality=bad_cq, account_fit=fit,
                                   choice=choice, sizing=sizing)
    assert r.executable is False
    assert r.blockers == ("structurally_invalid",)


def test_option_account_fit_ineligible():
    cq = _good_call_quality()
    fit = _strategy500_option_fit(cq=cq)   # rejected on Strategy500's $5 cap
    assert fit.eligible is False   # fixture sanity
    choice = _choice(InstrumentChoice.OPTION)
    sizing = _fake_sizing(InstrumentChoice.OPTION)   # otherwise-valid E, isolating B
    r = evaluate_option_executable(setup_tradeable=True, contract_quality=cq, account_fit=fit,
                                   choice=choice, sizing=sizing)
    assert r.executable is False
    assert r.blockers == ("account_ineligible",)


def test_option_D_not_option():
    cq, fit, _choice_unused, sizing = _full_option_pipeline()
    choice = _choice(InstrumentChoice.STOCK)   # D picked STOCK, not OPTION
    r = evaluate_option_executable(setup_tradeable=True, contract_quality=cq, account_fit=fit,
                                   choice=choice, sizing=sizing)
    assert r.executable is False
    assert "instrument_not_selected" in r.blockers


def test_option_E_wrong_instrument():
    cq, fit, choice, _sizing_unused = _full_option_pipeline()
    sizing = _fake_sizing(InstrumentChoice.STOCK)   # E says STOCK for an OPTION D-result
    r = evaluate_option_executable(setup_tradeable=True, contract_quality=cq, account_fit=fit,
                                   choice=choice, sizing=sizing)
    assert r.executable is False
    assert r.blockers == ("structurally_invalid",)


def test_option_E_zero_quantity():
    cq, fit, choice, _sizing_unused = _full_option_pipeline()
    sizing = _fake_sizing(InstrumentChoice.OPTION, quantity=0, sizeable=True)
    r = evaluate_option_executable(setup_tradeable=True, contract_quality=cq, account_fit=fit,
                                   choice=choice, sizing=sizing)
    assert r.executable is False
    assert r.blockers == ("zero_quantity",)


def test_option_E_not_sizeable():
    cq, fit, choice, _sizing_unused = _full_option_pipeline()
    sizing = _fake_sizing(InstrumentChoice.OPTION, quantity=1, sizeable=False)
    r = evaluate_option_executable(setup_tradeable=True, contract_quality=cq, account_fit=fit,
                                   choice=choice, sizing=sizing)
    assert r.executable is False
    assert r.blockers == ("not_sizeable",)


def test_option_execution_policy_blocked():
    cq, fit, choice, sizing = _full_option_pipeline()
    r = evaluate_option_executable(setup_tradeable=True, contract_quality=cq, account_fit=fit,
                                   choice=choice, sizing=sizing, execution_policy_allows=False)
    assert r.executable is False
    assert r.blockers == ("execution_policy_blocked",)


def test_option_shadow_only_blocks():
    cq, fit, choice, sizing = _full_option_pipeline()
    r = evaluate_option_executable(setup_tradeable=True, contract_quality=cq, account_fit=fit,
                                   choice=choice, sizing=sizing, shadow_only=True)
    assert r.executable is False
    assert r.blockers == ("shadow_only",)


def test_option_shadow_only_blocks_regardless_of_otherwise_clean_ABDE():
    """shadow_only=True fails closed even with a fully valid, otherwise-
    executable A+B+D+E chain — the shadow gate is unconditional."""
    cq, fit, choice, sizing = _full_option_pipeline()
    clean = evaluate_option_executable(setup_tradeable=True, contract_quality=cq, account_fit=fit,
                                       choice=choice, sizing=sizing, shadow_only=False)
    assert clean.executable is True   # sanity: the chain really was clean
    shadowed = evaluate_option_executable(setup_tradeable=True, contract_quality=cq, account_fit=fit,
                                          choice=choice, sizing=sizing, shadow_only=True)
    assert shadowed.executable is False
    assert shadowed.blockers == ("shadow_only",)


# ══════════════════════════════════════════════════════════════════════════
# Independence
# ══════════════════════════════════════════════════════════════════════════

def test_bad_option_does_not_block_stock_executable():
    """A structurally invalid, ineligible option must have zero influence on
    stock_executable — evaluate_stock_executable() has no option-side
    parameter at all, so this holds by construction; exercised end-to-end
    for good measure."""
    fit, choice, sizing = _full_stock_pipeline()
    r = evaluate_stock_executable(setup_tradeable=True, account_fit=fit, choice=choice, sizing=sizing)
    assert r.executable is True   # unaffected by any option-side state, none of which was even passed


def test_stock_executable_does_not_imply_option_executable():
    """Strategy500 $500/$1 shape, restated as the core independence
    invariant: a valid, executable stock leg says nothing about the option
    leg."""
    cq = _good_call_quality()
    stock_fit = _strategy500_stock_fit()
    option_fit = _strategy500_option_fit(cq=cq)
    assert stock_fit.eligible is True and option_fit.eligible is False   # fixture sanity
    choice = choose_instrument(setup_tradeable=True, stock_account_fit=stock_fit,
                               option_account_fit=option_fit, contract_quality=cq)
    assert choice.choice == InstrumentChoice.STOCK   # fixture sanity
    sizing = size_instrument(choice=choice, stock_account_fit=stock_fit, option_account_fit=option_fit)

    stock_r = evaluate_stock_executable(setup_tradeable=True, account_fit=stock_fit,
                                        choice=choice, sizing=sizing)
    option_r = evaluate_option_executable(setup_tradeable=True, contract_quality=cq,
                                          account_fit=option_fit, choice=choice, sizing=sizing)
    assert stock_r.executable is True
    assert option_r.executable is False


def test_option_executable_does_not_imply_stock_executable():
    """Dashboard $50k shape, restated: a valid, executable option leg says
    nothing about the stock leg — D chose OPTION, so stock is blocked even
    though B(stock) was independently eligible."""
    cq, option_fit, choice, sizing = _full_option_pipeline()
    stock_fit = _dashboard_stock_fit()
    assert stock_fit.eligible is True   # fixture sanity: stock was ALSO eligible on its own

    stock_r = evaluate_stock_executable(setup_tradeable=True, account_fit=stock_fit,
                                        choice=choice, sizing=sizing)
    option_r = evaluate_option_executable(setup_tradeable=True, contract_quality=cq,
                                          account_fit=option_fit, choice=choice, sizing=sizing)
    assert option_r.executable is True
    assert stock_r.executable is False


def test_both_can_be_false():
    fit, choice, sizing = _full_stock_pipeline()
    cq = _bad_quality()
    stock_r = evaluate_stock_executable(setup_tradeable=False, account_fit=fit, choice=choice, sizing=sizing)
    option_r = evaluate_option_executable(setup_tradeable=False, contract_quality=cq, account_fit=None,
                                          choice=choice, sizing=sizing)
    assert stock_r.executable is False
    assert option_r.executable is False


def test_both_never_simultaneously_true_for_one_D_result():
    """For any single D choice, feeding the SAME choice/sizing pair to both
    predicates must never let both be executable=True — D.choice is
    exclusive by construction (instrument_not_selected always blocks the
    non-chosen leg)."""
    # D chose STOCK
    stock_fit, stock_choice, stock_sizing = _full_stock_pipeline()
    cq = _good_call_quality()
    option_fit_for_stock_case = _strategy500_option_fit(cq=cq)
    r1_stock = evaluate_stock_executable(setup_tradeable=True, account_fit=stock_fit,
                                         choice=stock_choice, sizing=stock_sizing)
    r1_option = evaluate_option_executable(setup_tradeable=True, contract_quality=cq,
                                           account_fit=option_fit_for_stock_case,
                                           choice=stock_choice, sizing=stock_sizing)
    assert not (r1_stock.executable and r1_option.executable)

    # D chose OPTION
    cq2, option_fit2, option_choice2, option_sizing2 = _full_option_pipeline()
    stock_fit2 = _dashboard_stock_fit()
    r2_stock = evaluate_stock_executable(setup_tradeable=True, account_fit=stock_fit2,
                                         choice=option_choice2, sizing=option_sizing2)
    r2_option = evaluate_option_executable(setup_tradeable=True, contract_quality=cq2,
                                           account_fit=option_fit2,
                                           choice=option_choice2, sizing=option_sizing2)
    assert not (r2_stock.executable and r2_option.executable)


# ══════════════════════════════════════════════════════════════════════════
# Defense-in-depth / malformed state
# ══════════════════════════════════════════════════════════════════════════
# Several malformed-state scenarios from the task's own list are already
# covered above as dedicated per-predicate tests — each of those single-axis
# tests IS a malformed/inconsistent-state test:
#   D=OPTION but B(option)=False       -> test_option_account_fit_ineligible
#   D=OPTION but E quantity=0          -> test_option_E_zero_quantity
#   D=OPTION but E says STOCK          -> test_option_E_wrong_instrument
#   D=STOCK but stock B=False          -> test_stock_account_fit_ineligible
#   D=STOCK but E says OPTION          -> test_stock_E_wrong_instrument
#   A=False but D=OPTION               -> test_option_quality_fail_blocks
#   setup=False but everything else OK -> test_both_can_be_false /
#                                          test_stock_setup_not_tradeable /
#                                          test_option_setup_not_tradeable
# The tests below cover the remaining combinations: wrong-leg-type upstream
# objects, missing (None) upstream objects, and the raise-vs-fail-closed
# type boundary itself.

def test_option_account_fit_wrong_leg_type_fails_closed():
    """A STOCK AccountFitResult passed where an option one belongs is an
    inconsistent-upstream-objects scenario, not a caller type error — must
    fail closed as structurally_invalid, never raise, never silently treated
    as eligible."""
    cq = _good_call_quality()
    wrong_leg_fit = _dashboard_stock_fit()   # a stock AccountFitResult
    choice = _choice(InstrumentChoice.OPTION)
    sizing = _fake_sizing(InstrumentChoice.OPTION)
    r = evaluate_option_executable(setup_tradeable=True, contract_quality=cq,
                                   account_fit=wrong_leg_fit, choice=choice, sizing=sizing)
    assert r.executable is False
    assert r.blockers == ("structurally_invalid",)


def test_stock_account_fit_wrong_leg_type_fails_closed():
    fit = _dashboard_option_fit()   # an option AccountFitResult, not stock
    choice = _choice(InstrumentChoice.STOCK)
    sizing = _fake_sizing(InstrumentChoice.STOCK)
    r = evaluate_stock_executable(setup_tradeable=True, account_fit=fit, choice=choice, sizing=sizing)
    assert r.executable is False
    assert r.blockers == ("structurally_invalid",)


def test_missing_account_fit_fails_closed_not_raise():
    choice = _choice(InstrumentChoice.STOCK)
    sizing = _fake_sizing(InstrumentChoice.STOCK)
    r = evaluate_stock_executable(setup_tradeable=True, account_fit=None, choice=choice, sizing=sizing)
    assert r.executable is False
    assert r.blockers == ("structurally_invalid",)


def test_missing_sizing_fails_closed_not_raise():
    fit, choice, _sizing_unused = _full_stock_pipeline()
    r = evaluate_stock_executable(setup_tradeable=True, account_fit=fit, choice=choice, sizing=None)
    assert r.executable is False
    assert r.blockers == ("structurally_invalid",)


def test_missing_choice_fails_closed_not_raise():
    fit, _choice_unused, sizing = _full_stock_pipeline()
    r = evaluate_stock_executable(setup_tradeable=True, account_fit=fit, choice=None, sizing=sizing)
    assert r.executable is False
    assert r.blockers == ("instrument_not_selected",)


def test_missing_contract_quality_fails_closed_not_raise():
    _cq_unused, fit, choice, sizing = _full_option_pipeline()
    r = evaluate_option_executable(setup_tradeable=True, contract_quality=None, account_fit=fit,
                                   choice=choice, sizing=sizing)
    assert r.executable is False
    assert r.blockers == ("structurally_invalid",)


def test_wrong_type_account_fit_raises_type_error():
    choice = _choice(InstrumentChoice.STOCK)
    sizing = _fake_sizing(InstrumentChoice.STOCK)
    with pytest.raises(TypeError):
        evaluate_stock_executable(setup_tradeable=True, account_fit={"eligible": True},
                                  choice=choice, sizing=sizing)


def test_wrong_type_setup_tradeable_raises_type_error():
    fit, choice, sizing = _full_stock_pipeline()
    with pytest.raises(TypeError):
        evaluate_stock_executable(setup_tradeable="yes", account_fit=fit, choice=choice, sizing=sizing)


def test_wrong_type_contract_quality_raises_type_error():
    _cq_unused, fit, choice, sizing = _full_option_pipeline()
    with pytest.raises(TypeError):
        evaluate_option_executable(setup_tradeable=True, contract_quality={"quality_pass": True},
                                   account_fit=fit, choice=choice, sizing=sizing)


# ══════════════════════════════════════════════════════════════════════════
# Historical fixtures
# ══════════════════════════════════════════════════════════════════════════

def test_strategy500_500_vs_1_full_chain():
    """The Strategy-500 $500-equity / $1.00-premium headline regression,
    carried through the full A -> B -> D -> E -> executable chain, with
    shadow_only=True as Strategy-500 options always are. This is the
    strongest single architecture invariant: an ineligible option must never
    block an independently valid stock execution, and shadow mode must never
    let it slip through anyway."""
    cq = _good_call_quality()
    stock_fit = _strategy500_stock_fit()
    option_fit = _strategy500_option_fit(cq=cq)
    assert stock_fit.eligible is True
    assert option_fit.eligible is False

    choice = choose_instrument(setup_tradeable=True, stock_account_fit=stock_fit,
                               option_account_fit=option_fit, contract_quality=cq)
    assert choice.choice == InstrumentChoice.STOCK

    sizing = size_instrument(choice=choice, stock_account_fit=stock_fit, option_account_fit=option_fit)
    assert sizing.instrument == InstrumentChoice.STOCK
    assert sizing.sizeable is True
    assert sizing.quantity == pytest.approx(1.25, abs=1e-6)

    stock_r = evaluate_stock_executable(setup_tradeable=True, account_fit=stock_fit,
                                        choice=choice, sizing=sizing)
    option_r = evaluate_option_executable(setup_tradeable=True, contract_quality=cq,
                                          account_fit=option_fit, choice=choice, sizing=sizing,
                                          shadow_only=True)

    assert stock_r.executable is True
    assert stock_r.blockers == ()
    assert option_r.executable is False
    assert "shadow_only" in option_r.blockers


def test_dashboard_50k_full_chain():
    """Dashboard $50k fixture: A valid, B(stock)/B(option) both eligible,
    D picks OPTION, E sizes 1 contract, execution_policy_allows=True,
    shadow_only=False -> stock_executable=False, option_executable=True."""
    cq = _good_call_quality()
    stock_fit = _dashboard_stock_fit()
    option_fit = _dashboard_option_fit(cq=cq)
    assert stock_fit.eligible is True
    assert option_fit.eligible is True

    choice = choose_instrument(setup_tradeable=True, stock_account_fit=stock_fit,
                               option_account_fit=option_fit, contract_quality=cq)
    assert choice.choice == InstrumentChoice.OPTION

    sizing = size_instrument(choice=choice, stock_account_fit=stock_fit, option_account_fit=option_fit)
    assert sizing.instrument == InstrumentChoice.OPTION
    assert sizing.quantity == 1
    assert sizing.sizeable is True

    stock_r = evaluate_stock_executable(setup_tradeable=True, account_fit=stock_fit,
                                        choice=choice, sizing=sizing)
    option_r = evaluate_option_executable(setup_tradeable=True, contract_quality=cq,
                                          account_fit=option_fit, choice=choice, sizing=sizing,
                                          execution_policy_allows=True, shadow_only=False)

    assert stock_r.executable is False
    assert option_r.executable is True
    assert option_r.blockers == ()


def test_dashboard_50k_shadow_only_flip():
    """Same A/B/D/E as test_dashboard_50k_full_chain — only shadow_only
    flips from False to True — must flip option_executable to False without
    touching anything upstream."""
    cq = _good_call_quality()
    stock_fit = _dashboard_stock_fit()
    option_fit = _dashboard_option_fit(cq=cq)
    choice = choose_instrument(setup_tradeable=True, stock_account_fit=stock_fit,
                               option_account_fit=option_fit, contract_quality=cq)
    sizing = size_instrument(choice=choice, stock_account_fit=stock_fit, option_account_fit=option_fit)

    option_r = evaluate_option_executable(setup_tradeable=True, contract_quality=cq,
                                          account_fit=option_fit, choice=choice, sizing=sizing,
                                          shadow_only=True)
    assert option_r.executable is False
    assert option_r.blockers == ("shadow_only",)
