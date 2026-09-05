"""Canonical Option Architecture v1.1 — AccountFit (Layer B) dedicated tests.

Tests canonical/account_fit.py in isolation. Fully offline and deterministic
(fixed `now` for every ContractQualityResult built here).

Run: pytest tests/unit/test_account_fit.py -v
"""
from __future__ import annotations

import inspect
import os
import sys
from datetime import datetime, timezone

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from canonical.account_fit import (   # noqa: E402
    AccountFitResult, ConstraintViolation, InstrumentType, RiskConfidence,
    option_account_fit, stock_account_fit,
)
from canonical.contract_quality import evaluate_contract_quality  # noqa: E402
from canonical.risk_policy import (   # noqa: E402
    DASHBOARD_POLICY, STRATEGY_500_POLICY, RiskPolicy, effective_limit,
)

NOW = datetime(2026, 6, 15, 15, 30, tzinfo=timezone.utc)


def _good_call_quality(**overrides):
    base = dict(strike=103.0, underlying=100.0, side="CALL", bid=0.99, ask=1.01,
               volume=300, open_interest=1000, implied_volatility=0.30, delta=0.50,
               dte=14, quote_timestamp=NOW.isoformat(), session_open=True, now=NOW)
    base.update(overrides)
    return evaluate_contract_quality(**base)


def _good_put_quality(**overrides):
    base = dict(strike=93.0, underlying=100.0, side="PUT", bid=0.99, ask=1.01,
               volume=300, open_interest=1000, implied_volatility=0.30, delta=-0.50,
               dte=14, quote_timestamp=NOW.isoformat(), session_open=True, now=NOW)
    base.update(overrides)
    return evaluate_contract_quality(**base)


# ══════════════════════════════════════════════════════════════════════════
# Stock
# ══════════════════════════════════════════════════════════════════════════

def test_normal_eligible_stock():
    r = stock_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, entry=100.0,
                          stop=97.0, buying_power=400.0)
    assert r.eligible is True
    assert r.instrument == InstrumentType.STOCK
    assert r.quantity_allowed > 0
    assert r.capital_required > 0
    assert r.planned_risk == pytest.approx(r.quantity_allowed * 3.0, abs=0.01)


def test_stock_per_trade_risk_bound():
    """Tight stop -> low risk budget in shares is the binder when notional/
    cash are generous. Use a far stop so risk_per_share is huge, forcing
    q_risk to be the smallest candidate."""
    r = stock_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, entry=100.0,
                          stop=50.0,  # $50 risk/share -> q_risk = 5/50 = 0.1 shares
                          buying_power=400.0)
    assert r.binding_constraint == "per_trade_risk"
    assert r.eligible is True
    assert r.quantity_allowed == pytest.approx(5.0 / 50.0, abs=1e-6)


def test_stock_position_notional_bound():
    r = stock_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, entry=100.0,
                          stop=97.0, buying_power=400.0)
    # $5/$3=1.667 vs $125/$100=1.25 vs $400/$100=4 -> notional binds
    assert r.binding_constraint == "max_position_notional"
    assert r.quantity_allowed == pytest.approx(1.25, abs=1e-6)


def test_stock_buying_power_bound():
    # STRATEGY_500_POLICY.min_cash_reserve=100 is netted out first: net = 150-100=50
    r = stock_account_fit(policy=STRATEGY_500_POLICY, equity=5000.0, entry=100.0,
                          stop=99.0,  # min($5, 5000*0.01)=$5 -> q_risk=5 shares (generous)
                          buying_power=150.0)  # net-of-reserve $50 forces this to bind
    assert r.binding_constraint == "buying_power"
    assert r.quantity_allowed == pytest.approx(0.5, abs=1e-6)


def test_stock_cash_reserve_bound():
    """min_cash_reserve is netted OUT of buying_power before sizing (mirrors
    account_state()["buying_power"] = available_cash - min_cash_reserve), so
    a large reserve shrinks the affordable quantity rather than surfacing as
    an independent violation after the fact — exactly as in the legacy
    check_entry(), where the buying-power check mathematically implies the
    cash-reserve check once buying_power is itself net of the reserve."""
    pol = RiskPolicy(
        name="test", source="test",
        max_loss_per_trade=1000.0, risk_per_trade_pct=None,
        max_position_notional=1000.0, max_position_notional_pct=None,
        max_portfolio_exposure_pct=None, max_sector_exposure_pct=None,
        max_open_positions=None, max_positions_per_sector=None,
        max_daily_loss=None, max_daily_loss_pct=None,
        max_drawdown=None, max_drawdown_pct=None,
        min_cash_reserve=350.0,   # only 50 of the 400 raw buying power may be spent
        option_planned_risk_pct=None, max_long_option_premium_pct=None,
        max_total_option_premium_pct=None, max_portfolio_planned_risk_pct=None,
        max_portfolio_option_stress_risk_pct=None,
        max_portfolio_theoretical_option_loss_pct=None, max_open_option_positions=None,
        fractional_shares=True,
    )
    r = stock_account_fit(policy=pol, equity=500.0, entry=10.0, stop=9.0, buying_power=400.0)
    assert r.eligible is True
    assert r.binding_constraint == "buying_power"
    assert r.quantity_allowed == pytest.approx((400.0 - 350.0) / 10.0, abs=1e-6)  # 5 shares, not 40
    assert not any(v.check == "min_cash_reserve" for v in r.violations), (
        "once sizing respects the reserve, the standalone check must never "
        "independently fire — matches the legacy check_entry()'s redundancy"
    )


def test_stock_cash_reserve_exhausted_gives_zero_quantity_not_a_crash():
    pol = RiskPolicy(
        name="test2", source="test",
        max_loss_per_trade=1000.0, risk_per_trade_pct=None,
        max_position_notional=1000.0, max_position_notional_pct=None,
        max_portfolio_exposure_pct=None, max_sector_exposure_pct=None,
        max_open_positions=None, max_positions_per_sector=None,
        max_daily_loss=None, max_daily_loss_pct=None,
        max_drawdown=None, max_drawdown_pct=None,
        min_cash_reserve=400.0,   # reserve consumes the ENTIRE buying power
        option_planned_risk_pct=None, max_long_option_premium_pct=None,
        max_total_option_premium_pct=None, max_portfolio_planned_risk_pct=None,
        max_portfolio_option_stress_risk_pct=None,
        max_portfolio_theoretical_option_loss_pct=None, max_open_option_positions=None,
        fractional_shares=True,
    )
    r = stock_account_fit(policy=pol, equity=500.0, entry=10.0, stop=9.0, buying_power=400.0)
    assert r.eligible is False
    assert r.quantity_allowed == 0.0


def test_stock_max_open_positions_failure():
    r = stock_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, entry=100.0,
                          stop=97.0, buying_power=400.0, open_positions=3)
    assert r.eligible is False
    assert any(v.check == "max_open_positions" for v in r.violations)


def test_stock_sector_limit_failure():
    r = stock_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, entry=100.0,
                          stop=97.0, buying_power=400.0, sector="semis",
                          sector_open_positions=1)  # cap is 1 per sector
    assert r.eligible is False
    assert any(v.check == "max_positions_per_sector" for v in r.violations)


def test_stock_daily_loss_failure():
    r = stock_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, entry=100.0,
                          stop=97.0, buying_power=400.0, current_daily_loss=15.0)  # cap $10
    assert r.eligible is False
    assert any(v.check == "daily_loss_limit" for v in r.violations)


def test_stock_drawdown_failure():
    r = stock_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, entry=100.0,
                          stop=97.0, buying_power=400.0, current_drawdown=60.0)  # cap $50
    assert r.eligible is False
    assert any(v.check == "max_drawdown" for v in r.violations)


def test_stock_fractional_share_policy_true_gives_fractional_quantity():
    r = stock_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, entry=100.0,
                          stop=97.0, buying_power=400.0)
    assert STRATEGY_500_POLICY.fractional_shares is True
    assert r.quantity_allowed != int(r.quantity_allowed)  # genuinely fractional (1.25)


def test_stock_fractional_share_policy_false_floors_to_whole_shares():
    pol = RiskPolicy(
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
    r = stock_account_fit(policy=pol, equity=500.0, entry=100.0, stop=97.0, buying_power=400.0)
    assert r.quantity_allowed == float(int(r.quantity_allowed))  # whole number
    assert r.quantity_allowed == 1.0   # floor(1.25) = 1


def test_stock_invalid_stop_zero_risk_distance_fails_closed():
    r = stock_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, entry=100.0,
                          stop=100.0, buying_power=400.0)
    assert r.eligible is False
    assert r.quantity_allowed == 0.0
    assert r.binding_constraint == "invalid_levels"


def test_stock_zero_entry_fails_closed_not_infinite():
    r = stock_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, entry=0.0,
                          stop=-1.0, buying_power=400.0)
    assert r.eligible is False
    assert r.quantity_allowed == 0.0


def test_negative_equity_raises():
    with pytest.raises(ValueError):
        stock_account_fit(policy=STRATEGY_500_POLICY, equity=-1.0, entry=100.0, stop=97.0)


# ══════════════════════════════════════════════════════════════════════════
# Long option
# ══════════════════════════════════════════════════════════════════════════

def test_structurally_invalid_contract_short_circuits():
    bad = _good_call_quality(dte=0)   # 0-DTE -> hard fail
    assert bad.quality_pass is False
    r = option_account_fit(policy=DASHBOARD_POLICY, equity=50_000.0, contract_quality=bad,
                           side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                           strike=103.0, iv=0.30, dte=0)
    assert r.eligible is False
    assert r.binding_constraint == "structurally_invalid"
    assert r.violations[0].check == "structurally_invalid"
    assert r.capital_required == 0.0 and r.planned_risk == 0.0
    assert r.stress_risk is None and r.absolute_max_loss is None


def test_invalid_option_does_not_affect_sibling_stock_result():
    bad = _good_call_quality(dte=0)
    option_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, contract_quality=bad,
                       side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                       strike=103.0, iv=0.30, dte=0)  # discard — just exercising it
    r_stock = stock_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, entry=100.0,
                                stop=97.0, buying_power=400.0)
    assert r_stock.eligible is True
    assert r_stock.quantity_allowed == pytest.approx(1.25, abs=1e-6)


def test_structurally_valid_affordable_call():
    good = _good_call_quality()
    r = option_account_fit(policy=DASHBOARD_POLICY, equity=50_000.0, contract_quality=good,
                           side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                           strike=103.0, iv=0.30, dte=14, atr_pct=2.5, buying_power=40_000.0)
    assert r.eligible is True
    assert r.instrument == InstrumentType.LONG_CALL
    assert r.quantity_allowed == 1
    assert r.capital_required > 0


def test_structurally_valid_affordable_put():
    good = _good_put_quality()
    r = option_account_fit(policy=DASHBOARD_POLICY, equity=50_000.0, contract_quality=good,
                           side="PUT", limit_price=1.00, spot=100.0, stop=103.0,
                           strike=93.0, iv=0.30, dte=14, atr_pct=2.5, buying_power=40_000.0)
    assert r.eligible is True
    assert r.instrument == InstrumentType.LONG_PUT
    assert r.quantity_allowed == 1


def test_one_contract_too_expensive_the_historical_500_vs_1_case():
    """The exact historical regression scenario named in the task."""
    good = _good_call_quality()
    r = option_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, contract_quality=good,
                           side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                           strike=103.0, iv=0.30, dte=14, atr_pct=2.5, buying_power=400.0)
    assert r.eligible is False
    assert r.quantity_allowed == 0
    assert r.binding_constraint == "per_trade_risk"
    assert any(v.check == "per_trade_risk" and v.limit == pytest.approx(5.0) for v in r.violations)


def test_planned_risk_cap_violation():
    good = _good_call_quality()
    pol = RiskPolicy(
        name="tight_planned_risk", source="test",
        max_loss_per_trade=None, risk_per_trade_pct=None,
        max_position_notional=None, max_position_notional_pct=None,
        max_portfolio_exposure_pct=None, max_sector_exposure_pct=None,
        max_open_positions=None, max_positions_per_sector=None,
        max_daily_loss=None, max_daily_loss_pct=None,
        max_drawdown=None, max_drawdown_pct=None, min_cash_reserve=None,
        option_planned_risk_pct=0.001,   # extremely tight -> must violate
        max_long_option_premium_pct=None, max_total_option_premium_pct=None,
        max_portfolio_planned_risk_pct=None, max_portfolio_option_stress_risk_pct=None,
        max_portfolio_theoretical_option_loss_pct=None, max_open_option_positions=None,
        fractional_shares=True,
    )
    r = option_account_fit(policy=pol, equity=50_000.0, contract_quality=good,
                           side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                           strike=103.0, iv=0.30, dte=14, atr_pct=2.5)
    assert r.eligible is False
    assert any(v.check == "option_planned_risk_pct" for v in r.violations)


def test_premium_cap_violation():
    good = _good_call_quality()
    pol = RiskPolicy(
        name="tight_premium", source="test",
        max_loss_per_trade=None, risk_per_trade_pct=None,
        max_position_notional=None, max_position_notional_pct=None,
        max_portfolio_exposure_pct=None, max_sector_exposure_pct=None,
        max_open_positions=None, max_positions_per_sector=None,
        max_daily_loss=None, max_daily_loss_pct=None,
        max_drawdown=None, max_drawdown_pct=None, min_cash_reserve=None,
        option_planned_risk_pct=None,
        max_long_option_premium_pct=0.0001,  # extremely tight -> must violate
        max_total_option_premium_pct=None, max_portfolio_planned_risk_pct=None,
        max_portfolio_option_stress_risk_pct=None,
        max_portfolio_theoretical_option_loss_pct=None, max_open_option_positions=None,
        fractional_shares=True,
    )
    r = option_account_fit(policy=pol, equity=50_000.0, contract_quality=good,
                           side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                           strike=103.0, iv=0.30, dte=14, atr_pct=2.5)
    assert r.eligible is False
    assert any(v.check == "long_option_premium_pct" for v in r.violations)


def test_stress_risk_violation():
    good = _good_call_quality()
    pol = RiskPolicy(
        name="tight_stress", source="test",
        max_loss_per_trade=None, risk_per_trade_pct=None,
        max_position_notional=None, max_position_notional_pct=None,
        max_portfolio_exposure_pct=None, max_sector_exposure_pct=None,
        max_open_positions=None, max_positions_per_sector=None,
        max_daily_loss=None, max_daily_loss_pct=None,
        max_drawdown=None, max_drawdown_pct=None, min_cash_reserve=None,
        option_planned_risk_pct=None, max_long_option_premium_pct=None,
        max_total_option_premium_pct=None, max_portfolio_planned_risk_pct=None,
        max_portfolio_option_stress_risk_pct=0.0001,  # extremely tight
        max_portfolio_theoretical_option_loss_pct=None, max_open_option_positions=None,
        fractional_shares=True,
    )
    r = option_account_fit(policy=pol, equity=50_000.0, contract_quality=good,
                           side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                           strike=103.0, iv=0.30, dte=14, atr_pct=2.5)
    assert r.eligible is False
    assert any(v.check == "option_stress_risk_pct" for v in r.violations)


def test_theoretical_loss_violation():
    good = _good_call_quality()
    pol = RiskPolicy(
        name="tight_theoretical", source="test",
        max_loss_per_trade=None, risk_per_trade_pct=None,
        max_position_notional=None, max_position_notional_pct=None,
        max_portfolio_exposure_pct=None, max_sector_exposure_pct=None,
        max_open_positions=None, max_positions_per_sector=None,
        max_daily_loss=None, max_daily_loss_pct=None,
        max_drawdown=None, max_drawdown_pct=None, min_cash_reserve=None,
        option_planned_risk_pct=None, max_long_option_premium_pct=None,
        max_total_option_premium_pct=None, max_portfolio_planned_risk_pct=None,
        max_portfolio_option_stress_risk_pct=None,
        max_portfolio_theoretical_option_loss_pct=0.0001,  # extremely tight
        max_open_option_positions=None, fractional_shares=True,
    )
    r = option_account_fit(policy=pol, equity=50_000.0, contract_quality=good,
                           side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                           strike=103.0, iv=0.30, dte=14, atr_pct=2.5)
    assert r.eligible is False
    assert any(v.check == "option_theoretical_loss_pct" for v in r.violations)


def test_max_open_option_positions_violation():
    good = _good_call_quality()
    r = option_account_fit(policy=DASHBOARD_POLICY, equity=50_000.0, contract_quality=good,
                           side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                           strike=103.0, iv=0.30, dte=14, atr_pct=2.5, buying_power=40_000.0,
                           current_open_option_positions=DASHBOARD_POLICY.max_open_option_positions)
    assert r.eligible is False
    assert any(v.check == "max_open_option_positions" for v in r.violations)


def test_portfolio_option_premium_violation():
    good = _good_call_quality()
    r = option_account_fit(policy=DASHBOARD_POLICY, equity=50_000.0, contract_quality=good,
                           side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                           strike=103.0, iv=0.30, dte=14, atr_pct=2.5, buying_power=40_000.0,
                           current_option_premium_committed=50_000.0 * DASHBOARD_POLICY.max_total_option_premium_pct)
    assert r.eligible is False
    assert any(v.check == "max_total_option_premium_pct" for v in r.violations)


def test_option_contracts_remain_integer():
    good = _good_call_quality()
    r = option_account_fit(policy=DASHBOARD_POLICY, equity=50_000.0, contract_quality=good,
                           side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                           strike=103.0, iv=0.30, dte=14, atr_pct=2.5, buying_power=40_000.0,
                           contracts_candidate=3)
    assert r.eligible is True
    assert r.quantity_allowed == 3
    assert isinstance(r.quantity_allowed, int)
    r0 = option_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, contract_quality=good,
                            side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                            strike=103.0, iv=0.30, dte=14, atr_pct=2.5, buying_power=400.0)
    assert r0.quantity_allowed == 0
    assert r0.quantity_allowed != 0.5   # never a fraction


def test_call_and_put_use_the_same_account_fit_function():
    call_good = _good_call_quality()
    put_good = _good_put_quality()
    r_call = option_account_fit(policy=DASHBOARD_POLICY, equity=50_000.0,
                                contract_quality=call_good, side="CALL", limit_price=1.00,
                                spot=100.0, stop=97.0, strike=103.0, iv=0.30, dte=14,
                                atr_pct=2.5, buying_power=40_000.0)
    r_put = option_account_fit(policy=DASHBOARD_POLICY, equity=50_000.0,
                               contract_quality=put_good, side="PUT", limit_price=1.00,
                               spot=100.0, stop=103.0, strike=93.0, iv=0.30, dte=14,
                               atr_pct=2.5, buying_power=40_000.0)
    assert r_call.eligible is True and r_put.eligible is True
    assert r_call.instrument != r_put.instrument
    assert option_account_fit.__module__ == "canonical.account_fit"  # one function, both calls


def test_malformed_instrument_type_raises():
    good = _good_call_quality()
    with pytest.raises(ValueError):
        option_account_fit(policy=DASHBOARD_POLICY, equity=50_000.0, contract_quality=good,
                           side="BOGUS", limit_price=1.00, spot=100.0, stop=97.0,
                           strike=103.0, iv=0.30, dte=14)


def test_missing_contract_quality_raises():
    with pytest.raises(ValueError):
        option_account_fit(policy=DASHBOARD_POLICY, equity=50_000.0, contract_quality=None,
                           side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                           strike=103.0, iv=0.30, dte=14)


def test_negative_option_premium_fails_closed_not_raise():
    good = _good_call_quality()
    r = option_account_fit(policy=DASHBOARD_POLICY, equity=50_000.0, contract_quality=good,
                           side="CALL", limit_price=-1.00, spot=100.0, stop=97.0,
                           strike=103.0, iv=0.30, dte=14)
    assert r.eligible is False
    assert r.binding_constraint == "invalid_levels"


# ══════════════════════════════════════════════════════════════════════════
# Cross-policy
# ══════════════════════════════════════════════════════════════════════════

def test_strategy500_and_dashboard_use_the_same_function():
    good = _good_call_quality()
    r_strat = option_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, contract_quality=good,
                                 side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                                 strike=103.0, iv=0.30, dte=14, atr_pct=2.5, buying_power=400.0)
    r_dash = option_account_fit(policy=DASHBOARD_POLICY, equity=500.0, contract_quality=good,
                                side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                                strike=103.0, iv=0.30, dte=14, atr_pct=2.5, buying_power=400.0)
    assert isinstance(r_strat, AccountFitResult) and isinstance(r_dash, AccountFitResult)
    # both currently ineligible at $500 equity, via different binding checks —
    # proving the DIVERGENCE traces to configured values, not a code fork
    assert r_strat.eligible is False and r_dash.eligible is False
    assert r_strat.binding_constraint != r_dash.binding_constraint


def test_same_policy_same_state_gives_byte_equivalent_result():
    good = _good_call_quality()
    kwargs = dict(policy=STRATEGY_500_POLICY, equity=500.0, contract_quality=good,
                 side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                 strike=103.0, iv=0.30, dte=14, atr_pct=2.5, buying_power=400.0)
    r1 = option_account_fit(**kwargs)
    r2 = option_account_fit(**kwargs)
    assert r1 == r2


def test_different_profile_values_may_produce_different_eligibility():
    good = _good_call_quality()
    at_high_equity_dash = option_account_fit(
        policy=DASHBOARD_POLICY, equity=50_000.0, contract_quality=good, side="CALL",
        limit_price=1.00, spot=100.0, stop=97.0, strike=103.0, iv=0.30, dte=14,
        atr_pct=2.5, buying_power=40_000.0)
    at_high_equity_strat = option_account_fit(
        policy=STRATEGY_500_POLICY, equity=50_000.0, contract_quality=good, side="CALL",
        limit_price=1.00, spot=100.0, stop=97.0, strike=103.0, iv=0.30, dte=14,
        atr_pct=2.5, buying_power=40_000.0)
    assert at_high_equity_dash.eligible is True
    # Strategy500's ABSOLUTE $5/$125 caps still bind even at $50k equity —
    # this is a real, intentional policy divergence (Strategy500 is a fixed-
    # dollar $500-cash-account policy, not scale-invariant), not a bug.
    assert at_high_equity_strat.eligible is False


def test_no_profile_name_branching_in_account_fit_source():
    """Guards against an actual CODE branch on policy identity — e.g.
    `if policy.name == "Strategy500Policy":`. Deliberately does not forbid
    the bare substrings "Strategy500"/"Dashboard" anywhere in the source,
    since account_fit.py's own docstrings legitimately name them as
    provenance/rationale (e.g. explaining why the general per_trade_risk
    check matters for a profile with no option-specific fields) — banning
    the word entirely would make honest documentation impossible to write."""
    import re
    import canonical.account_fit as af
    src_stock = inspect.getsource(af.stock_account_fit)
    src_option = inspect.getsource(af.option_account_fit)
    branch_pattern = re.compile(r'policy\.name\s*==|\.name\s*==\s*["\'](Strategy500|Dashboard)')
    for src, fname in ((src_stock, "stock_account_fit"), (src_option, "option_account_fit")):
        assert not branch_pattern.search(src), f"{fname} branches on policy identity"


# ══════════════════════════════════════════════════════════════════════════
# Historical regression
# ══════════════════════════════════════════════════════════════════════════

def test_500_dollar_1_dollar_premium_case_rejected_under_strategy500():
    good = _good_call_quality()
    r = option_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, contract_quality=good,
                           side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                           strike=103.0, iv=0.30, dte=14, atr_pct=2.5, buying_power=400.0)
    assert r.eligible is False
    assert r.capital_required == 0.0   # zeroed out on ineligibility, never a phantom $100


def test_account_fit_never_reports_permitted_risk_above_the_effective_per_trade_cap():
    """For every eligible result this suite produces (stock and option,
    both policies), planned_risk must never exceed the policy's own
    effective per-trade cap at that equity."""
    cases = []
    good = _good_call_quality()
    for pol, equity, bp in ((STRATEGY_500_POLICY, 500.0, 400.0),
                            (DASHBOARD_POLICY, 500.0, 400.0),
                            (DASHBOARD_POLICY, 50_000.0, 40_000.0)):
        cases.append(stock_account_fit(policy=pol, equity=equity, entry=100.0, stop=97.0,
                                       buying_power=bp))
        cases.append(option_account_fit(policy=pol, equity=equity, contract_quality=good,
                                        side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                                        strike=103.0, iv=0.30, dte=14, atr_pct=2.5,
                                        buying_power=bp))
    checked = 0
    for r, pol, equity in zip(cases, (STRATEGY_500_POLICY, STRATEGY_500_POLICY, DASHBOARD_POLICY,
                                      DASHBOARD_POLICY, DASHBOARD_POLICY, DASHBOARD_POLICY),
                              (500.0, 500.0, 500.0, 500.0, 50_000.0, 50_000.0)):
        if not r.eligible:
            continue
        cap = effective_limit(absolute=pol.max_loss_per_trade, percent=pol.risk_per_trade_pct,
                              equity=equity)
        if cap.limit is not None:
            assert r.planned_risk <= cap.limit + 1e-6, (
                f"{r.instrument} planned_risk {r.planned_risk} exceeds effective "
                f"per-trade cap {cap.limit} at equity {equity}"
            )
            checked += 1
    assert checked > 0, "at least one eligible, capped case must have been exercised"


# ══════════════════════════════════════════════════════════════════════════
# Step 5.1 — structured risk_confidence
# ══════════════════════════════════════════════════════════════════════════

def test_stock_account_fit_confidence_is_high_when_eligible():
    """Stock risk is an exact formula, never modelled/degraded — matches
    option_risk_math.stock_risk()'s own hardcoded 'high'."""
    r = stock_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, entry=100.0,
                          stop=97.0, buying_power=400.0)
    assert r.eligible is True
    assert r.risk_confidence == RiskConfidence.HIGH


def test_stock_account_fit_confidence_is_none_when_ineligible():
    r = stock_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, entry=100.0,
                          stop=100.0, buying_power=400.0)   # zero risk distance -> fails closed
    assert r.eligible is False
    assert r.risk_confidence is None, "no risk was computed for an ineligible/invalid candidate"


def test_option_account_fit_confidence_survives_from_option_risk_math():
    """The structured field must carry the exact same value
    option_risk_math.option_risk()'s own 'confidence' key produced — not a
    re-derived or approximated one."""
    good = _good_call_quality()
    r = option_account_fit(policy=DASHBOARD_POLICY, equity=50_000.0, contract_quality=good,
                           side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                           strike=103.0, iv=0.30, dte=14, atr_pct=2.5, buying_power=40_000.0)
    assert r.eligible is True
    assert r.risk_confidence in (RiskConfidence.LOW, RiskConfidence.MODELLED)

    # Directly cross-check against the underlying option_risk_math call this
    # fixture drives, to prove it's not a coincidental/approximated value.
    from canonical.option_risk_math import option_risk as raw_option_risk, STRESS_DEFAULTS
    raw = raw_option_risk(contracts=1, limit_price=1.00, spot=100.0, stop=97.0, strike=103.0,
                          side="CALL", iv=0.30, dte=14, atr_pct=2.5, fee_per_contract=0.06,
                          r=0.042, pol=dict(STRESS_DEFAULTS))
    assert r.risk_confidence.value == raw["confidence"]


def test_option_account_fit_confidence_is_modelled_for_a_well_calibrated_contract():
    """A contract whose premium the model can actually reproduce reaches the
    full repriced success path -> 'modelled', not the 'low' fallback."""
    good = _good_call_quality()
    from canonical.option_risk_math import option_risk as raw_option_risk, STRESS_DEFAULTS, reprice
    modelled_price = reprice(underlying=100.0, strike=100.0, iv=0.30, dte_remaining=21,
                             side="CALL", r=0.042)["value"]
    r = option_account_fit(policy=DASHBOARD_POLICY, equity=50_000.0, contract_quality=good,
                           side="CALL", limit_price=modelled_price, spot=100.0, stop=97.0,
                           strike=100.0, iv=0.30, dte=21, atr_pct=2.0, buying_power=40_000.0)
    assert r.eligible is True
    assert r.risk_confidence == RiskConfidence.MODELLED


def test_option_account_fit_confidence_is_none_when_ineligible():
    good = _good_call_quality()
    r = option_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, contract_quality=good,
                           side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                           strike=103.0, iv=0.30, dte=14, atr_pct=2.5, buying_power=400.0)
    assert r.eligible is False
    assert r.risk_confidence is None


def test_option_account_fit_confidence_is_none_when_structurally_invalid():
    bad = _good_call_quality(dte=0)
    r = option_account_fit(policy=DASHBOARD_POLICY, equity=50_000.0, contract_quality=bad,
                           side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                           strike=103.0, iv=0.30, dte=0, buying_power=40_000.0)
    assert r.eligible is False
    assert r.risk_confidence is None


def test_risk_confidence_does_not_change_other_accountfitresult_fields():
    """Adding the field must not perturb eligible/quantity_allowed/
    capital_required/planned_risk/binding_constraint/violations — the
    Step 5.1 acceptance criterion (schema-only change)."""
    good = _good_call_quality()
    r = option_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, contract_quality=good,
                           side="CALL", limit_price=1.00, spot=100.0, stop=97.0,
                           strike=103.0, iv=0.30, dte=14, atr_pct=2.5, buying_power=400.0)
    assert r.eligible is False
    assert r.quantity_allowed == 0
    assert r.capital_required == 0.0
    assert r.planned_risk == 0.0
    assert r.binding_constraint == "per_trade_risk"
    assert r.violations[0].actual == pytest.approx(100.06, abs=0.01)
    assert r.violations[0].limit == pytest.approx(5.0)
