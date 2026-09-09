"""Canonical Option Architecture v1.1 — Layer E: position sizing.

Answers exactly one question: "Given the instrument Layer D selected and the
maximum quantity Layer B permits, what quantity should this trade actually
carry?" It owns the FINAL CLAMP AND ROUNDING step only — it never re-grades
contract structure (A), never recalculates affordability or risk caps (B),
never chooses STOCK vs OPTION (D), never evaluates the directional setup
(C), and never executes, fetches account state, or fetches market data.

── Phase 1: existing sizing implementations, inspected this session ───────

lab/paper/risk.py:position_size()
  Inputs: equity, entry, stop, risk_fraction, fill_price, buying_power.
  Formula: qty = min(risk_budget/per_share, notional_cap/px, buying_power/px),
  where risk_budget = min(max_loss_per_trade, equity*risk_per_trade_pct).
  Floors to whole shares when fractional_shares is False; reports a
  binding_constraint among {risk_budget, position_cap, buying_power,
  whole_share_minimum, fractional_minimum}. No conviction/setup-quality
  input. Genuinely authoritative for Strategy-500 today.
  DISPOSITION: ALREADY OWNED BY B. Step 4's stock_account_fit() already
  reimplements this exact three-way min()+flooring pattern via
  canonical.risk_policy.effective_limit() — AccountFitResult.quantity_allowed
  IS this ceiling. E must not recompute any part of it.

strategy_service.py:_size_option()
  Inputs: premium, balance. Formula: max_commit = balance*0.25 (MAX_COMMIT_PCT,
  a flat 25%-of-balance cap with NO connection to any per-trade risk cap);
  contracts = int(max_commit // (premium*100)), clamped to [1, 3];
  max_loss = contracts*premium*100; planned_risk = max_loss*0.5. For
  premium=$1.00, balance=$500: max_commit=$125, cost/contract=$100,
  contracts=1, max_loss=$100 — the exact historical mismatch against
  Strategy-500's real $5 per-trade cap.
  DISPOSITION: RETIRE. Not ported in any form — canonical B(option)'s
  eligibility+quantity_allowed (0, since $100.06 > $5 effective_limit)
  supersedes this formula entirely. Left untouched in production this step
  (caller migration is out of scope); `test_no_path_exceeds_configured_per_trade_cap`
  continues to guard it directly and stays red until migration.

lab/decision_engine.py:evaluate()'s inline stock sizing (lines ~541-566)
  risk_budget = equity*(0.03 if not spec else 0.015); uncertainty_scale =
  max(0.3, agreement*data_conf) — agreement (cross-family signal alignment)
  and data_conf (mean family confidence) scale the budget within [0.3, 1.0]
  of the base 3%/1.5%; dollar_risk = risk_budget*uncertainty_scale, then
  optionally clamped by a SEPARATE portfolio engine (risk_engine.py,
  untouched, out of scope); shares = dollar_risk/risk_ps.
  DISPOSITION: PIPELINE-SPECIFIC TARGET-SIZE CANDIDATE, not duplicated risk
  math to retire outright — agreement/data_conf genuinely represent a
  strategy's OWN conviction-scaled preference, distinct from B's hard caps.
  The historical audit found this formula alone (uncertainty_scale up to
  1.0) can suggest ~3.82 shares (~$12.04 planned risk) against a $500
  account whose real cap is $5 — i.e. it is unsafe to trust directly. This
  step does NOT migrate decision_engine to call E (out of scope), but
  designs E so that WHEN it eventually does, this computed number becomes
  exactly a `target_quantity` input — clamped to B's ceiling by
  construction, never trusted as a final size. See
  test_conviction_target_above_b_ceiling_is_clamped for the proof:
  target=3.82 (the historical "strong conviction" number), B ceiling=1.25
  (Strategy-500 at $500 equity) -> E=1.25, never 3.82.

canonical/account_fit.py (Step 4) / dashboard/option_risk.py (untouched)
  AccountFitResult.quantity_allowed is already the authoritative ceiling for
  both instruments: a float (possibly fractional, exactly matching
  RiskPolicy.fractional_shares as already applied by B) for stock, and
  strictly `contracts_candidate` or `0` (always an int) for options. E reads
  this field only — it never re-reads RiskPolicy.fractional_shares, equity,
  buying power, or any cap directly.

No other sizing implementation was found in this neighborhood; the repo has
no broker/execution-constraint code today (confirmed by inspection) — so
`ExecutionConstraints` below is a genuinely new, additive concept with no
legacy formula to reconcile.

── Canonical principle ─────────────────────────────────────────────────────

    requested / target quantity
              v
    min(target quantity, B.quantity_allowed)      <- the ONLY risk clamp
              v
    instrument-specific rounding (options: integer, always)
              v
    ExecutionConstraints (broker minimum/increment/whole-units), if supplied
              v
    final quantity

`quantity <= AccountFitResult.quantity_allowed` holds by construction, not
by a runtime re-check of policy — there is no second, independent risk
budget anywhere in this module.

ADDITIVE ONLY. No caller anywhere in the repository is migrated to use this
module. `lab/paper/risk.py`, `strategy_service._size_option()`,
`decision_engine.evaluate()`, tracker/journal/workflow, and every execution
path are all untouched.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from .account_fit import AccountFitResult, InstrumentType
from .instrument_choice import InstrumentChoice, InstrumentChoiceResult


# ══════════════════════════════════════════════════════════════════════════
# Types
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ExecutionConstraints:
    """Pure data only — no broker client, account API, environment state, or
    live positions. For options, whole-unit-only is intrinsic (enforced
    unconditionally below, per the task's own note that it "may not need to
    be configurable") — this object exists for the STOCK case and for any
    broker-specific minimum/increment on either instrument.
    """
    min_quantity: Optional[float] = None
    quantity_increment: Optional[float] = None
    whole_units_only: bool = False


@dataclass(frozen=True)
class SizingResult:
    instrument: InstrumentChoice
    quantity: float
    max_quantity_allowed: float
    requested_quantity: Optional[float]
    binding_constraint: Optional[str]
    # Named `sizeable`, not `executable` — per the task's own guidance, this
    # means only "E produced a positive, valid quantity." The trade-wide
    # executable predicate (spanning A+B+D+E+execution policy+shadow mode)
    # is a later step's job, not this one's.
    sizeable: bool
    reasons: Tuple[str, ...]
    rounding_applied: bool = False


class SizingError(ValueError):
    """A programming/caller error — malformed input, not a normal trade-data
    variation. Mirrors the RAISE side of every prior layer's fail-closed-vs-
    raise convention (see canonical/account_fit.py's module docstring)."""


# ══════════════════════════════════════════════════════════════════════════
# The canonical Layer-E entry point
# ══════════════════════════════════════════════════════════════════════════

def _apply_execution_constraints(qty: float, constraints: Optional[ExecutionConstraints],
                                 force_whole_units: bool) -> Tuple[float, bool, Optional[str]]:
    """Returns (quantity, rounding_applied, binding_reason). Never rounds up
    — only down or to zero — so the B ceiling can never be exceeded here."""
    rounding_applied = False
    whole_units = force_whole_units or (constraints.whole_units_only if constraints else False)
    if whole_units and qty != int(qty):
        qty = float(int(qty))
        rounding_applied = True

    if constraints and constraints.quantity_increment:
        inc = constraints.quantity_increment
        if inc <= 0:
            raise SizingError(f"quantity_increment must be positive, got {inc}")
        steps = int(qty / inc + 1e-9)   # floor to the nearest whole increment
        rounded = round(steps * inc, 10)
        if rounded != qty:
            rounding_applied = True
        qty = rounded

    if constraints and constraints.min_quantity is not None:
        if qty < constraints.min_quantity:
            return 0.0, rounding_applied, "execution_constraint:min_quantity"

    return qty, rounding_applied, None


def size_instrument(
    *,
    choice: InstrumentChoiceResult,
    stock_account_fit: Optional[AccountFitResult],
    option_account_fit: Optional[AccountFitResult],
    target_quantity: Optional[float] = None,
    execution_constraints: Optional[ExecutionConstraints] = None,
) -> SizingResult:
    """Layer E. No RiskPolicy, equity, buying power, entry/stop, premium,
    OI, spread, or DTE parameter exists on this signature, by construction —
    those questions were already resolved upstream by A/B/D.

    Decision ordering, exactly:
      1. choice.choice == NO_TRADE -> quantity=0, no sizing math at all.
      2. choice.choice == STOCK -> require stock_account_fit.eligible and
         .quantity_allowed > 0, or fail closed. Option is ignored entirely.
      3. choice.choice == OPTION -> require option_account_fit.eligible and
         .quantity_allowed >= 1, or fail closed. Stock is ignored entirely.
      4. Clamp: quantity = min(target_quantity or ceiling, ceiling) — never
         target > ceiling winning, by construction.
      5. Instrument-specific rounding (options: always whole; a fractional
         target for an option is a caller/input error and raises, per
         "prefer validating the invariant rather than silently normalizing
         corrupted canonical state").
      6. ExecutionConstraints, if supplied — rounds down or to zero only.

    D's choice is authoritative and cannot be bypassed: a caller cannot
    obtain an OPTION quantity by supplying target_quantity while
    choice.choice == STOCK (or vice versa) — the non-selected leg's
    AccountFitResult is never even inspected.
    """
    if not isinstance(choice, InstrumentChoiceResult):
        raise SizingError(f"choice must be an InstrumentChoiceResult, got {type(choice).__name__}")
    for name, val in (("stock_account_fit", stock_account_fit),
                      ("option_account_fit", option_account_fit)):
        if val is not None and not isinstance(val, AccountFitResult):
            raise SizingError(f"{name} must be an AccountFitResult or None, got {type(val).__name__}")
    if target_quantity is not None and not isinstance(target_quantity, (int, float)):
        raise SizingError(f"target_quantity must be numeric or None, got {type(target_quantity).__name__}")
    if execution_constraints is not None and not isinstance(execution_constraints, ExecutionConstraints):
        raise SizingError("execution_constraints must be an ExecutionConstraints or None, got "
                          f"{type(execution_constraints).__name__}")

    if choice.choice == InstrumentChoice.NO_TRADE:
        return SizingResult(instrument=InstrumentChoice.NO_TRADE, quantity=0.0,
                            max_quantity_allowed=0.0, requested_quantity=target_quantity,
                            binding_constraint=None, sizeable=False,
                            reasons=("no_trade_selected",))

    if choice.choice == InstrumentChoice.STOCK:
        fit = stock_account_fit
        force_whole = False
        if fit is not None and fit.instrument != InstrumentType.STOCK:
            raise SizingError(
                f"D selected STOCK but stock_account_fit.instrument is {fit.instrument!r} "
                "— mismatched upstream objects")
        if fit is None or not fit.eligible or not (fit.quantity_allowed > 0):
            return SizingResult(
                instrument=InstrumentChoice.STOCK, quantity=0.0, max_quantity_allowed=0.0,
                requested_quantity=target_quantity, binding_constraint=None, sizeable=False,
                reasons=("stock_account_fit_missing_or_ineligible",))
        ceiling = fit.quantity_allowed
        ceiling_binder = f"account_fit:{fit.binding_constraint}" if fit.binding_constraint else "account_fit"
    else:  # OPTION
        fit = option_account_fit
        force_whole = True
        if fit is not None and fit.instrument not in (InstrumentType.LONG_CALL, InstrumentType.LONG_PUT):
            raise SizingError(
                f"D selected OPTION but option_account_fit.instrument is {fit.instrument!r} "
                "— mismatched upstream objects")
        if fit is None or not fit.eligible or not (fit.quantity_allowed >= 1):
            return SizingResult(
                instrument=InstrumentChoice.OPTION, quantity=0, max_quantity_allowed=0,
                requested_quantity=target_quantity, binding_constraint=None, sizeable=False,
                reasons=("option_account_fit_missing_or_ineligible",))
        ceiling = fit.quantity_allowed
        if ceiling != int(ceiling):
            raise SizingError(
                f"option_account_fit.quantity_allowed must be a whole number, got {ceiling} "
                "— B's contract guarantees this; corrupted upstream state")
        ceiling_binder = f"account_fit:{fit.binding_constraint}" if fit.binding_constraint else "account_fit"

    # ── clamp: target is a downward limiter only, never a way to exceed B ──
    if target_quantity is None:
        # No current sizing path has a separate "target" concept at all —
        # position_size()/_size_option()/decision_engine's inline formula
        # each just return THE size to use, standalone. "No target supplied
        # -> use the full B ceiling" is the closest match to that existing
        # behavior, documented rather than assumed.
        quantity = ceiling
        binding = ceiling_binder
        requested = None
    elif target_quantity < 0:
        return SizingResult(
            instrument=choice.choice, quantity=0.0 if choice.choice == InstrumentChoice.STOCK else 0,
            max_quantity_allowed=ceiling, requested_quantity=target_quantity,
            binding_constraint=None, sizeable=False, reasons=("negative_target_quantity",))
    elif target_quantity == 0:
        quantity = 0.0 if choice.choice == InstrumentChoice.STOCK else 0
        binding = "target_quantity"
        requested = target_quantity
        return SizingResult(instrument=choice.choice, quantity=quantity,
                            max_quantity_allowed=ceiling, requested_quantity=requested,
                            binding_constraint=binding, sizeable=False, reasons=("zero_target_quantity",))
    else:
        quantity = min(target_quantity, ceiling)
        binding = "target_quantity" if target_quantity < ceiling else ceiling_binder
        requested = target_quantity

    # ── instrument-specific rounding ────────────────────────────────────────
    reasons = []
    if choice.choice == InstrumentChoice.OPTION:
        if quantity != int(quantity):
            raise SizingError(
                f"a fractional option quantity ({quantity}) is impossible — options are "
                "always whole contracts; this indicates a non-integer target_quantity was "
                "supplied for an OPTION selection, which is a caller error, not normalized "
                "silently")
        quantity = int(quantity)

    # ── execution constraints (broker-level; rounds down/to-zero only) ─────
    rounding_applied = False
    exec_reason = None
    if execution_constraints is not None:
        quantity, rounding_applied, exec_reason = _apply_execution_constraints(
            quantity, execution_constraints, force_whole_units=force_whole)
        if exec_reason:
            # An execution-constraint zero-out (e.g. below the broker's
            # minimum) is exactly the "why is quantity zero" provenance the
            # task wants surfaced, unlike a bare upstream-ineligibility zero
            # (already explained by that path's own `reasons`) — so this
            # binding_constraint is shown even though the result ends up
            # unsizeable.
            binding = exec_reason
            reasons.append(exec_reason)
        if choice.choice == InstrumentChoice.OPTION:
            quantity = int(quantity)

    # ── invariant, defensively re-checked rather than only assumed ─────────
    if quantity > ceiling + 1e-9:
        raise SizingError(
            f"internal invariant violated: sized quantity {quantity} exceeds the B ceiling "
            f"{ceiling} — this must never happen by construction")

    sizeable = quantity > 0
    if sizeable and not reasons:
        reasons.append(binding)

    return SizingResult(
        instrument=choice.choice, quantity=quantity, max_quantity_allowed=ceiling,
        requested_quantity=requested,
        binding_constraint=binding if (sizeable or exec_reason) else None,
        sizeable=sizeable, reasons=tuple(reasons) if reasons else ("zero_quantity",),
        rounding_applied=rounding_applied,
    )
