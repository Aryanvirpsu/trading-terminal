"""Instrument-aware risk model for long single-leg options.

WHY THIS EXISTS
---------------
The previous policy set `planned_risk = premium x 100` for a long option — the full
theoretical max loss. Combined with a stock-style "risk at most 1% of the account per
trade" rule, that made options arithmetically unreachable on a small account: at $448
buying power the budget is $4.48, so an "affordable" contract would have to cost
$0.04/share. Every contract on every name failed, forever, for a reason that had
nothing to do with the contract.

The error is conflating four different quantities. This module separates them:

  capital_committed   cash that leaves the account to open the position
  planned_risk        expected loss if the THESIS fails — i.e. the underlying reaches
                      its invalidation level — including execution costs
  stress_risk         loss under adverse but plausible conditions (gap, IV crush,
                      spread widening, a slow grind that burns theta)
  absolute_max_loss   the true worst case; for a long option this is the whole premium

For a stock, planned_risk and absolute_max_loss differ only by gap risk, so the
distinction rarely mattered and the codebase never made it. For a long option they can
differ by 3-5x, and collapsing them into one number is what broke the engine.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not loosen any risk control. Every limit that existed still exists; several new
portfolio-level ones are added. It does not choose contracts — contract QUALITY is
decided upstream by the existing liquidity/spread/OI/delta/DTE/theta gates, and a
contract that fails those never reaches this module. Affordability is applied only to
contracts that already passed quality, so a junk far-OTM lottery strike can never be
selected merely because it is cheap.

It never invents a smaller planned_risk to make a contract pass: when repricing inputs
are missing or untrustworthy, planned_risk falls back to absolute_max_loss and the
result is tagged with reduced confidence.

All percentages live in the profiles below or in the environment. None are hardcoded in
decision logic.
"""

from __future__ import annotations

import math
import os
import sys
from typing import Any, Dict, List, Optional

# Step 4.1 (Canonical Option Architecture v1.1): the pure repricing/stress/
# four-way risk model below now lives in canonical/option_risk_math.py, so
# canonical code never has to import THIS module (which reads RISK_PROFILE
# and other environment state via policy()) just to reuse that math. This
# minimal path insertion — repo root only, nothing else — exists solely so
# `from canonical.option_risk_math import ...` resolves when this module is
# imported on its own (e.g. by tests that put only `dashboard/` on
# sys.path, without the repo root). It is scoped to this dashboard module;
# canonical.* modules never perform this insertion themselves.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ── Risk profiles ────────────────────────────────────────────────────────────
# These are POLICY, not findings. They are starting points chosen for internal
# consistency (an option's planned risk is typically 40-70% of premium when the
# underlying reaches its stop, so an option planned-risk budget has to be several times
# the stock one to permit any contract at all). They are NOT fitted to historical
# returns and MUST be reviewed by the account owner before being relied on.
#
# AGGRESSIVE_SMALL_ACCOUNT exists because at a few hundred dollars of equity a single
# contract is inherently a large percentage of the account. It is never selected
# implicitly — see active_profile().

PROFILES: Dict[str, Dict[str, float]] = {
    "CONSERVATIVE": {
        "stock_planned_risk_pct": 0.5,
        "option_planned_risk_pct": 2.0,
        "max_long_option_premium_pct": 5.0,
        "max_total_option_premium_pct": 10.0,
        "max_portfolio_planned_risk_pct": 3.0,
        "max_portfolio_option_stress_risk_pct": 6.0,
        "max_portfolio_theoretical_option_loss_pct": 10.0,
        "max_open_option_positions": 1,
    },
    "BALANCED": {
        "stock_planned_risk_pct": 1.0,
        "option_planned_risk_pct": 4.0,
        "max_long_option_premium_pct": 10.0,
        "max_total_option_premium_pct": 20.0,
        "max_portfolio_planned_risk_pct": 6.0,
        "max_portfolio_option_stress_risk_pct": 12.0,
        "max_portfolio_theoretical_option_loss_pct": 20.0,
        "max_open_option_positions": 2,
    },
    "AGGRESSIVE_SMALL_ACCOUNT": {
        "stock_planned_risk_pct": 2.0,
        "option_planned_risk_pct": 12.0,
        "max_long_option_premium_pct": 25.0,
        "max_total_option_premium_pct": 40.0,
        "max_portfolio_planned_risk_pct": 15.0,
        "max_portfolio_option_stress_risk_pct": 25.0,
        "max_portfolio_theoretical_option_loss_pct": 40.0,
        "max_open_option_positions": 2,
    },
    # CUSTOM SMALL-ACCOUNT PAPER POLICY — INITIAL / UNCALIBRATED.
    # Supplied by the account owner on 2026-08-07 for SHADOW/PAPER EVALUATION ONLY.
    # These are starting values, not optimised or fitted ones, and no result has yet
    # been measured against them. `max_portfolio_planned_risk_pct` was NOT specified in
    # that set and is inherited from BALANCED (6.0) rather than invented — see
    # profile_note, which says so at runtime.
    "CUSTOM": {
        "stock_planned_risk_pct": 1.0,
        "option_planned_risk_pct": 6.0,
        "max_long_option_premium_pct": 15.0,
        "max_total_option_premium_pct": 20.0,
        "max_portfolio_planned_risk_pct": 6.0,        # inherited — not specified
        "max_portfolio_option_stress_risk_pct": 15.0,
        "max_portfolio_theoretical_option_loss_pct": 20.0,
        "max_open_option_positions": 1,
    },
}

# Values in CUSTOM that the owner did not specify and which were inherited rather than
# chosen. Surfaced at runtime so an unreviewed number is never mistaken for a decided one.
CUSTOM_INHERITED = ("max_portfolio_planned_risk_pct",)

# Stress assumptions — also policy, also overridable.
# Extracted to canonical/option_risk_math.py (Step 4.1); imported back here
# so `option_risk.STRESS_DEFAULTS` still resolves for any existing caller,
# and so policy()'s env-override merge below is unchanged.
from canonical.option_risk_math import STRESS_DEFAULTS  # noqa: E402


class RiskConfigError(ValueError):
    """The risk policy configuration is invalid.

    Deliberately fatal rather than falling back to a default: a risk engine that
    silently substitutes BALANCED when it does not recognise RISK_PROFILE is one whose
    active limits nobody can audit from the outside. A typo must be visible, not
    absorbed.
    """

_PROFILE_KEYS = tuple(PROFILES["BALANCED"].keys())


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


VALID_PROFILES = tuple(sorted(PROFILES))


def active_profile() -> str:
    """The profile in force. Unset means BALANCED; an UNRECOGNISED value is a
    configuration error and raises. The aggressive profile is only ever active because
    someone set it explicitly — never as a fallback or an escalation."""
    raw = os.environ.get("RISK_PROFILE")
    if raw is None or not raw.strip():
        return "BALANCED"
    name = raw.strip().upper()
    if name not in VALID_PROFILES:
        raise RiskConfigError(
            f"RISK_PROFILE={raw!r} is not a recognised risk profile. "
            f"Valid values: {', '.join(VALID_PROFILES)}. "
            "Refusing to fall back to a default — fix the value or unset it.")
    return name


def policy(profile: Optional[str] = None,
           overrides: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """Resolve the active policy. Precedence: explicit overrides > environment >
    profile defaults. CUSTOM starts from BALANCED and expects env/overrides.

    Raises RiskConfigError on an unrecognised profile name, from either argument or
    environment. There is no silent default.
    """
    if profile is not None:
        name = str(profile).strip().upper()
        if name not in VALID_PROFILES:
            raise RiskConfigError(
                f"Unknown risk profile {profile!r}. "
                f"Valid values: {', '.join(VALID_PROFILES)}.")
    else:
        name = active_profile()
    base = dict(PROFILES[name])
    out: Dict[str, Any] = {}
    for k in _PROFILE_KEYS:
        out[k] = _f(f"RISK_{k.upper()}", base[k])
    for k, v in STRESS_DEFAULTS.items():
        out[k] = _f(f"RISK_{k.upper()}", v)
    if overrides:
        out.update({k: v for k, v in overrides.items() if v is not None})
    out["max_open_option_positions"] = int(out["max_open_option_positions"])
    out["profile"] = name
    out["profile_is_explicit"] = bool(os.environ.get("RISK_PROFILE"))
    out["profile_note"] = (
        f"{name} policy in force."
        + ("" if out["profile_is_explicit"] else " Default — RISK_PROFILE was not set.")
        + (" AGGRESSIVE_SMALL_ACCOUNT accepts a much larger percentage loss per trade; "
           "it is appropriate only if the whole account is risk capital."
           if name == "AGGRESSIVE_SMALL_ACCOUNT" else "")
        + (" CUSTOM SMALL-ACCOUNT PAPER POLICY — INITIAL / UNCALIBRATED. Owner-supplied "
           "starting values for SHADOW/PAPER EVALUATION ONLY; nothing has been measured "
           "against them yet. Inherited (not specified by the owner): "
           + ", ".join(CUSTOM_INHERITED) + "."
           if name == "CUSTOM" else "")
    )
    out["label"] = ("CUSTOM SMALL-ACCOUNT PAPER POLICY — INITIAL / UNCALIBRATED"
                    if name == "CUSTOM" else name)
    out["paper_only"] = (name == "CUSTOM")
    out["inherited_keys"] = list(CUSTOM_INHERITED) if name == "CUSTOM" else []
    return out


# ── Quality tiers ────────────────────────────────────────────────────────────
# Allocation may scale WITHIN policy, never beyond it. The multiplier lifts a trade
# toward the cap; it can never lift the cap.

TIERS = (
    ("EXCEPTIONAL", 85.0, 1.00),
    ("HIGH_QUALITY", 70.0, 0.75),
    ("QUALIFIED", 55.0, 0.50),
    ("WATCH", 45.0, 0.0),
    ("REJECT", float("-inf"), 0.0),
)


def quality_tier(score: Optional[float]) -> Dict[str, Any]:
    """Map a setup score to a tier and the fraction of the policy cap it may use."""
    if score is None:
        # NOT "WATCH" — a setup nobody scored is a different fact from one scored and
        # found weak, and the reason the caller sees should say which happened.
        return {"tier": "UNSCORED", "allocation_fraction": 0.0,
                "note": "no setup score supplied — no allocation"}
    for name, floor, frac in TIERS:
        if score >= floor:
            return {"tier": name, "allocation_fraction": frac,
                    "note": f"score {score:.1f} -> {name}, may use {frac:.0%} of the policy cap"}
    return {"tier": "REJECT", "allocation_fraction": 0.0, "note": "below every tier"}


# ── Repricing / four-way risk model ──────────────────────────────────────────
# Extracted to canonical/option_risk_math.py (Canonical Option Architecture
# v1.1, Step 4.1). Public names re-exported below so every existing caller
# (option_risk_mod.option_risk(...), option_risk_mod.stock_risk(...), the
# private helpers used only within this file's own docstrings/comments)
# resolves exactly as before. There is exactly ONE implementation.
#
# Behavioral note on option_risk()'s `pol` default: originally
# `pol = pol or policy()`. The extracted version defaults to
# `dict(STRESS_DEFAULTS)` instead, because `policy()` (env-var reads,
# RISK_PROFILE selection) stays in THIS module and importing it back into
# the neutral module would be circular. This is a no-op in practice: (a)
# option_risk() only ever reads STRESS_DEFAULTS-shaped keys from `pol`
# (max_model_calibration_error, min_credible_planned_loss_pct, the fast/
# slow-path fractions, iv_crush_pct, spread_widen_multiple, gap_atr_multiple)
# — never a PROFILES percentage key; (b) policy()'s STRESS_DEFAULTS portion
# equals STRESS_DEFAULTS exactly unless a RISK_<STRESS_KEY> env override is
# set, which no current caller or test does; (c) the one real production
# caller (options_desk.py:evaluate_candidate()) always passes `pol=`
# explicitly already, never relying on this default. Verified by the parity
# suite (tests/unit/test_canonical_import_purity.py) and by an unchanged
# tests/unit/test_option_risk.py, which calls option_risk() without `pol=`
# in several places.
from canonical.option_risk_math import (  # noqa: E402
    _bs_price, days_to_invalidation, _failure_paths, reprice,
    stock_risk, option_risk, stress_scenarios,
)


# ── Eligibility: one contract, or none ───────────────────────────────────────

def contract_eligibility(*, risk: Dict[str, Any], equity: float,
                         pol: Optional[Dict[str, Any]] = None,
                         quality: Optional[Dict[str, Any]] = None,
                         portfolio: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Can this account carry ONE contract under policy?

    Options are indivisible. "0.06 contracts" is not a recommendation, it is a rounding
    artifact, so this answers a yes/no question and says which limit decides it.
    """
    pol = pol or policy()
    checks: List[Dict[str, Any]] = []
    if not equity or equity <= 0:
        return {"eligible": False, "contracts": 0,
                "status": "NO CONTRACT FITS CURRENT RISK POLICY",
                "reason": "no live account equity available — cannot size against policy",
                "checks": [], "profile": pol["profile"]}

    # Fail closed on an unscored setup. Options are the leveraged instrument; allowing
    # one because nobody supplied a quality score would let the strictest gate be
    # bypassed by omission. A missing score is a reason to decline, not to assume.
    frac = (quality or {}).get("allocation_fraction", 0.0)
    tier = (quality or {}).get("tier", "UNSCORED")
    if frac <= 0:
        return {"eligible": False, "contracts": 0,
                "status": "NO CONTRACT FITS CURRENT RISK POLICY",
                "reason": ("no setup score supplied — option suitability cannot be established"
                           if tier == "UNSCORED" else
                           f"setup tier {tier} carries no option allocation"),
                "binding_constraint": "setup quality", "checks": [], "failed": [],
                "profile": pol["profile"], "profile_note": pol["profile_note"],
                "quality_tier": tier, "equity_used": round(equity, 2),
                "note": "Options are indivisible — this is a yes/no on one contract, "
                        "never a fractional quantity."}

    def limit(label: str, value: float, pct_key: str, scaled: bool) -> None:
        cap_pct = pol[pct_key]
        cap = equity * cap_pct / 100.0
        # Quality scales a trade WITHIN the cap. It can never raise the cap.
        effective = cap * frac if scaled else cap
        checks.append({
            "check": label, "value": round(value, 2),
            "limit": round(effective, 2), "limit_pct_of_equity": cap_pct,
            "scaled_by_quality": scaled,
            "pass": value <= effective + 1e-9,
            "detail": (f"${value:,.2f} vs ${effective:,.2f} "
                       f"({cap_pct}% of ${equity:,.2f}"
                       + (f" x {frac:.0%} for tier {tier}" if scaled and frac != 1.0 else "")
                       + ")"),
        })

    limit("premium (capital committed)", risk["capital_committed"],
          "max_long_option_premium_pct", True)
    limit("planned risk at invalidation", risk["planned_risk"],
          "option_planned_risk_pct", True)
    limit("stress risk", risk["stress_risk"],
          "max_portfolio_option_stress_risk_pct", False)
    limit("absolute max loss", risk["absolute_max_loss"],
          "max_portfolio_theoretical_option_loss_pct", False)

    if portfolio:
        checks.extend(portfolio.get("checks", []))

    failed = [c for c in checks if not c["pass"]]
    ok = not failed
    return {
        "eligible": ok, "contracts": 1 if ok else 0,
        "status": "1 CONTRACT ELIGIBLE" if ok else "NO CONTRACT FITS CURRENT RISK POLICY",
        "reason": None if ok else "; ".join(c["check"] for c in failed),
        "binding_constraint": (min(failed, key=lambda c: c["limit"] - c["value"])["check"]
                               if failed else None),
        "checks": checks, "failed": failed,
        "profile": pol["profile"], "profile_note": pol["profile_note"],
        "quality_tier": tier, "equity_used": round(equity, 2),
        "note": "Options are indivisible — this is a yes/no on one contract, never a "
                "fractional quantity.",
    }


# ── Portfolio-level control ──────────────────────────────────────────────────

def portfolio_check(*, equity: float, open_options: Optional[List[Dict[str, Any]]] = None,
                    candidate_risk: Optional[Dict[str, Any]] = None,
                    existing_planned_risk: float = 0.0,
                    sector: Optional[str] = None,
                    sector_option_count: int = 0,
                    correlated_count: int = 0,
                    same_event_count: int = 0,
                    remaining_buying_power: Optional[float] = None,
                    pol: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Aggregate option exposure across the book. Hard limits here override any setup
    score — a great setup in a book that is already full is still a no."""
    pol = pol or policy()
    open_options = open_options or []
    cand = candidate_risk or {}

    committed = sum((p.get("capital_committed") or p.get("premium") or 0.0)
                    for p in open_options)
    stress = sum((p.get("stress_risk") or p.get("capital_committed") or 0.0)
                 for p in open_options)
    theoretical = sum((p.get("absolute_max_loss") or p.get("capital_committed") or 0.0)
                      for p in open_options)

    new_committed = committed + (cand.get("capital_committed") or 0.0)
    new_stress = stress + (cand.get("stress_risk") or 0.0)
    new_theoretical = theoretical + (cand.get("absolute_max_loss") or 0.0)
    new_planned = existing_planned_risk + (cand.get("planned_risk") or 0.0)

    checks: List[Dict[str, Any]] = []

    def add(label: str, value: float, pct_key: str) -> None:
        cap = equity * pol[pct_key] / 100.0
        checks.append({"check": label, "value": round(value, 2), "limit": round(cap, 2),
                       "limit_pct_of_equity": pol[pct_key], "scaled_by_quality": False,
                       "pass": value <= cap + 1e-9,
                       "detail": f"${value:,.2f} vs ${cap:,.2f} "
                                 f"({pol[pct_key]}% of ${equity:,.2f})"})

    add("portfolio option premium", new_committed, "max_total_option_premium_pct")
    add("portfolio planned risk", new_planned, "max_portfolio_planned_risk_pct")
    add("portfolio option stress risk", new_stress, "max_portfolio_option_stress_risk_pct")
    add("portfolio theoretical option loss", new_theoretical,
        "max_portfolio_theoretical_option_loss_pct")

    n = len(open_options)
    checks.append({"check": "open option positions", "value": n + 1,
                   "limit": pol["max_open_option_positions"], "scaled_by_quality": False,
                   "pass": n + 1 <= pol["max_open_option_positions"],
                   "detail": f"{n} open + 1 new vs a {pol['max_open_option_positions']} limit"})

    if remaining_buying_power is not None:
        need = cand.get("capital_committed") or 0.0
        checks.append({"check": "buying power", "value": round(need, 2),
                       "limit": round(remaining_buying_power, 2), "scaled_by_quality": False,
                       "pass": need <= remaining_buying_power + 1e-9,
                       "detail": f"${need:,.2f} needed vs ${remaining_buying_power:,.2f} available"})

    for label, count, cap in (("sector option concentration", sector_option_count, 1),
                              ("correlated option exposure", correlated_count, 1),
                              ("same-event option exposure", same_event_count, 1)):
        checks.append({"check": label, "value": count + 1, "limit": cap,
                       "scaled_by_quality": False, "pass": count + 1 <= cap,
                       "detail": f"{count} existing + 1 new vs a {cap} limit"
                                 + (f" ({sector})" if sector and "sector" in label else "")})

    failed = [c for c in checks if not c["pass"]]
    return {"pass": not failed, "checks": checks, "failed": failed,
            "totals": {"option_premium_committed": round(new_committed, 2),
                       "planned_risk": round(new_planned, 2),
                       "option_stress_risk": round(new_stress, 2),
                       "theoretical_option_loss": round(new_theoretical, 2),
                       "open_option_positions": n},
            "profile": pol["profile"]}


# ── Instrument choice ────────────────────────────────────────────────────────

INSTRUMENTS = ("OPTION PREFERRED", "STOCK PREFERRED", "NO TRADE")


def instrument_choice(*, stock: Optional[Dict[str, Any]],
                      option: Optional[Dict[str, Any]],
                      option_eligibility: Optional[Dict[str, Any]],
                      stock_sizeable: bool,
                      option_quality_ok: bool,
                      reward_per_share: Optional[float] = None,
                      option_edge: Optional[Dict[str, Any]] = None,
                      quality_tilt: int = 0) -> Dict[str, Any]:
    """Pick the instrument AFTER both have been priced and checked.

    Deliberately NOT decided by either of the two reflexes this engine used to have:
    shares are not preferred merely because an option's max loss exceeds the stock's
    stop distance (that compares two different quantities), and options are not
    preferred merely because they carry more leverage. Suitability is a gate; quality
    and efficiency decide what happens inside it.
    """
    reasons: List[str] = []
    eligible = bool(option_eligibility and option_eligibility.get("eligible"))

    if option is None or not option_quality_ok:
        reasons.append("no contract cleared the quality gates" if option is None
                       else "the best contract failed a quality gate")
    elif not eligible:
        reasons.append("contract is sound but fails portfolio suitability: "
                       + str((option_eligibility or {}).get("reason")))

    if not stock_sizeable and not (eligible and option_quality_ok):
        return {"instrument": "NO TRADE",
                "reasons": reasons + ["the stock leg cannot be sized either"],
                "option_eligible": eligible}

    if not (eligible and option_quality_ok):
        return {"instrument": "STOCK PREFERRED", "reasons": reasons,
                "option_eligible": eligible}

    if not stock_sizeable:
        return {"instrument": "OPTION PREFERRED",
                "reasons": ["the stock leg cannot be sized within policy, the contract can"],
                "option_eligible": True}

    # Both are viable — compare efficiency, not leverage.
    s_planned = (stock or {}).get("planned_risk") or 0.0
    o_planned = (option or {}).get("planned_risk") or 0.0
    s_capital = (stock or {}).get("capital_committed") or 0.0
    o_capital = (option or {}).get("capital_committed") or 0.0
    for_option: List[str] = []
    for_stock: List[str] = []

    if o_capital and s_capital and o_capital < s_capital:
        for_option.append(f"less capital committed (${o_capital:,.2f} vs ${s_capital:,.2f})")
    elif o_capital and s_capital:
        for_stock.append(f"less capital committed (${s_capital:,.2f} vs ${o_capital:,.2f})")

    if o_planned and s_planned:
        if o_planned <= s_planned:
            for_option.append(f"lower planned risk (${o_planned:,.2f} vs ${s_planned:,.2f})")
        else:
            for_stock.append(f"lower planned risk (${s_planned:,.2f} vs ${o_planned:,.2f})")

    if (option or {}).get("confidence") == "low":
        for_stock.append("the option's planned risk could not be modelled — the whole "
                         "premium has to be assumed at risk")
    if (option_edge or {}).get("theta_pct_per_day") is not None and \
            option_edge["theta_pct_per_day"] > 1.5:
        for_stock.append(f"theta costs {option_edge['theta_pct_per_day']:.1f}% of premium "
                         f"per day; shares do not decay")
    if (option_edge or {}).get("break_even_within_expected_move") is False:
        for_stock.append("break-even sits outside the expected move for this expiry")
    elif (option_edge or {}).get("break_even_within_expected_move") is True:
        for_option.append("break-even sits inside the expected move for this expiry")

    # `quality_tilt` carries the caller's contract-quality comparison (spread, OI, IV,
    # break-even and so on) so that verdict is decided once, on both quality AND risk
    # efficiency, rather than in two places that can disagree.
    score_option = len(for_option) + max(0, quality_tilt)
    score_stock = len(for_stock) + max(0, -quality_tilt)
    verdict = "OPTION PREFERRED" if score_option > score_stock else "STOCK PREFERRED"
    return {"instrument": verdict, "reasons_for_option": for_option,
            "reasons_for_stock": for_stock, "option_eligible": True,
            "quality_tilt": quality_tilt,
            "tally": {"option": score_option, "stock": score_stock},
            "reasons": reasons}


# ── Mapping onto the existing authoritative vocabulary ───────────────────────

TABLE_ROWS = (
    ("stock planned-risk %", "stock_planned_risk_pct", True),
    ("option planned-risk %", "option_planned_risk_pct", True),
    ("max premium / trade %", "max_long_option_premium_pct", True),
    ("max total option premium %", "max_total_option_premium_pct", True),
    ("stress-risk limit %", "max_portfolio_option_stress_risk_pct", True),
    ("theoretical option-loss limit %", "max_portfolio_theoretical_option_loss_pct", True),
    ("max option positions", "max_open_option_positions", False),
)


def profile_table(equity: float,
                  profiles: Optional[List[str]] = None) -> Dict[str, Any]:
    """The policy values, with the dollar limits they imply at a given equity.

    These numbers are POLICY AWAITING REVIEW, not findings. They were chosen for
    internal consistency and are NOT fitted to any historical result.
    """
    names = profiles or ["CONSERVATIVE", "BALANCED", "CUSTOM",
                         "AGGRESSIVE_SMALL_ACCOUNT"]
    pols = {n: policy(n) for n in names}
    rows = []
    for label, key, is_pct in TABLE_ROWS:
        row = {"limit": label, "key": key, "is_pct": is_pct, "values": {}}
        for n in names:
            v = pols[n][key]
            row["values"][n] = {"value": v,
                                "dollars": round(equity * v / 100.0, 2) if is_pct else None}
        rows.append(row)
    return {"equity": round(equity, 2), "profiles": names, "rows": rows,
            "active_profile": active_profile(),
            "status": "POLICY AWAITING REVIEW — not authoritative until signed off"}


def render_profile_table(equity: float) -> str:
    """Plain-text rendering of profile_table() for terminal review."""
    t = profile_table(equity)
    names = t["profiles"]
    w = 26
    out = [f"RISK PROFILE CONFIGURATION — {t['status']}",
           f"account equity used: ${t['equity']:,.2f}   |   active profile: {t['active_profile']}",
           ""]
    out.append(f"{'limit':<34s}" + "".join(f"{n:>{w}s}" for n in names))
    out.append("-" * (34 + w * len(names)))
    for row in t["rows"]:
        cells = ""
        for n in names:
            v = row["values"][n]
            cells += (f"{v['value']:>10.2f}% = ${v['dollars']:>10,.2f}" if row["is_pct"]
                      else f"{int(v['value']):>26d}")
        out.append(f"{row['limit']:<34s}" + cells)
    if "CUSTOM" in names:
        out += ["", "CUSTOM = " + policy("CUSTOM")["label"],
                "  shadow/paper evaluation only — not the live profile",
                "  inherited, NOT owner-specified: " + ", ".join(CUSTOM_INHERITED)]
    return "\n".join(out)


def to_decision(instrument: str, *, setup_actionable: bool) -> str:
    """Map an instrument choice onto the engine's existing TRADEABLE/MONITOR/REJECT
    semantics. This module never invents a new authority — it only chooses HOW to
    express a setup the existing gates already judged."""
    if not setup_actionable:
        return "REJECT"
    if instrument == "NO TRADE":
        return "MONITOR"
    return "TRADEABLE"
