"""Executable-risk invariant: a position approved for a $X maximum planned loss must not lose more than $X when the
protective stop fills under the SAME assumptions the paper broker uses (ask + slippage entry, stop - slippage exit,
fees). The realised loss is computed with the broker's own fill simulator, not re-derived."""
import dataclasses
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for folder in ("src", "dashboard", "lab"):
    sys.path.insert(0, str(ROOT / folder))

from canonical.account_fit import stock_account_fit
from canonical.risk_policy import STRATEGY_500_POLICY as POLICY
from paper import config as cfg
from paper import db, fills, risk
from paper.canonical_bridge import _executable_buy_price
from paper.fills import Quote

BUDGET = 5.0                      # STRATEGY_500_POLICY.max_loss_per_trade
TOL = 0.006                       # the ONLY tolerated excess: cent rounding of planned_risk


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("PAPER_DATA_DIR", str(tmp_path))
    db.reset_for_tests(str(tmp_path))
    yield
    db.close()


def realised_stop_loss(qty, quote, stop):
    """Dollar loss if the stop is hit intrabar, using the paper broker's own simulator for BOTH legs."""
    buy = fills.simulate_market("BUY", qty, quote)
    assert buy.status in ("filled", "partial"), buy
    q_exit = Quote(quote.symbol, last=stop, open=stop + 1.0, high=stop + 2.0, low=stop - 1.0,
                   bid=stop - 0.02, ask=stop + 0.02, source_ts=quote.source_ts, provider="t")
    sell = fills.simulate_stop("SELL", qty, stop, q_exit)
    assert sell.status in ("filled", "partial"), sell
    return qty * buy.price - qty * sell.price + buy.fees + sell.fees, buy.price


def fit_for(quote, entry, stop, *, policy=POLICY, bp=400.0, **kw):
    e = cfg.execution()
    return stock_account_fit(policy=policy, equity=500.0, entry=entry, stop=stop, buying_power=bp,
                             fill_price=_executable_buy_price(quote), exit_slippage_bps=e.slippage_bps,
                             fee_per_share=e.fee_per_share, **kw)


def q(bid, ask, last=None):
    import time
    return Quote("X", last=last or (bid + ask) / 2, bid=bid, ask=ask, source_ts=time.time(), provider="t")


SCENARIOS = {
    # id: (bid, ask, entry_ref, stop, env)
    "reference_equals_fill": (100.0, 100.0, 100.0, 95.0, {"PAPER_SLIPPAGE_BPS": "0"}),
    "fill_higher_than_reference": (100.0, 100.6, 100.0, 95.0, {}),
    "large_spread": (99.0, 101.0, 100.0, 95.0, {}),
    "high_slippage": (100.0, 100.05, 100.0, 95.0, {"PAPER_SLIPPAGE_BPS": "40"}),
    "tight_stop": (100.0, 100.10, 100.0, 99.5, {}),          # spread is a large share of the stop distance
    "dell_first_session": (565.3, 565.4026, 563.42, 517.79, {}),
    "meta_first_session": (750.0, 750.1249, 748.36, 706.2, {}),
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_risk_budget_binding_never_exceeded(monkeypatch, name):
    bid, ask, entry, stop, env = SCENARIOS[name]
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    quote = q(bid, ask)
    r = fit_for(quote, entry, stop)
    assert r.eligible and r.quantity_allowed > 0
    loss, _ = realised_stop_loss(r.quantity_allowed, quote, stop)
    assert loss <= BUDGET + TOL, (name, loss, r)
    assert r.planned_risk <= BUDGET + TOL
    if name in ("reference_equals_fill",):
        assert loss == pytest.approx(BUDGET, abs=0.02)        # binding: uses (almost) the whole budget


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_legacy_position_size_obeys_the_same_invariant(monkeypatch, name):
    bid, ask, entry, stop, env = SCENARIOS[name]
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    quote = q(bid, ask)
    s = risk.position_size(500.0, entry, stop, fill_price=_executable_buy_price(quote), buying_power=400.0)
    assert s["affordable"] and s["quantity"] > 0
    loss, _ = realised_stop_loss(s["quantity"], quote, stop)
    assert loss <= BUDGET + TOL, (name, loss, s)


def test_fees_are_part_of_the_risk(monkeypatch):
    monkeypatch.setenv("PAPER_FEE_PER_SHARE", "0.05")          # round-trip $0.10/share
    quote = q(100.0, 100.05)
    r = fit_for(quote, 100.0, 95.0)
    loss, _ = realised_stop_loss(r.quantity_allowed, quote, 95.0)
    assert loss <= BUDGET + TOL


def test_position_cap_binding_realised_risk_is_below_budget():
    quote = q(500.0, 500.2)
    r = fit_for(quote, 500.0, 490.0)                            # risk-sized qty would cost ~$250 > $125 cap
    assert r.binding_constraint == "max_position_notional"
    assert r.capital_required <= POLICY.max_position_notional + 0.01
    loss, _ = realised_stop_loss(r.quantity_allowed, quote, 490.0)
    assert loss < BUDGET


def test_cash_binding_realised_risk_is_below_budget():
    quote = q(100.0, 100.05)
    r = fit_for(quote, 100.0, 90.0, bp=130.0)                   # only $30 spendable after the $100 reserve
    assert r.binding_constraint == "buying_power"
    loss, _ = realised_stop_loss(r.quantity_allowed, quote, 90.0)
    assert loss < BUDGET and r.capital_required <= 30.01


def test_sector_cap_binding_marks_ineligible_and_never_oversizes():
    quote = q(50.0, 50.03)
    r = fit_for(quote, 50.0, 48.0, sector="tech", sector_open_positions=0, sector_exposure=140.0)
    assert not r.eligible and any(v.check == "max_sector_exposure" for v in r.violations)
    if r.quantity_allowed > 0:                                   # an ineligible fit may zero the quantity
        loss, _ = realised_stop_loss(r.quantity_allowed, quote, 48.0)
        assert loss <= BUDGET + TOL                              # ...and if it does not, it still respects the budget


def test_whole_shares_round_down_and_stay_within_budget():
    quote = q(20.0, 20.02)
    p = dataclasses.replace(POLICY, fractional_shares=False)
    r = fit_for(quote, 20.0, 18.0, policy=p)
    assert r.quantity_allowed == int(r.quantity_allowed) and r.quantity_allowed >= 1
    loss, _ = realised_stop_loss(r.quantity_allowed, quote, 18.0)
    assert loss <= BUDGET + TOL


def test_fractional_quantity_rounds_down_to_six_decimals():
    quote = q(565.3, 565.4026)
    r = fit_for(quote, 563.42, 517.79)
    assert abs(r.quantity_allowed * 1e6 - round(r.quantity_allowed * 1e6)) < 1e-6
    # one more micro-share would breach the budget: the quantity is the maximal one, rounded DOWN
    loss_up, _ = realised_stop_loss(r.quantity_allowed + 2e-6, quote, 517.79)
    assert loss_up > BUDGET - 1e-6


def test_first_session_dell_old_sizing_breached_and_new_sizing_does_not():
    """Historical DELL (2026-09-25): reference entry 563.42, stop 517.79, ask 565.4026 -> fill 565.6853; the OLD
    quantity 0.109577 lost more than the $5.00 budget at the stop. The corrected quantity does not."""
    quote = q(565.3, 565.4026)
    old_qty = 0.109577
    old_loss, fill = realised_stop_loss(old_qty, quote, 517.79)
    assert fill == pytest.approx(565.6853, abs=1e-4) and old_loss > BUDGET + 0.2      # ~ $5.28
    r = fit_for(quote, 563.42, 517.79)
    assert r.quantity_allowed < old_qty
    new_loss, _ = realised_stop_loss(r.quantity_allowed, quote, 517.79)
    assert new_loss <= BUDGET + TOL
