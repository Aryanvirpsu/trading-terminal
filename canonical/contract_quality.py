"""Canonical Option Architecture v1.1 — Layer A: contract structural quality.

Answers exactly one question: "Is this option contract itself structurally good
enough to consider?" — bid/ask, spread, open interest, volume, session context,
moneyness, DTE, quote freshness, and Greeks provenance. It has ZERO account
awareness: the public function below is keyword-only with an explicit parameter
list and no **kwargs, so there is no way to pass `balance`, `equity`,
`buying_power`, `portfolio`, or any other account-fit concept to it — doing so
raises TypeError at the call site, not a runtime check. See
test_layer_a_has_zero_account_parameters in the accompanying test file for a
test that pins this down structurally (inspects the signature itself).

This module is ADDITIVE. It does not migrate, wrap, call, or modify:
  * tradingview_mcp.core.services.options_grading.grade_contract()
  * dashboard/options_desk.py: analyse_contract()
  * lab/decision_engine.py: _grade_option_chain()
No caller anywhere in the repository has been changed to use this module.

quality_pass is decided ONLY by `hard_failures` — a contract can have a
respectable `score`/`grade` and still have quality_pass=False (e.g. a perfectly
liquid, tight-spread contract with a 40-hour-old quote). Callers must gate on
`quality_pass`, never on `score` or `grade` alone.

── Locked policy (v1.1 correction pass — corroborated 2-3 ways in the existing
   codebase, or a plain structural axiom) ────────────────────────────────────
  max_otm_pct          7.0    — strategy_service.MAX_OPTION_OTM_PCT and
                                 options_desk.analyse_contract()'s OPT_MAX_OTM_PCT
                                 independently converge on 7%.
  min_dte_days         7      — options_desk's OPT_MIN_DTE and
                                 strategy_service's `7 <= dte <= 60` expiry
                                 filter both converge on 7; a 0-DTE contract can
                                 never get quality_pass=True.
  max_quote_age_hours  30.0   — options_desk's OPT_MAX_QUOTE_AGE_H and
                                 dashboard/scanner.py's stale-quote default both
                                 use 30h.
  missing/one-sided/zero bid  — a structural axiom (can't price a quote nobody
                                 is making), not a calibrated number.

── Open / provisional policy (v1.1 correction 6 — INSUFFICIENT DATA: this
   session confirmed both ~/.tradingview_mcp_data/portfolio.db (11 tables, 0
   rows) and ~/.tradingview_mcp_data/paper/ (does not exist) hold zero
   historical shadow/paper records to calibrate against) ───────────────────
  oi_floor_x                50    — grade_contract()'s existing effective hard
                                     floor. Below this: HARD FAIL / ILLIQUID.
  oi_floor_y                250   — analyse_contract()'s existing hard floor.
                                     At/above this: ADEQUATE+. [oi_floor_x,
                                     oi_floor_y) is the THIN / soft-penalty band
                                     — using BOTH existing conflicting numbers as
                                     the band's two ends, rather than picking
                                     one, so nothing here claims a resolution
                                     the data doesn't support.
  max_spread_pct             12.0  — analyse_contract()'s current ceiling (the
                                     stricter of the two candidates: 12 vs 20).
  max_dte_warn_days          60    — strategy_service's actual expiry-loop
                                     ceiling (the tighter of 60 vs 90). SOFT
                                     WARNING only, never a hard fail.
  min_session_open_volume    10    — analyse_contract()'s current floor,
                                     "tentatively accepted" per v1.1 correction
                                     6, kept configurable rather than locked.
  spread_score_reference_pct 10.0  — _grade_option_chain()'s scoring reference
                                     (score reaches 0 at this spread%). NEVER a
                                     hard-fail threshold — do not confuse with
                                     max_spread_pct.
  oi_score_normalizer        500.0 — _grade_option_chain()'s OI depth divisor.
  volume_score_normalizer    100.0 — _grade_option_chain()'s volume depth
                                     divisor.
Every "open" field above is a ContractQualityPolicy attribute precisely so a
caller can override it; treat the defaults as provisional, not as answers.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

# Reuse the existing, tested Black-Scholes implementation rather than
# reimplementing it. As of Step 4.1 this is a plain intra-package import —
# no dashboard module, no sys.path mutation, no transitive dependency on
# options_desk.py/research.py/Flask. See canonical/option_risk_math.py's
# module docstring for the full extraction history.
from .option_risk_math import bs_greeks


# ══════════════════════════════════════════════════════════════════════════
# Enums
# ══════════════════════════════════════════════════════════════════════════

class LiquidityTier(str, Enum):
    ILLIQUID = "illiquid"          # OI < oi_floor_x -> also a hard failure
    THIN = "thin"                  # oi_floor_x <= OI < oi_floor_y -> soft penalty
    ADEQUATE_PLUS = "adequate+"    # OI >= oi_floor_y


class GreeksProvenance(str, Enum):
    OBSERVED = "observed"      # delta came from the provider
    MODEL = "model"            # delta was substituted via Black-Scholes
    UNAVAILABLE = "unavailable"  # neither observed nor modelable -> hard fail


class FreshnessStatus(str, Enum):
    FRESH = "fresh"
    STALE = "stale"          # timestamp present, too old -> hard fail
    UNKNOWN = "unknown"      # no timestamp at all -> hard fail


# ══════════════════════════════════════════════════════════════════════════
# Policy (all OPEN/provisional numeric knobs, plus the LOCKED ones named as
# constants rather than magic numbers — see module docstring for which is
# which). Deliberately NOT the future canonical.risk_policy.RiskPolicy: this
# object carries zero dollars, zero equity, zero account concept of any kind.
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ContractQualityPolicy:
    # locked (v1.1 correction pass)
    max_otm_pct: float = 7.0
    min_dte_days: int = 7
    max_quote_age_hours: float = 30.0

    # open / provisional (v1.1 correction 6 — INSUFFICIENT DATA)
    oi_floor_x: int = 50
    oi_floor_y: int = 250
    max_spread_pct: float = 12.0
    max_dte_warn_days: int = 60
    min_session_open_volume: int = 10

    # open / provisional — scoring-only, never gate hard_failures
    spread_score_reference_pct: float = 10.0
    oi_score_normalizer: float = 500.0
    volume_score_normalizer: float = 100.0

    # informational: which fields above are still open policy decisions,
    # kept in code so a caller/test can introspect it rather than relying on
    # the docstring alone.
    OPEN_FIELDS = (
        "oi_floor_x", "oi_floor_y", "max_spread_pct", "max_dte_warn_days",
        "min_session_open_volume", "spread_score_reference_pct",
        "oi_score_normalizer", "volume_score_normalizer",
    )

    def __post_init__(self) -> None:
        if not (self.oi_floor_x < self.oi_floor_y):
            raise ValueError(
                f"oi_floor_x ({self.oi_floor_x}) must be strictly less than "
                f"oi_floor_y ({self.oi_floor_y}) — the three-tier OI model "
                "(hard-fail / thin / adequate+) requires a non-empty, "
                "non-overlapping THIN band."
            )
        if self.max_otm_pct <= 0 or self.min_dte_days < 0 or self.max_quote_age_hours <= 0:
            raise ValueError("locked policy fields must be positive")


DEFAULT_POLICY = ContractQualityPolicy()


# ══════════════════════════════════════════════════════════════════════════
# Result
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ContractQualityResult:
    quality_pass: bool
    score: float
    grade: str
    hard_failures: List[str]
    warnings: List[str]

    spread_pct: Optional[float]
    spread_dollars: Optional[float]
    moneyness_pct: Optional[float]
    dte: Optional[int]

    liquidity_tier: Optional[LiquidityTier]
    greeks_provenance: GreeksProvenance
    missing_observed_delta: bool
    freshness_status: FreshnessStatus

    # extra diagnostics, additive to the architecture's minimum schema
    two_sided: bool
    open_interest: Optional[int]
    volume: Optional[int]
    quote_age_hours: Optional[float]


# ══════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════

def _parse_iso(ts: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _grade(score: float) -> str:
    return ("A" if score >= 80 else "B" if score >= 60 else
            "C" if score >= 40 else "D" if score >= 20 else "F")


# ══════════════════════════════════════════════════════════════════════════
# The canonical Layer-A entry point
# ══════════════════════════════════════════════════════════════════════════

def evaluate_contract_quality(
    *,
    strike: Optional[float],
    underlying: Optional[float],
    side: str,
    bid: Optional[float],
    ask: Optional[float],
    volume: Optional[int],
    open_interest: Optional[int],
    implied_volatility: Optional[float],
    delta: Optional[float],
    dte: Optional[int],
    quote_timestamp: Optional[str],
    session_open: bool = True,
    now: Optional[datetime] = None,
    allow_model_greeks: bool = True,
    risk_free_rate: float = 0.042,
    policy: Optional[ContractQualityPolicy] = None,
) -> ContractQualityResult:
    """Layer A. No `balance`/`equity`/`buying_power`/`portfolio` parameter
    exists on this signature, by construction — there is no **kwargs, so
    passing one raises TypeError immediately rather than being silently
    accepted and ignored.

    `now` is injectable so freshness is deterministic and testable; it
    defaults to the real wall clock for production use. `implied_volatility`
    is expected as a fraction (0.30, not 30.0), matching the rest of the
    codebase's primary convention.
    """
    pol = policy or DEFAULT_POLICY
    now = now or datetime.now(timezone.utc)
    side = (side or "CALL").upper()
    hard_failures: List[str] = []
    warnings: List[str] = []

    # ── bid/ask: distinguish None (missing) from 0 (a genuine, quoted zero) ──
    two_sided = False
    spread_dollars: Optional[float] = None
    spread_pct: Optional[float] = None
    mid: Optional[float] = None
    if bid is None and ask is None:
        hard_failures.append("missing bid/ask — no quote available")
    elif bid is None:
        hard_failures.append("missing bid — one-sided quote (ask only)")
    elif ask is None:
        hard_failures.append("missing ask — one-sided quote (bid only)")
    elif bid == 0 and ask == 0:
        hard_failures.append("zero bid and zero ask — no market")
    elif bid == 0:
        hard_failures.append("zero bid — no one is bidding, cannot exit at any price")
    elif ask == 0:
        hard_failures.append("zero ask — quote is not usable")
    elif bid > ask:
        hard_failures.append(f"crossed quote — bid {bid} > ask {ask}")
    else:
        two_sided = True
        mid = round((bid + ask) / 2, 4)
        spread_dollars = round(ask - bid, 4)
        spread_pct = round(spread_dollars / mid * 100, 2) if mid else None
        if spread_pct is not None and spread_pct > pol.max_spread_pct:
            hard_failures.append(
                f"spread {spread_pct}% exceeds the {pol.max_spread_pct}% canonical "
                "ceiling (open/provisional — v1.1 correction 6)")

    # ── moneyness / OTM% (direction-aware, matches analyse_contract()'s sign
    #    convention) ─────────────────────────────────────────────────────────
    moneyness_pct: Optional[float] = None
    if strike is not None and underlying:
        raw = (strike - underlying) / underlying * 100.0
        moneyness_pct = round(raw if side == "CALL" else -raw, 2)
        if abs(moneyness_pct) > pol.max_otm_pct:
            hard_failures.append(
                f"{abs(moneyness_pct)}% OTM exceeds the {pol.max_otm_pct}% "
                f"canonical ceiling ({side})")
    else:
        warnings.append("cannot compute moneyness — strike or underlying price unavailable")

    # ── DTE: distinguish None (missing) from 0 (genuine 0-DTE) ──────────────
    if dte is None:
        hard_failures.append("missing DTE — cannot verify the minimum-DTE floor")
    elif dte < pol.min_dte_days:
        hard_failures.append(
            f"DTE {dte} is below the {pol.min_dte_days}-day minimum"
            + (" (0-DTE)" if dte == 0 else ""))
    elif dte > pol.max_dte_warn_days:
        warnings.append(
            f"DTE {dte} exceeds the {pol.max_dte_warn_days}-day soft ceiling "
            "(open/provisional) — long-dated, confirm this is intentional")

    # ── quote freshness ──────────────────────────────────────────────────────
    quote_age_hours: Optional[float] = None
    if quote_timestamp is None:
        hard_failures.append("missing quote timestamp — freshness cannot be verified")
        freshness_status = FreshnessStatus.UNKNOWN
    else:
        parsed = _parse_iso(quote_timestamp)
        if parsed is None:
            hard_failures.append(f"unparseable quote timestamp {quote_timestamp!r}")
            freshness_status = FreshnessStatus.UNKNOWN
        else:
            quote_age_hours = round(max(0.0, (now - parsed).total_seconds() / 3600.0), 2)
            if quote_age_hours > pol.max_quote_age_hours:
                hard_failures.append(
                    f"stale quote — {quote_age_hours}h old exceeds the "
                    f"{pol.max_quote_age_hours}h canonical ceiling")
                freshness_status = FreshnessStatus.STALE
            else:
                freshness_status = FreshnessStatus.FRESH

    # ── open interest: three-tier model, X/Y open (v1.1 correction 1) ───────
    liquidity_tier: Optional[LiquidityTier] = None
    if open_interest is None:
        warnings.append("missing open interest — liquidity cannot be verified")
    elif open_interest < pol.oi_floor_x:
        liquidity_tier = LiquidityTier.ILLIQUID
        hard_failures.append(
            f"open interest {open_interest} is below the illiquid floor of "
            f"{pol.oi_floor_x} (open/provisional — v1.1 correction 6)")
    elif open_interest < pol.oi_floor_y:
        liquidity_tier = LiquidityTier.THIN
        warnings.append(
            f"open interest {open_interest} is thin "
            f"({pol.oi_floor_x}-{pol.oi_floor_y - 1}) — soft penalty, not a "
            "hard fail (open/provisional band)")
    else:
        liquidity_tier = LiquidityTier.ADEQUATE_PLUS

    # ── session-aware volume: distinguish None (missing) from 0 (genuine) ───
    if volume is None:
        warnings.append("missing volume — session activity cannot be verified")
    elif session_open and volume < pol.min_session_open_volume:
        hard_failures.append(
            f"volume {volume} below the {pol.min_session_open_volume}-contract "
            "session-open floor (open/provisional)")
    elif not session_open and volume < pol.min_session_open_volume:
        warnings.append(
            f"volume {volume} is below the session-open floor, but the market "
            "is closed — waived, not a hard fail")

    # ── Greeks: three-state provenance, never the historical 100/100 bug ────
    missing_observed_delta = delta is None
    modeled_delta_val: Optional[float] = None
    if delta is not None:
        greeks_provenance = GreeksProvenance.OBSERVED
    else:
        modeled = None
        if allow_model_greeks and implied_volatility and dte and dte > 0 and strike and underlying:
            modeled = bs_greeks(underlying, strike, implied_volatility, dte, side, risk_free_rate)
        if modeled is not None:
            modeled_delta_val = modeled["delta"]
            greeks_provenance = GreeksProvenance.MODEL
            warnings.append(
                "delta is MODELED (Black-Scholes), not observed — original "
                "provider delta was missing; treat with reduced confidence "
                f"(modeled delta={modeled_delta_val})")
        else:
            greeks_provenance = GreeksProvenance.UNAVAILABLE
            hard_failures.append(
                "Greeks unavailable — delta missing and cannot be modeled "
                + ("(no usable IV/DTE/strike/underlying to model from)"
                   if allow_model_greeks else "(modeling disabled)"))

    # ── IV: soft — missing is a warning, extreme is a warning, neither hard-fails
    if implied_volatility is None:
        warnings.append("missing implied volatility — cannot compute implied move; "
                        "Greeks cannot be modeled if the observed delta is also missing")
    elif implied_volatility > 1.20:
        warnings.append(
            f"extreme IV {round(implied_volatility * 100, 1)}% — verify before "
            "trusting modeled Greeks or pricing (not a hard failure solely for being extreme)")

    # ── informational score (never gates quality_pass) ───────────────────────
    spread_component = (_clamp01(1.0 - (spread_pct / pol.spread_score_reference_pct))
                        if spread_pct is not None and pol.spread_score_reference_pct else 0.0)
    depth_component = (_clamp01((open_interest or 0) / pol.oi_score_normalizer) * 0.6
                       + _clamp01((volume or 0) / pol.volume_score_normalizer) * 0.4)
    dte_component = (1.0 if (dte is not None and pol.min_dte_days <= dte <= pol.max_dte_warn_days)
                     else (0.5 if dte is not None else 0.0))
    score = round(100 * (0.5 * spread_component + 0.3 * depth_component + 0.2 * dte_component), 1)
    grade = _grade(score)

    return ContractQualityResult(
        quality_pass=not hard_failures,
        score=score,
        grade=grade,
        hard_failures=hard_failures,
        warnings=warnings,
        spread_pct=spread_pct,
        spread_dollars=spread_dollars,
        moneyness_pct=moneyness_pct,
        dte=dte,
        liquidity_tier=liquidity_tier,
        greeks_provenance=greeks_provenance,
        missing_observed_delta=missing_observed_delta,
        freshness_status=freshness_status,
        two_sided=two_sided,
        open_interest=open_interest,
        volume=volume,
        quote_age_hours=quote_age_hours,
    )


# ══════════════════════════════════════════════════════════════════════════
# Adapters — read-only translations from the two EXISTING raw contract-dict
# shapes into a Layer-A call. Neither options_grading.py nor options_desk.py
# is imported for its grading logic here, only used as a shape reference; no
# call site anywhere is migrated to use these.
# ══════════════════════════════════════════════════════════════════════════

def from_grade_contract_shape(
    contract: Dict[str, Any], underlying: Optional[float], side: str, dte: Optional[int],
    *, quote_timestamp: Optional[str] = None, session_open: bool = True,
    now: Optional[datetime] = None, policy: Optional[ContractQualityPolicy] = None,
) -> ContractQualityResult:
    """Adapts options_grading.grade_contract()'s (contract, underlying, side,
    dte) call shape. grade_contract() has no quote_timestamp concept at all,
    so it must be supplied separately — passing None (its implicit default)
    means this will legitimately hard-fail on freshness, which is itself part
    of the divergence audit's finding, not a bug in the adapter."""
    return evaluate_contract_quality(
        strike=contract.get("strike"), underlying=underlying, side=side,
        bid=contract.get("bid"), ask=contract.get("ask"),
        volume=contract.get("volume"), open_interest=contract.get("open_interest"),
        implied_volatility=contract.get("implied_volatility"),
        delta=contract.get("delta"), dte=dte, quote_timestamp=quote_timestamp,
        session_open=session_open, now=now, policy=policy,
    )


def from_analyse_contract_shape(
    contract: Dict[str, Any], spot: Optional[float],
    *, session_open: bool = True, now: Optional[datetime] = None,
    policy: Optional[ContractQualityPolicy] = None,
) -> ContractQualityResult:
    """Adapts options_desk.analyse_contract()'s (contract, spot=...) shape,
    including deriving DTE from its `expiry` date string the same way
    analyse_contract() itself does."""
    dte = None
    expiry = contract.get("expiry")
    if expiry:
        try:
            ref = (now or datetime.now(timezone.utc)).date()
            dte = (date.fromisoformat(str(expiry)) - ref).days
        except Exception:
            dte = None
    return evaluate_contract_quality(
        strike=contract.get("strike"), underlying=spot,
        side=contract.get("side", "CALL"),
        bid=contract.get("bid"), ask=contract.get("ask"),
        volume=contract.get("volume"), open_interest=contract.get("open_interest"),
        implied_volatility=contract.get("implied_volatility"),
        delta=contract.get("delta"), dte=dte,
        quote_timestamp=contract.get("quote_timestamp"),
        session_open=session_open, now=now, policy=policy,
    )
