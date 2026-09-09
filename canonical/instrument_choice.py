"""Canonical Option Architecture v1.1 — Layer D: instrument choice.

Answers exactly one question: "Given a valid directional setup and
separately evaluated stock and option legs, should the system use STOCK,
OPTION, or NO_TRADE?" It owns PREFERENCE only — it never grades contract
structure (Layer A), never calculates affordability or risk caps (Layer B),
never evaluates the directional setup (Layer C), never sizes a position
(Layer E, Step 6), and never executes anything.

── Phase 1: dashboard.option_risk.instrument_choice() input classification ──
(read this session; see the Step 5 report for the full write-up)

  stock: dict (planned_risk, capital_committed)   -> UPSTREAM B. Canonical
                                                      reads AccountFitResult
                                                      directly; no raw dict.
  option: dict (planned_risk, capital_committed,
          confidence)                              -> UPSTREAM B. `confidence`
                                                      specifically flows through
                                                      as the structured
                                                      AccountFitResult.risk_confidence
                                                      field added in Step 5.1
                                                      (see below) — not parsed
                                                      from free text.
  option_eligibility: dict (eligible, reason)       -> UPSTREAM B. Canonical
                                                      reads
                                                      option_account_fit.eligible.
  stock_sizeable: bool                              -> UPSTREAM B. Canonical
                                                      reads
                                                      stock_account_fit.eligible.
  option_quality_ok: bool                           -> UPSTREAM A. Canonical
                                                      reads
                                                      contract_quality.quality_pass.
  reward_per_share: Optional[float]                 -> OBSOLETE. Declared in
                                                      the legacy signature but
                                                      never read in the legacy
                                                      function body. Dropped.
  option_edge: dict (theta_pct_per_day,
          break_even_within_expected_move)          -> KEEP IN D as a genuine
                                                      comparative-preference
                                                      input. The VALUES are
                                                      computed upstream
                                                      (decision_engine/
                                                      options_desk from EV/
                                                      Greeks data) — D only
                                                      compares them. Canonical:
                                                      the OptionEdge dataclass
                                                      below, extended with
                                                      `ev_positive` (see item
                                                      10 in the Step 5 report
                                                      — the _grade_option_chain()
                                                      `ev_ok` preservation).
  quality_tilt: int                                 -> KEEP IN D as an
                                                      optional comparative
                                                      nudge. Its SOURCE is
                                                      Layer A's score (a
                                                      structural-quality
                                                      comparison), but D does
                                                      not compute it — a
                                                      caller supplies it.
  (none — new)  setup_tradeable: bool                -> SETUP/C RESPONSIBILITY.
                                                      The legacy function has
                                                      NO setup-validity gate at
                                                      all — its only caller
                                                      (options_desk.decide())
                                                      always invoked it after
                                                      already confirming the
                                                      setup elsewhere. v1.1's
                                                      corrected decision order
                                                      makes this an explicit,
                                                      first-class precondition
                                                      of D itself, not an
                                                      assumption about what
                                                      the caller already did.

DISPLAY-ONLY: none identified — every legacy field maps to a real decision
input somewhere in A/B/C/D.

RESOLVED in Step 5.1 (was a documented gap in Step 5): the legacy function's
`if option["confidence"] == "low": for_stock.append(...)` comparative signal
(a low-confidence, fallback-priced option is nudged toward the stock leg) is
now restored. `canonical.account_fit.AccountFitResult` gained a structured
`risk_confidence: Optional[RiskConfidence]` field (Step 5.1) carrying the
exact same value `option_risk_math.option_risk()`'s own `confidence` key
already produced — this module reads that typed field directly, never
`"low" in assumption`-style string matching. See the comparative branch
below and `canonical/account_fit.py`'s `RiskConfidence` docstring for the
full provenance trace.

── Extraction strategy (Phase 3) ──────────────────────────────────────────
`dashboard/option_risk.py:instrument_choice()` is LEFT ENTIRELY UNCHANGED —
zero modification, confirmed by `git diff` showing no change to that file.
Rationale: its dict-based signature is fundamentally different in shape from
the canonical typed one (raw dicts vs. AccountFitResult/ContractQualityResult
objects), and it is directly exercised by existing, currently-passing tests
(tests/unit/test_option_risk.py — test_no_trade_when_neither_instrument_fits,
test_shares_are_not_preferred_merely_because_option_max_loss_exceeds_the_stop,
test_instrument_verdict_maps_onto_the_existing_decision_vocabulary, plus
indirect coverage through evaluate_candidate()/decide()). A wrapper/adapter
that reshapes dicts into typed objects and back would touch a well-tested
production function for zero behavioral gain and real regression risk
(rebuilding the dropped `confidence` signal, matching every dict key,
etc.) for a step whose acceptance criterion does not require it. The task's
own fallback explicitly sanctions this: "If a compatibility adapter is
necessary, prove output parity instead" — parity is proven here via a
side-by-side legacy-vs-canonical test matrix
(tests/unit/test_instrument_choice.py's TestLegacyParity class), not object
identity and not a live delegation. `dashboard/option_risk.py` is therefore
NOT part of this step's file-modification list, despite being named as an
"expected" file in the task — a deliberate, reported deviation per the
task's own "stop and report before broadening scope" principle, applied
here in the conservative direction (touching LESS than expected, not more).

ADDITIVE ONLY, otherwise. No caller anywhere is migrated.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from .account_fit import AccountFitResult, RiskConfidence
from .contract_quality import ContractQualityResult


# ══════════════════════════════════════════════════════════════════════════
# Types
# ══════════════════════════════════════════════════════════════════════════

class InstrumentChoice(str, Enum):
    STOCK = "STOCK"
    OPTION = "OPTION"
    NO_TRADE = "NO_TRADE"


@dataclass(frozen=True)
class OptionEdge:
    """Comparative preference signals for the option leg. Every value is
    computed upstream (decision_engine/options_desk, from EV/Greeks/expected-
    move data Layer D never touches) — this dataclass only carries them into
    the comparison; D neither computes nor validates them.
    """
    theta_pct_per_day: Optional[float] = None
    break_even_within_expected_move: Optional[bool] = None
    # Generalizes _grade_option_chain()'s `ev_ok = option_block.ev_per_contract
    # > 0` (v1.1 correction 5 preservation map, item "ev_ok"). Positive EV may
    # only ever influence OPTION vs STOCK preference AFTER option eligibility
    # (Layer B) has already passed — it can never override B, and this
    # dataclass is only ever consulted in choose_instrument()'s comparative
    # branch, which is unreachable unless both legs are already eligible.
    ev_positive: Optional[bool] = None


@dataclass(frozen=True)
class InstrumentChoiceResult:
    choice: InstrumentChoice
    reason: str
    reasons: Tuple[str, ...]
    stock_eligible: bool
    option_eligible: bool
    # Populated only when both legs were eligible and the comparative branch
    # actually ran; None otherwise (a terminal-eligibility case never
    # computes a tally — there is nothing to compare).
    option_score: Optional[float] = None
    stock_score: Optional[float] = None


# ══════════════════════════════════════════════════════════════════════════
# The canonical Layer-D entry point
# ══════════════════════════════════════════════════════════════════════════

def _option_unavailable_reason(contract_quality: Optional[ContractQualityResult],
                               option_account_fit: Optional[AccountFitResult]) -> str:
    if contract_quality is None:
        return "no_option_candidate"
    if not contract_quality.quality_pass:
        return "option_structurally_invalid"
    if option_account_fit is None:
        return "no_option_account_fit"
    if not option_account_fit.eligible:
        return "option_account_ineligible"
    return "option_available"  # should not be reached by callers of this helper


def choose_instrument(
    *,
    setup_tradeable: bool,
    stock_account_fit: Optional[AccountFitResult],
    option_account_fit: Optional[AccountFitResult],
    contract_quality: Optional[ContractQualityResult],
    option_edge: Optional[OptionEdge] = None,
    quality_tilt: float = 0.0,
) -> InstrumentChoiceResult:
    """Layer D. Pick STOCK, OPTION, or NO_TRADE.

    Preconditions, checked before anything else can matter, in this exact
    order (v1.1's corrected decision invariant):

        1. setup_tradeable must be True, or the result is NO_TRADE
           unconditionally — no leg is even inspected.
        2. stock_available = stock_account_fit is not None and .eligible
        3. option_available = contract_quality is not None and
           contract_quality.quality_pass and option_account_fit is not None
           and option_account_fit.eligible

    `option_available == False` can NEVER produce OPTION, regardless of any
    edge/quality_tilt signal — no comparative score can resurrect an
    ineligible leg. Symmetrically, `stock_available == False` can never
    produce STOCK. Only when BOTH are available does the comparative branch
    (capital efficiency, planned risk, theta, break-even, EV, quality_tilt)
    run at all.

    Raises TypeError on a malformed (wrong-type) input — a caller/programming
    error, not a trade-data variation. A None stock_account_fit/
    option_account_fit/contract_quality is a normal, expected "this leg
    was never evaluated" state, not an error.
    """
    if not isinstance(setup_tradeable, bool):
        raise TypeError(f"setup_tradeable must be bool, got {type(setup_tradeable).__name__}")
    for name, val, cls in (("stock_account_fit", stock_account_fit, AccountFitResult),
                           ("option_account_fit", option_account_fit, AccountFitResult),
                           ("contract_quality", contract_quality, ContractQualityResult)):
        if val is not None and not isinstance(val, cls):
            raise TypeError(f"{name} must be a {cls.__name__} or None, got {type(val).__name__}")
    if option_edge is not None and not isinstance(option_edge, OptionEdge):
        raise TypeError(f"option_edge must be an OptionEdge or None, got {type(option_edge).__name__}")

    if not setup_tradeable:
        return InstrumentChoiceResult(
            choice=InstrumentChoice.NO_TRADE, reason="setup_not_tradeable",
            reasons=("setup_not_tradeable",), stock_eligible=False, option_eligible=False)

    stock_available = bool(stock_account_fit and stock_account_fit.eligible)
    option_available = bool(contract_quality and contract_quality.quality_pass
                            and option_account_fit and option_account_fit.eligible)

    if not stock_available and not option_available:
        opt_reason = _option_unavailable_reason(contract_quality, option_account_fit)
        return InstrumentChoiceResult(
            choice=InstrumentChoice.NO_TRADE, reason="neither_instrument_eligible",
            reasons=("stock_account_ineligible", opt_reason),
            stock_eligible=False, option_eligible=False)

    if stock_available and not option_available:
        opt_reason = _option_unavailable_reason(contract_quality, option_account_fit)
        return InstrumentChoiceResult(
            choice=InstrumentChoice.STOCK, reason=f"{opt_reason}_stock_available",
            reasons=(opt_reason, "stock_account_eligible"),
            stock_eligible=True, option_eligible=False)

    if option_available and not stock_available:
        return InstrumentChoiceResult(
            choice=InstrumentChoice.OPTION, reason="stock_ineligible_option_available",
            reasons=("stock_account_ineligible", "option_available"),
            stock_eligible=False, option_eligible=True)

    # ── both legs available — compare efficiency, not leverage ──────────────
    # Preserves dashboard.option_risk.instrument_choice()'s proven tally
    # logic exactly (verified this session): capital committed, planned
    # risk, theta drag, break-even-within-expected-move, plus the
    # generalized EV signal and an optional caller-supplied quality_tilt
    # sourced from Layer A's score. Deliberately NOT the two reflexes the
    # legacy function's own docstring says it replaced: shares are not
    # preferred merely because an option's max loss exceeds the stock's
    # stop distance, and options are not preferred merely because they
    # carry more leverage.
    s_planned = stock_account_fit.planned_risk or 0.0
    o_planned = option_account_fit.planned_risk or 0.0
    s_capital = stock_account_fit.capital_required or 0.0
    o_capital = option_account_fit.capital_required or 0.0
    for_option: list = []
    for_stock: list = []

    if o_capital and s_capital and o_capital < s_capital:
        for_option.append(f"less capital committed (${o_capital:,.2f} vs ${s_capital:,.2f})")
    elif o_capital and s_capital:
        for_stock.append(f"less capital committed (${s_capital:,.2f} vs ${o_capital:,.2f})")

    if o_planned and s_planned:
        if o_planned <= s_planned:
            for_option.append(f"lower planned risk (${o_planned:,.2f} vs ${s_planned:,.2f})")
        else:
            for_stock.append(f"lower planned risk (${s_planned:,.2f} vs ${o_planned:,.2f})")

    # Step 5.1: restores the legacy `option["confidence"] == "low" ->
    # penalty toward STOCK` signal, reading canonical B's STRUCTURED
    # risk_confidence field (never assumptions free text — no
    # `"low" in assumption`-style parsing anywhere in this module). Only
    # reachable here, in the both-available comparative branch: it never
    # affects eligibility and never resurrects an ineligible leg, because
    # this whole branch is unreachable unless option_available was already
    # True (Layer B already passed).
    if option_account_fit.risk_confidence == RiskConfidence.LOW:
        for_stock.append("the option's planned risk could not be modelled — the whole "
                         "premium has to be assumed at risk")

    edge = option_edge or OptionEdge()
    if edge.theta_pct_per_day is not None and edge.theta_pct_per_day > 1.5:
        for_stock.append(f"theta costs {edge.theta_pct_per_day:.1f}% of premium per day; "
                         "shares do not decay")
    if edge.break_even_within_expected_move is False:
        for_stock.append("break-even sits outside the expected move for this expiry")
    elif edge.break_even_within_expected_move is True:
        for_option.append("break-even sits inside the expected move for this expiry")
    if edge.ev_positive is True:
        for_option.append("positive expected value at this DTE/IV")
    elif edge.ev_positive is False:
        for_stock.append("expected value is not positive at this DTE/IV")

    score_option = len(for_option) + max(0.0, quality_tilt)
    score_stock = len(for_stock) + max(0.0, -quality_tilt)
    # Tie -> STOCK, deterministically (`>`, not `>=`) — identical to the
    # legacy function's own tie behavior, pinned in tests, never left to
    # dict/set/hash ordering.
    if score_option > score_stock:
        choice = InstrumentChoice.OPTION
        reason = "option_edge_superior"
    else:
        choice = InstrumentChoice.STOCK
        reason = "stock_edge_superior" if score_stock > score_option else "tie_favors_stock"

    return InstrumentChoiceResult(
        choice=choice, reason=reason,
        reasons=tuple(for_option) if choice == InstrumentChoice.OPTION else tuple(for_stock),
        stock_eligible=True, option_eligible=True,
        option_score=score_option, stock_score=score_stock,
    )
