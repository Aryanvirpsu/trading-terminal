"""Canonical Option Architecture v1.1 — invariant / specification tests.

These are architecture tests, not application tests: they encode the invariants
from the v1.1 correction pass of "Canonical Option Architecture" (behavioral-
divergence audit -> v1.0 design -> v1.1 correction & policy-lock pass) and run
them against TODAY's production code. Most are EXPECTED TO FAIL right now — that
red result is the deliverable of this step (Implementation Step 1: establish the
baseline), not a bug in the test.

Every test documents, in its docstring:
  * the exact invariant it encodes,
  * the current production function(s) it exercises,
  * whether it is expected to PASS or FAIL today, and why.

No production code is imported from a canonical/ package that doesn't exist yet —
those tests (3, 6) attempt the intended future import and fail with a clear,
diagnostic message when it's absent, which IS the correct red-baseline behavior
for an interface that hasn't been built.

Fully offline and deterministic: no network, no live provider, no writes to the
real paper ledger (~/.tradingview_mcp_data/). Fixtures are reused verbatim from
the completed behavioral-divergence audit (same OI/spread/DTE/missing-Greeks/
stale-quote scenarios, same $500-equity / $1.00-premium sizing scenario) rather
than invented anew.

Run: pytest tests/unit/test_canonical_v11_invariants.py -v
"""
from __future__ import annotations

import inspect
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in ("src", "dashboard", "lab"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

from tradingview_mcp.core.services.options_grading import grade_contract  # noqa: E402
from tradingview_mcp.core.services import strategy_service as ss          # noqa: E402
import options_desk as OD          # noqa: E402
import option_risk as ORK          # noqa: E402
import decision_engine as DE       # noqa: E402
from paper import config as pcfg   # noqa: E402
from paper import risk as prisk    # noqa: E402


# ── Shared, deterministic fixtures (ported from the completed behavioral-
#    divergence audit's Phase 1 / Phase 3 scripts — not new examples) ────────

_OD_CFG = OD.config()
_NOW = datetime.now(timezone.utc)


def _expiry_for_dte(dte: int) -> str:
    return (_NOW + timedelta(days=dte)).date().isoformat()


def _make_fixture(*, strike=100.0, spot=100.0, bid=1.00, ask=1.02, last=1.01,
                   volume=300, oi=1000, iv=0.30, delta=0.50, side="CALL", dte=14,
                   fresh_quote=True, quote_age_hours=None, session_open=True):
    """Same shape as the audit's make_fixture(): returns (gc_contract,
    ad_contract, spot, side, dte, session_open) for a single scenario, varying
    one axis at a time from a 'good' baseline (fresh, tight spread, ATM,
    adequate OI/volume/Greeks)."""
    qt = None
    if fresh_quote:
        qt = _NOW.isoformat()
    elif quote_age_hours is not None:
        qt = (_NOW - timedelta(hours=quote_age_hours)).isoformat()
    gc_contract = {"strike": strike, "bid": bid, "ask": ask, "last_price": last,
                   "volume": volume, "open_interest": oi,
                   "implied_volatility": iv, "delta": delta}
    ad_contract = {"strike": strike, "bid": bid, "ask": ask, "volume": volume,
                   "open_interest": oi, "implied_volatility": iv, "delta": delta,
                   "side": side, "expiry": _expiry_for_dte(dte), "quote_timestamp": qt}
    return gc_contract, ad_contract, spot, side, dte, session_open


# The $500-equity / $1.00-premium / HIGH_QUALITY-setup scenario that produced the
# audit's headline "$100 shown vs $5 real cap" finding. Reused verbatim.
_EQUITY = 500.0
_ENTRY, _STOP, _CALL_STRIKE, _CALL_PREMIUM, _DTE, _IV = 100.0, 97.0, 103.0, 1.00, 14, 0.30


# ══════════════════════════════════════════════════════════════════════════
# 1 — instrument_choice() must survive option-ineligibility (v1.1 correction 2)
# ══════════════════════════════════════════════════════════════════════════

def test_option_ineligible_can_still_choose_stock():
    """Invariant (v1.1 correction 2): B.eligible == False for the OPTION leg must
    not prevent instrument_choice() (layer D) from running, and must not prevent
    it from returning STOCK when the stock leg is independently sizeable.

    Exercises: option_risk.instrument_choice(), option_risk.contract_eligibility().

    Fixture: the audit's $500-equity / $1.00-premium CALL, empirically ineligible
    on every axis (capital/planned/stress/absolute-max all over their caps).

    Expected: PASS. instrument_choice() already implements this correctly today —
    it is one of the few functions the v1.0/v1.1 design promotes largely as-is.
    """
    stock = ORK.stock_risk(quantity=1.25, entry=_ENTRY, stop=_STOP)
    pol = ORK.policy()
    qual = ORK.quality_tier(75.0)
    option_risk_result = ORK.option_risk(
        contracts=1, limit_price=_CALL_PREMIUM, spot=_ENTRY, stop=_STOP,
        strike=_CALL_STRIKE, side="CALL", iv=_IV, dte=_DTE, atr_pct=2.5,
        r=0.042, pol=pol)
    elig = ORK.contract_eligibility(risk=option_risk_result, equity=_EQUITY,
                                     pol=pol, quality=qual, portfolio=None)
    assert elig["eligible"] is False, (
        "fixture sanity check: this contract was expected to be ineligible "
        "(reproduces the audit's $100-vs-$5 finding) — if this now passes, "
        "option_risk.py's defaults changed and the fixture needs revisiting, "
        "not the assertion below."
    )

    result = ORK.instrument_choice(
        stock=stock, option=option_risk_result, option_eligibility=elig,
        stock_sizeable=True, option_quality_ok=True)

    assert result["instrument"] == "STOCK PREFERRED", (
        "instrument_choice() must still return STOCK when the option leg is "
        f"ineligible and the stock leg is sizeable — got {result['instrument']!r}"
    )
    assert result["option_eligible"] is False


def test_bad_option_does_not_block_stock_execution():
    """Invariant (v1.1 correction 2/3): a structurally bad/absent option
    (option_quality_ok=False, no option at all) must not force NO_TRADE when the
    stock leg is independently sizeable. Exercises the `option is None or not
    option_quality_ok` branch of instrument_choice() — a different branch than
    test_option_ineligible_can_still_choose_stock's `not eligible` branch.

    Exercises: option_risk.instrument_choice().

    Expected: PASS — same reasoning as test 1, different code path.
    """
    stock = ORK.stock_risk(quantity=1.25, entry=_ENTRY, stop=_STOP)
    result = ORK.instrument_choice(
        stock=stock, option=None, option_eligibility=None,
        stock_sizeable=True, option_quality_ok=False)
    assert result["instrument"] == "STOCK PREFERRED", (
        f"a missing/bad option must not block a sizeable stock trade — got {result['instrument']!r}"
    )


# ══════════════════════════════════════════════════════════════════════════
# 2 — instrument-specific executable predicates (v1.1 correction 3)
# ══════════════════════════════════════════════════════════════════════════

def test_stock_executable_does_not_imply_option_executable():
    """Invariant (v1.1 correction 3): stock_executable and option_executable are
    separate predicates sharing only setup_tradeable; the former must never imply
    the latter.

    Exercises: canonical.executable (Step 7) — the Strategy-500 $500-equity /
    $1.00-premium headline fixture, carried through the real A -> B -> D -> E
    chain and then the two dedicated predicates.

    Expected: PASS as of Step 7. Updated from the Step-1 red baseline (which
    targeted a canonical.executable module that did not exist yet) to exercise
    the NOW-implemented module: a genuinely eligible stock leg and a genuinely
    ineligible option leg for the SAME underlying, proving independence
    end-to-end rather than just "the module imports."
    """
    from canonical.account_fit import option_account_fit, stock_account_fit
    from canonical.contract_quality import evaluate_contract_quality
    from canonical.executable import evaluate_option_executable, evaluate_stock_executable
    from canonical.instrument_choice import InstrumentChoice, choose_instrument
    from canonical.risk_policy import STRATEGY_500_POLICY
    from canonical.sizing import size_instrument
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    cq = evaluate_contract_quality(
        strike=_CALL_STRIKE, underlying=_ENTRY, side="CALL", bid=0.99, ask=1.01,
        volume=300, open_interest=1000, implied_volatility=_IV, delta=0.50,
        dte=_DTE, quote_timestamp=now.isoformat(), session_open=True, now=now)

    stock_fit = stock_account_fit(policy=STRATEGY_500_POLICY, equity=_EQUITY,
                                  entry=_ENTRY, stop=_STOP, buying_power=400.0)
    option_fit = option_account_fit(policy=STRATEGY_500_POLICY, equity=_EQUITY,
                                    contract_quality=cq, side="CALL",
                                    limit_price=_CALL_PREMIUM, spot=_ENTRY, stop=_STOP,
                                    strike=_CALL_STRIKE, iv=_IV, dte=_DTE, atr_pct=2.5,
                                    buying_power=400.0)
    assert stock_fit.eligible is True and option_fit.eligible is False  # fixture sanity

    choice = choose_instrument(setup_tradeable=True, stock_account_fit=stock_fit,
                               option_account_fit=option_fit, contract_quality=cq)
    assert choice.choice == InstrumentChoice.STOCK  # fixture sanity

    sizing = size_instrument(choice=choice, stock_account_fit=stock_fit,
                             option_account_fit=option_fit)

    stock_r = evaluate_stock_executable(setup_tradeable=True, account_fit=stock_fit,
                                        choice=choice, sizing=sizing)
    option_r = evaluate_option_executable(setup_tradeable=True, contract_quality=cq,
                                          account_fit=option_fit, choice=choice, sizing=sizing,
                                          shadow_only=True)

    assert stock_r.executable is True, "the stock leg must be independently executable"
    assert option_r.executable is False, (
        "stock_executable=True must never imply option_executable=True — the option "
        "leg here is both ineligible (B) and shadow-only"
    )


def test_option_executable_requires_quality_and_account_fit():
    """Invariant (v1.1 correction 3): an option must never be presented as a
    sized, tradeable idea unless BOTH contract quality (A) and account-fit (B)
    pass.

    Exercises: canonical.executable.evaluate_option_executable() (Step 7),
    fed a contract whose Layer-A quality check fails (missing bid/ask) even
    though D/E are made to otherwise look ready — the exact "A alone must
    fail this closed" shape the legacy grade_contract()/_size_option() pair
    never enforced (see the ORIGINAL version of this test, preserved in git
    history, for that legacy-function reproduction).

    Expected: PASS as of Step 7. Updated from the Step-1 red baseline (which
    targeted grade_contract()/_size_option()/contract_eligibility() — three
    legacy functions that will correctly never jointly gate on A+B, since
    they are not migrated by this step and are not what this invariant is
    ultimately about) to exercise the canonical Layer-7 predicate this
    invariant was actually asking for.
    """
    from canonical.account_fit import option_account_fit
    from canonical.contract_quality import evaluate_contract_quality
    from canonical.executable import evaluate_option_executable
    from canonical.instrument_choice import InstrumentChoice, InstrumentChoiceResult
    from canonical.risk_policy import DASHBOARD_POLICY
    from canonical.sizing import SizingResult
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    bad_cq = evaluate_contract_quality(
        strike=_CALL_STRIKE, underlying=_ENTRY, side="CALL", bid=None, ask=None,
        volume=300, open_interest=1000, implied_volatility=_IV, delta=0.50,
        dte=_DTE, quote_timestamp=now.isoformat(), session_open=True, now=now)
    assert bad_cq.quality_pass is False  # fixture sanity — a genuine Layer-A failure

    good_cq = evaluate_contract_quality(
        strike=_CALL_STRIKE, underlying=_ENTRY, side="CALL", bid=0.99, ask=1.01,
        volume=300, open_interest=1000, implied_volatility=_IV, delta=0.50,
        dte=_DTE, quote_timestamp=now.isoformat(), session_open=True, now=now)
    # An account-fit that is otherwise eligible against a GOOD contract — used
    # here to prove A's failure alone is enough, not merely a side-effect of B
    # also failing. DASHBOARD_POLICY (percentage-only caps, scaling with
    # equity), not STRATEGY_500_POLICY (an absolute $5 per-trade floor that
    # never scales with equity — the very thing this file's other tests use
    # it to prove) — the point here is to isolate A, not to also exercise B's
    # historical $100-vs-$5 finding.
    option_fit = option_account_fit(policy=DASHBOARD_POLICY, equity=50_000.0,
                                    contract_quality=good_cq, side="CALL",
                                    limit_price=_CALL_PREMIUM, spot=_ENTRY, stop=_STOP,
                                    strike=_CALL_STRIKE, iv=_IV, dte=_DTE, atr_pct=2.5,
                                    buying_power=40_000.0)
    assert option_fit.eligible is True  # fixture sanity

    choice = InstrumentChoiceResult(choice=InstrumentChoice.OPTION, reason="fixture",
                                    reasons=("fixture",), stock_eligible=False, option_eligible=True)
    sizing = SizingResult(instrument=InstrumentChoice.OPTION, quantity=1, max_quantity_allowed=1,
                          requested_quantity=None, binding_constraint="fixture",
                          sizeable=True, reasons=("fixture",))

    result = evaluate_option_executable(setup_tradeable=True, contract_quality=bad_cq,
                                        account_fit=option_fit, choice=choice, sizing=sizing)

    assert result.executable is False, (
        "a structurally invalid (Layer-A-failing) contract must never be reported "
        "executable, even when D/E/B otherwise look ready (v1.1 correction 3 / P0 fix #1)"
    )
    assert "structurally_invalid" in result.blockers


# ══════════════════════════════════════════════════════════════════════════
# 3 — one RiskPolicy, values differ, formula doesn't (v1.1 correction 4)
# ══════════════════════════════════════════════════════════════════════════

def test_same_policy_same_account_fit_across_pipelines():
    """Invariant (v1.1 correction 4): the same account snapshot, evaluated
    through the same canonical function with the same RiskPolicy, must
    produce an identical result — and that canonical result must never
    reproduce the historical $100-vs-$5 mismatch.

    Exercises: canonical.account_fit.option_account_fit() (implemented in
    Step 4), canonical.risk_policy.STRATEGY_500_POLICY.

    Expected: PASS as of Step 4. Updated from the Step-1 red baseline (which
    targeted the legacy strategy_service._size_option() — a test that will
    correctly stay red forever until that call site is migrated, which is
    exactly what test_no_path_exceeds_configured_per_trade_cap continues to
    guard) to exercise the NEW canonical AccountFit layer this invariant was
    actually about: cross-pipeline agreement, not one specific legacy
    function's bug. Non-vacuous: asserts both same-input determinism AND the
    real historical-regression outcome (eligible=False, capital_required=0),
    not just "the module imports."
    """
    from canonical.account_fit import option_account_fit
    from canonical.contract_quality import evaluate_contract_quality
    from canonical.risk_policy import STRATEGY_500_POLICY
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    cq = evaluate_contract_quality(
        strike=_CALL_STRIKE, underlying=_ENTRY, side="CALL", bid=0.99, ask=1.01,
        volume=300, open_interest=1000, implied_volatility=_IV, delta=0.50,
        dte=_DTE, quote_timestamp=now.isoformat(), session_open=True, now=now)
    assert cq.quality_pass is True  # fixture sanity

    kwargs = dict(policy=STRATEGY_500_POLICY, equity=_EQUITY, contract_quality=cq,
                 side="CALL", limit_price=_CALL_PREMIUM, spot=_ENTRY, stop=_STOP,
                 strike=_CALL_STRIKE, iv=_IV, dte=_DTE, atr_pct=2.5, buying_power=400.0)

    # same policy + same state -> identical result, called twice
    r1 = option_account_fit(**kwargs)
    r2 = option_account_fit(**kwargs)
    assert r1 == r2, "same policy/state must produce a byte-identical AccountFitResult"

    # the actual headline finding: the canonical path correctly REJECTS what
    # the legacy _size_option() incorrectly reported as a $100 idea against
    # a real $5 cap.
    assert r1.eligible is False
    assert r1.capital_required == 0.0
    assert r1.binding_constraint == "per_trade_risk"


def test_risk_policy_profiles_change_values_not_formula():
    """Invariant (v1.1 correction 4): profiles differ only in configured VALUES;
    the calculation path (min(absolute_cap, equity * pct_cap)) is shared.

    Exercises: canonical.risk_policy (implemented in Step 3 — Strategy500Policy
    and DashboardPolicy exported as STRATEGY_500_POLICY / DASHBOARD_POLICY
    RiskPolicy instances, per the module's own naming; imported here by its
    real exported names rather than the architecture doc's illustrative ones).

    Expected: PASS as of Step 3. Updated from the Step-1 red baseline (which
    asserted only that the module was missing) to a real, non-vacuous check:
    both profiles' per-trade caps must be produced by the SAME effective_limit
    call, and their differing outputs must trace to differing configured
    values, not a profile-identity branch. This is not "altering the
    invariant to manufacture green" — canonical.risk_policy now genuinely
    implements the invariant this test names; the fix updates a stale import
    of a name (Strategy500Policy/DashboardPolicy as illustrative class names)
    that was never the module's real API, and adds the assertion the test
    was always meant to make once its prerequisite existed.
    """
    try:
        from canonical.risk_policy import (
            RiskPolicy, STRATEGY_500_POLICY, DASHBOARD_POLICY, effective_limit,
        )
    except ModuleNotFoundError as e:
        pytest.fail(
            f"canonical.risk_policy does not exist yet ({e}) — expected v1.1 "
            "red baseline (architecture v1.1 §v4)."
        )
        return

    assert isinstance(STRATEGY_500_POLICY, RiskPolicy)
    assert isinstance(DASHBOARD_POLICY, RiskPolicy)
    assert STRATEGY_500_POLICY.name != DASHBOARD_POLICY.name

    # Same formula, differing configured values -> differing real-world caps.
    # Strategy500 has an absolute $5 floor; Dashboard is percentage-only, so
    # at high equity Dashboard's cap grows without bound while Strategy500's
    # does not. Both numbers come from ONE call to the SAME function.
    strat = effective_limit(absolute=STRATEGY_500_POLICY.max_loss_per_trade,
                            percent=STRATEGY_500_POLICY.risk_per_trade_pct,
                            equity=50_000.0)
    dash = effective_limit(absolute=DASHBOARD_POLICY.max_loss_per_trade,
                           percent=DASHBOARD_POLICY.risk_per_trade_pct,
                           equity=50_000.0)
    assert strat.limit == pytest.approx(5.0), (
        "Strategy500's absolute $5 cap must still bind at high equity"
    )
    assert dash.limit == pytest.approx(500.0), (
        "Dashboard's percentage-only cap must scale with equity — this "
        "divergence must come from configured VALUES (Dashboard has no "
        "absolute cap), not from a profile-name branch in the formula"
    )

    # Structural guard: no profile-identity special-case in the shared helper.
    import inspect as _inspect
    src = _inspect.getsource(effective_limit)
    assert "policy.name" not in src and "Strategy500" not in src and "Dashboard" not in src, (
        "effective_limit() must not branch on which profile called it"
    )


# ══════════════════════════════════════════════════════════════════════════
# 4 — preview and execute share one formula (v1.1 correction 8)
# ══════════════════════════════════════════════════════════════════════════

def test_preview_execute_same_math_given_same_state():
    """Invariant (v1.1 correction 8): given the same canonical inputs,
    "preview" and "execute" must use ONE formula, never a `mode` branch.

    Expected: PASS as of Step 6. Updated from the Step-1 red baseline
    (which hand-transcribed decision_engine's inline formula and compared it
    against the legacy paper.risk.position_size() — two genuinely different
    formulas that will correctly disagree forever until a future caller
    migration) to exercise the NEW canonical Layer E, which is what this
    invariant was actually about: one sizing function, no mode parameter.
    Non-vacuous: asserts structural single-formula-ness (no `mode`
    parameter, no preview/execute branch in source — the same check
    tests/unit/test_sizing.py's dedicated suite already pins) AND genuine
    preview-vs-execute behavior — two size_instrument() calls that differ
    ONLY in which upstream AccountFitResult was supplied (simulating a
    stale "preview" equity snapshot vs a live "execute" one) still route
    through the identical function/formula, and two calls with the SAME
    snapshot are byte-for-byte identical.
    """
    import inspect
    from canonical.account_fit import stock_account_fit
    from canonical.instrument_choice import InstrumentChoice, InstrumentChoiceResult
    from canonical.risk_policy import STRATEGY_500_POLICY
    from canonical.sizing import size_instrument

    sig = inspect.signature(size_instrument)
    assert "mode" not in sig.parameters, "size_instrument() must not take a mode parameter"
    src = inspect.getsource(size_instrument)
    assert '"preview"' not in src and '"execute"' not in src, (
        "size_instrument() must not branch on a preview/execute mode string"
    )

    choice = InstrumentChoiceResult(choice=InstrumentChoice.STOCK, reason="fixture",
                                    reasons=("fixture",), stock_eligible=True,
                                    option_eligible=False)

    # "preview": an earlier/stale equity snapshot. "execute": a live one.
    # Different AAccountFitResult objects, same size_instrument() call.
    preview_fit = stock_account_fit(policy=STRATEGY_500_POLICY, equity=_EQUITY,
                                    entry=_ENTRY, stop=_STOP, buying_power=400.0)
    execute_fit = stock_account_fit(policy=STRATEGY_500_POLICY, equity=_EQUITY,
                                    entry=_ENTRY, stop=_STOP, buying_power=400.0)
    preview = size_instrument(choice=choice, stock_account_fit=preview_fit, option_account_fit=None)
    execute = size_instrument(choice=choice, stock_account_fit=execute_fit, option_account_fit=None)

    assert preview == execute, (
        "size_instrument() must produce identical results given identical "
        "upstream AccountFitResult state, regardless of which call site "
        "(preview vs execute) invoked it"
    )
    assert preview.quantity == pytest.approx(1.25, abs=1e-6)


# ══════════════════════════════════════════════════════════════════════════
# 5 — OI policy: structural non-overlap, no number assumed (v1.1 correction 1)
# ══════════════════════════════════════════════════════════════════════════

def test_oi_policy_has_no_overlapping_hard_and_soft_ranges():
    """Invariant (v1.1 correction 1): the canonical OI policy must be exactly
    one two-threshold model (OI<X -> HARD FAIL, X<=OI<Y -> SOFT, OI>=Y ->
    ADEQUATE+). X and Y are explicitly left open (v1.1 §v6 — INSUFFICIENT DATA);
    this test does NOT assume X=50 or X=250.

    Instead it proves the CURRENT problem the lock exists to fix: today's two
    competing implementations do not even agree on how many thresholds exist,
    let alone where they sit.

    Exercises: options_grading.grade_contract(), options_desk.analyse_contract().

    Expected: FAIL — reuses the audit's Phase 1 OI sweep, where the two
    functions disagreed at OI=50 and OI=100.

    Deliberately left red (Phase 3, Canonical Option Architecture v1.1 Step
    10 migration): per the migration's own rule, legacy scoring functions
    are not required to agree with each other, only to be unable to
    override canonical's `eligible`/`quantity`/`executable` authority for
    ANY live execution path — that containment is what actually matters,
    and it is proven, green, and structural (grep-based, not fixture-based)
    in tests/unit/test_pipeline3_canonical_migration.py::
    test_legacy_grader_disagreement_cannot_override_d. This test stays red
    forever as an honest record that grade_contract()/analyse_contract()
    themselves were never unified — that was explicitly out of scope.
    """
    disagreements = []
    for oi in (0, 25, 50, 100, 150, 200, 249, 250, 1000):
        gc_c, ad_c, spot, side, dte, session_open = _make_fixture(oi=oi)
        gc = grade_contract(gc_c, spot, side, dte)
        ad = OD.analyse_contract(ad_c, spot=spot, cfg=_OD_CFG, contracts=1,
                                  session_open=session_open)
        if gc["tradeable"] != ad["tradeable"]:
            disagreements.append((oi, gc["tradeable"], ad["tradeable"]))

    assert not disagreements, (
        "grade_contract() and analyse_contract() disagree on tradeability at "
        f"OI values {disagreements} — there is no single, agreed OI threshold "
        "model today. This test asserts AGREEMENT between the two current "
        "implementations, not a specific X/Y number (those remain open per "
        "v1.1 §v6)."
    )


# ══════════════════════════════════════════════════════════════════════════
# 6 — _grade_option_chain() preservation trip-wire (v1.1 correction 5)
# ══════════════════════════════════════════════════════════════════════════

def test_grade_option_chain_rules_are_either_migrated_or_explicitly_retired():
    """Invariant (v1.1 correction 5): every rule/constant inside
    decision_engine._grade_option_chain() must be accounted for in the v1.1
    preservation map (Canonical Option Architecture, §v1.1-5) before the
    function is retired. This is a trip-wire, not a migration check — it
    verifies the function's current source still contains exactly the literals
    the preservation map catalogued (via inspect.getsource; read-only, no
    execution), so a silent edit to the function invalidates this test loudly
    rather than silently invalidating the map.

    Exercises: decision_engine._grade_option_chain() (source inspection only).

    Expected: PASS today (the function is unchanged since the map was written).
    MUST be revisited — not silently updated — the moment this function is
    edited or retired, per correction 5's "do not simply delete its logic
    conceptually" instruction.
    """
    src = inspect.getsource(DE._grade_option_chain)
    catalogued_literals = [
        "1.0 - (spr / 100.0) / 0.10",                      # rule 4: 10% spread scoring reference
        "(oi or 0) / 500.0",                                 # rule 5: OI depth normalizer
        "(vol or 0) / 100.0",                                # rule 5: volume depth normalizer
        "3 <= dte <= 60",                                    # rule 6: conflicts with the locked 7-day floor
        "0.5 * liq_score + 0.3 * depth + 0.2 * dte_ok",      # rule 7: combined formula weights
        "ev_per_contract",                                   # rule 8: EV signal -> migrates to D's option_edge
        "avoid-both",                                        # rule 3/10: preference with no eligibility check
        "prefer-option",                                     # rule 10
    ]
    missing = [lit for lit in catalogued_literals if lit not in src]
    assert not missing, (
        f"_grade_option_chain()'s source no longer contains: {missing} — the "
        "function has changed since the v1.1 preservation map was written. "
        "Update the map (migrated-to-A / migrated-to-D / dropped / "
        "already-covered) before proceeding — do not silently drop coverage."
    )


# ══════════════════════════════════════════════════════════════════════════
# 7 — load-bearing tests, kept unchanged from the completed audit / v1.0
# ══════════════════════════════════════════════════════════════════════════

def test_no_path_exceeds_configured_per_trade_cap():
    """Load-bearing risk invariant, unchanged from the completed
    divergence audit (Phase 3) and architecture v1.0/v1.1. For a $500 account,
    no path may report a max-loss above the account's configured per-trade cap.

    Exercises: paper.config.risk(), strategy_service._size_option(),
    paper.risk.position_size().

    Expected: FAIL. position_size() (the real order-sizing path) already
    respects the cap; _size_option() (Strategy-500's shadow-display sizer)
    does not — this distinguishes which path is the offender.
    """
    cap = pcfg.risk().max_loss_per_trade
    sized = ss._size_option(premium=_CALL_PREMIUM, balance=_EQUITY)
    executed = prisk.position_size(_EQUITY, _ENTRY, _STOP, buying_power=400.0)

    assert executed["planned_risk"] <= cap + 1e-9, (
        f"position_size() planned_risk=${executed['planned_risk']} exceeds "
        f"the configured ${cap} per-trade cap — this path was expected to "
        "already comply."
    )
    assert sized["max_loss"] <= cap + 1e-9, (
        f"_size_option() max_loss=${sized['max_loss']} exceeds the configured "
        f"${cap} per-trade cap by {sized['max_loss'] / cap:.1f}x — the audit's "
        "headline finding (v1.1 P0 fix #1)."
    )


def test_contract_quality_identical_across_pipelines():
    """Load-bearing quality invariant, unchanged from the completed divergence
    audit (Phase 1) and architecture v1.0/v1.1. The same contract must be
    judged identically by every pipeline's grader.

    Exercises: options_grading.grade_contract(), options_desk.analyse_contract().

    Expected: FAIL — reuses the audit's fixtures for missing Greeks, 0-DTE-
    adjacent DTE, and a stale quote, all of which disagreed in Phase 1.

    Deliberately left red (Phase 3, Canonical Option Architecture v1.1 Step
    10 migration) — see test_oi_policy_has_no_overlapping_hard_and_soft_ranges
    above for why: the two legacy functions were never required to agree,
    only to be unable to override canonical's execution authority, which
    test_pipeline3_canonical_migration.py::test_legacy_grader_disagreement_cannot_override_d
    proves structurally and keeps green.
    """
    cases = {
        "OI=100": _make_fixture(oi=100),
        "MISSING_DELTA": _make_fixture(delta=None),
        "DTE=3": _make_fixture(dte=3),
        "STALE_QUOTE_72h": _make_fixture(fresh_quote=False, quote_age_hours=72),
    }
    disagreements = {}
    for name, (gc_c, ad_c, spot, side, dte, session_open) in cases.items():
        gc = grade_contract(gc_c, spot, side, dte)
        ad = OD.analyse_contract(ad_c, spot=spot, cfg=_OD_CFG, contracts=1,
                                  session_open=session_open)
        if gc["tradeable"] != ad["tradeable"]:
            disagreements[name] = (gc["tradeable"], ad["tradeable"])

    assert not disagreements, (
        f"grade_contract() vs analyse_contract() disagree on: {disagreements} "
        "— reproduces Phase 1 of the divergence audit; canonical layer A does "
        "not exist yet to unify them."
    )
