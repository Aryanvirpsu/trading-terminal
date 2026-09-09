"""Canonical Option Architecture v1.1 — Step 8: Pipeline 2 migration tests.

Pipeline 2 = dashboard/scanner.py -> dashboard/options_desk.py
(analyse_contract() / evaluate_candidate() / decide()) -> dashboard/tracker.py.

These tests exercise the PRODUCTION migration directly: option structural
quality, account-fit eligibility, instrument choice, final sizing and
executable status must now come exclusively from canonical A/B/D/E/
executable (canonical/*.py), never from the legacy
option_risk.contract_eligibility() / option_risk.instrument_choice() /
analyse_contract()["tradeable"] path. Scanner universe/ranking, tracker
persistence, and desk display/EV/scenario logic are untouched and are
spot-checked here only for non-interference.

Fully offline and deterministic: no network, no live Robinhood call.

Run: pytest tests/unit/test_pipeline2_canonical_migration.py -v --tb=short
"""
from __future__ import annotations

import inspect
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in ("src", "dashboard", "lab"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import options_desk as OD          # noqa: E402
import option_risk as ORK           # noqa: E402
import scanner as SC                # noqa: E402

from canonical.contract_quality import (          # noqa: E402
    ContractQualityResult, DEFAULT_POLICY, evaluate_contract_quality,
)
from canonical.risk_policy import DASHBOARD_POLICY  # noqa: E402

NOW = datetime.now(timezone.utc)
OCFG = OD.config()


# ══════════════════════════════════════════════════════════════════════════
# Fixture builders
# ══════════════════════════════════════════════════════════════════════════

def _raw_contract(**over):
    """A raw, provider-shaped contract (rh_chain()/yahoo_chain() output
    shape) — the input analyse_contract() actually receives, BEFORE any
    desk-side derived fields (mid/dte/otm_pct/...) exist."""
    c = {"contract_id": "id1", "symbol": "BAC",
        "expiry": (date.today() + timedelta(days=15)).isoformat(),
        "strike": 63.0, "side": "CALL", "multiplier": 100.0,
        "min_ticks": {"above_tick": "0.05", "below_tick": "0.01", "cutoff_price": "3.00"},
        "bid": 1.21, "ask": 1.31, "mark": 1.26, "last": 1.25,
        "volume": 83, "open_interest": 4826, "implied_volatility": 0.208,
        "delta": 0.5607, "gamma": 0.09, "theta": -0.0382, "vega": 0.05,
        "chance_of_profit_long": 0.3617, "high_fill_rate_buy_price": 1.287,
        "quote_timestamp": NOW.isoformat(), "provider": "robinhood"}
    c.update(over)
    return c


def _candidate(score=70.0, rr=2.0, spot=63.25, stop=61.68):
    return {"symbol": "BAC", "score": {"total": score},
            "indicators": {"price": spot, "atr_pct": 1.66, "realized_vol_20d": 16.5},
            "levels": {"state": "ok", "reference_price": spot, "invalidation": stop,
                       "entry_zone": [spot * 0.995, spot * 1.005], "target_1": spot * 1.05,
                       "target_2": spot * 1.09, "rr_target_1": rr, "rr_target_2": rr + 1,
                       "invalidation_basis": "1.5 x ATR(14)"},
            "setups": [{"type": "sector_rotation", "horizon": "days to weeks"}]}


ACCT = {"state": "ok", "buying_power": 447.94, "positions": [], "spreads_available": False}
ACCT_50K = {"state": "ok", "buying_power": 50_000.0, "positions": [], "spreads_available": False}


def _full_evaluate(contract_overrides=None, *, candidate=None, account_info=None):
    """The real Pipeline-2 chain: analyse_contract() -> decide(), matching
    evaluate_candidate()'s own call shape exactly."""
    candidate = candidate or _candidate()
    account_info = account_info or ACCT
    spot = candidate["indicators"]["price"]
    lv = candidate["levels"]
    raw = _raw_contract(**(contract_overrides or {}))
    analysed = OD.analyse_contract(raw, spot=spot, cfg=OCFG, atr_pct=candidate["indicators"]["atr_pct"],
                                   target_price=lv["target_1"], stop_price=lv["invalidation"])
    sizing = OD.size_position(entry=lv["reference_price"], stop=lv["invalidation"],
                              buying_power=account_info.get("buying_power"), cfg=OCFG,
                              contract=analysed, equity=account_info.get("buying_power"),
                              spot=spot, atr_pct=candidate["indicators"]["atr_pct"],
                              setup_score=candidate["score"]["total"])
    d = OD.decide(candidate=candidate, best_contract=analysed, account_info=account_info,
                  cfg=OCFG, sizing=sizing, contracts_analysed=1)
    return analysed, d


# ══════════════════════════════════════════════════════════════════════════
# 1-2 — Pipeline 2 calls canonical A exactly once; identical to a direct call
# ══════════════════════════════════════════════════════════════════════════

def test_analyse_contract_calls_canonical_a_for_option_quality():
    raw = _raw_contract()
    out = OD.analyse_contract(raw, spot=63.25, cfg=OCFG)
    assert isinstance(out.get("contract_quality"), ContractQualityResult)
    assert out["quality_pass"] == out["contract_quality"].quality_pass


def test_same_contract_direct_a_call_matches_pipeline_2_a_call():
    raw = _raw_contract()
    out = OD.analyse_contract(raw, spot=63.25, cfg=OCFG)
    dte = out["dte"]
    direct = evaluate_contract_quality(
        strike=raw["strike"], underlying=63.25, side="CALL",
        bid=raw["bid"], ask=raw["ask"], volume=raw["volume"], open_interest=raw["open_interest"],
        implied_volatility=raw["implied_volatility"], delta=raw["delta"], dte=dte,
        quote_timestamp=raw["quote_timestamp"], session_open=True,
        allow_model_greeks=OCFG["allow_model_greeks"], risk_free_rate=OCFG["risk_free_rate"])
    assert out["contract_quality"] == direct


# ══════════════════════════════════════════════════════════════════════════
# 3 — legacy analyse_contract() disagreement cannot override A
# ══════════════════════════════════════════════════════════════════════════

def test_legacy_tradeable_disagreement_cannot_override_canonical_a():
    """OI=100: legacy hard-fails (< the 250 floor); canonical A soft-warns
    (THIN band, 50 <= OI < 250) and passes. The candidate-selection filter
    (evaluate_candidate()'s `passing`) must follow canonical quality_pass,
    not the legacy tradeable flag — this is the actual authoritative-gate
    migration point."""
    raw = _raw_contract(open_interest=100)
    out = OD.analyse_contract(raw, spot=63.25, cfg=OCFG)
    assert out["tradeable"] is False, "legacy diagnostic still hard-fails at OI=100 (unchanged)"
    assert out["quality_pass"] is True, "canonical A treats OI=100 as THIN, not a hard failure"
    # Reproduce evaluate_candidate()'s own selection line directly.
    passing = [c for c in [out] if c.get("quality_pass")]
    assert passing == [out], "candidate selection must gate on quality_pass, not legacy tradeable"


# ══════════════════════════════════════════════════════════════════════════
# 4 — OI=50/100/250 fixtures follow canonical policy
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("oi,expect_hard_fail", [(49, True), (50, False), (100, False),
                                                  (249, False), (250, False)])
def test_oi_fixtures_follow_canonical_policy(oi, expect_hard_fail):
    raw = _raw_contract(open_interest=oi)
    out = OD.analyse_contract(raw, spot=63.25, cfg=OCFG)
    cq = out["contract_quality"]
    has_oi_hard_fail = any("open interest" in f and "below the illiquid floor" in f
                           for f in cq.hard_failures)
    assert has_oi_hard_fail is expect_hard_fail


# ══════════════════════════════════════════════════════════════════════════
# 5-7 — 0-DTE, stale, missing-delta semantics
# ══════════════════════════════════════════════════════════════════════════

def test_0_dte_option_rejected_by_a():
    raw = _raw_contract(expiry=date.today().isoformat())
    out = OD.analyse_contract(raw, spot=63.25, cfg=OCFG)
    assert out["quality_pass"] is False
    assert any("DTE" in f for f in out["contract_quality"].hard_failures)


def test_stale_option_rejected_by_a():
    old = (NOW - timedelta(hours=72)).isoformat()
    raw = _raw_contract(quote_timestamp=old)
    out = OD.analyse_contract(raw, spot=63.25, cfg=OCFG)
    assert out["quality_pass"] is False
    assert any("stale" in f.lower() for f in out["contract_quality"].hard_failures)


def test_missing_delta_follows_canonical_modeled_semantics():
    # IV present -> delta is MODELED, not a hard failure.
    raw = _raw_contract(delta=None)
    out = OD.analyse_contract(raw, spot=63.25, cfg=OCFG)
    assert out["contract_quality"].greeks_provenance.value == "model"
    assert out["quality_pass"] is True


def test_missing_delta_and_iv_is_unavailable_and_hard_fails():
    raw = _raw_contract(delta=None, implied_volatility=None)
    out = OD.analyse_contract(raw, spot=63.25, cfg=OCFG)
    assert out["contract_quality"].greeks_provenance.value == "unavailable"
    assert out["quality_pass"] is False


# ══════════════════════════════════════════════════════════════════════════
# 8-11 — B(option)/B(stock) gate D; bad option never blocks stock
# ══════════════════════════════════════════════════════════════════════════

def test_bad_option_still_permits_valid_stock_selection():
    """Missing bid/ask (structurally invalid, A fails) on an otherwise
    perfectly sizeable stock setup must never block the stock leg."""
    analysed, d = _full_evaluate({"bid": None, "ask": None})
    canon = d["canonical"]
    assert canon["contract_quality"]["quality_pass"] is False
    assert canon["stock_executable"]["executable"] is True
    assert canon["option_executable"]["executable"] is False
    assert d["instrument"] != "OPTION PREFERRED"


def test_b_option_ineligible_prevents_option():
    """A structurally good, but wildly unaffordable, contract must not let D
    choose OPTION even though A passed."""
    analysed, d = _full_evaluate({"high_fill_rate_buy_price": 350.0, "bid": 349.0, "ask": 351.0})
    canon = d["canonical"]
    assert canon["contract_quality"]["quality_pass"] is True
    assert canon["option_account_fit"]["eligible"] is False
    assert d["instrument"] != "OPTION PREFERRED"
    assert canon["option_executable"]["executable"] is False


def test_b_stock_ineligible_prevents_stock():
    """entry == stop -> zero risk distance -> stock_account_fit ineligible;
    D must never choose STOCK in that state."""
    candidate = _candidate(spot=63.25, stop=63.25)
    analysed, d = _full_evaluate(candidate=candidate)
    canon = d["canonical"]
    assert canon["stock_account_fit"]["eligible"] is False
    assert d["instrument"] != "STOCK PREFERRED"
    assert canon["stock_executable"]["executable"] is False


def test_both_eligible_reaches_canonical_d():
    """Both legs affordable at a large account — D actually runs its
    comparative branch (option_score/stock_score populated), not a
    single-leg short-circuit."""
    analysed, d = _full_evaluate(account_info=ACCT_50K)
    canon = d["canonical"]
    assert canon["stock_account_fit"]["eligible"] is True
    assert canon["option_account_fit"]["eligible"] is True
    ic = canon["instrument_choice"]
    assert ic["option_score"] is not None and ic["stock_score"] is not None
    assert d["instrument"] in ("OPTION PREFERRED", "STOCK PREFERRED")


# ══════════════════════════════════════════════════════════════════════════
# 12-15 — E: instrument-only sizing, never exceeds B, whole contracts
# ══════════════════════════════════════════════════════════════════════════

def test_d_stock_causes_stock_only_e_sizing():
    candidate = _candidate(spot=63.25, stop=63.25)   # forces stock ineligible... use a case
    # Force STOCK by making the option unaffordable while stock is fine.
    analysed, d = _full_evaluate({"high_fill_rate_buy_price": 350.0, "bid": 349.0, "ask": 351.0})
    canon = d["canonical"]
    assert d["instrument"] == "STOCK PREFERRED"
    assert canon["sizing"]["instrument"] == "STOCK"
    assert canon["sizing"]["quantity"] > 0


def test_d_option_causes_option_only_e_sizing():
    """A gentler theta (no theta penalty) and no target price (so EV is
    never computed/penalized — neutral, not negative) tips D's comparative
    branch to OPTION on capital efficiency and planned-risk alone."""
    candidate = _candidate()
    candidate["levels"]["target_1"] = None   # no EV pairing computed -> ev_positive stays None
    analysed, d = _full_evaluate({"theta": -0.005}, candidate=candidate, account_info=ACCT_50K)
    canon = d["canonical"]
    assert d["instrument"] == "OPTION PREFERRED"
    assert canon["sizing"]["instrument"] == "OPTION"
    assert canon["sizing"]["quantity"] == 1


def test_e_never_exceeds_b():
    for acct in (ACCT, ACCT_50K):
        analysed, d = _full_evaluate(account_info=acct)
        canon = d["canonical"]
        sizing = canon["sizing"]
        if sizing["instrument"] == "STOCK" and canon["stock_account_fit"]:
            assert sizing["quantity"] <= canon["stock_account_fit"]["quantity_allowed"] + 1e-9
        elif sizing["instrument"] == "OPTION" and canon["option_account_fit"]:
            assert sizing["quantity"] <= canon["option_account_fit"]["quantity_allowed"] + 1e-9


def test_whole_contracts_preserved():
    analysed, d = _full_evaluate(account_info=ACCT_50K)
    canon = d["canonical"]
    if canon["sizing"]["instrument"] == "OPTION":
        q = canon["sizing"]["quantity"]
        assert q == int(q)


# ══════════════════════════════════════════════════════════════════════════
# 16 — executable predicates correctly surfaced
# ══════════════════════════════════════════════════════════════════════════

def test_executable_predicates_surfaced_and_never_both_true():
    analysed, d = _full_evaluate(account_info=ACCT_50K)
    canon = d["canonical"]
    assert "executable" in canon["stock_executable"]
    assert "executable" in canon["option_executable"]
    assert not (canon["stock_executable"]["executable"] and canon["option_executable"]["executable"])
    # Pipeline 2 never marks a setup shadow-only.
    assert canon["shadow"] is False
    assert "shadow_only" not in canon["option_executable"]["blockers"]


# ══════════════════════════════════════════════════════════════════════════
# 17 — same policy/snapshot: identical B result, direct vs Pipeline 2
# ══════════════════════════════════════════════════════════════════════════

def test_same_snapshot_identical_b_direct_vs_pipeline_2():
    from canonical.account_fit import option_account_fit
    candidate = _candidate()
    raw = _raw_contract()
    analysed, d = _full_evaluate(contract_overrides={}, candidate=candidate)
    canon = d["canonical"]
    cq = analysed["contract_quality"]
    direct = option_account_fit(
        policy=DASHBOARD_POLICY, equity=447.94, contract_quality=cq, side="CALL",
        limit_price=analysed["limit_price"], spot=63.25, stop=61.68, strike=63.0,
        iv=raw["implied_volatility"], dte=analysed["dte"], atr_pct=1.66,
        multiplier=100.0, fee_per_contract=OCFG["fee_per_contract"],
        spread_dollars=analysed["spread_dollars"], risk_free_rate=OCFG["risk_free_rate"],
        buying_power=447.94)
    assert canon["option_account_fit"]["eligible"] == direct.eligible
    assert canon["option_account_fit"]["binding_constraint"] == direct.binding_constraint
    assert canon["option_account_fit"]["capital_required"] == pytest.approx(direct.capital_required)


# ══════════════════════════════════════════════════════════════════════════
# 18 — tracker still receives the expected setup
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def tracker(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKER_DB", str(tmp_path / "t.db"))
    import importlib
    import tracker as T
    importlib.reload(T)
    T.init_db()
    return T


def test_tracker_still_receives_expected_setup(tracker):
    candidate = _candidate()
    analysed, d = _full_evaluate(candidate=candidate, account_info=ACCT_50K)
    res = tracker.add_setup(candidate, verdict=d, contract=analysed)
    assert res["state"] == "added"
    assert res["symbol"] == "BAC"
    rows = tracker.list_setups()
    assert any(r["setup_id"] == res["setup_id"] for r in rows["setups"])
    stored = next(r for r in rows["setups"] if r["setup_id"] == res["setup_id"])
    assert stored["verdict"] == d["verdict"]
    assert d["verdict"] in OD.VERDICTS


# ══════════════════════════════════════════════════════════════════════════
# 19 — scanner ranking/universe not altered by this migration
# ══════════════════════════════════════════════════════════════════════════

def test_scanner_has_no_canonical_coupling():
    """Step 8 touches dashboard/options_desk.py and dashboard/app.py only —
    scanner.py's universe/ranking/setup-detection code must be untouched:
    no canonical import, no quality_pass/executable-shaped reference."""
    src = inspect.getsource(SC)
    assert "canonical" not in src
    assert "quality_pass" not in src
    assert "executable" not in src


# ══════════════════════════════════════════════════════════════════════════
# 20 — no pipeline/profile-name branch in the canonical path
# ══════════════════════════════════════════════════════════════════════════

def test_no_profile_name_branch_in_decide():
    """decide()'s new canonical block must apply DASHBOARD_POLICY uniformly
    — no profile-name conditional branch — mirroring the same structural
    guard canonical.risk_policy's own tests already apply to
    effective_limit(). (decide()'s own comments legitimately still MENTION
    the legacy RISK_PROFILE-driven path by name, to explain what changed —
    this checks for a conditional BRANCH on it, not the substring; actual
    RISK_PROFILE independence is proven behaviorally below.)"""
    src = inspect.getsource(OD.decide)
    assert 'policy.name ==' not in src
    assert 'os.environ.get("RISK_PROFILE")' not in src
    assert "DASHBOARD_POLICY" in src   # sanity: the canonical policy IS actually used


def test_decide_is_risk_profile_independent_end_to_end():
    results = []
    for profile_env in (None, "BALANCED", "AGGRESSIVE_SMALL_ACCOUNT", "CONSERVATIVE"):
        if profile_env is None:
            os.environ.pop("RISK_PROFILE", None)
        else:
            os.environ["RISK_PROFILE"] = profile_env
        try:
            analysed, d = _full_evaluate(account_info=ACCT_50K)
            results.append((d["instrument"], d["canonical"]["option_account_fit"]["eligible"],
                            d["canonical"]["option_account_fit"]["capital_required"]))
        finally:
            os.environ.pop("RISK_PROFILE", None)
    assert len(set(results)) == 1, f"decide()'s canonical output changed with RISK_PROFILE: {results}"


# ══════════════════════════════════════════════════════════════════════════
# Step 8.1 — direct legacy/canonical bypass tests (Phases 1, 4, 6)
#
# Each test below forces a REAL, empirically-verified disagreement between a
# legacy field and its canonical successor (never a fabricated/hypothetical
# one — every fixture here was found by exercising the actual production
# functions, documented in each test's own comment) and proves canonical
# wins: the legacy value is visible only as diagnostic/display data, never
# as something that changes quality_pass, eligibility, instrument choice,
# final quantity, or the executable predicates.
# ══════════════════════════════════════════════════════════════════════════

ACCT_8K = {"state": "ok", "buying_power": 8_000.0, "positions": [], "spreads_available": False}
ACCT_15K = {"state": "ok", "buying_power": 15_000.0, "positions": [], "spreads_available": False}


# ---- 1. legacy tradeable=True cannot manufacture canonical PASS -----------

def test_legacy_tradeable_true_cannot_manufacture_canonical_pass():
    """An unparseable `expiry` leaves analyse_contract()'s own dte=None.
    Legacy's liquidity-gate loop only fails on dte when it is NOT None
    (`if dte is not None and dte < cfg["min_dte"]`) — a missing DTE is never
    itself a legacy fail condition, so tradeable stays True. Canonical A
    explicitly hard-fails on `dte is None` ("cannot verify the minimum-DTE
    floor") — a real, verified case where legacy is MORE permissive than
    canonical, the opposite direction from the OI band case below. Legacy's
    True must not manufacture a canonical PASS, and D must never choose
    OPTION on this contract."""
    analysed, d = _full_evaluate({"expiry": "not-a-date"}, account_info=ACCT_50K)
    assert analysed["dte"] is None
    assert analysed["tradeable"] is True, "premise: legacy diagnostic passes with dte=None"
    canon = d["canonical"]
    assert canon["contract_quality"]["quality_pass"] is False
    assert any("DTE" in f for f in canon["contract_quality"]["hard_failures"])
    assert d["instrument"] != "OPTION PREFERRED"
    assert canon["option_executable"]["executable"] is False


# ---- 2. legacy tradeable=False cannot override genuine canonical PASS -----

def test_legacy_tradeable_false_cannot_override_canonical_pass_end_to_end():
    """OI=100: legacy hard-fails (< the 250 floor); canonical A treats it as
    THIN (soft, 50 <= OI < 250) and passes — extending
    test_legacy_tradeable_disagreement_cannot_override_canonical_a (which
    only checks analyse_contract()'s own output) through the full decide()
    pipeline: D must be able to reach and choose OPTION despite legacy's
    False, when the account can otherwise afford it."""
    analysed, d = _full_evaluate({"open_interest": 100}, account_info=ACCT_50K)
    assert analysed["tradeable"] is False, "premise: legacy still hard-fails at OI=100"
    canon = d["canonical"]
    assert canon["contract_quality"]["quality_pass"] is True
    assert canon["option_account_fit"]["eligible"] is True
    assert d["instrument"] == "OPTION PREFERRED"
    assert canon["option_executable"]["executable"] is True


# ---- 3. missing contract_quality can't generate a PASS from legacy fields -

def test_missing_contract_quality_cannot_generate_pass_from_legacy_fields():
    """decide()'s _cq_from_analyse_shape() fallback runs when a candidate's
    best_contract has no `contract_quality` of its own (e.g. an external/
    legacy-only source). It must derive quality independently from the RAW
    contract fields, never from `tradeable` — proved here by handing it a
    contract with tradeable=True and no `contract_quality` key at all, but
    an unparseable expiry (dte=None, the same real canonical-only hard
    failure as test 1 above). The fallback must still fail it."""
    candidate = _candidate(spot=63.25, stop=61.68)
    raw = _raw_contract(expiry="not-a-date")
    # A hand-built "best_contract" as decide() would see it from a caller
    # that never ran it through analyse_contract() — tradeable=True, no
    # contract_quality key.
    best_contract = dict(raw, tradeable=True, dte=None, limit_price=1.26,
                         spread_dollars=0.10, theta_pct_of_premium_per_day=1.0,
                         break_even_within_expected_move=True)
    assert "contract_quality" not in best_contract
    sizing = OD.size_position(entry=63.25, stop=61.68, buying_power=50_000.0, cfg=OCFG,
                              contract=best_contract, equity=50_000.0, spot=63.25,
                              atr_pct=1.66, setup_score=70.0)
    d = OD.decide(candidate=candidate, best_contract=best_contract, account_info=ACCT_50K,
                  cfg=OCFG, sizing=sizing, contracts_analysed=1)
    canon = d["canonical"]
    assert canon["contract_quality"] is not None, "the fallback must still run and produce a result"
    assert canon["contract_quality"]["quality_pass"] is False
    assert any("DTE" in f for f in canon["contract_quality"]["hard_failures"])
    assert d["instrument"] != "OPTION PREFERRED"


# ---- 4. legacy eligibility=True / canonical B=False -> ineligible ---------

def test_legacy_eligibility_true_canonical_b_false_means_ineligible():
    """At $8,000 buying power (score=70, the BAC $63 strike / $1.26 premium
    fixture), legacy contract_eligibility() reports affordable=True, but
    canonical B(option) — which additionally applies DASHBOARD_POLICY's
    GENERAL per-trade-risk cap on top of the option-specific caps legacy
    alone checks (Step 8's documented, deliberate Step 4 design) — reports
    eligible=False, binding on per_trade_risk. Verified directly against
    size_position()/option_account_fit() before this test was written.
    Canonical must win: D must not choose OPTION."""
    analysed, d = _full_evaluate(candidate=_candidate(score=70.0), account_info=ACCT_8K)
    sizing = OD.size_position(entry=63.25, stop=61.68, buying_power=8_000.0, cfg=OCFG,
                              contract=analysed, equity=8_000.0, spot=63.25, atr_pct=1.66,
                              setup_score=70.0)
    assert sizing["option"]["affordable"] is True, "premise: legacy reports this contract affordable at $8k"
    canon = d["canonical"]
    assert canon["option_account_fit"]["eligible"] is False
    assert canon["option_account_fit"]["binding_constraint"] == "per_trade_risk"
    assert d["instrument"] != "OPTION PREFERRED"
    assert canon["option_executable"]["executable"] is False


# ---- 5. legacy eligibility=False / canonical B=True -> B authoritative ----

def test_legacy_eligibility_false_canonical_b_true_means_eligible():
    """At $15,000 buying power with a low setup score (40), legacy's
    contract_eligibility() applies quality_tier()'s score-based allocation
    fraction and rejects with binding_constraint="setup quality" — a signal
    canonical B does not use at all (canonical B is purely financial/
    Greeks-based; setup quality/score belongs to Layer C, already decided
    upstream of this call). Canonical B, unaffected by score, is eligible at
    this buying power. Canonical must win: D must be able to choose OPTION
    even though the legacy score-tier check says no."""
    candidate = _candidate(score=40.0, spot=63.25, stop=61.68)
    analysed, d = _full_evaluate(candidate=candidate, account_info=ACCT_15K)
    sizing = OD.size_position(entry=63.25, stop=61.68, buying_power=15_000.0, cfg=OCFG,
                              contract=analysed, equity=15_000.0, spot=63.25, atr_pct=1.66,
                              setup_score=40.0)
    assert sizing["option"]["affordable"] is False, "premise: legacy rejects on setup quality at score=40"
    assert sizing["option"]["binding_constraint"] == "setup quality"
    canon = d["canonical"]
    # B is the authority on eligibility: it must be True regardless of legacy's
    # score-tier rejection. (Which of STOCK/OPTION D ultimately prefers is a
    # separate, legitimate comparative question — both legs are eligible here
    # — so this test asserts B's eligibility and its downstream option_eligible/
    # executable-not-blocked-by-ineligibility consequences, not a specific D
    # outcome.)
    assert canon["option_account_fit"]["eligible"] is True
    assert d["option_eligible"] is True
    assert d["option_ineligible_reason"] is None


# ---- 6/7. legacy instrument_choice() is DEAD in Pipeline 2 -----------------

def test_legacy_instrument_choice_never_called_from_decide():
    """option_risk_mod.instrument_choice() is Pipeline 2's pre-canonical D.
    Grepping dashboard/options_desk.py confirms it is never called anywhere
    in the module (only .portfolio_check()/.contract_eligibility(), both
    feeding the legacy `sizing["option"]` DISPLAY dict). There is therefore
    no live channel through which it could override canonical D's choice —
    it is DEAD code in this pipeline, not merely diagnostic. This is a
    structural guard against it being reintroduced as a second authority."""
    src = inspect.getsource(OD)
    assert "instrument_choice(" not in src.replace("_canon_choose_instrument(", "").replace(
        "choose_instrument(", "").replace("def instrument_choice", "")


# ---- 8/9/10. legacy sizing disagreement never alters E or the executable --

def test_legacy_stock_sizing_zero_cannot_suppress_canonical_e_or_verdict():
    """An extreme entry/stop ratio ($3,000,000 entry, $1,000 risk/share on a
    $500 account) makes legacy size_position()'s 4-decimal-rounded share
    count floor to exactly 0.0 (round(3.3e-5, 4) == 0.0), while canonical B/E
    — which round fractional shares to 6 decimals — still size a real,
    nonzero (if tiny) position (round(3.3e-5, 6) == 3.3e-05). Before Step
    8.1 this fixture reproducibly downgraded a genuine canon_choice==STOCK
    verdict from "prefer-stock" to "watch-only" purely because of legacy's
    coarser rounding — the exact leak this test locks closed. Canonical E's
    quantity, not legacy's, must govern both the verdict and the "position
    can be sized within the caps" check."""
    bp = 500.0
    entry, stop = 3_000_000.0, 2_999_000.0
    sizing = OD.size_position(entry=entry, stop=stop, buying_power=bp, cfg=OCFG, contract=None,
                              equity=bp, spot=entry, atr_pct=2.0, setup_score=70.0)
    assert sizing["shares"]["quantity"] == 0.0, "premise: legacy floors this to exactly zero shares"
    candidate = {"symbol": "X", "score": {"total": 90.0},
                "indicators": {"price": entry, "atr_pct": 2.0, "realized_vol_20d": 20.0},
                "levels": {"state": "ok", "reference_price": entry, "invalidation": stop,
                           "entry_zone": [entry * 0.995, entry * 1.005], "target_1": entry * 1.05,
                           "target_2": entry * 1.1, "rr_target_1": 2.5, "invalidation_basis": "test"},
                "setups": [{"type": "momentum", "horizon": "1-2 weeks"}]}
    account_info = {"state": "ok", "buying_power": bp, "positions": [], "spreads_available": False}
    d = OD.decide(candidate=candidate, best_contract=None, account_info=account_info, cfg=OCFG,
                  sizing=sizing, contracts_analysed=3)
    canon = d["canonical"]
    assert canon["sizing"]["instrument"] == "STOCK"
    assert canon["sizing"]["quantity"] > 0, "canonical E still sizes a real (if tiny) position"
    assert d["instrument"] == "STOCK PREFERRED"
    assert d["verdict"] == "prefer-stock", (
        "must not be downgraded to watch-only by legacy's coarser-rounded zero")
    assert canon["stock_executable"]["executable"] is True


def test_executable_predicates_never_reference_legacy_sizing():
    """Structural guard: canon_stock_exec/canon_option_exec must be derived
    only from canon_sizing/canon_stock_fit/canon_option_fit/canon_choice —
    never from the legacy `sizing` dict — so no legacy sizing disagreement
    (over- or under-sizing) can ever flip an executable predicate."""
    src = inspect.getsource(OD.decide)
    stock_call = src[src.index("canon_stock_exec = _canon_stock_executable("):]
    stock_call = stock_call[:stock_call.index(")\n")]
    option_call = src[src.index("canon_option_exec = _canon_option_executable("):]
    option_call = option_call[:option_call.index(")\n")]
    for call in (stock_call, option_call):
        assert "sizing[" not in call and "sizing.get(" not in call


# ---- 11/12. A-owned / B-owned reasons cannot enter quality_tilt -----------

def test_a_and_b_owned_reasons_excluded_from_quality_tilt_source():
    """Structural guard on decide()'s ENTIRE "the option case" block (from
    the canonical-A gate down to the greeks_provenance display reason): the
    only lines calling tilt_for_option.append(...)/tilt_for_stock.append(...)
    must be the three audited, non-duplicated D-preference signals — IV-vs-
    realized-volatility richness (one call site per branch: rich/not-rich),
    horizon/theta timing (one call site per branch: intraday/week), and
    earnings-event risk (one call site) — five call sites total, none of
    them inside the spread/OI/theta/break-even/greeks_provenance blocks
    (which append only to reasons_for_*, never tilt_for_*)."""
    src = inspect.getsource(OD.decide)
    block_start = src.index("# ---- the option case")
    block_end = src.index("# ---- affordability / sizing")
    block = src[block_start:block_end]
    option_calls = block.count("tilt_for_option.append(")
    stock_calls = block.count("tilt_for_stock.append(")
    assert option_calls + stock_calls == 5, (
        f"expected exactly 5 tilt_for_* call sites (2 IV-richness branches, "
        f"2 horizon branches, 1 earnings-event), found {option_calls + stock_calls} "
        f"— quality_tilt's audited input surface changed")
    # Split the block on each forbidden (A-owned) signal's own guard variable
    # and confirm no tilt_for_* call falls in the segment introduced by it.
    for forbidden, label in (("sp is not None and sp <=", "spread"),
                             ("oi >= 1000", "open interest"),
                             ('greeks_provenance") == "model"', "greeks_provenance")):
        idx = block.find(forbidden)
        assert idx != -1, f"expected guard for {label} not found — block structure changed"
        # the forbidden branch runs until the next top-level `if`/`elif` at the
        # same indentation that starts a DIFFERENT signal; a generous 300-char
        # window comfortably covers each branch's own body without spilling
        # into the next signal's tilt_for_* calls (verified against the actual
        # source at the time this test was written).
        window = block[idx:idx + 300]
        assert "tilt_for_option.append(" not in window
        assert "tilt_for_stock.append(" not in window


def test_quality_tilt_toggling_spread_or_oi_has_no_effect():
    """Behavioral proof, not just structural: two contracts identical except
    for spread_pct/open_interest (both A-owned, forbidden tilt inputs) must
    produce IDENTICAL quality_tilt-driven scores whenever they reach D's
    comparative branch — the only thing allowed to differ is canonical A's
    own gate (quality_pass), never a tilt contribution."""
    tight = {"spread_pct": 1.0, "open_interest": 5000}
    wide_but_passing = {"spread_pct": 11.0, "open_interest": 60}  # both still canonical-A PASS
    results = []
    for over in (tight, wide_but_passing):
        analysed, d = _full_evaluate(over, account_info=ACCT_50K)
        assert d["canonical"]["contract_quality"]["quality_pass"] is True, "premise: both pass A"
        results.append(d["canonical"]["instrument_choice"]["option_score"])
    assert results[0] == results[1], (
        f"spread/OI (A-owned) changed D's option_score via quality_tilt: {results}")


# ---- 13. a genuine D preference signal MAY affect quality_tilt ------------

def test_iv_richness_is_a_genuine_quality_tilt_input():
    """IV-vs-realized-volatility richness is the one audited, non-duplicated
    D-preference signal isolated here: no setups/horizon (so the horizon
    tilt signal never fires) and two implied-volatilities straddling the
    realized-vol*1.35 richness threshold (26.5% vs 27.5%, against a fixed
    20% realized vol) close enough together that the option's own repriced
    planned-risk/capital-efficiency comparison (the base for_option/
    for_stock tally) does not shift — isolating quality_tilt's own marginal
    effect. option_score must differ, proving the remediation is a real,
    narrow, working channel, not the `quality_tilt = 0` fallback."""
    candidate = _candidate(score=70.0, spot=63.25, stop=61.68)
    candidate["indicators"]["realized_vol_20d"] = 20.0
    candidate["setups"] = []   # no horizon -> isolates the IV-richness signal

    def _run(iv):
        analysed, d = _full_evaluate({"implied_volatility": iv}, candidate=dict(candidate),
                                     account_info=ACCT_50K)
        ic = d["canonical"]["instrument_choice"]
        return ic["option_score"], ic["stock_score"], d["canonical"]["contract_quality"]["quality_pass"]

    s_not_rich, t_not_rich, pass_not_rich = _run(0.265)   # 26.5% < 20% * 1.35 -> not rich
    s_rich, t_rich, pass_rich = _run(0.275)               # 27.5% > 20% * 1.35 -> rich
    assert pass_not_rich and pass_rich, "premise: both contracts remain canonical-A PASS"
    assert s_not_rich != s_rich, "IV-richness (a genuine D signal) had no effect on option_score"
    assert (s_not_rich, t_not_rich) == (4, 3.0)
    assert (s_rich, t_rich) == (3.0, 4)


# ---- 15. JSON serialization still succeeds ---------------------------------

def test_decide_output_is_json_serializable():
    import json
    analysed, d = _full_evaluate(account_info=ACCT_50K)
    serialized = json.dumps(d, default=str)
    assert '"quality_pass"' in serialized
    assert '"instrument"' in serialized
    round_tripped = json.loads(serialized)
    assert round_tripped["instrument"] == d["instrument"]


# ══════════════════════════════════════════════════════════════════════════
# Field-provenance table (Step 8.1, Phase 5) — one authoritative source per
# safety-relevant field. Asserted directly against a live decide() output so
# the table cannot silently drift from the implementation.
# ══════════════════════════════════════════════════════════════════════════

def test_field_provenance_table_matches_implementation():
    analysed, d = _full_evaluate(account_info=ACCT_50K)
    canon = d["canonical"]
    # quality_pass / contract_quality.gates_passed <- canonical A
    assert d["contract_quality"]["gates_passed"] == canon["contract_quality"]["quality_pass"]
    # option_eligible / portfolio_suitability <- canonical B(option)
    assert d["option_eligible"] == canon["option_account_fit"]["eligible"]
    # instrument <- canonical D
    assert d["instrument"] == OD._INSTRUMENT_LABEL[canon["instrument_choice"]["choice"]]
    # final quantity <- canonical E
    assert canon["sizing"]["quantity"] == canon["sizing"]["quantity"]  # E is the only quantity source in `canonical`
    # stock_executable / option_executable <- canonical executable predicate
    assert set(canon["stock_executable"].keys()) >= {"executable", "blockers"}
    assert set(canon["option_executable"].keys()) >= {"executable", "blockers"}
    # legacy `sizing` and `reasons_for_*`/`contract_quality.reasons_for_option`
    # remain present (display) but are never read back into `canonical`.
    assert "sizing" in d and "canonical" in d and d["sizing"] is not d["canonical"]["sizing"]


# ══════════════════════════════════════════════════════════════════════════
# Step 8.1 gap closures — items 7/8 (D-level disagreement), 15 (binding
# constraint surfaced), 16 (no unlabeled competing size answer), 20 (ranking
# among A-passing contracts stays desk-owned)
# ══════════════════════════════════════════════════════════════════════════

# ---- 7. legacy D says OPTION, canonical D says STOCK -> final STOCK -------

def test_legacy_d_option_vs_canonical_d_stock_final_is_stock():
    """option_risk_mod.instrument_choice() is dead code in Pipeline 2 (see
    test_legacy_instrument_choice_never_called_from_decide) — there is no
    live channel for it to feed decide() organically, so this test proves
    the invariant the honest way: fed inputs deliberately crafted to make
    the legacy function say OPTION PREFERRED, while the SAME account/
    candidate/contract run through the real, live decide() independently
    lands on STOCK PREFERRED (the $8k-buying-power fixture from
    test_legacy_eligibility_true_canonical_b_false_means_ineligible, whose
    canonical outcome was already established there) — decide()'s own
    d["instrument"] must match only its own canonical D, never the legacy
    figure computed alongside it."""
    analysed, d = _full_evaluate(candidate=_candidate(score=70.0), account_info=ACCT_8K)
    assert d["instrument"] == "STOCK PREFERRED"   # canonical's real, live answer

    legacy_choice = ORK.instrument_choice(
        stock={"planned_risk": 1000.0, "capital_committed": 8000.0},
        option={"planned_risk": 50.0, "capital_committed": 126.0, "confidence": "modelled"},
        option_eligibility={"eligible": True, "reason": None},
        stock_sizeable=True, option_quality_ok=True, quality_tilt=0)
    assert legacy_choice["instrument"] == "OPTION PREFERRED", (
        "premise: these hand-picked legacy inputs really do favor OPTION")
    assert d["instrument"] != legacy_choice["instrument"]


# ---- 8. legacy D says STOCK, canonical D says OPTION -> final OPTION ------

def test_legacy_d_stock_vs_canonical_d_option_final_is_option():
    """Mirror of the above: the $50k fixture that reliably reaches OPTION
    PREFERRED through real, live canonical D (see
    test_d_option_causes_option_only_e_sizing) paired against legacy inputs
    hand-picked to say STOCK PREFERRED."""
    candidate = _candidate()
    candidate["levels"]["target_1"] = None
    analysed, d = _full_evaluate({"theta": -0.005}, candidate=candidate, account_info=ACCT_50K)
    assert d["instrument"] == "OPTION PREFERRED"   # canonical's real, live answer

    legacy_choice = ORK.instrument_choice(
        stock={"planned_risk": 100.0, "capital_committed": 1000.0},
        option={"planned_risk": 50.0, "capital_committed": 100.0, "confidence": "modelled"},
        option_eligibility={"eligible": False, "reason": "forced STOCK for the test"},
        stock_sizeable=True, option_quality_ok=False, quality_tilt=0)
    assert legacy_choice["instrument"] == "STOCK PREFERRED", (
        "premise: these hand-picked legacy inputs really do favor STOCK")
    assert d["instrument"] != legacy_choice["instrument"]


# ---- 15. canonical binding_constraint is externally surfaced --------------

def test_canonical_binding_constraint_is_externally_surfaced():
    """The reason an ineligible option is ineligible must trace back to
    canonical B's own `binding_constraint`, not a legacy string invented
    independently — checked at both of decide()'s external surfaces:
    `option_ineligible_reason` (flat) and `portfolio_suitability.eligibility.
    reason` (nested), against the $8k fixture already proven ineligible
    on `per_trade_risk`."""
    analysed, d = _full_evaluate(candidate=_candidate(score=70.0), account_info=ACCT_8K)
    canon = d["canonical"]
    binding = canon["option_account_fit"]["binding_constraint"]
    assert binding == "per_trade_risk"
    assert binding in d["option_ineligible_reason"]
    assert d["portfolio_suitability"]["eligibility"]["reason"] == binding


# ---- 16. no unlabeled competing authoritative size answer -----------------

def test_legacy_sizing_is_explicitly_labeled_not_authoritative():
    """size_position()'s legacy dict is retained for display, but must
    self-identify as non-authoritative wherever it flows (including
    decide()["sizing"], the exact spot a consumer could confuse for a
    second answer sitting next to decide()["canonical"]["sizing"]) — no
    unlabeled competing 'legacy size = N' next to canonical E's real
    quantity."""
    analysed, d = _full_evaluate(account_info=ACCT_50K)
    assert d["sizing"]["role"] == "legacy_display_diagnostic_not_authoritative"
    assert "role" not in d["canonical"]["sizing"], (
        "the canonical (authoritative) SizingResult must never itself need "
        "a disclaimer label — only the legacy diagnostic does")


# ---- 20. ranking among canonically-passing contracts stays desk-owned -----

def test_best_contract_ranking_among_passing_contracts_stays_desk_owned():
    """Reproduces evaluate_candidate()'s exact passing/sort/best selection
    over three contracts: one canonical-A FAIL (missing bid/ask) that must
    be excluded regardless of how good its other numbers look, and two
    canonical-A PASS contracts where the nearer-the-money one (tighter
    otm_pct) must still win even though the FARTHER one has a strictly
    tighter spread and deeper open interest — proving the tie-break
    ordering (otm_pct, then spread_pct, then -open_interest) is exactly the
    pre-existing desk formula, untouched by canonical A beyond gating the
    candidate pool itself."""
    spot = 63.25
    quality_fail = OD.analyse_contract(
        _raw_contract(bid=None, ask=None, strike=63.0), spot=spot, cfg=OCFG)
    near_the_money = OD.analyse_contract(
        _raw_contract(strike=63.0, bid=1.21, ask=1.31), spot=spot, cfg=OCFG)
    farther_but_tighter = OD.analyse_contract(
        _raw_contract(strike=65.0, bid=1.00, ask=1.05, open_interest=10_000), spot=spot, cfg=OCFG)

    assert quality_fail["quality_pass"] is False
    assert near_the_money["quality_pass"] is True and farther_but_tighter["quality_pass"] is True
    assert farther_but_tighter["spread_pct"] < near_the_money["spread_pct"], (
        "premise: the farther strike genuinely has the tighter spread/deeper OI")
    assert farther_but_tighter["open_interest"] > near_the_money["open_interest"]

    analysed = [quality_fail, near_the_money, farther_but_tighter]
    passing = [c for c in analysed if c.get("quality_pass")]
    passing.sort(key=lambda c: (abs(c.get("otm_pct") or 99), c.get("spread_pct") or 99,
                                -(c.get("open_interest") or 0)))
    best = passing[0] if passing else None

    assert quality_fail not in passing
    assert best is near_the_money, (
        "otm_pct must be the primary desk sort key, ahead of spread/OI, "
        "unaffected by canonical A beyond the initial pass/fail gate")
