"""Canonical Option Architecture v1.1 — Step 7: instrument-specific
executable predicates (final output-stage safety gates).

Answers exactly one question, twice: "Is the already-selected, already-sized
STOCK/OPTION leg allowed to be represented as executable?" — once for stock
(`evaluate_stock_executable`), once for a long option
(`evaluate_option_executable`). This is the LAST gate in the A -> B -> D -> E
pipeline, not a new decision layer:

    A  contract_quality   (structural quality)
    B  account_fit        (affordability/risk)
    D  instrument_choice  (STOCK vs OPTION vs NO_TRADE preference)
    E  sizing             (final clamped quantity)
    -> executable          <- THIS MODULE. Reads A/B/D/E's own verdicts only.

These predicates do NOT:
  * choose the instrument (that's D)
  * size the instrument (that's E)
  * recalculate AccountFit (that's B)
  * inspect raw RiskPolicy caps (never imported here — see module purity note
    below)
  * evaluate option quality (that's A)
  * fetch state, execute an order, or mutate persistence

Two independent predicates, deliberately not one ambiguous flag
──────────────────────────────────────────────────────────────────────────
A `trade.executable` field spanning both legs would be structurally
ambiguous — which leg does it refer to when D chose STOCK but a stale OPTION
sizing result also happens to exist? The architecture requires
`stock_executable` and `option_executable` as two separate return values,
sharing only `setup_tradeable`. OPTION carries two prerequisites STOCK does
not (contract_quality and shadow_only), which is the other reason these are
kept as separate functions rather than one instrument-polymorphic one — the
extra OPTION-only parameters would otherwise have to be silently optional on
a shared signature, defeating the type-level guarantee that an option can
never be evaluated executable without them.

Defense-in-depth, not trust ("malformed-state defense")
──────────────────────────────────────────────────────────────────────────
This module never assumes A/B/D/E already agree with each other. Every
value read from an upstream result is re-checked here:
  * a stock predicate is fed an AccountFitResult whose `.instrument` is not
    InstrumentType.STOCK -> fails closed, never crashes
  * a SizingResult whose `.instrument` does not match the leg being
    evaluated (e.g. D said STOCK but the SizingResult carries OPTION) ->
    fails closed
  * a contract_quality that is None or quality_pass=False for the option
    predicate -> fails closed, regardless of what B/D/E say
This is why "structurally_invalid" is reused for both a genuine Layer-A
quality failure AND an upstream object/instrument-type mismatch: both are
the same underlying condition from this layer's point of view — "the
object(s) this leg needed are not what they should structurally be" — and
it is exactly the same word account_fit.py already uses for Layer-A failure
(`option_account_fit()` sets `binding_constraint="structurally_invalid"`
when `contract_quality.quality_pass` is False; this module's usage is a
direct extension of that established vocabulary, not a new coinage).

Fail-closed vs raise, applied exactly as in every prior layer
──────────────────────────────────────────────────────────────────────────
  * RAISES TypeError — a caller/programming error: `setup_tradeable`,
    `execution_policy_allows`, or `shadow_only` not a bool, or an
    `account_fit`/`choice`/`sizing`/`contract_quality` argument that is
    neither `None` nor an instance of its expected canonical dataclass
    (e.g. a raw dict was passed instead of an AccountFitResult). This
    mirrors `choose_instrument()`'s own TypeError convention.
  * RETURNS `executable=False` with the applicable blocker(s) (fails
    closed) — every other kind of "wrong": `None` where a result was
    expected, an eligible=False leg, an instrument-type mismatch between
    sibling canonical objects, a zero/non-sizeable quantity, a policy
    block, or shadow-only mode. None of these are programming errors; all
    of them are normal, expected states this gate exists to catch.

Import purity / no RiskPolicy
──────────────────────────────────────────────────────────────────────────
`execution_policy_allows` is a plain bool — an output-stage permission bit,
never a `canonical.risk_policy.RiskPolicy` object. This module imports
nothing from `canonical.risk_policy` and nothing from `dashboard.*`; it
reads only the four upstream canonical result types
(`ContractQualityResult`, `AccountFitResult`, `InstrumentChoiceResult`,
`SizingResult`) and re-derives nothing they already computed.

ADDITIVE ONLY. No caller anywhere in the repository is migrated to use this
module: `strategy_service.py`, `decision_engine.py`,
`dashboard/options_desk.py`, `dashboard/option_risk.py`, `lab/paper/*`,
tracker, journal, workflow, and broker/execution code are all untouched.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Optional, Tuple

from .account_fit import AccountFitResult, InstrumentType
from .contract_quality import ContractQualityResult
from .instrument_choice import InstrumentChoice, InstrumentChoiceResult
from .sizing import SizingResult


# ══════════════════════════════════════════════════════════════════════════
# Result
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ExecutableResult:
    executable: bool
    instrument: InstrumentChoice   # STOCK or OPTION — which leg this result is about
    blockers: Tuple[str, ...]      # every applicable reason, deterministically ordered; empty iff executable


# Deterministic ordering for `blockers` — fixed and documented, mirroring
# account_fit.py's `_CHECK_PRIORITY` / `_binding()` pattern, so tests and
# downstream debugging see a stable order rather than set/dict iteration
# order. ALL applicable blockers are reported, not only the first — this
# tuple only controls the order they appear in.
_BLOCKER_ORDER: Tuple[str, ...] = (
    "setup_not_tradeable",
    "structurally_invalid",
    "account_ineligible",
    "instrument_not_selected",
    "not_sizeable",
    "zero_quantity",
    "execution_policy_blocked",
    "shadow_only",
)


def _ordered_blockers(found: FrozenSet[str]) -> Tuple[str, ...]:
    ordered = [name for name in _BLOCKER_ORDER if name in found]
    # Defensive only — every blocker this module ever adds is named in
    # _BLOCKER_ORDER above, so this should always be empty in practice.
    leftover = sorted(found.difference(_BLOCKER_ORDER))
    return tuple(ordered + leftover)


# ══════════════════════════════════════════════════════════════════════════
# Shared validation — TypeError on genuinely wrong types, never on a
# legitimate None ("this leg was never evaluated") or a value-level mismatch
# ══════════════════════════════════════════════════════════════════════════

def _require_bool(name: str, value: object) -> None:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be bool, got {type(value).__name__}")


def _require_type_or_none(name: str, value: object, cls: type) -> None:
    if value is not None and not isinstance(value, cls):
        raise TypeError(f"{name} must be a {cls.__name__} or None, got {type(value).__name__}")


# ══════════════════════════════════════════════════════════════════════════
# STOCK
# ══════════════════════════════════════════════════════════════════════════

def evaluate_stock_executable(
    *,
    setup_tradeable: bool,
    account_fit: Optional[AccountFitResult],
    choice: Optional[InstrumentChoiceResult],
    sizing: Optional[SizingResult],
    execution_policy_allows: bool = True,
) -> ExecutableResult:
    """Final gate for the STOCK leg.

    Conceptually:

        stock_executable = (
            setup_tradeable
            and stock_account_fit.eligible
            and instrument_choice == STOCK
            and sizing.instrument == STOCK
            and sizing.quantity > 0
            and sizing.sizeable
            and execution_policy_allows
        )

    `account_fit` must be a stock leg's AccountFitResult
    (`.instrument == InstrumentType.STOCK`) — an option AccountFitResult
    supplied here, or `None`, fails closed as "structurally_invalid" rather
    than raising: this is a normal "wrong/missing input for this leg" state,
    not a caller type error. Never inspects `contract_quality` or any
    option-side object — the STOCK predicate has no such parameter, so a
    structurally invalid or ineligible option can never influence this
    result (v1.1 correction 3's independence invariant, held by
    construction, not by a runtime check).
    """
    _require_bool("setup_tradeable", setup_tradeable)
    _require_bool("execution_policy_allows", execution_policy_allows)
    _require_type_or_none("account_fit", account_fit, AccountFitResult)
    _require_type_or_none("choice", choice, InstrumentChoiceResult)
    _require_type_or_none("sizing", sizing, SizingResult)

    blockers = set()

    if not setup_tradeable:
        blockers.add("setup_not_tradeable")

    if account_fit is None or account_fit.instrument != InstrumentType.STOCK:
        blockers.add("structurally_invalid")
    elif not account_fit.eligible:
        blockers.add("account_ineligible")

    if choice is None or choice.choice != InstrumentChoice.STOCK:
        blockers.add("instrument_not_selected")

    if sizing is None or sizing.instrument != InstrumentChoice.STOCK:
        blockers.add("structurally_invalid")
    else:
        if not sizing.sizeable:
            blockers.add("not_sizeable")
        if not (sizing.quantity > 0):
            blockers.add("zero_quantity")

    if not execution_policy_allows:
        blockers.add("execution_policy_blocked")

    blockers_t = _ordered_blockers(frozenset(blockers))
    return ExecutableResult(
        executable=not blockers_t,
        instrument=InstrumentChoice.STOCK,
        blockers=blockers_t,
    )


# ══════════════════════════════════════════════════════════════════════════
# OPTION
# ══════════════════════════════════════════════════════════════════════════

def evaluate_option_executable(
    *,
    setup_tradeable: bool,
    contract_quality: Optional[ContractQualityResult],
    account_fit: Optional[AccountFitResult],
    choice: Optional[InstrumentChoiceResult],
    sizing: Optional[SizingResult],
    execution_policy_allows: bool = True,
    shadow_only: bool = False,
) -> ExecutableResult:
    """Final gate for the OPTION leg.

    Conceptually:

        option_executable = (
            setup_tradeable
            and contract_quality.quality_pass
            and option_account_fit.eligible
            and instrument_choice == OPTION
            and sizing.instrument == OPTION
            and sizing.quantity > 0
            and sizing.sizeable
            and execution_policy_allows
            and not shadow_only
        )

    Requires BOTH Layer A (`contract_quality.quality_pass`) and Layer B
    (`account_fit.eligible`) — even if D accidentally reports OPTION and a
    sizing result with quantity=1 somehow exists, a False `quality_pass` or
    a missing/wrong-type `account_fit` fails this closed via
    "structurally_invalid" before eligibility is even consulted (fail-closed
    ordering, not a raise: a bad contract is normal trade data, not a
    caller bug). `shadow_only=True` fails closed unconditionally,
    regardless of how clean A/B/D/E are — Strategy-500 options remain
    shadow-only per the historical regression this predicate must preserve.
    """
    _require_bool("setup_tradeable", setup_tradeable)
    _require_bool("execution_policy_allows", execution_policy_allows)
    _require_bool("shadow_only", shadow_only)
    _require_type_or_none("contract_quality", contract_quality, ContractQualityResult)
    _require_type_or_none("account_fit", account_fit, AccountFitResult)
    _require_type_or_none("choice", choice, InstrumentChoiceResult)
    _require_type_or_none("sizing", sizing, SizingResult)

    blockers = set()

    if not setup_tradeable:
        blockers.add("setup_not_tradeable")

    quality_ok = contract_quality is not None and contract_quality.quality_pass
    account_fit_is_option_shaped = (
        account_fit is not None
        and account_fit.instrument in (InstrumentType.LONG_CALL, InstrumentType.LONG_PUT)
    )
    if not quality_ok or not account_fit_is_option_shaped:
        # Covers a Layer-A failure (or missing contract_quality), a missing
        # account_fit, and an account_fit that is structurally the wrong
        # leg (e.g. a STOCK AccountFitResult passed where an option one was
        # expected) — all "the object(s) this leg needed are not what they
        # should structurally be", the same condition account_fit.py itself
        # names "structurally_invalid" for a Layer-A failure.
        blockers.add("structurally_invalid")
    elif not account_fit.eligible:
        blockers.add("account_ineligible")

    if choice is None or choice.choice != InstrumentChoice.OPTION:
        blockers.add("instrument_not_selected")

    if sizing is None or sizing.instrument != InstrumentChoice.OPTION:
        blockers.add("structurally_invalid")
    else:
        if not sizing.sizeable:
            blockers.add("not_sizeable")
        if not (sizing.quantity > 0):
            blockers.add("zero_quantity")

    if not execution_policy_allows:
        blockers.add("execution_policy_blocked")

    if shadow_only:
        blockers.add("shadow_only")

    blockers_t = _ordered_blockers(frozenset(blockers))
    return ExecutableResult(
        executable=not blockers_t,
        instrument=InstrumentChoice.OPTION,
        blockers=blockers_t,
    )
