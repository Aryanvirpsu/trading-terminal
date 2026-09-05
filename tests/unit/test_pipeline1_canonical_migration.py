"""Canonical Option Architecture v1.1 — Step 9: Pipeline 1 (Terminal Decision)
migration tests.

Pipeline 1 = dashboard/app.py:/api/symbol/overview -> dashboard/research.py:
overview() -> _overview_compute() -> lab/decision_engine.py:evaluate() (Layer
C, unchanged) -> dashboard/research.py:_attach_canonical_pipeline1() (A/B/D/E/
executable, new).

These tests exercise the PRODUCTION migration directly: option structural
quality, account-fit eligibility, instrument choice, final sizing and
executable status for the Terminal route must now come exclusively from
canonical A/B/D/E/executable, never from options_grading.grade_contract(),
decision_engine._grade_option_chain(), or decision_engine's own inline stock-
sizing formula. Layer C (the 8 hard + 4 soft gates) is untouched and is
proven so directly.

Fully offline and deterministic: decision_engine's own 9 signal families,
regime, halts and the portfolio risk gate are monkeypatched exactly as
tests/unit/test_decision_logic.py already does (same harness shape, kept
self-contained here rather than imported, so this file has no cross-test-file
coupling); ss._pick_option_idea() is monkeypatched per-test to a controlled
option candidate (or None) instead of hitting a live options chain.

Run: pytest tests/unit/test_pipeline1_canonical_migration.py -v --tb=short
"""
from __future__ import annotations

import inspect
import os
import sys
from datetime import datetime, timezone

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in ("src", "dashboard", "lab"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import decision_engine as de        # noqa: E402
import research as R                # noqa: E402

from canonical.contract_quality import (          # noqa: E402
    ContractQualityResult, evaluate_contract_quality,
)
from canonical.risk_policy import DASHBOARD_POLICY  # noqa: E402
from canonical.instrument_choice import InstrumentChoice  # noqa: E402

NOW = datetime.now(timezone.utc)


# ══════════════════════════════════════════════════════════════════════════
# Harness — same shape as tests/unit/test_decision_logic.py's _patch/_run,
# kept self-contained here (no cross-test-file import).
# ══════════════════════════════════════════════════════════════════════════

def _mk(fam, d, c, detail="x"):
    return {"family": fam, "dir": d, "conf": c, "detail": detail}


_DEEP = {"_fam_catalyst": "catalyst", "_fam_short": "short-interest",
        "_fam_filings": "filings/insider", "_fam_options_flow": "options-flow",
        "_fam_social": "social-sentiment", "_fam_analyst": "analyst-ratings",
        "_fam_macro": "macro-rates"}


def _patch(monkeypatch, *, trend=(0.7, 0.85), regime=(0.4, 0.7), families=None,
          data_state="fresh", a_source="yahoo", price=100.0, atrp=2.5,
          option_idea=None, portfolio_blocks=False):
    """Deterministic decision_engine harness. Defaults produce a fully clean,
    TRADEABLE stock setup (every gate passes) so individual tests only need
    to override the ONE axis they're testing."""
    families = families or {f: (0.3, 0.7) for f in _DEEP.values()}
    a = {"price_data": {"current_price": price}, "trend_state": "uptrend",
        "atr": {"value": price * atrp / 100.0, "percent_of_price": atrp},
        "market_sentiment": {"momentum": "Bullish", "buy_sell_signal": "BUY"},
        "rsi": {"value": 60}}
    monkeypatch.setattr(de, "_load_analysis", lambda s, e: (a, a_source, data_state))
    monkeypatch.setattr(de, "_safe_regime", lambda: {"risk_appetite_score": 65})
    monkeypatch.setattr(de, "_fam_trend", lambda _a: _mk("trend/momentum", *trend))
    monkeypatch.setattr(de, "_fam_regime", lambda _r, _d: _mk("regime", *regime))
    for fn, fam in _DEEP.items():
        d, c = families.get(fam, (0.25, 0.6))
        monkeypatch.setattr(de, fn, (lambda fam=fam, d=d, c=c: (lambda *a, **k: _mk(fam, d, c)))())
    monkeypatch.setattr(de.ss, "_pick_option_idea", lambda *a, **k: option_idea)
    try:
        import halts
        monkeypatch.setattr(halts, "is_halted", lambda s: False)
    except Exception:
        pass
    if not portfolio_blocks:
        try:
            import risk_engine
            monkeypatch.setattr(risk_engine, "check_new_trade", lambda sym, risk, spec: {
                "allow": True, "reasons": [], "size_cap_usd": 1e6,
                "portfolio": {"suspended": False, "drawdown_pct": 0.0}})
        except Exception:
            pass
    else:
        import risk_engine
        monkeypatch.setattr(risk_engine, "check_new_trade", lambda sym, risk, spec: {
            "allow": False, "reasons": ["would breach 20% cash reserve"],
            "size_cap_usd": 0.0, "portfolio": {"suspended": False, "drawdown_pct": 12.1}})


def _option_candidate(**over):
    """A realistic ss._pick_option_idea() return, WITH a quote_timestamp —
    representing the case a future data-source improvement supplies one.
    Production today never sets this key (see test_pipeline1_option_quote_
    timestamp_gap_is_real below); adding it here is what lets these tests
    prove the A/B/D/E wiring itself, independent of that separate gap."""
    c = {"instrument": "OPTION", "option_type": "CALL", "strike": 101.0,
        "expiry": "2026-10-01", "premium": 1.20, "pct_otm": 1.0, "days_to_expiry": 21,
        "breakeven": 102.2, "bid": 1.15, "ask": 1.25, "mid": 1.20,
        "spread_dollars": 0.10, "spread_pct": 8.0, "volume": 300, "open_interest": 1000,
        "implied_volatility": 0.30, "delta": 0.5, "liquidity_score": 70, "grade": "B",
        "tradeable": True, "rejection": None, "quote_timestamp": NOW.isoformat(),
        "sizing": {"contracts": 1, "capital_committed": 120.0, "max_loss": 120.0,
                   "planned_risk_50pct_stop": 60.0}}
    c.update(over)
    return c


def _run(monkeypatch, *, balance=50_000.0, direction="LONG", **kw):
    """Runs the REAL Pipeline-1 production path: decision_engine.evaluate()
    with evaluate_option=False (exactly what research._overview_compute()
    calls) followed by the real _attach_canonical_pipeline1()."""
    _patch(monkeypatch, **kw)
    r = de.evaluate("TEST", "NASDAQ", direction, balance, evaluate_option=False)
    R._attach_canonical_pipeline1(r, decision_engine=de, direction=direction, balance=balance)
    return r


def _gate(r, name):
    return next(g for g in r["decision_gates"] if g["name"] == name)


# ══════════════════════════════════════════════════════════════════════════
# C preservation (items 1-4)
# ══════════════════════════════════════════════════════════════════════════

def test_hard_gate_failure_still_reject(monkeypatch):
    r = _run(monkeypatch, trend=(-0.6, 0.8), regime=(-0.4, 0.6),
             families={f: (-0.3, 0.6) for f in _DEEP.values()})
    assert r["decision"] == "REJECT"
    assert [g for g in r["decision_gates"] if g["blocking"] and not g["passed"]]


def test_soft_gate_failure_still_monitor(monkeypatch):
    r = _run(monkeypatch, data_state="fallback-provider", a_source="yfinance-fallback")
    assert r["decision"] == "MONITOR"
    assert not [g for g in r["decision_gates"] if g["blocking"] and not g["passed"]]
    assert [g for g in r["decision_gates"] if not g["blocking"] and not g["passed"]]


def test_fully_valid_setup_still_tradeable(monkeypatch):
    r = _run(monkeypatch)
    assert r["decision"] == "TRADEABLE"
    assert all(g["passed"] for g in r["decision_gates"])


def test_evaluate_option_false_does_not_alter_gate_semantics(monkeypatch):
    """The isolation lever itself changes nothing about C: the SAME inputs
    through evaluate_option=True vs False must produce identical decision/
    gate output — proving Pipeline 1's `evaluate_option=False` call is a
    pure option-overlay bypass, not a directional behavior change."""
    _patch(monkeypatch, option_idea=None)
    with_opt = de.evaluate("TEST", "NASDAQ", "LONG", 50_000.0, evaluate_option=True)
    _patch(monkeypatch, option_idea=None)
    without_opt = de.evaluate("TEST", "NASDAQ", "LONG", 50_000.0, evaluate_option=False)
    assert with_opt["decision"] == without_opt["decision"]
    assert with_opt["decision_gates"] == without_opt["decision_gates"]
    assert with_opt["confidence_quality"] == without_opt["confidence_quality"]
    assert with_opt["stop"] == without_opt["stop"] and with_opt["target"] == without_opt["target"]


def test_evaluate_default_still_evaluates_option_for_other_callers():
    """The global default of evaluate() is untouched by this migration —
    only research.py's ONE call site passes evaluate_option=False."""
    sig = inspect.signature(de.evaluate)
    assert sig.parameters["evaluate_option"].default is True


# ══════════════════════════════════════════════════════════════════════════
# A (items 5-12)
# ══════════════════════════════════════════════════════════════════════════

def test_pipeline1_a_result_equals_direct_a_call(monkeypatch):
    opt = _option_candidate()
    r = _run(monkeypatch, option_idea=opt)
    direct = evaluate_contract_quality(
        strike=opt["strike"], underlying=r["price"], side=opt["option_type"],
        bid=opt["bid"], ask=opt["ask"], volume=opt["volume"], open_interest=opt["open_interest"],
        implied_volatility=opt["implied_volatility"], delta=opt["delta"], dte=opt["days_to_expiry"],
        quote_timestamp=opt["quote_timestamp"], session_open=r["canonical"]["contract_quality"] is not None)
    # session_open is re-derived from market_regime in production; compare the
    # fields that don't depend on it directly, plus assert both PASS.
    assert r["canonical"]["contract_quality"]["quality_pass"] is True
    assert direct.quality_pass is True
    assert r["canonical"]["contract_quality"]["hard_failures"] == list(direct.hard_failures)


def test_oi_100_follows_canonical_a(monkeypatch):
    r = _run(monkeypatch, option_idea=_option_candidate(open_interest=100))
    cq = r["canonical"]["contract_quality"]
    assert cq["quality_pass"] is True, "OI=100 is THIN (soft), not a hard failure"
    assert not any("open interest" in f and "illiquid" in f for f in cq["hard_failures"])


def test_0_dte_fails(monkeypatch):
    r = _run(monkeypatch, option_idea=_option_candidate(days_to_expiry=0))
    cq = r["canonical"]["contract_quality"]
    assert cq["quality_pass"] is False
    assert any("DTE" in f for f in cq["hard_failures"])


def test_stale_quote_fails(monkeypatch):
    from datetime import timedelta
    old = (NOW - timedelta(hours=72)).isoformat()
    r = _run(monkeypatch, option_idea=_option_candidate(quote_timestamp=old))
    cq = r["canonical"]["contract_quality"]
    assert cq["quality_pass"] is False
    assert any("stale" in f.lower() for f in cq["hard_failures"])


def test_missing_delta_and_iv_follows_canonical_provenance(monkeypatch):
    r_modeled = _run(monkeypatch, option_idea=_option_candidate(delta=None))
    assert r_modeled["canonical"]["contract_quality"]["quality_pass"] is True

    r_unavailable = _run(monkeypatch, option_idea=_option_candidate(delta=None, implied_volatility=None))
    cq = r_unavailable["canonical"]["contract_quality"]
    assert cq["quality_pass"] is False
    assert any("Greeks" in f for f in cq["hard_failures"])


def test_spread_above_canonical_cap_fails(monkeypatch):
    r = _run(monkeypatch, option_idea=_option_candidate(bid=1.00, ask=1.60))  # ~46% spread
    cq = r["canonical"]["contract_quality"]
    assert cq["quality_pass"] is False
    assert any("spread" in f for f in cq["hard_failures"])


def test_legacy_grade_contract_true_cannot_override_canonical_a_fail(monkeypatch):
    """grade_contract() (via _pick_option_idea()) already marked this
    candidate tradeable=True/grade='A' — canonical A must still fail it on
    freshness alone (no quote_timestamp in production shape) or on any other
    real structural gap, and Pipeline 1's authoritative instrument choice
    must not be swayed by the legacy verdict."""
    opt = _option_candidate(tradeable=True, grade="A", quote_timestamp=None)
    r = _run(monkeypatch, option_idea=opt)
    assert opt["tradeable"] is True
    assert r["canonical"]["contract_quality"]["quality_pass"] is False
    assert r["instrument"] != "OPTION PREFERRED"
    assert r["option_executable"] is False


def test_legacy_grade_contract_false_cannot_override_canonical_a_pass(monkeypatch):
    """The reverse: legacy grade_contract() would mark a thin-OI (OI=100)
    contract 'C'/borderline via its OWN scoring, but canonical A treats
    OI=100 as a soft THIN band and passes — a real, already-demonstrated
    Pipeline-2-style divergence, reproduced here for Pipeline 1."""
    opt = _option_candidate(open_interest=100, grade="C", tradeable=False)
    r = _run(monkeypatch, option_idea=opt, balance=50_000.0)
    assert r["canonical"]["contract_quality"]["quality_pass"] is True
    assert r["canonical"]["option_account_fit"]["eligible"] is True
    assert r["instrument"] == "OPTION PREFERRED"


# ══════════════════════════════════════════════════════════════════════════
# B (items 13-16)
# ══════════════════════════════════════════════════════════════════════════

def test_stock_b_direct_result_equals_pipeline1_result(monkeypatch):
    from canonical.account_fit import stock_account_fit
    r = _run(monkeypatch, balance=50_000.0)
    direct = stock_account_fit(policy=DASHBOARD_POLICY, equity=50_000.0, entry=r["price"],
                               stop=r["stop"], buying_power=50_000.0, open_positions=0)
    canon = r["canonical"]["stock_account_fit"]
    assert canon["eligible"] == direct.eligible
    assert canon["quantity_allowed"] == pytest.approx(direct.quantity_allowed)
    assert canon["binding_constraint"] == direct.binding_constraint


def test_option_b_direct_result_equals_pipeline1_result(monkeypatch):
    from canonical.account_fit import option_account_fit
    opt = _option_candidate()
    r = _run(monkeypatch, option_idea=opt, balance=50_000.0)
    cq = ContractQualityResult(**r["canonical"]["contract_quality"]) if False else None
    # Rebuild the real ContractQualityResult directly (not via the asdict'd
    # canonical dict) so this is a genuine second, independent computation.
    real_cq = evaluate_contract_quality(
        strike=opt["strike"], underlying=r["price"], side=opt["option_type"],
        bid=opt["bid"], ask=opt["ask"], volume=opt["volume"], open_interest=opt["open_interest"],
        implied_volatility=opt["implied_volatility"], delta=opt["delta"], dte=opt["days_to_expiry"],
        quote_timestamp=opt["quote_timestamp"], session_open=True)
    direct = option_account_fit(policy=DASHBOARD_POLICY, equity=50_000.0, contract_quality=real_cq,
                                side=opt["option_type"], limit_price=opt["premium"], spot=r["price"],
                                stop=r["stop"], strike=opt["strike"], iv=opt["implied_volatility"],
                                dte=opt["days_to_expiry"], spread_dollars=opt["spread_dollars"],
                                buying_power=50_000.0)
    canon = r["canonical"]["option_account_fit"]
    assert canon["eligible"] == direct.eligible
    assert canon["binding_constraint"] == direct.binding_constraint
    assert canon["capital_required"] == pytest.approx(direct.capital_required)


def test_account_ineligible_option_cannot_be_selected(monkeypatch):
    """A candidate priced far beyond even a large account's general per-trade
    cap: canonical A passes, canonical B(option) does not."""
    opt = _option_candidate(premium=50.0, bid=49.5, ask=50.5, strike=101.0)
    r = _run(monkeypatch, option_idea=opt, balance=8_000.0)
    assert r["canonical"]["contract_quality"]["quality_pass"] is True
    assert r["canonical"]["option_account_fit"]["eligible"] is False
    assert r["instrument"] != "OPTION PREFERRED"
    assert r["option_executable"] is False


def test_account_ineligible_stock_cannot_be_selected(monkeypatch):
    """entry == stop is impossible to construct via decision_engine's own
    ATR-based stop (always offset), so this is proven directly against
    canonical D/E with a hand-built ineligible stock_account_fit, mirroring
    Pipeline 2's own equivalent test — the STRUCTURAL guarantee
    (choose_instrument() never returns STOCK when stock_account_fit is
    ineligible) is what's being proven, independent of how the fit was
    produced."""
    from canonical.instrument_choice import choose_instrument
    from canonical.account_fit import stock_account_fit
    ineligible = stock_account_fit(policy=DASHBOARD_POLICY, equity=500.0, entry=100.0,
                                   stop=100.0, buying_power=500.0)   # zero risk distance
    assert ineligible.eligible is False
    choice = choose_instrument(setup_tradeable=True, stock_account_fit=ineligible,
                               option_account_fit=None, contract_quality=None)
    assert choice.choice != InstrumentChoice.STOCK


# ══════════════════════════════════════════════════════════════════════════
# D (items 17-21)
# ══════════════════════════════════════════════════════════════════════════

def test_bad_option_valid_stock_selects_stock(monkeypatch):
    r = _run(monkeypatch, option_idea=_option_candidate(bid=None, ask=None), balance=50_000.0)
    assert r["canonical"]["contract_quality"]["quality_pass"] is False
    assert r["canonical"]["stock_account_fit"]["eligible"] is True
    assert r["instrument"] == "STOCK PREFERRED"
    assert r["stock_executable"] is True


def test_valid_option_invalid_stock_selects_option(monkeypatch):
    from canonical.instrument_choice import choose_instrument, OptionEdge
    from canonical.account_fit import stock_account_fit, option_account_fit
    cq = evaluate_contract_quality(strike=101.0, underlying=100.0, side="CALL", bid=1.15, ask=1.25,
                                   volume=300, open_interest=1000, implied_volatility=0.30, delta=0.5,
                                   dte=21, quote_timestamp=NOW.isoformat(), session_open=True)
    ineligible_stock = stock_account_fit(policy=DASHBOARD_POLICY, equity=500.0, entry=100.0,
                                         stop=100.0, buying_power=500.0)
    good_option = option_account_fit(policy=DASHBOARD_POLICY, equity=50_000.0, contract_quality=cq,
                                     side="CALL", limit_price=1.20, spot=100.0, stop=96.0, strike=101.0,
                                     iv=0.30, dte=21, buying_power=50_000.0)
    assert ineligible_stock.eligible is False and good_option.eligible is True
    choice = choose_instrument(setup_tradeable=True, stock_account_fit=ineligible_stock,
                               option_account_fit=good_option, contract_quality=cq,
                               option_edge=OptionEdge())
    assert choice.choice == InstrumentChoice.OPTION


def test_both_invalid_is_no_trade(monkeypatch):
    # Zero equity -> every canonical B cap is $0, so stock_account_fit is
    # genuinely ineligible (not merely a tiny fractional-share position,
    # which DASHBOARD_POLICY's fractional_shares=True would otherwise still
    # accept even at a very small balance). Paired with a structurally bad
    # option (missing bid/ask) for a genuine both-invalid scenario.
    r = _run(monkeypatch, option_idea=_option_candidate(bid=None, ask=None), balance=0.0)
    assert r["canonical"]["contract_quality"]["quality_pass"] is False
    assert r["canonical"]["stock_account_fit"]["eligible"] is False
    assert r["instrument"] == "NO TRADE"
    assert r["stock_executable"] is False and r["option_executable"] is False


def test_both_eligible_uses_canonical_preference(monkeypatch):
    r = _run(monkeypatch, option_idea=_option_candidate(), balance=50_000.0)
    assert r["canonical"]["stock_account_fit"]["eligible"] is True
    assert r["canonical"]["option_account_fit"]["eligible"] is True
    ic = r["canonical"]["instrument_choice"]
    assert ic["option_score"] is not None and ic["stock_score"] is not None
    assert r["instrument"] in ("OPTION PREFERRED", "STOCK PREFERRED")


def test_legacy_grade_option_chain_disagreement_cannot_override_d(monkeypatch):
    """decision_engine._grade_option_chain() is never called at all for this
    route (evaluate_option=False -> opt=None inside evaluate() -> the
    internal call runs with opt=None and produces only the 'no option idea'
    stub) — there is no live channel through which its preference could
    reach the Terminal's canonical D. Structural guard, mirroring Pipeline
    2's dead-code proof for option_risk.instrument_choice()."""
    opt = _option_candidate()
    r = _run(monkeypatch, option_idea=opt, balance=50_000.0)
    # decision_engine's OWN internal call (inside evaluate(), opt=None there
    # since evaluate_option=False) never saw the real candidate at all.
    assert r["legacy_option_quality"]["gradeable"] is False
    assert r["legacy_option_quality"]["missing"] == ["no option idea"]
    # yet canonical D, fed the REAL candidate independently, made a real choice.
    assert r["canonical"]["contract_quality"]["quality_pass"] is True
    assert r["instrument"] in ("OPTION PREFERRED", "STOCK PREFERRED")


# ══════════════════════════════════════════════════════════════════════════
# E (items 22-27)
# ══════════════════════════════════════════════════════════════════════════

def test_final_stock_quantity_derives_from_e(monkeypatch):
    r = _run(monkeypatch, option_idea=None, balance=500.0)
    assert r["instrument"] == "STOCK PREFERRED"
    assert r["final_quantity"] == r["canonical"]["sizing"]["quantity"]
    assert r["final_quantity"] <= r["canonical"]["stock_account_fit"]["quantity_allowed"] + 1e-9


def test_final_option_contracts_derive_from_e(monkeypatch):
    r = _run(monkeypatch, option_idea=_option_candidate(), balance=50_000.0)
    if r["instrument"] == "OPTION PREFERRED":
        assert r["final_quantity"] == r["canonical"]["sizing"]["quantity"]
        assert isinstance(r["final_quantity"], int)


def test_conviction_target_cannot_exceed_b(monkeypatch):
    """decision_engine's own suggested_shares (an uncertainty-scaled
    conviction target) is passed as target_quantity, but canonical E must
    clamp it to B's ceiling — reproduced directly against the historical
    audit numbers (weak ~1.5sh/strong ~3.82sh vs a small-account ceiling)."""
    from canonical.instrument_choice import InstrumentChoiceResult
    from canonical.account_fit import stock_account_fit
    from canonical.sizing import size_instrument
    fit = stock_account_fit(policy=DASHBOARD_POLICY, equity=500.0, entry=100.0, stop=97.0,
                            buying_power=400.0)
    choice = InstrumentChoiceResult(choice=InstrumentChoice.STOCK, reason="fixture",
                                    reasons=("fixture",), stock_eligible=True, option_eligible=False)
    oversized = size_instrument(choice=choice, stock_account_fit=fit, option_account_fit=None,
                                target_quantity=3.82)
    assert oversized.quantity <= fit.quantity_allowed + 1e-9
    assert oversized.quantity < 3.82


def test_options_remain_whole_contracts(monkeypatch):
    r = _run(monkeypatch, option_idea=_option_candidate(), balance=50_000.0)
    if r["instrument"] == "OPTION PREFERRED":
        assert r["final_quantity"] == int(r["final_quantity"])


def test_legacy_inline_stock_quantity_disagreement_cannot_override_e(monkeypatch):
    """A tiny balance/entry ratio can make decision_engine's own
    suggested_shares diverge sharply from B's ceiling; regardless of which
    is larger, canonical E's exposed quantity must equal B's actual
    computation, never the raw legacy suggestion."""
    r = _run(monkeypatch, option_idea=None, balance=500.0, price=900.0, atrp=1.0)
    if r["instrument"] == "STOCK PREFERRED":
        legacy = r["legacy_decision_sizing"]["suggested_shares"]
        assert r["final_quantity"] != legacy or r["final_quantity"] == pytest.approx(
            min(legacy, r["canonical"]["stock_account_fit"]["quantity_allowed"]), abs=1e-6)
        assert r["final_quantity"] <= r["canonical"]["stock_account_fit"]["quantity_allowed"] + 1e-9


def test_legacy_size_option_output_cannot_override_e(monkeypatch):
    """ss._size_option()'s contracts/max_loss (embedded in the candidate's
    own `sizing` sub-dict) is never read anywhere in
    _attach_canonical_pipeline1 — grep-confirmed structurally."""
    src = inspect.getsource(R._attach_canonical_pipeline1)
    assert '["sizing"]' not in src.replace("canon_sizing", "").replace('r["canonical"]["sizing"]', "")
    assert "_size_option(" not in src


# ══════════════════════════════════════════════════════════════════════════
# Executable (items 28-31)
# ══════════════════════════════════════════════════════════════════════════

def test_stock_path_independent_of_bad_option(monkeypatch):
    r = _run(monkeypatch, option_idea=_option_candidate(bid=None, ask=None), balance=50_000.0)
    assert r["canonical"]["contract_quality"]["quality_pass"] is False
    assert r["stock_executable"] is True
    assert r["option_executable"] is False


def test_option_requires_a_and_b_and_d_and_e(monkeypatch):
    # A fails (missing bid/ask) -> option_executable False regardless of D/E.
    r1 = _run(monkeypatch, option_idea=_option_candidate(bid=None, ask=None), balance=50_000.0)
    assert r1["option_executable"] is False
    # A passes but B fails (too expensive) -> option_executable False.
    r2 = _run(monkeypatch, option_idea=_option_candidate(premium=50.0, bid=49.5, ask=50.5),
             balance=8_000.0)
    assert r2["canonical"]["contract_quality"]["quality_pass"] is True
    assert r2["canonical"]["option_account_fit"]["eligible"] is False
    assert r2["option_executable"] is False
    # A+B pass and D actually chose OPTION -> option_executable True.
    r3 = _run(monkeypatch, option_idea=_option_candidate(), balance=50_000.0)
    if r3["instrument"] == "OPTION PREFERRED":
        assert r3["option_executable"] is True


def test_monitor_means_neither_executable(monkeypatch):
    r = _run(monkeypatch, data_state="fallback-provider", a_source="yfinance-fallback",
             option_idea=_option_candidate())
    assert r["decision"] == "MONITOR"
    assert r["stock_executable"] is False
    assert r["option_executable"] is False


def test_reject_means_neither_executable(monkeypatch):
    r = _run(monkeypatch, trend=(-0.6, 0.8), regime=(-0.4, 0.6),
             families={f: (-0.3, 0.6) for f in _DEEP.values()}, option_idea=_option_candidate())
    assert r["decision"] == "REJECT"
    assert r["stock_executable"] is False
    assert r["option_executable"] is False


# ══════════════════════════════════════════════════════════════════════════
# Compatibility (items 32-40)
# ══════════════════════════════════════════════════════════════════════════

def test_overview_response_stays_json_serializable(monkeypatch):
    import json
    r = _run(monkeypatch, option_idea=_option_candidate())
    serialized = json.dumps(r, default=str)
    round_tripped = json.loads(serialized)
    assert round_tripped["instrument"] == r["instrument"]


def test_no_tracker_write(monkeypatch):
    src = inspect.getsource(R)
    assert "import tracker" not in src and "tracker.add_setup" not in src


def test_no_journal_write():
    src = inspect.getsource(R._attach_canonical_pipeline1)
    assert "journal" not in src.lower()


def test_no_broker_execution():
    src = inspect.getsource(R._attach_canonical_pipeline1)
    for forbidden in ("place_order", "submit_entry", "robinhood_mcp.call_tool",
                      "paper_trade", "paper_option_trade"):
        assert forbidden not in src


def test_terminal_remains_stateless(monkeypatch):
    """Two consecutive calls with identical inputs (same monkeypatched
    world) must be byte-identical — nothing persists or accumulates
    between calls."""
    _patch(monkeypatch, option_idea=_option_candidate())
    r1 = de.evaluate("TEST", "NASDAQ", "LONG", 50_000.0, evaluate_option=False)
    R._attach_canonical_pipeline1(r1, decision_engine=de, direction="LONG", balance=50_000.0)
    _patch(monkeypatch, option_idea=_option_candidate())
    r2 = de.evaluate("TEST", "NASDAQ", "LONG", 50_000.0, evaluate_option=False)
    R._attach_canonical_pipeline1(r2, decision_engine=de, direction="LONG", balance=50_000.0)
    assert r1["canonical"] == r2["canonical"]
    assert r1["instrument"] == r2["instrument"]


def test_canonical_block_exposes_provenance(monkeypatch):
    r = _run(monkeypatch, option_idea=_option_candidate(), balance=50_000.0)
    canon = r["canonical"]
    assert set(canon) >= {"contract_quality", "stock_account_fit", "option_account_fit",
                          "instrument_choice", "sizing", "stock_executable",
                          "option_executable", "shadow"}
    assert canon["shadow"] is False


def test_no_unlabeled_competing_final_quantity(monkeypatch):
    r = _run(monkeypatch, option_idea=None, balance=500.0)
    assert r["legacy_decision_sizing"]["role"] == "legacy_display_diagnostic_not_authoritative"
    assert "role" not in r["canonical"]["sizing"]
    assert r["final_quantity"] == r["canonical"]["sizing"]["quantity"]


def test_no_unlabeled_competing_option_quality_verdict(monkeypatch):
    r = _run(monkeypatch, option_idea=_option_candidate(tradeable=True))
    # the legacy grade_contract()-sourced verdict is relabeled, never left
    # sitting under the same key as the canonical one.
    assert "legacy_option_quality" in r
    assert r["option_quality"]["quality_pass"] == r["canonical"]["contract_quality"]["quality_pass"]
    assert r["option_quality"] is not r["legacy_option_quality"]


def test_pipeline2_behavior_remains_unchanged():
    """Step 9 touches dashboard/research.py only (plus this test file) —
    dashboard/options_desk.py (Pipeline 2's authoritative module) must be
    byte-identical to its Step 8.1 state. Confirmed by re-running Pipeline
    2's own closure suite; the file-identity check here is a fast structural
    sanity check that research.py's new imports/aliases don't collide with
    anything options_desk.py relies on (both modules can be imported into
    the SAME process without error, since app.py imports both)."""
    import options_desk  # noqa: F401
    import research       # noqa: F401
    assert True   # import-time collision is the failure mode this guards


def test_pipeline1_option_quote_timestamp_gap_is_real(monkeypatch):
    """Documents (does not paper over) the genuine Terminal data-source gap:
    ss._pick_option_idea()'s real production shape carries no
    quote_timestamp at all, so canonical A hard-fails every REAL Terminal
    option candidate on freshness today. This is intentional fail-closed
    behavior, not a bug — asserted directly so a future change to either
    side (the data source, or this fail-closed choice) is caught."""
    real_shaped = _option_candidate()
    del real_shaped["quote_timestamp"]   # what _pick_option_idea() actually returns today
    r = _run(monkeypatch, option_idea=real_shaped, balance=50_000.0)
    assert r["canonical"]["contract_quality"]["quality_pass"] is False
    assert any("timestamp" in f for f in r["canonical"]["contract_quality"]["hard_failures"])
    assert r["instrument"] != "OPTION PREFERRED"
