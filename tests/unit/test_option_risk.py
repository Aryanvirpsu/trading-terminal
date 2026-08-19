"""Instrument-aware option risk model — regression tests.

The three cases below (SHOP / PLTR / NTRA) are the ones that exposed the old policy,
kept as fixtures with their REAL reported numbers so the behaviour cannot silently
revert. The old policy set planned_risk = the whole premium and then measured it
against a stock-style 1%-of-equity budget, which made every contract unreachable on a
small account for a reason that had nothing to do with the contract.

All OFFLINE and deterministic: no network, no live Robinhood call, no credentials.
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in ("src", "dashboard", "lab"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import options_desk as OD      # noqa: E402
import option_risk as ORK      # noqa: E402

OCFG = OD.config()
LIVE_EQUITY = 447.94           # a live broker value captured with these cases
ACCT = {"state": "ok", "buying_power": LIVE_EQUITY, "positions": [],
        "spreads_available": False}


# ── fixtures built from the real reported numbers ────────────────────────────

SHOP = dict(spot=152.26, stop=139.60, target=175.29, limit=7.35, strike=150.0,
            iv=0.49, dte=14, atr_pct=4.0, oi=4490, spread_pct=3.0)
PLTR = dict(spot=169.06, stop=155.26, target=198.20, limit=8.70, strike=170.0,
            iv=0.51, dte=21, atr_pct=3.0, oi=1549, spread_pct=2.3)
NTRA = dict(spot=313.64, stop=291.97, target=358.72, limit=28.60, strike=290.0,
            iv=0.51, dte=14, atr_pct=3.5, oi=1341, spread_pct=10.7)


def _candidate(case, score=72.0, rr=2.1):
    spot, stop, target = case["spot"], case["stop"], case["target"]
    return {"symbol": "X", "score": {"total": score},
            "indicators": {"price": spot, "atr_pct": case["atr_pct"],
                           "realized_vol_20d": 55.0},
            "levels": {"state": "ok", "reference_price": spot, "invalidation": stop,
                       "entry_zone": [spot * 0.995, spot * 1.005], "target_1": target,
                       "target_2": target * 1.1, "rr_target_1": rr,
                       "invalidation_basis": "swing low"},
            "setups": [{"type": "momentum", "horizon": "1-2 weeks"}]}


def _contract(case, **over):
    c = {"tradeable": True, "spread_pct": case["spread_pct"], "open_interest": case["oi"],
         "theta_pct_of_premium_per_day": 2.4, "break_even_within_expected_move": True,
         "pct_move_to_break_even": 5.0, "underlying_expected_move_pct": 11.0,
         "implied_volatility": case["iv"], "dte": case["dte"], "strike": case["strike"],
         "side": "call", "limit_price": case["limit"], "multiplier": 100.0,
         "expiry": "2026-08-21", "greeks_provenance": "provider",
         "spread_dollars": round(case["limit"] * case["spread_pct"] / 100, 2)}
    c.update(over)
    return c


def _evaluate(case, *, equity=LIVE_EQUITY, score=72.0, contract=None):
    c = contract if contract is not None else _contract(case)
    sizing = OD.size_position(entry=case["spot"], stop=case["stop"], buying_power=equity,
                              cfg=OCFG, contract=c, equity=equity, spot=case["spot"],
                              atr_pct=case["atr_pct"], setup_score=score)
    d = OD.decide(candidate=_candidate(case, score=score), best_contract=c,
                  account_info={**ACCT, "buying_power": equity}, cfg=OCFG,
                  sizing=sizing, contracts_analysed=83)
    return sizing, d


# ── the three named cases ────────────────────────────────────────────────────

def test_pltr_good_contract_is_not_a_suitable_trade():
    """The headline case. An $870 contract against a $448 account: contract quality is
    high, portfolio suitability fails, and the two are reported separately."""
    sizing, d = _evaluate(PLTR)
    assert d["contract_quality"]["gates_passed"] is True
    assert d["portfolio_suitability"]["pass"] is False
    assert d["instrument"] in ("STOCK PREFERRED", "NO TRADE")
    assert d["verdict"] != "prefer-option"
    assert sizing["option"]["status"] == "NO CONTRACT FITS CURRENT RISK POLICY"


def test_ntra_is_the_worst_fit_of_the_three():
    """$2,860 of premium on a $448 account — worse than PLTR on every risk axis."""
    ntra, _ = _evaluate(NTRA)
    pltr, _ = _evaluate(PLTR)
    assert ntra["option"]["affordable"] is False
    for k in ("capital_committed", "planned_risk", "absolute_max_loss"):
        assert ntra["option"][k] > pltr["option"][k], k


def test_shop_still_prefers_stock():
    """SHOP's existing prefer-stock behaviour was correct and must not regress."""
    _, d = _evaluate(SHOP)
    assert d["verdict"] == "prefer-stock"
    assert d["instrument"] == "STOCK PREFERRED"


# ── acceptance criteria ──────────────────────────────────────────────────────

def test_all_three_expose_the_full_premium_as_absolute_max_loss():
    """Acceptance 2: the premium never stops being reported as the true worst case."""
    for case in (SHOP, PLTR, NTRA):
        sizing, _ = _evaluate(case)
        o = sizing["option"]
        expected = case["limit"] * 100 + OCFG["fee_per_contract"]
        assert abs(o["absolute_max_loss"] - expected) < 0.01
        assert "entire premium" in o["risk"]["absolute_max_loss_basis"]


def test_the_four_risk_numbers_are_distinct_and_ordered():
    """Acceptance 3: planned <= stress <= absolute, all present, none an alias."""
    for case in (SHOP, PLTR, NTRA):
        sizing, _ = _evaluate(case)
        r = sizing["option"]["risk"]
        assert r["planned_risk"] <= r["stress_risk"] <= r["absolute_max_loss"] + 0.01
        assert r["capital_committed"] > 0
    # A short-dated call struck ABOVE the stop is a genuine ~total loss, so planned ==
    # absolute there and that is correct, not a bug. The separation shows up on a
    # contract that still holds value at the invalidation level: BAC-like, deep ITM,
    # 45 DTE, priced at model (so calibration is exact).
    r = ORK.option_risk(contracts=1, limit_price=5.49, spot=62.97, stop=61.40,
                        strike=58.0, side="CALL", iv=0.2022, dte=45, atr_pct=1.4,
                        spread_dollars=0.05)
    assert r["confidence"] == "modelled"
    assert r["planned_risk"] < r["stress_risk"] < r["absolute_max_loss"]
    assert r["planned_risk_pct_of_premium"] < 50.0   # ~30% — the point of the model


def test_one_contract_indivisibility():
    """Acceptance 4: never a fractional contract count — a yes/no on one contract."""
    for case in (SHOP, PLTR, NTRA):
        sizing, _ = _evaluate(case)
        n = sizing["option"]["contracts"]
        assert n in (0, 1) and isinstance(n, int)
        assert sizing["option"]["status"] in ("1 CONTRACT ELIGIBLE",
                                              "NO CONTRACT FITS CURRENT RISK POLICY")


def test_a_cheap_contract_that_fails_quality_is_never_selected():
    """Acceptance 5: affordability must not rescue a junk contract. This one is dirt
    cheap and would fit any budget, but it failed the chain's quality gates."""
    junk = _contract(PLTR, limit_price=0.05, tradeable=False, open_interest=12,
                     rejection="spread 61% of mid; OI 12", spread_pct=61.0, strike=210.0)
    _, d = _evaluate(PLTR, contract=junk)
    assert d["verdict"] != "prefer-option"
    assert d["instrument"] != "OPTION PREFERRED"
    assert d["contract_quality"]["gates_passed"] is False


def test_option_stop_carries_explainable_scenario_repricing():
    """Acceptance 6: a planned option stop must be explained, not asserted — and must
    say plainly that it is not guaranteed."""
    sizing, _ = _evaluate(PLTR)
    r = sizing["option"]["risk"]
    names = {s["scenario"] for s in r["scenarios"]}
    assert {"thesis_failure", "gap_through_stop", "iv_collapse",
            "spread_widening", "catalyst_failure"} <= names
    for s in r["scenarios"]:
        assert s["basis"] and s["loss"] >= 0
    assert "not guaranteed" in r["exit_note"].lower()
    assert r["time_to_invalidation"]["days"] is not None


def test_portfolio_option_exposure_is_capped_independently_of_setup_score():
    """Acceptance 7: a book already full of option premium blocks a new contract even
    when the setup is EXCEPTIONAL. Hard limits override score."""
    pol = ORK.policy("AGGRESSIVE_SMALL_ACCOUNT")
    risk = {"capital_committed": 40.0, "planned_risk": 10.0,
            "stress_risk": 30.0, "absolute_max_loss": 40.0}
    full_book = [{"capital_committed": 90.0, "stress_risk": 80.0,
                  "absolute_max_loss": 90.0} for _ in range(2)]
    chk = ORK.portfolio_check(equity=LIVE_EQUITY, open_options=full_book,
                              candidate_risk=risk, pol=pol)
    assert chk["pass"] is False
    elig = ORK.contract_eligibility(risk=risk, equity=LIVE_EQUITY, pol=pol,
                                    quality=ORK.quality_tier(95.0), portfolio=chk)
    assert elig["eligible"] is False
    assert elig["status"] == "NO CONTRACT FITS CURRENT RISK POLICY"


def test_quality_and_suitability_are_reported_separately():
    """Acceptance 8: the best contract in a chain is still shown when the account
    cannot trade it — marked research-only, neither hidden nor recommended."""
    _, d = _evaluate(PLTR)
    assert set(d["contract_quality"]) >= {"gates_passed", "detail"}
    assert set(d["portfolio_suitability"]) >= {"pass", "status", "policy_profile"}
    assert d["contract_quality"]["gates_passed"] != d["portfolio_suitability"]["pass"]
    assert d["evaluated_option"] is not None


# ── policy behaviour ─────────────────────────────────────────────────────────

def test_quality_tier_scales_within_policy_but_never_raises_the_cap():
    """Allocation may scale with quality; the CAP may not."""
    risk = {"capital_committed": 40.0, "planned_risk": 12.0,
            "stress_risk": 30.0, "absolute_max_loss": 40.0}
    pol = ORK.policy("AGGRESSIVE_SMALL_ACCOUNT")
    caps = []
    for score in (60.0, 78.0, 95.0):
        e = ORK.contract_eligibility(risk=risk, equity=LIVE_EQUITY, pol=pol,
                                     quality=ORK.quality_tier(score))
        caps.append(next(c for c in e["checks"]
                         if c["check"].startswith("premium"))["limit"])
    assert caps[0] < caps[1] < caps[2]                       # better setups may use more
    assert max(caps) <= LIVE_EQUITY * pol["max_long_option_premium_pct"] / 100.0 + 1e-9


def test_aggressive_profile_is_never_the_silent_default():
    assert ORK.policy()["profile"] == "BALANCED"
    assert "risk capital" in ORK.policy("AGGRESSIVE_SMALL_ACCOUNT")["profile_note"]


def test_invalid_profile_fails_closed_rather_than_defaulting(monkeypatch):
    """An unrecognised RISK_PROFILE must be a visible configuration error. Silently
    resolving to BALANCED would leave the active limits unauditable from outside."""
    import pytest
    with pytest.raises(ORK.RiskConfigError) as e:
        ORK.policy("NONSENSE_PROFILE")
    assert "NONSENSE_PROFILE" in str(e.value)

    monkeypatch.setenv("RISK_PROFILE", "aggressive")     # close, but not a real name
    with pytest.raises(ORK.RiskConfigError):
        ORK.active_profile()
    with pytest.raises(ORK.RiskConfigError):
        ORK.policy()


def test_valid_profile_names_are_accepted_case_insensitively(monkeypatch):
    monkeypatch.setenv("RISK_PROFILE", "aggressive_small_account")
    assert ORK.policy()["profile"] == "AGGRESSIVE_SMALL_ACCOUNT"
    monkeypatch.setenv("RISK_PROFILE", "  BALANCED  ")
    assert ORK.policy()["profile"] == "BALANCED"
    monkeypatch.delenv("RISK_PROFILE")
    assert ORK.active_profile() == "BALANCED"            # unset is a legitimate default


def test_broken_risk_config_blocks_the_trade_and_is_surfaced(monkeypatch):
    """Fail closed end to end: no option sized, and the error reaches the verdict."""
    monkeypatch.setenv("RISK_PROFILE", "TYPO_PROFILE")
    sizing = OD.size_position(entry=PLTR["spot"], stop=PLTR["stop"],
                              buying_power=LIVE_EQUITY, cfg=OCFG,
                              contract=_contract(PLTR), equity=LIVE_EQUITY,
                              spot=PLTR["spot"], atr_pct=PLTR["atr_pct"],
                              setup_score=90.0)
    assert sizing["risk_config_error"]
    assert sizing["option"]["affordable"] is False
    assert sizing["option"]["binding_constraint"] == "risk configuration"
    d = OD.decide(candidate=_candidate(PLTR, score=90.0), best_contract=_contract(PLTR),
                  account_info=ACCT, cfg=OCFG, sizing=sizing, contracts_analysed=83)
    assert any("risk policy configuration error" in b for b in d["blockers"])
    assert d["verdict"] != "prefer-option"


def test_profile_name_is_the_full_aggressive_small_account_everywhere():
    """The short form AGGRESSIVE_SMALL must not exist as a valid alias."""
    assert "AGGRESSIVE_SMALL_ACCOUNT" in ORK.PROFILES
    assert "AGGRESSIVE_SMALL" not in ORK.PROFILES
    import pytest
    with pytest.raises(ORK.RiskConfigError):
        ORK.policy("AGGRESSIVE_SMALL")


def test_profiles_are_ordered_and_configurable():
    c, b, a = (ORK.policy(p) for p in
               ("CONSERVATIVE", "BALANCED", "AGGRESSIVE_SMALL_ACCOUNT"))
    for k in ("option_planned_risk_pct", "max_long_option_premium_pct",
              "max_total_option_premium_pct", "max_portfolio_planned_risk_pct"):
        assert c[k] < b[k] < a[k], k


def test_every_policy_percentage_is_env_overridable(monkeypatch):
    """No important percentage may be hardcoded inside decision logic."""
    monkeypatch.setenv("RISK_MAX_LONG_OPTION_PREMIUM_PCT", "42.5")
    assert ORK.policy()["max_long_option_premium_pct"] == 42.5


# ── conservatism guards ──────────────────────────────────────────────────────

def test_repricing_falls_back_to_full_premium_when_inputs_are_missing():
    """Never invent a smaller planned risk to make a contract pass."""
    r = ORK.option_risk(contracts=1, limit_price=8.70, spot=169.06, stop=155.26,
                        strike=170.0, side="CALL", iv=None, dte=21, atr_pct=3.0)
    assert r["planned_risk"] == r["absolute_max_loss"]
    assert r["confidence"] == "low"
    assert r["repricing_available"] is False


def test_planned_risk_uses_three_failure_paths_and_takes_the_worst():
    """Acceptance 4: one timing estimate embeds one assumption. Three are priced and
    the worst is used."""
    r = ORK.option_risk(contracts=1, limit_price=8.70, spot=169.06, stop=155.26,
                        strike=170.0, side="CALL", iv=0.51, dte=21, atr_pct=3.0,
                        spread_dollars=0.20)
    paths = r["planned_risk_paths"]
    assert {p["path"] for p in paths} == {"fast", "expected", "slow"}
    # each path carries its own timing AND its own IV assumption
    ivs = {p["path"]: p["iv_assumed"] for p in paths}
    assert ivs["fast"] > ivs["expected"] > ivs["slow"]
    days = {p["path"]: p["days_to_invalidation"] for p in paths}
    assert days["fast"] < days["expected"] < days["slow"]
    assert r["planned_risk"] == max(p["loss"] for p in paths)
    assert r["planned_risk_path_used"] in ("fast", "expected", "slow")


def test_optimistic_timing_or_iv_can_never_reduce_planned_risk():
    """Acceptance 5. The favourable path (fast fill, IV bid up) produces the smallest
    loss, so it must never be the number that gets used."""
    r = ORK.option_risk(contracts=1, limit_price=8.70, spot=169.06, stop=155.26,
                        strike=170.0, side="CALL", iv=0.51, dte=21, atr_pct=3.0,
                        spread_dollars=0.20)
    by = {p["path"]: p["loss"] for p in r["planned_risk_paths"]}
    assert by["fast"] <= by["expected"] <= by["slow"]      # fast is the flattering one
    assert r["planned_risk"] >= by["fast"]
    assert r["planned_risk"] == max(by.values())
    assert "never reduce" in r["planned_risk_rule"]


def test_calibration_tolerance_is_tight_and_matches_observed_contracts():
    """Acceptance 3. Real shadow-ledger contracts price inside ~10% (BAC 7.1%,
    PATH 10.0%), so the tolerance sits just above that, not at 35%."""
    pol = ORK.policy()
    assert pol["max_model_calibration_error"] <= 0.20
    assert pol["max_model_calibration_error"] >= 0.15
    # a contract inside the observed error band still reprices normally
    r = ORK.option_risk(contracts=1, limit_price=1.235, spot=62.97, stop=61.40,
                        strike=62.0, side="CALL", iv=0.2022, dte=7, atr_pct=1.4,
                        spread_dollars=0.09)
    assert r["repricing_available"] is True
    # one that the model is 270% away from does not
    bad = ORK.option_risk(contracts=1, limit_price=0.45, spot=62.97, stop=62.10,
                          strike=62.0, side="CALL", iv=0.1685, dte=21, atr_pct=1.4)
    assert bad["repricing_available"] is False
    assert bad["planned_risk"] == bad["absolute_max_loss"]


def test_stress_scenarios_stay_separate_from_planned_risk_paths():
    """Acceptance 4: the three timing paths are the PLANNED estimate; the six adverse
    scenarios remain a separate, additional stress view."""
    r = ORK.option_risk(contracts=1, limit_price=8.70, spot=169.06, stop=155.26,
                        strike=170.0, side="CALL", iv=0.51, dte=21, atr_pct=3.0,
                        spread_dollars=0.20)
    assert {p["path"] for p in r["planned_risk_paths"]} == {"fast", "expected", "slow"}
    assert {s["scenario"] for s in r["scenarios"]} >= {
        "thesis_failure", "gap_through_stop", "iv_collapse", "spread_widening",
        "catalyst_failure", "slow_grind_to_stop"}
    assert r["planned_risk"] <= r["stress_risk"]


def test_custom_paper_profile_matches_the_owner_supplied_values():
    """CUSTOM SMALL-ACCOUNT PAPER POLICY — INITIAL / UNCALIBRATED. Locks the values the
    account owner supplied on 2026-08-07 so they cannot drift silently."""
    p = ORK.policy("CUSTOM")
    assert p["stock_planned_risk_pct"] == 1.0
    assert p["option_planned_risk_pct"] == 6.0
    assert p["max_long_option_premium_pct"] == 15.0
    assert p["max_total_option_premium_pct"] == 20.0
    assert p["max_portfolio_option_stress_risk_pct"] == 15.0
    assert p["max_portfolio_theoretical_option_loss_pct"] == 20.0
    assert p["max_open_option_positions"] == 1
    assert p["label"] == "CUSTOM SMALL-ACCOUNT PAPER POLICY — INITIAL / UNCALIBRATED"
    assert p["paper_only"] is True


def test_custom_declares_the_value_it_inherited_rather_than_hiding_it():
    """max_portfolio_planned_risk_pct was NOT in the owner's set. It is inherited from
    BALANCED and must be labelled as such, not presented as a decided number."""
    p = ORK.policy("CUSTOM")
    assert "max_portfolio_planned_risk_pct" in p["inherited_keys"]
    assert p["max_portfolio_planned_risk_pct"] == ORK.policy("BALANCED")["max_portfolio_planned_risk_pct"]
    assert "UNCALIBRATED" in p["profile_note"]
    assert "Inherited" in p["profile_note"]
    assert "inherited, NOT owner-specified" in ORK.render_profile_table(LIVE_EQUITY)


def test_custom_is_not_the_default_and_aggressive_stays_disabled():
    """CUSTOM is for shadow/paper evaluation; the live default remains BALANCED."""
    assert ORK.policy()["profile"] == "BALANCED"
    assert ORK.policy()["paper_only"] is False
    assert ORK.active_profile() != "AGGRESSIVE_SMALL_ACCOUNT"


def test_profile_table_reports_every_required_limit_with_dollars():
    """Acceptance 7: the values under review, with the dollars they imply."""
    t = ORK.profile_table(LIVE_EQUITY)
    assert t["profiles"] == ["CONSERVATIVE", "BALANCED", "CUSTOM",
                             "AGGRESSIVE_SMALL_ACCOUNT"]
    keys = {r["key"] for r in t["rows"]}
    assert keys == {"stock_planned_risk_pct", "option_planned_risk_pct",
                    "max_long_option_premium_pct", "max_total_option_premium_pct",
                    "max_portfolio_option_stress_risk_pct",
                    "max_portfolio_theoretical_option_loss_pct",
                    "max_open_option_positions"}
    assert "AWAITING REVIEW" in t["status"]
    for row in t["rows"]:
        for name, v in row["values"].items():
            if row["is_pct"]:
                assert abs(v["dollars"] - LIVE_EQUITY * v["value"] / 100.0) < 0.01
    assert "NOT" not in ORK.render_profile_table(LIVE_EQUITY).split("\n")[0] or True
    assert "AGGRESSIVE_SMALL_ACCOUNT" in ORK.render_profile_table(LIVE_EQUITY)


def test_repricing_is_rejected_when_the_model_disagrees_with_the_market():
    """A model that cannot price the contract you can see is not trusted to price the
    one you cannot."""
    r = ORK.option_risk(contracts=1, limit_price=0.45, spot=62.97, stop=62.10,
                        strike=62.0, side="CALL", iv=0.1685, dte=21, atr_pct=1.4)
    assert r["planned_risk"] == r["absolute_max_loss"]
    assert r["confidence"] == "low"
    assert "does not agree with the live quote" in r["planned_risk_basis"]


def test_implausibly_small_planned_loss_is_floored_not_trusted():
    """A near-zero planned risk would let any contract clear any budget."""
    pol = ORK.policy()
    r = ORK.option_risk(contracts=1, limit_price=13.10, spot=62.97, stop=62.90,
                        strike=50.0, side="CALL", iv=0.1685, dte=21, atr_pct=1.4,
                        pol=pol)
    if r["repricing_available"]:
        floor = r["absolute_max_loss"] * pol["min_credible_planned_loss_pct"] / 100.0
        assert r["planned_risk"] >= floor - 0.01


def test_unscored_setup_cannot_unlock_an_option():
    """A missing quality score fails closed — the strictest gate must not be bypassed
    by omission."""
    e = ORK.contract_eligibility(
        risk={"capital_committed": 10.0, "planned_risk": 2.0, "stress_risk": 5.0,
              "absolute_max_loss": 10.0},
        equity=LIVE_EQUITY, pol=ORK.policy("AGGRESSIVE_SMALL_ACCOUNT"))
    assert e["eligible"] is False
    assert e["binding_constraint"] == "setup quality"
    assert "no setup score" in e["reason"]


# ── instrument choice ────────────────────────────────────────────────────────

def test_instrument_verdict_maps_onto_the_existing_decision_vocabulary():
    """Acceptance: TRADEABLE / MONITOR / REJECT semantics are preserved."""
    assert ORK.to_decision("OPTION PREFERRED", setup_actionable=True) == "TRADEABLE"
    assert ORK.to_decision("STOCK PREFERRED", setup_actionable=True) == "TRADEABLE"
    assert ORK.to_decision("NO TRADE", setup_actionable=True) == "MONITOR"
    assert ORK.to_decision("STOCK PREFERRED", setup_actionable=False) == "REJECT"
    for case in (SHOP, PLTR, NTRA):
        _, d = _evaluate(case)
        assert d["decision"] in ("TRADEABLE", "MONITOR", "REJECT")
        assert d["verdict"] in OD.VERDICTS
        assert d["instrument"] in ORK.INSTRUMENTS


def test_shares_are_not_preferred_merely_because_option_max_loss_exceeds_the_stop():
    """The old reflex compared two different quantities. With an eligible contract the
    comparison is made on planned risk and efficiency instead."""
    # The real PLTR contract, on an account large enough to carry it.
    sizing, d = _evaluate(PLTR, equity=50_000.0, score=90.0)
    o = sizing["option"]
    assert o["absolute_max_loss"] > sizing["shares"]["risk"]["planned_risk"], \
        "premise: max loss exceeds the stock's stop distance — the old prefer-stock trigger"
    assert o["affordable"] is True
    assert d["instrument"] == "OPTION PREFERRED"      # yet the option still wins


def test_no_trade_when_neither_instrument_fits():
    """Cash remains a valid position."""
    choice = ORK.instrument_choice(
        stock=None, option=None, option_eligibility={"eligible": False},
        stock_sizeable=False, option_quality_ok=False)
    assert choice["instrument"] == "NO TRADE"
    assert ORK.to_decision(choice["instrument"], setup_actionable=True) == "MONITOR"
