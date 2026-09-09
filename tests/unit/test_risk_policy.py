"""Canonical Option Architecture v1.1 — RiskPolicy dedicated unit tests.

Tests canonical/risk_policy.py in isolation. Fully offline, pure, no imports
of any legacy config module (that's the point — this module is deliberately
decoupled from lab/paper/config.py and dashboard/option_risk.py at runtime;
parity is checked here against literal values transcribed from source, not by
importing the legacy modules and comparing live).

Run: pytest tests/unit/test_risk_policy.py -v
"""
from __future__ import annotations

import inspect
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from canonical.risk_policy import (   # noqa: E402
    STRATEGY_500_POLICY, DASHBOARD_POLICY, RiskPolicy, LimitResult,
    BindingSource, effective_limit, per_trade_loss_cap, position_notional_cap,
    daily_loss_cap, drawdown_cap, future_live_policy,
)


def _all_none_kwargs():
    """A minimal, fully-unbounded RiskPolicy body for constructing throwaway
    test instances without repeating all 19 fields each time."""
    return dict(
        max_loss_per_trade=None, risk_per_trade_pct=None,
        max_position_notional=None, max_position_notional_pct=None,
        max_portfolio_exposure_pct=None, max_sector_exposure_pct=None,
        max_open_positions=None,
        max_positions_per_sector=None, max_daily_loss=None, max_daily_loss_pct=None,
        max_drawdown=None, max_drawdown_pct=None, min_cash_reserve=None,
        option_planned_risk_pct=None, max_long_option_premium_pct=None,
        max_total_option_premium_pct=None, max_portfolio_planned_risk_pct=None,
        max_portfolio_option_stress_risk_pct=None,
        max_portfolio_theoretical_option_loss_pct=None,
        max_open_option_positions=None, fractional_shares=True,
    )


# ══════════════════════════════════════════════════════════════════════════
# Schema
# ══════════════════════════════════════════════════════════════════════════

def test_strategy500_profile_instantiates():
    assert isinstance(STRATEGY_500_POLICY, RiskPolicy)
    assert STRATEGY_500_POLICY.name == "Strategy500Policy"


def test_dashboard_profile_instantiates():
    assert isinstance(DASHBOARD_POLICY, RiskPolicy)
    assert DASHBOARD_POLICY.name == "DashboardPolicy"


def test_profiles_are_separate_objects_same_type():
    assert STRATEGY_500_POLICY is not DASHBOARD_POLICY
    assert type(STRATEGY_500_POLICY) is type(DASHBOARD_POLICY) is RiskPolicy


def test_risk_policy_carries_no_runtime_trade_or_account_state():
    """Structural test: none of RiskPolicy's field names may represent
    equity, buying power, positions, quotes, premium, entry, stop, planned
    risk, or instrument preference — that state belongs to Step 4 AccountFit."""
    forbidden_substrings = (
        "equity", "buying_power", "balance", "position_id", "positions",
        "quote", "premium_paid", "entry", "stop", "planned_risk_dollars",
        "instrument_preference", "preferred", "cash_on_hand", "portfolio_value",
    )
    field_names = {f for f in inspect.signature(RiskPolicy.__init__).parameters if f != "self"}
    # "positions" would false-positive on max_open_positions/
    # max_positions_per_sector/max_open_option_positions, which are POLICY
    # counts (a configured limit), not runtime state (an actual held
    # position) -- excluded deliberately, and asserted separately below to
    # keep the intent visible rather than silently narrowing the check.
    policy_count_fields = {"max_open_positions", "max_positions_per_sector",
                           "max_open_option_positions"}
    checked = field_names - policy_count_fields
    for name in checked:
        for bad in forbidden_substrings:
            assert bad not in name, f"RiskPolicy.{name} looks like runtime/account state ({bad!r})"
    for name in policy_count_fields:
        assert name in field_names  # sanity: still part of the schema


def test_invalid_negative_dollar_field_rejects():
    with pytest.raises(ValueError):
        RiskPolicy(name="x", source="test", **{**_all_none_kwargs(), "max_loss_per_trade": -1.0})


def test_invalid_negative_pct_field_rejects():
    with pytest.raises(ValueError):
        RiskPolicy(name="x", source="test", **{**_all_none_kwargs(), "risk_per_trade_pct": -0.01})


def test_invalid_zero_or_negative_count_rejects():
    with pytest.raises(ValueError):
        RiskPolicy(name="x", source="test", **{**_all_none_kwargs(), "max_open_positions": 0})
    with pytest.raises(ValueError):
        RiskPolicy(name="x", source="test", **{**_all_none_kwargs(), "max_open_positions": -3})


def test_empty_name_rejects():
    with pytest.raises(ValueError):
        RiskPolicy(name="", source="test", **_all_none_kwargs())


def test_empty_source_rejects():
    with pytest.raises(ValueError):
        RiskPolicy(name="x", source="", **_all_none_kwargs())


def test_percentage_convention_is_fraction_not_points():
    """0.01 must mean 1%, not 1%-as-100. Pinned by checking BALANCED's
    documented 1% stock risk survives conversion as 0.01, not 1.0."""
    assert DASHBOARD_POLICY.risk_per_trade_pct == pytest.approx(0.01)
    assert DASHBOARD_POLICY.risk_per_trade_pct < 1.0, (
        "a 1% risk budget must be represented as 0.01, not 1.0 — "
        "the fraction convention, not percentage-points"
    )


def test_a_policy_over_100_pct_is_not_arbitrarily_rejected():
    """The task explicitly forbids enforcing <=100% unless genuinely correct
    for every field — construct one at 150% and confirm it's accepted."""
    RiskPolicy(name="x", source="test", **{**_all_none_kwargs(), "max_portfolio_theoretical_option_loss_pct": 1.5})


# ══════════════════════════════════════════════════════════════════════════
# Cap math — effective_limit()
# ══════════════════════════════════════════════════════════════════════════

def test_absolute_tighter_than_percentage():
    r = effective_limit(absolute=5.0, percent=0.05, equity=500.0)  # pct=25.0
    assert r == LimitResult(5.0, BindingSource.ABSOLUTE)


def test_percentage_tighter_than_absolute():
    r = effective_limit(absolute=50.0, percent=0.01, equity=500.0)  # pct=5.0
    assert r == LimitResult(5.0, BindingSource.PERCENTAGE)


def test_absolute_only():
    r = effective_limit(absolute=10.0, percent=None, equity=500.0)
    assert r == LimitResult(10.0, BindingSource.ABSOLUTE)


def test_percentage_only():
    r = effective_limit(absolute=None, percent=0.20, equity=500.0)
    assert r == LimitResult(100.0, BindingSource.PERCENTAGE)


def test_equal_caps_report_both_deterministically():
    r = effective_limit(absolute=5.0, percent=0.01, equity=500.0)  # both = 5.0
    assert r.limit == 5.0
    assert r.binding_source == BindingSource.BOTH


def test_neither_side_is_unbounded_not_zero():
    r = effective_limit(absolute=None, percent=None, equity=500.0)
    assert r.limit is None
    assert r.binding_source == BindingSource.UNBOUNDED


def test_zero_equity_is_deterministic_not_a_crash():
    r = effective_limit(absolute=5.0, percent=0.01, equity=0.0)
    assert r == LimitResult(0.0, BindingSource.PERCENTAGE)
    r2 = effective_limit(absolute=None, percent=0.01, equity=0.0)
    assert r2 == LimitResult(0.0, BindingSource.PERCENTAGE)
    r3 = effective_limit(absolute=5.0, percent=None, equity=0.0)
    assert r3 == LimitResult(5.0, BindingSource.ABSOLUTE)


def test_negative_equity_rejects():
    with pytest.raises(ValueError):
        effective_limit(absolute=5.0, percent=0.01, equity=-100.0)


def test_negative_absolute_cap_rejects():
    with pytest.raises(ValueError):
        effective_limit(absolute=-1.0, percent=None, equity=500.0)


def test_negative_percentage_cap_rejects():
    with pytest.raises(ValueError):
        effective_limit(absolute=None, percent=-0.01, equity=500.0)


# ══════════════════════════════════════════════════════════════════════════
# Strategy500 parity — verify against source, not restate from memory
# ══════════════════════════════════════════════════════════════════════════

def test_strategy500_matches_the_500_dollar_5_dollar_cap_relationship():
    """The documented relationship in lab/paper/config.py's own comment
    ("1% of $500 = $5") — verified structurally, not by repeating both
    numbers independently: 0.01 * 500 must equal max_loss_per_trade exactly."""
    assert STRATEGY_500_POLICY.risk_per_trade_pct * 500.0 == pytest.approx(
        STRATEGY_500_POLICY.max_loss_per_trade)


def test_strategy500_per_trade_cap_at_500_equity_is_exactly_5_dollars():
    r = per_trade_loss_cap(STRATEGY_500_POLICY, 500.0)
    assert r.limit == pytest.approx(5.0)
    assert r.binding_source == BindingSource.BOTH  # 5.0 == 500*0.01 exactly


def test_strategy500_position_notional_cap_is_125():
    assert STRATEGY_500_POLICY.max_position_notional == pytest.approx(125.0)
    r = position_notional_cap(STRATEGY_500_POLICY, 500.0)
    assert r.limit == pytest.approx(125.0)
    assert r.binding_source == BindingSource.ABSOLUTE  # no pct side set


def test_strategy500_open_positions_and_sector_limit():
    assert STRATEGY_500_POLICY.max_open_positions == 3
    assert STRATEGY_500_POLICY.max_positions_per_sector == 1


def test_strategy500_daily_loss_and_drawdown_caps():
    r_daily = daily_loss_cap(STRATEGY_500_POLICY, 500.0)
    assert r_daily.limit == pytest.approx(10.0)
    r_dd = drawdown_cap(STRATEGY_500_POLICY, 500.0)
    assert r_dd.limit == pytest.approx(50.0)


def test_strategy500_sector_exposure_pct_is_030():
    """Added in Step 4 after discovering RiskConfig.max_sector_exposure_pct
    (0.30) was missing from the original Step 3 field list."""
    assert STRATEGY_500_POLICY.max_sector_exposure_pct == pytest.approx(0.30)


def test_dashboard_has_no_sector_exposure_pct():
    assert DASHBOARD_POLICY.max_sector_exposure_pct is None


def test_strategy500_fractional_shares_true():
    assert STRATEGY_500_POLICY.fractional_shares is True


def test_strategy500_has_no_option_policy_fabricated():
    """lab/paper/config.py has zero option-specific fields — every option
    field on this profile must be None, never a made-up number."""
    for f in ("option_planned_risk_pct", "max_long_option_premium_pct",
              "max_total_option_premium_pct", "max_portfolio_planned_risk_pct",
              "max_portfolio_option_stress_risk_pct",
              "max_portfolio_theoretical_option_loss_pct",
              "max_open_option_positions"):
        assert getattr(STRATEGY_500_POLICY, f) is None, f"{f} must be None (no source), got {getattr(STRATEGY_500_POLICY, f)}"


# ══════════════════════════════════════════════════════════════════════════
# Dashboard parity
# ══════════════════════════════════════════════════════════════════════════

def test_dashboard_option_percentages_preserved():
    assert DASHBOARD_POLICY.option_planned_risk_pct == pytest.approx(0.04)
    assert DASHBOARD_POLICY.max_long_option_premium_pct == pytest.approx(0.10)
    assert DASHBOARD_POLICY.max_total_option_premium_pct == pytest.approx(0.20)
    assert DASHBOARD_POLICY.max_portfolio_planned_risk_pct == pytest.approx(0.06)
    assert DASHBOARD_POLICY.max_portfolio_option_stress_risk_pct == pytest.approx(0.12)
    assert DASHBOARD_POLICY.max_portfolio_theoretical_option_loss_pct == pytest.approx(0.20)
    assert DASHBOARD_POLICY.max_open_option_positions == 2


def test_dashboard_stock_percentages_preserved():
    assert DASHBOARD_POLICY.risk_per_trade_pct == pytest.approx(0.01)
    assert DASHBOARD_POLICY.max_position_notional_pct == pytest.approx(0.20)
    assert DASHBOARD_POLICY.max_portfolio_exposure_pct == pytest.approx(0.60)
    assert DASHBOARD_POLICY.max_open_positions == 5
    assert DASHBOARD_POLICY.max_positions_per_sector == 2


def test_dashboard_missing_absolute_per_trade_cap_stays_unbounded():
    """The v1.1-identified gap: DashboardPolicy has percentage limits but no
    absolute $ per-trade cap. Must be None, not silently filled with a
    fabricated number, and NOT the source's own 0.0-means-unbounded literal
    (which would mean zero dollars of risk in this schema's convention)."""
    assert DASHBOARD_POLICY.max_loss_per_trade is None


def test_dashboard_missing_cap_makes_the_effective_limit_percentage_only():
    r = per_trade_loss_cap(DASHBOARD_POLICY, 500.0)
    assert r.binding_source == BindingSource.PERCENTAGE
    assert r.limit == pytest.approx(5.0)  # 500 * 0.01


def test_dashboard_has_no_ceiling_at_high_equity_unlike_strategy500():
    """Demonstrates the real-world consequence of the gap: at high equity,
    DashboardPolicy's per-trade cap grows without bound (percentage-only),
    while Strategy500Policy's absolute $5 floor caps it regardless of
    equity. This is not "fixed" here — v1.1 leaves it a human policy
    decision — but the schema must faithfully reproduce the current gap."""
    dash = per_trade_loss_cap(DASHBOARD_POLICY, 50_000.0)
    strat = per_trade_loss_cap(STRATEGY_500_POLICY, 50_000.0)
    assert dash.limit == pytest.approx(500.0)
    assert strat.limit == pytest.approx(5.0)
    assert dash.limit > strat.limit


def test_dashboard_dead_config_fields_not_carried_as_enforced_limits():
    """options_desk.config()'s SIZE_MAX_DAILY_LOSS_PCT (3.0%) is defined but
    verified unused (no cfg["max_daily_loss_pct"] read anywhere) — must not
    appear here as an enforced-looking limit."""
    assert DASHBOARD_POLICY.max_daily_loss_pct is None
    assert DASHBOARD_POLICY.max_daily_loss is None


# ══════════════════════════════════════════════════════════════════════════
# Formula invariance — the architectural invariant this whole step exists for
# ══════════════════════════════════════════════════════════════════════════

def test_both_profiles_route_through_the_same_helper_function():
    """Not an identity check on the profiles — a check that the SAME
    function object computes both, so there is no per-profile code path to
    diverge. per_trade_loss_cap() is a thin wrapper around effective_limit();
    confirm it calls that exact function object for both profiles by
    checking effective_limit is referenced in its bytecode constants/globals."""
    assert "effective_limit" in per_trade_loss_cap.__code__.co_names
    r1 = effective_limit(absolute=STRATEGY_500_POLICY.max_loss_per_trade,
                         percent=STRATEGY_500_POLICY.risk_per_trade_pct, equity=500.0)
    r2 = effective_limit(absolute=DASHBOARD_POLICY.max_loss_per_trade,
                         percent=DASHBOARD_POLICY.risk_per_trade_pct, equity=500.0)
    assert r1.limit == r2.limit == 5.0   # same VALUE at this equity...
    assert r1.binding_source != r2.binding_source  # ...via different binding sources, proving the values (not a branch) drove the difference


def test_no_profile_identity_branch_in_cap_math():
    """Structural guard: greps this module's own source for a profile-name
    special-case inside the cap-combining functions. If someone later adds
    `if policy.name == "Strategy500Policy":` to effective_limit() or any
    *_cap() wrapper, this test catches it."""
    import canonical.risk_policy as rp
    for fn in (rp.effective_limit, rp.per_trade_loss_cap, rp.position_notional_cap,
              rp.daily_loss_cap, rp.drawdown_cap):
        src = inspect.getsource(fn)
        assert "policy.name ==" not in src, (
            f"{fn.__name__} contains a profile-identity branch — profiles "
            "must differ only in configured values, never in formula."
        )
        assert "Strategy500" not in src and "Dashboard" not in src, (
            f"{fn.__name__} references a specific profile by name — the cap "
            "math must be profile-agnostic."
        )


def test_different_profile_different_binding_source_same_equity():
    """A concrete demonstration that DIFFERING VALUES (not code branching)
    are what produce different outcomes: at $500 equity both profiles land
    on the same $5 number via different binding sources (BOTH vs
    PERCENTAGE), proving the formula is identical and only the configured
    numbers differ."""
    r_strat = per_trade_loss_cap(STRATEGY_500_POLICY, 500.0)
    r_dash = per_trade_loss_cap(DASHBOARD_POLICY, 500.0)
    assert r_strat.limit == r_dash.limit == 5.0
    assert {r_strat.binding_source, r_dash.binding_source} == {BindingSource.BOTH, BindingSource.PERCENTAGE}


# ══════════════════════════════════════════════════════════════════════════
# Future live policy — must remain non-instantiable with permissive defaults
# ══════════════════════════════════════════════════════════════════════════

def test_future_live_policy_always_raises():
    with pytest.raises(NotImplementedError):
        future_live_policy()


def test_no_module_level_live_policy_constant_exists():
    import canonical.risk_policy as rp
    for name in dir(rp):
        if "LIVE" in name.upper() and isinstance(getattr(rp, name), RiskPolicy):
            pytest.fail(f"found a module-level live RiskPolicy constant: {name} — "
                       "live risk appetite must never be silently populated")
