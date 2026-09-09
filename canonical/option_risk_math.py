"""Canonical Option Architecture v1.1 — Step 4.1: neutral pure option-risk math.

The ONE Black-Scholes / repricing / stress-scenario implementation in the
codebase, extracted from `dashboard/options_desk.py` (Black-Scholes Greeks)
and `dashboard/option_risk.py` (repricing, thesis-failure paths, stress
scenarios, the four-way risk model) so that neither `canonical.contract_quality`
nor `canonical.account_fit` needs to import dashboard code — directly or
transitively — to reuse this math.

This module is pure stdlib (`math` only), no I/O, no environment reads, no
`sys.path` mutation, no dashboard/Flask/UI dependency of any kind. It is
imported FROM both directions:

    canonical.contract_quality  ─┐
    canonical.account_fit       ─┼──►  canonical.option_risk_math
    dashboard.options_desk      ─┤     (bs_greeks)
    dashboard.option_risk       ─┘     (everything else)

`dashboard/options_desk.py` and `dashboard/option_risk.py` now DELEGATE to
this module (mechanical import + re-export) rather than defining their own
copies — there is exactly one implementation, not two. Their public APIs
(`options_desk.bs_greeks`, `option_risk.option_risk`, `option_risk.stock_risk`,
etc.) are unchanged for any existing caller.

No business logic changed during extraction. Every function body below is
the original, verbatim — see the accompanying parity test suite
(tests/unit/test_canonical_import_purity.py) for a before/after numerical
comparison across a call/put/ATM/OTM/IV/DTE matrix.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

# ══════════════════════════════════════════════════════════════════════════
# Stress-scenario / repricing policy defaults
# (originally dashboard/option_risk.py:STRESS_DEFAULTS)
# ══════════════════════════════════════════════════════════════════════════

STRESS_DEFAULTS: Dict[str, float] = {
    "gap_atr_multiple": 1.0,        # underlying gaps this many ATRs BEYOND the stop
    "iv_crush_pct": 30.0,           # IV falls this much once the move has happened
    "spread_widen_multiple": 2.0,   # exit spread is this many times the entry spread
    # If the model cannot reproduce the price the market is actually charging to within
    # this fraction, its repricing at the invalidation level is not trustworthy either.
    # Measured against the real shadow-ledger contracts that carry usable quotes
    # (BAC 62C 7DTE: 7.1% error, PATH 15C 7DTE: 10.0%), a well-formed contract prices
    # inside ~10%. 0.18 leaves headroom above that while still rejecting genuinely
    # broken inputs — the mispriced case that motivated this gate was 270% off. The
    # previous 0.35 was permissive enough to admit contracts the model did not
    # understand. Sample is only n=2, so this is deliberately not tuned tighter.
    "max_model_calibration_error": 0.18,
    # A modelled planned loss below this fraction of premium is treated as a modelling
    # artifact rather than a real edge. Long options do not lose ~nothing when the
    # underlying reaches the level that invalidates the thesis; a number that says so
    # is measuring a bad input.
    "min_credible_planned_loss_pct": 5.0,
    # Thesis-failure timing paths. Planned risk is repriced on ALL THREE and the WORST
    # is taken, so a favourable timing or IV assumption can never reduce planned risk.
    "fast_path_fraction": 0.35,      # invalidation hit in 35% of the expected time
    "slow_path_multiple": 2.5,       # ...or in 2.5x it, grinding down (capped at DTE)
    "fast_iv_shift_pct": 10.0,       # a sharp adverse move tends to bid volatility UP
    "slow_iv_bleed_pct": 15.0,       # a slow grind lets volatility bleed OUT
}


# ══════════════════════════════════════════════════════════════════════════
# Black-Scholes (originally dashboard/options_desk.py:95-132)
# ══════════════════════════════════════════════════════════════════════════

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_greeks(spot: float, strike: float, iv: float, dte_days: float, side: str,
              r: float = 0.042) -> Optional[Dict[str, Any]]:
    """Black-Scholes Greeks. Returns None unless every input is usable. The result
    is tagged `provenance: model` by the caller and must never be shown as observed."""
    if not (spot and strike and iv and dte_days) or spot <= 0 or strike <= 0 or iv <= 0 or dte_days <= 0:
        return None
    T = dte_days / 365.0
    try:
        d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * T) / (iv * math.sqrt(T))
        d2 = d1 - iv * math.sqrt(T)
    except (ValueError, ZeroDivisionError):
        return None
    disc = math.exp(-r * T)
    call = side.upper().startswith("C")
    delta = _norm_cdf(d1) if call else _norm_cdf(d1) - 1.0
    gamma = _norm_pdf(d1) / (spot * iv * math.sqrt(T))
    vega = spot * _norm_pdf(d1) * math.sqrt(T) / 100.0            # per 1 vol point
    theta_year = (-spot * _norm_pdf(d1) * iv / (2 * math.sqrt(T))
                  + (-r * strike * disc * _norm_cdf(d2) if call
                     else r * strike * disc * _norm_cdf(-d2)))
    price = (spot * _norm_cdf(d1) - strike * disc * _norm_cdf(d2)) if call else \
            (strike * disc * _norm_cdf(-d2) - spot * _norm_cdf(-d1))
    return {"delta": round(delta, 4), "gamma": round(gamma, 6),
            "theta": round(theta_year / 365.0, 4), "vega": round(vega, 4),
            "theoretical_price": round(price, 4),
            "provenance": "model", "model": "black_scholes",
            "inputs": {"spot": spot, "strike": strike, "iv": iv,
                       "dte_days": dte_days, "risk_free_rate": r}}


# ══════════════════════════════════════════════════════════════════════════
# Repricing (originally dashboard/option_risk.py:251-336)
# ══════════════════════════════════════════════════════════════════════════

def _bs_price(spot: float, strike: float, iv: float, dte_days: float,
              side: str, r: float) -> Optional[float]:
    """Black-Scholes value. Originally imported `bs_greeks` lazily from
    `options_desk` specifically so option_risk.py stayed importable on its
    own with exactly ONE pricing model in the codebase (see the module this
    was extracted from) — now a direct call to the local, same-module
    `bs_greeks` above, which is the ONE model, same guarantee, no import
    needed at all."""
    g = bs_greeks(spot, strike, iv, dte_days, side, r)
    return g.get("theoretical_price") if g else None


def days_to_invalidation(*, spot: float, stop: float, atr_pct: Optional[float],
                         dte: Optional[float]) -> Dict[str, Any]:
    """Estimate how long the underlying takes to reach its invalidation level.

    Random-walk first passage: time to travel distance d at daily volatility s scales
    like (d/s)^2. Rough, but it is the difference between pricing the exit with most of
    the option's time value intact and pricing it with none. Capped at DTE — an
    invalidation that cannot be reached before expiry is priced at expiry.
    """
    if not (spot and stop) or atr_pct in (None, 0) or spot <= 0:
        return {"days": None, "basis": "unavailable — no ATR to estimate travel time",
                "confidence": "none"}
    daily = spot * (atr_pct / 100.0)
    if daily <= 0:
        return {"days": None, "basis": "unavailable — non-positive daily range",
                "confidence": "none"}
    d = abs(spot - stop)
    est = (d / daily) ** 2
    capped = min(est, dte) if dte else est
    return {"days": round(max(0.25, capped), 2),
            "basis": f"first-passage estimate: ({d:.2f} / {daily:.2f} ATR)^2 = {est:.1f}d"
                     + (f", capped at {dte:.0f}d to expiry" if dte and est > dte else ""),
            "confidence": "modelled", "uncapped_days": round(est, 2)}


def _failure_paths(t_expected: float, dte: float, iv: float,
                   pol: Dict[str, Any]) -> List[tuple]:
    """The three ways a thesis fails, as (name, days_used, iv_assumed, note).

    Fast and slow are not symmetric in their effect on a long option: reaching the stop
    quickly leaves time value intact and usually comes with a volatility bid, while a
    slow grind burns theta and lets IV bleed. The slow path is therefore normally the
    worst, which is exactly why it must be priced rather than assumed away.
    """
    fast_days = max(0.25, t_expected * pol["fast_path_fraction"])
    slow_days = min(dte, max(t_expected * pol["slow_path_multiple"], t_expected + 3.0))
    return [
        ("fast", fast_days, iv * (1 + pol["fast_iv_shift_pct"] / 100.0),
         f"invalidation hit in {fast_days:.1f}d — most time value intact, IV bid up "
         f"{pol['fast_iv_shift_pct']:.0f}%"),
        ("expected", t_expected, iv,
         f"invalidation hit in the estimated {t_expected:.1f}d, IV unchanged"),
        ("slow", slow_days, iv * (1 - pol["slow_iv_bleed_pct"] / 100.0),
         f"a {slow_days:.1f}d grind to the same level — theta burned, IV bled "
         f"{pol['slow_iv_bleed_pct']:.0f}%"),
    ]


def reprice(*, underlying: float, strike: float, iv: Optional[float],
            dte_remaining: Optional[float], side: str, r: float = 0.042,
            intrinsic_floor: bool = True) -> Dict[str, Any]:
    """Value one contract-share at a hypothetical underlying price and time.

    Falls back to intrinsic value when the model cannot run — intrinsic is a hard floor
    on what the option is worth, so this is conservative in the right direction for a
    LONG holder (it never overstates the exit value, which would understate the loss).
    """
    call = str(side).upper().startswith("C")
    intrinsic = max(0.0, (underlying - strike) if call else (strike - underlying))
    modelled = None
    if iv and dte_remaining and dte_remaining > 0:
        modelled = _bs_price(underlying, strike, iv, dte_remaining, side, r)
    if modelled is None:
        return {"value": round(intrinsic, 4), "basis": "intrinsic value only "
                "(no usable IV/DTE — time value assumed zero)", "confidence": "low"}
    value = max(modelled, intrinsic) if intrinsic_floor else modelled
    return {"value": round(value, 4),
            "basis": f"Black-Scholes at underlying {underlying:.2f}, "
                     f"{dte_remaining:.1f}d remaining, IV {iv*100:.0f}%",
            "confidence": "modelled", "intrinsic": round(intrinsic, 4),
            "time_value": round(max(0.0, value - intrinsic), 4)}


# ══════════════════════════════════════════════════════════════════════════
# The four-way risk model (originally dashboard/option_risk.py:338-559)
# ══════════════════════════════════════════════════════════════════════════

def stock_risk(*, quantity: float, entry: float, stop: float,
               slippage_bps: float = 3.0, fee: float = 0.0) -> Dict[str, Any]:
    """Stock leg. capital_committed is the notional; the planned loss is the stop
    distance. absolute_max_loss for a long share position is the whole notional (the
    company can go to zero) — reported honestly even though it is not the working
    number."""
    notional = quantity * entry
    slip = notional * (slippage_bps / 10_000.0)
    planned = quantity * abs(entry - stop) + slip + fee
    return {
        "instrument": "stock",
        "capital_committed": round(notional, 2),
        "planned_risk": round(planned, 2),
        "stress_risk": round(planned * 1.5 + slip, 2),
        "stress_basis": "a gap through the stop; 1.5x the planned stop distance",
        "absolute_max_loss": round(notional, 2),
        "absolute_max_loss_basis": "the entire position value — a share can go to zero",
        "confidence": "high",
        "quantity": quantity,
    }


def option_risk(*, contracts: int, limit_price: float, spot: float, stop: float,
                strike: float, side: str, iv: Optional[float], dte: Optional[float],
                atr_pct: Optional[float] = None, multiplier: float = 100.0,
                fee_per_contract: float = 0.06, spread_dollars: Optional[float] = None,
                r: float = 0.042, pol: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The four risk numbers for a long single-leg option, each with its basis.

    planned_risk = entry value - estimated value at the underlying's invalidation
                   + execution costs (exit slippage + fees)

    If that cannot be estimated with usable inputs, planned_risk becomes
    absolute_max_loss. A missing model is never allowed to produce a flattering number.
    """
    pol = pol or dict(STRESS_DEFAULTS)
    premium = limit_price * multiplier * contracts
    fees = fee_per_contract * contracts
    capital = premium + fees
    absolute = capital
    half_spread = ((spread_dollars or 0.0) / 2.0) * multiplier * contracts

    t = days_to_invalidation(spot=spot, stop=stop, atr_pct=atr_pct, dte=dte)
    reasons: List[str] = []

    if t["days"] is None or not iv or not dte:
        missing = [n for n, v in (("time-to-invalidation", t["days"]),
                                  ("IV", iv), ("DTE", dte)) if not v]
        return {
            "instrument": "option", "contracts": contracts,
            "capital_committed": round(capital, 2),
            "planned_risk": round(absolute, 2),
            "planned_risk_basis": "FALLBACK — cannot reprice at invalidation ("
                                  + ", ".join(f"no {m}" for m in missing)
                                  + "); the full premium is assumed at risk",
            "stress_risk": round(absolute, 2),
            "stress_basis": "same fallback — no scenario can be modelled",
            "absolute_max_loss": round(absolute, 2),
            "absolute_max_loss_basis": "long single-leg option — the entire premium "
                                       "plus fees is at risk",
            "confidence": "low", "repricing_available": False,
            "scenarios": [], "time_to_invalidation": t,
        }

    # Calibration gate. Before trusting the model to price the EXIT, check that it can
    # reproduce the price the market is charging for the ENTRY. A model that disagrees
    # with the live quote on a contract you can see is not a model you should believe
    # about a contract you cannot. Usually a stale quote, a wrong IV, or a contract
    # priced on something Black-Scholes cannot see (hard-to-borrow, pending action).
    at_entry = reprice(underlying=spot, strike=strike, iv=iv,
                       dte_remaining=dte, side=side, r=r)
    calib_err = (abs(at_entry["value"] - limit_price) / limit_price) if limit_price else None
    if calib_err is None or calib_err > pol["max_model_calibration_error"]:
        return {
            "instrument": "option", "contracts": contracts,
            "capital_committed": round(capital, 2),
            "planned_risk": round(absolute, 2),
            "planned_risk_basis": (
                "FALLBACK — the pricing model does not agree with the live quote "
                f"(model ${at_entry['value']:.2f} vs paid ${limit_price:.2f}"
                + (f", {calib_err:.0%} off" if calib_err is not None else "")
                + "), so its repricing at the invalidation level cannot be trusted; "
                  "the full premium is assumed at risk"),
            "stress_risk": round(absolute, 2),
            "stress_basis": "same fallback — repricing is not calibrated",
            "absolute_max_loss": round(absolute, 2),
            "absolute_max_loss_basis": "long single-leg option — the entire premium "
                                       "plus fees is at risk",
            "confidence": "low", "repricing_available": False,
            "model_calibration_error": round(calib_err, 4) if calib_err is not None else None,
            "scenarios": [], "time_to_invalidation": t,
        }

    # Planned risk is repriced on THREE thesis-failure paths, not one. A single
    # time-to-invalidation estimate embeds a timing assumption, and a favourable one
    # would quietly shrink planned risk and make a contract eligible that should not
    # be. Each path also carries its own IV assumption: a sharp adverse move tends to
    # bid volatility up (helping a long holder), a slow grind lets it bleed out.
    #
    # The WORST of the three is taken. That is what makes the estimate conservative and
    # is the guarantee that no optimistic timing or IV assumption can ever REDUCE
    # planned risk — a favourable path can only fail to be the maximum.
    paths: List[Dict[str, Any]] = []
    for name, days_used, iv_used, note in _failure_paths(t["days"], dte, iv, pol):
        left = max(0.0, dte - days_used)
        v = reprice(underlying=stop, strike=strike, iv=iv_used,
                    dte_remaining=left, side=side, r=r)
        proceeds = max(0.0, v["value"] * multiplier * contracts - half_spread)
        paths.append({"path": name, "days_to_invalidation": round(days_used, 2),
                      "dte_remaining": round(left, 2), "iv_assumed": round(iv_used, 4),
                      "contract_value": v["value"], "exit_proceeds": round(proceeds, 2),
                      "loss": round(min(absolute, max(0.0, capital - proceeds)), 2),
                      "note": note, "basis": v["basis"]})

    worst_path = max(paths, key=lambda p: p["loss"])
    planned = worst_path["loss"]
    at_stop = {"value": worst_path["contract_value"],
               "confidence": "modelled"}
    exit_value = worst_path["exit_proceeds"]
    dte_left = max(0.0, dte - t["days"])          # expected path, for stress scenarios
    reasons.append(
        f"repriced on 3 thesis-failure paths at the ${stop:.2f} invalidation "
        + "; ".join(f"{p['path']} ${p['loss']:.2f}" for p in paths)
        + f" — taking the worst ({worst_path['path']}: contract worth "
          f"${worst_path['contract_value']:.2f} with "
          f"{worst_path['dte_remaining']:.1f}d left)")

    # Floor an implausibly small modelled loss. This is the direction the spec warns
    # about: a near-zero planned risk would let any contract clear any budget, and it
    # is far more likely to be a bad input than a genuinely riskless option.
    floor = absolute * (pol["min_credible_planned_loss_pct"] / 100.0)
    if planned < floor:
        reasons.append(f"modelled loss ${max(0.0, planned):.2f} is implausibly small for a long "
                       f"option at its invalidation level — floored at "
                       f"{pol['min_credible_planned_loss_pct']:.0f}% of premium "
                       f"(${floor:.2f}) rather than trusted")
        planned = floor

    scenarios = stress_scenarios(
        contracts=contracts, limit_price=limit_price, spot=spot, stop=stop,
        strike=strike, side=side, iv=iv, dte=dte, dte_left=dte_left,
        atr_pct=atr_pct, multiplier=multiplier, fees=fees,
        half_spread=half_spread, r=r, pol=pol, capital=capital)

    worst = max((s["loss"] for s in scenarios), default=planned)
    stress = min(absolute, max(worst, planned))

    # Never report a planned risk above the true maximum, and never below zero.
    planned = max(0.0, min(planned, absolute))

    return {
        "instrument": "option", "contracts": contracts,
        "capital_committed": round(capital, 2),
        "planned_risk": round(planned, 2),
        "planned_risk_basis": "; ".join(reasons),
        "planned_risk_pct_of_premium": round(planned / absolute * 100, 1) if absolute else None,
        "planned_risk_paths": paths,
        "planned_risk_path_used": worst_path["path"],
        "planned_risk_rule": "worst of the fast/expected/slow thesis-failure paths — a "
                             "favourable timing or IV assumption can never reduce it",
        "stress_risk": round(stress, 2),
        "stress_basis": "worst modelled adverse scenario, capped at the premium",
        "absolute_max_loss": round(absolute, 2),
        "absolute_max_loss_basis": "long single-leg option — the entire premium plus "
                                   "fees is at risk",
        "confidence": "modelled" if at_stop["confidence"] != "low" else "low",
        "repricing_available": True,
        "estimated_value_at_invalidation": at_stop["value"],
        "estimated_exit_proceeds": round(exit_value, 2),
        "time_to_invalidation": t,
        "dte_remaining_at_invalidation": round(dte_left, 2),
        "scenarios": scenarios,
        "exit_note": "A planned option stop is NOT guaranteed. It assumes a two-sided "
                     "market at the modelled price; a gap, an IV collapse or a widened "
                     "spread can all produce a worse fill. Size to stress_risk, not to "
                     "planned_risk.",
    }


def stress_scenarios(*, contracts: int, limit_price: float, spot: float, stop: float,
                     strike: float, side: str, iv: float, dte: float, dte_left: float,
                     atr_pct: Optional[float], multiplier: float, fees: float,
                     half_spread: float, r: float, pol: Dict[str, Any],
                     capital: float) -> List[Dict[str, Any]]:
    """Adverse-but-plausible repricings. Each returns the loss in dollars, capped at the
    premium, so the list can be read as 'how bad does this get, and why'."""
    out: List[Dict[str, Any]] = []
    atr_dollars = spot * ((atr_pct or 0) / 100.0)

    def add(name: str, underlying: float, use_iv: float, days_left: float,
            extra_spread: float, note: str) -> None:
        v = reprice(underlying=underlying, strike=strike, iv=use_iv,
                    dte_remaining=days_left, side=side, r=r)
        proceeds = max(0.0, v["value"] * multiplier * contracts - half_spread - extra_spread)
        loss = min(capital, max(0.0, capital - proceeds))
        out.append({"scenario": name, "underlying": round(underlying, 2),
                    "iv": round(use_iv, 4), "days_left": round(days_left, 2),
                    "contract_value": v["value"], "proceeds": round(proceeds, 2),
                    "loss": round(loss, 2),
                    "loss_pct_of_premium": round(loss / capital * 100, 1) if capital else None,
                    "note": note, "basis": v["basis"]})

    crush = 1.0 - (pol["iv_crush_pct"] / 100.0)
    widen = pol["spread_widen_multiple"]

    add("thesis_failure", stop, iv, dte_left, 0.0,
        "the underlying reaches its invalidation level in the estimated time")
    add("slow_grind_to_stop", stop, iv, max(0.0, min(dte_left, 1.0)), 0.0,
        "the same move, but late in the contract's life — nearly all time value burned")
    add("gap_through_stop", stop - atr_dollars * pol["gap_atr_multiple"], iv, dte_left, 0.0,
        f"the underlying gaps {pol['gap_atr_multiple']:.1f} ATR beyond the stop before "
        f"any exit is possible")
    add("iv_collapse", stop, iv * crush, dte_left, 0.0,
        f"implied volatility falls {pol['iv_crush_pct']:.0f}% once the move has happened")
    add("spread_widening", stop, iv, dte_left, half_spread * (widen - 1.0),
        f"the exit spread widens to {widen:.1f}x its entry width")
    add("catalyst_failure", stop - atr_dollars * pol["gap_atr_multiple"], iv * crush,
        dte_left, half_spread * (widen - 1.0),
        "the classic combination: adverse gap, IV crush and a wider spread at once")
    return out
