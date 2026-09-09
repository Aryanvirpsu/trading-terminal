"""Canonical Option Architecture v1.1 — Layer B: AccountFit for stock and long options.

Answers exactly one question, twice: "Can THIS account carry THIS specific
candidate instrument under THIS RiskPolicy?" — once for a stock leg
(`stock_account_fit`), once for a long call/put leg (`option_account_fit`).

Two related but distinct tracks, per the architecture:

    B(stock)   stock affordability/risk only — NO Layer-A dependency at all
    B(option)  requires contract_quality.quality_pass first, THEN option
               affordability/risk. A structurally invalid option short-
               circuits to eligible=False without any account-risk math —
               and, symmetrically, an invalid OPTION never touches or
               influences a `stock_account_fit()` call for the same
               candidate; the two functions share no mutable state.

RUNTIME/ACCOUNT/TRADE STATE lives here (equity, buying power, open
positions, entry/stop/strike/IV/DTE, portfolio exposure) — never inside
`canonical.risk_policy.RiskPolicy`, which carries limits only. Every
argument below that is state rather than policy is a plain keyword
parameter, never a RiskPolicy field.

Design decision — where the account-level CAP math comes from vs where the
option PRICING math comes from (two different kinds of reuse):
  * Cap-combining (`min(absolute, equity * pct)`) is NEVER duplicated here —
    every check routes through `canonical.risk_policy.effective_limit()`,
    exactly per Step 3's mandate. Two identical calls with different
    RiskPolicy values, never a profile-name branch.
  * The OPTION's own risk numbers (capital_committed / planned_risk /
    stress_risk / absolute_max_loss) are NOT reimplemented — that model
    (Black-Scholes repricing across three thesis-failure paths, six stress
    scenarios, a live-quote calibration gate) lives in
    `dashboard/option_risk.py:option_risk()` and is read-only IMPORTED and
    called directly, exactly as the task instructs ("use the shape and
    semantics already present ... rather than inventing a new risk model").
    `option_risk.py` itself is never modified. Its STRESS_DEFAULTS constant
    is imported directly (a plain dict copy) rather than via its `policy()`
    function, which reads the `RISK_PROFILE` environment variable — routing
    around that keeps AccountFit deterministic and independent of the
    calling process's environment, matching the rest of this package's
    "pure and network-free" discipline.
  * `option_risk.py`'s OWN account-cap-checking functions
    (`contract_eligibility()`, `portfolio_check()`) are deliberately NOT
    reused here — those operate on a raw percentage-points `pol` dict, not
    the canonical fraction-convention `RiskPolicy`, and reusing them would
    bypass Step 3's `effective_limit()` entirely. B(option)'s eligibility
    checks are a fresh application of RiskPolicy's option fields via
    `effective_limit()`, matching `contract_eligibility()`'s CHECK SET
    (premium%, planned-risk%, stress-risk%, theoretical-loss%) but computed
    canonically. One exception, explicitly not reproduced: the original
    `contract_eligibility()` scales caps by a setup-quality "allocation
    fraction" tier. B(option)'s only precondition is Layer A's
    `quality_pass` (per the task's explicit precondition definition) — no
    setup-quality/conviction input exists at this layer, so no such scaling
    is applied. Full policy caps apply unscaled. This is a deliberate scope
    decision, not an oversight.

Option indivisibility: `option_account_fit()` evaluates a caller-supplied
`contracts_candidate` (default 1) — never a fraction of a contract, and
never a computed "how many contracts fit" beyond that candidate. Layer E
(Step 6) owns final executable sizing, including any search over N; this
layer answers only "does exactly this many contracts, most usefully one,
fit" — the direct structural fix for the historical $100-vs-$5 mismatch: a
contract that doesn't fit is eligible=False / quantity_allowed=0, never
scaled down below one contract.

Fail-closed vs raise, applied consistently:
  * RAISES ValueError — programming/config errors: negative equity, negative
    buying power, an unrecognised instrument, or an option call missing its
    required `contract_quality` argument. These represent an impossible
    account state or a caller bug, not a normal variation in trade data.
  * RETURNS eligible=False (fails closed) — trade/market-data-level
    issues that can legitimately arise from bad quotes: non-positive entry
    price, non-positive/invalid stop (zero risk distance), negative option
    premium. Matches lab/paper/risk.py:position_size()'s own convention of
    returning quantity=0 rather than raising on a zero stop distance.

ADDITIVE ONLY. No caller anywhere in the repository is migrated to use this
module. `option_risk.contract_eligibility()`, `option_risk.option_risk()`
(called, not modified), `lab/paper/risk.py`, `lab/paper/config.py`,
`strategy_service._size_option()`, `decision_engine.evaluate()`,
`instrument_choice()`, tracker/journal/workflow, and every execution path
are all untouched.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from .risk_policy import RiskPolicy, effective_limit
from .contract_quality import ContractQualityResult
# Reuse of the existing, tested option risk model — see module docstring and
# canonical/option_risk_math.py's own docstring for the extraction history
# (Step 4.1). Plain intra-package import: no dashboard module, no sys.path
# mutation, no environment/RISK_PROFILE dependency. STRESS_DEFAULTS is a
# plain constant dict, copied per call, never mutated.
from .option_risk_math import option_risk as _option_risk_calc, STRESS_DEFAULTS as _STRESS_DEFAULTS


# ══════════════════════════════════════════════════════════════════════════
# Types
# ══════════════════════════════════════════════════════════════════════════

class InstrumentType(str, Enum):
    STOCK = "stock"
    LONG_CALL = "long_call"
    LONG_PUT = "long_put"


class RiskConfidence(str, Enum):
    """Step 5.1. The exact, and only, values found at the source this
    session (canonical/option_risk_math.py):
      * stock_risk()'s top-level `confidence` is hardcoded "high" always —
        stock risk is exactly known (abs(entry-stop)*quantity), never
        modelled or degraded.
      * option_risk()'s top-level `confidence` is "low" in either fallback
        path (missing time-to-invalidation/IV/DTE, or a failed live-quote
        calibration check) and "modelled" in the full repriced-on-3-paths
        success path. A third value never occurs in practice: the internal
        `at_stop["confidence"]` the success path's ternary tests against is
        itself hardcoded "modelled", so `"low" if at_stop[...] != "low"
        else ...` can only ever select "modelled" there — not "fixed" here,
        since Step 5.1 changes provenance plumbing only, never option-risk
        formulas.
    No richer model (e.g. a MEDIUM tier) exists in the source; none is
    invented here.
    """
    HIGH = "high"
    MODELLED = "modelled"
    LOW = "low"


@dataclass(frozen=True)
class ConstraintViolation:
    check: str
    actual: Optional[float]
    limit: Optional[float]
    basis: str


@dataclass(frozen=True)
class AccountFitResult:
    instrument: InstrumentType
    eligible: bool
    quantity_allowed: float          # shares (stock, may be fractional) or 0/1 (option)
    capital_required: float
    planned_risk: float
    stress_risk: Optional[float]
    absolute_max_loss: Optional[float]
    binding_constraint: Optional[str]
    violations: Tuple[ConstraintViolation, ...]
    assumptions: Tuple[str, ...]
    # Step 5.1: structured, leg-wide risk confidence/provenance — the SAME
    # value option_risk.py's/option_risk_math.py's own `confidence` field
    # already carries, just typed instead of buried only in `assumptions`
    # free text. None when no risk was computed at all (ineligible/
    # structurally-invalid short-circuits, or invalid-levels fail-closed
    # returns) — never fabricated. Confidence here is leg-wide, not
    # per-component (planned_risk/stress_risk/absolute_max_loss do not carry
    # individually distinct confidences at the source's top level — see
    # RiskConfidence's docstring for the exact provenance trace).
    risk_confidence: Optional[RiskConfidence] = None


# Deterministic priority order for `binding_constraint`. The FIRST name in
# this tuple that appears among a result's violations is reported as
# binding — fixed and documented so tests are stable, not "first failure
# encountered during evaluation."
_CHECK_PRIORITY: Tuple[str, ...] = (
    "structurally_invalid",
    "buying_power",
    "min_cash_reserve",
    "max_position_notional",
    "per_trade_risk",
    "max_open_positions",
    "max_positions_per_sector",
    "max_sector_exposure",
    "daily_loss_limit",
    "max_drawdown",
    "long_option_premium_pct",
    "option_planned_risk_pct",
    "option_stress_risk_pct",
    "option_theoretical_loss_pct",
    "max_total_option_premium_pct",
    "portfolio_planned_risk_pct",
    "max_open_option_positions",
)


def _binding(violations: Tuple[ConstraintViolation, ...]) -> Optional[str]:
    present = {v.check for v in violations}
    for name in _CHECK_PRIORITY:
        if name in present:
            return name
    return next(iter(present), None)   # fallback: any unlisted check name


# ══════════════════════════════════════════════════════════════════════════
# B(stock)
# ══════════════════════════════════════════════════════════════════════════

def stock_account_fit(
    *,
    policy: RiskPolicy,
    equity: float,
    entry: float,
    stop: float,
    buying_power: Optional[float] = None,
    fill_price: Optional[float] = None,
    open_positions: int = 0,
    sector: Optional[str] = None,
    sector_open_positions: int = 0,
    sector_exposure: float = 0.0,
    current_daily_loss: float = 0.0,
    current_drawdown: float = 0.0,
) -> AccountFitResult:
    """Stock affordability/risk only — no Layer-A dependency.

    planned_risk = abs(entry - stop) * quantity — lab/paper/risk.py's own
    convention (position_size()/check_entry()), generalized. Deliberately
    NOT option_risk.py:stock_risk()'s convention, which bakes a small
    slippage/fee estimate directly into planned_risk — lab/paper/risk.py
    instead applies realistic cost only to the AFFORDABILITY check via a
    caller-supplied `fill_price`, which this function mirrors via the same
    parameter, keeping planned_risk itself a pure risk-distance number.

    quantity_allowed is the smallest of: risk-budget capacity, position-
    notional capacity, and buying-power capacity — the same three-way
    pattern as position_size(), computed here via
    canonical.risk_policy.effective_limit() rather than position_size()'s
    own inline min() calls.

    `buying_power` is the RAW/pre-reserve spendable cash figure (matches
    lab/paper/risk.py:account_state()'s `available_cash`, not its derived
    `buying_power` field, which is already net of the reserve). Sizing uses
    `buying_power - min_cash_reserve` internally, so a policy reserve is
    respected BY the sized quantity, not just checked after the fact — which
    is also why, exactly as in the legacy check_entry(), the separate
    `min_cash_reserve` violation is mathematically implied by (and so will
    essentially never fire independently of) the `buying_power` check: it is
    kept as its own named, separately-reported check purely for audit
    clarity, matching check_entry()'s own redundant-by-construction check.
    """
    if equity < 0:
        raise ValueError(f"equity must be nonnegative, got {equity}")
    if buying_power is not None and buying_power < 0:
        raise ValueError(f"buying_power must be nonnegative, got {buying_power}")

    px = fill_price if fill_price is not None else entry
    per_share = abs(entry - stop)

    if entry <= 0 or px <= 0 or per_share <= 0:
        return AccountFitResult(
            instrument=InstrumentType.STOCK, eligible=False, quantity_allowed=0.0,
            capital_required=0.0, planned_risk=0.0, stress_risk=None,
            absolute_max_loss=None, binding_constraint="invalid_levels",
            violations=(ConstraintViolation("invalid_levels", actual=per_share, limit=None,
                                            basis="non-positive entry/fill price or zero "
                                                  "entry-stop risk distance — fails closed, "
                                                  "never infinite capacity"),),
            assumptions=("entry, fill price, and stop distance must all be strictly "
                        "positive; matches position_size()'s zero-stop-distance handling",),
        )

    # Net of the policy's cash reserve, if any — mirrors
    # account_state()["buying_power"] = available_cash - min_cash_reserve.
    bp_raw = buying_power if buying_power is not None else float("inf")
    bp_eff = (max(0.0, bp_raw - policy.min_cash_reserve)
             if (buying_power is not None and policy.min_cash_reserve is not None)
             else bp_raw)

    risk_cap = effective_limit(absolute=policy.max_loss_per_trade,
                               percent=policy.risk_per_trade_pct, equity=equity)
    notional_cap = effective_limit(absolute=policy.max_position_notional,
                                   percent=policy.max_position_notional_pct, equity=equity)

    q_risk = (risk_cap.limit / per_share) if risk_cap.limit is not None else float("inf")
    q_notional = (notional_cap.limit / px) if notional_cap.limit is not None else float("inf")
    q_cash = bp_eff / px

    candidates = [(q_risk, "per_trade_risk"), (q_notional, "max_position_notional"),
                 (q_cash, "buying_power")]
    qty, sizing_binder = min(candidates, key=lambda t: t[0])

    if policy.fractional_shares:
        qty = round(qty, 6)
    else:
        qty = float(int(qty))  # always round down — never overspend

    violations = []
    if qty <= 0 or not (qty < float("inf")):
        violations.append(ConstraintViolation(
            sizing_binder, actual=0.0,
            limit=(risk_cap.limit if sizing_binder == "per_trade_risk" else
                  notional_cap.limit if sizing_binder == "max_position_notional" else bp_eff),
            basis=f"{sizing_binder} leaves zero affordable shares"))

    capital_required = round(qty * px, 2) if qty not in (float("inf"),) else 0.0
    planned_risk = round(qty * per_share, 2) if qty not in (float("inf"),) else 0.0

    # ── account-level eligibility checks (all optional; None = unconstrained) ──
    def _check(name: str, actual: float, limit: Optional[float], ok: bool, basis: str) -> None:
        if not ok:
            violations.append(ConstraintViolation(name, actual=actual, limit=limit, basis=basis))

    if policy.max_open_positions is not None:
        _check("max_open_positions", open_positions + 1, policy.max_open_positions,
              open_positions + 1 <= policy.max_open_positions,
              f"{open_positions} open position(s) + 1 new vs the "
              f"{policy.max_open_positions}-position limit")

    if policy.max_positions_per_sector is not None:
        _check("max_positions_per_sector", sector_open_positions + 1,
              policy.max_positions_per_sector,
              sector_open_positions + 1 <= policy.max_positions_per_sector,
              f"{sector_open_positions} position(s) in "
              f"{sector or 'this sector'} + 1 new vs the "
              f"{policy.max_positions_per_sector}-per-sector limit")

    if policy.max_sector_exposure_pct is not None:
        sec_cap = effective_limit(absolute=None, percent=policy.max_sector_exposure_pct,
                                  equity=equity)
        new_sector_exposure = sector_exposure + capital_required
        _check("max_sector_exposure", new_sector_exposure, sec_cap.limit,
              sec_cap.limit is None or new_sector_exposure <= sec_cap.limit + 1e-9,
              f"{sector or 'this sector'} exposure ${new_sector_exposure:,.2f} "
              f"would exceed the ${sec_cap.limit} cap")

    daily_cap = effective_limit(absolute=policy.max_daily_loss,
                                percent=policy.max_daily_loss_pct, equity=equity)
    if daily_cap.limit is not None:
        _check("daily_loss_limit", current_daily_loss, daily_cap.limit,
              current_daily_loss <= daily_cap.limit + 1e-9,
              f"today's realized loss ${current_daily_loss:,.2f} is at/over the "
              f"${daily_cap.limit} daily-loss limit")

    dd_cap = effective_limit(absolute=policy.max_drawdown,
                             percent=policy.max_drawdown_pct, equity=equity)
    if dd_cap.limit is not None:
        _check("max_drawdown", current_drawdown, dd_cap.limit,
              current_drawdown < dd_cap.limit,
              f"drawdown ${current_drawdown:,.2f} is at/over the ${dd_cap.limit} limit")

    if policy.min_cash_reserve is not None and buying_power is not None:
        # Uses the RAW (pre-reserve) buying_power, matching check_entry()'s
        # own available_cash-based check. Mathematically implied by the
        # buying_power/q_cash sizing above (which already nets out the
        # reserve) — kept as its own named check for audit clarity, not
        # because it can independently fail once sizing respects the net.
        cash_after = buying_power - capital_required
        _check("min_cash_reserve", cash_after, policy.min_cash_reserve,
              cash_after >= policy.min_cash_reserve - 1e-9,
              f"cash after entry ${cash_after:,.2f} would fall below the "
              f"${policy.min_cash_reserve} minimum reserve")

    violations_t = tuple(violations)
    eligible = qty > 0 and not violations_t
    return AccountFitResult(
        instrument=InstrumentType.STOCK,
        eligible=eligible,
        quantity_allowed=qty if eligible else 0.0,
        capital_required=capital_required if eligible else 0.0,
        planned_risk=planned_risk if eligible else 0.0,
        stress_risk=None,
        absolute_max_loss=None,
        binding_constraint=_binding(violations_t) if violations_t else (
            sizing_binder if qty > 0 else None),
        violations=violations_t,
        assumptions=(
            "planned_risk = abs(entry - stop) * quantity, no fee/slippage add-on "
            "(lab/paper/risk.py convention)",
            f"quantity sizing binder before eligibility checks: {sizing_binder}",
            "stress_risk/absolute_max_loss are not modelled for stock in this layer "
            "(a long share position's absolute max loss is its full notional, "
            "informational only — not a gating number here)",
        ),
        # Stock risk is an exact formula, never modelled or degraded — matches
        # option_risk_math.stock_risk()'s own hardcoded "high". None (not
        # fabricated HIGH) when ineligible, matching every other zeroed field.
        risk_confidence=RiskConfidence.HIGH if eligible else None,
    )


# ══════════════════════════════════════════════════════════════════════════
# B(option)
# ══════════════════════════════════════════════════════════════════════════

def option_account_fit(
    *,
    policy: RiskPolicy,
    equity: float,
    contract_quality: ContractQualityResult,
    side: str,
    limit_price: float,
    spot: float,
    stop: float,
    strike: float,
    iv: Optional[float],
    dte: Optional[float],
    contracts_candidate: int = 1,
    atr_pct: Optional[float] = None,
    multiplier: float = 100.0,
    fee_per_contract: float = 0.06,
    spread_dollars: Optional[float] = None,
    risk_free_rate: float = 0.042,
    buying_power: Optional[float] = None,
    current_option_premium_committed: float = 0.0,
    current_option_planned_risk: float = 0.0,
    current_open_option_positions: int = 0,
) -> AccountFitResult:
    """Long call/put affordability/risk. REQUIRES a Layer-A ContractQualityResult
    — a structurally invalid contract short-circuits to eligible=False before
    any account-risk math runs, and this never affects a sibling
    stock_account_fit() call for the same underlying (no shared state).

    Risk numbers (capital_committed/planned_risk/stress_risk/
    absolute_max_loss) come directly from dashboard/option_risk.py:
    option_risk() — read-only reuse of the existing Black-Scholes repricing
    model, not a reimplementation. Eligibility checks apply RiskPolicy's
    option fields via canonical.risk_policy.effective_limit().

    contracts_candidate is evaluated as given (default 1) — never scaled
    down below one contract, never searched over N. A contract that doesn't
    fit is eligible=False / quantity_allowed=0, matching the historical
    $100-vs-$5 mismatch's structural fix: no path silently shrinks a
    contract into affordability.
    """
    if equity < 0:
        raise ValueError(f"equity must be nonnegative, got {equity}")
    if buying_power is not None and buying_power < 0:
        raise ValueError(f"buying_power must be nonnegative, got {buying_power}")
    if contract_quality is None or not isinstance(contract_quality, ContractQualityResult):
        raise ValueError(
            "option_account_fit() requires a canonical.contract_quality."
            "ContractQualityResult — Layer B(option) must not recompute "
            "structural quality itself."
        )
    side_u = (side or "").upper()
    if side_u.startswith("C"):
        instrument = InstrumentType.LONG_CALL
    elif side_u.startswith("P"):
        instrument = InstrumentType.LONG_PUT
    else:
        raise ValueError(f"unrecognised option side {side!r} — expected CALL or PUT")
    if contracts_candidate <= 0:
        raise ValueError(f"contracts_candidate must be a positive integer, got {contracts_candidate}")

    # ── precondition: Layer A must have passed ──────────────────────────────
    if not contract_quality.quality_pass:
        return AccountFitResult(
            instrument=instrument, eligible=False, quantity_allowed=0,
            capital_required=0.0, planned_risk=0.0, stress_risk=None,
            absolute_max_loss=None, binding_constraint="structurally_invalid",
            violations=(ConstraintViolation(
                "structurally_invalid", actual=None, limit=None,
                basis="Layer A quality_pass=False: " + "; ".join(contract_quality.hard_failures)),),
            assumptions=(
                "no account-risk math was performed — a structurally invalid "
                "contract short-circuits before any RiskPolicy check runs",
            ),
        )

    # ── trade-data-level fail-closed cases (not a raise) ────────────────────
    if limit_price <= 0 or spot <= 0 or strike <= 0:
        return AccountFitResult(
            instrument=instrument, eligible=False, quantity_allowed=0,
            capital_required=0.0, planned_risk=0.0, stress_risk=None,
            absolute_max_loss=None, binding_constraint="invalid_levels",
            violations=(ConstraintViolation(
                "invalid_levels", actual=limit_price, limit=None,
                basis="non-positive premium, underlying, or strike — fails closed"),),
            assumptions=("premium, underlying, and strike must all be strictly positive",),
        )

    # ── risk numbers: read-only reuse of option_risk.py's own model ────────
    risk = _option_risk_calc(
        contracts=contracts_candidate, limit_price=limit_price, spot=spot, stop=stop,
        strike=strike, side=side_u, iv=iv, dte=dte, atr_pct=atr_pct,
        multiplier=multiplier, fee_per_contract=fee_per_contract,
        spread_dollars=spread_dollars, r=risk_free_rate, pol=dict(_STRESS_DEFAULTS),
    )
    capital_required = risk["capital_committed"]
    planned_risk = risk["planned_risk"]
    stress_risk = risk["stress_risk"]
    absolute_max_loss = risk["absolute_max_loss"]

    violations = []

    def _check(name: str, actual: float, cap_result, basis_fmt: str) -> None:
        if cap_result.limit is None:
            return
        ok = actual <= cap_result.limit + 1e-9
        if not ok:
            violations.append(ConstraintViolation(name, actual=actual, limit=cap_result.limit,
                                                  basis=basis_fmt))

    if buying_power is not None:
        _check("buying_power", capital_required,
              effective_limit(absolute=buying_power, percent=None, equity=equity),
              f"premium+fees ${capital_required:,.2f} exceeds buying power ${buying_power:,.2f}")

    # ── GENERAL per-trade / per-position caps, shared with stock ───────────
    # These are NOT option-specific fields — they are the same
    # max_loss_per_trade/risk_per_trade_pct and max_position_notional(_pct)
    # stock_account_fit() uses. They apply to an option candidate too: a
    # long option's capital_required/planned_risk is real account risk
    # regardless of instrument, and this is exactly the check the historical
    # "$100 option vs $5 real cap" divergence was about — a profile with NO
    # option-specific fields configured (Strategy500Policy) must still
    # reject an oversized option candidate via its general per-trade cap,
    # not silently pass it because no *option* field happened to be set.
    _check("per_trade_risk", planned_risk,
          effective_limit(absolute=policy.max_loss_per_trade,
                          percent=policy.risk_per_trade_pct, equity=equity),
          f"planned risk ${planned_risk:,.2f} exceeds the general per-trade risk cap "
          "(shared with stock — Strategy500Policy has no option-specific fields, "
          "so this general cap is what protects it)")

    _check("max_position_notional", capital_required,
          effective_limit(absolute=policy.max_position_notional,
                          percent=policy.max_position_notional_pct, equity=equity),
          f"premium+fees ${capital_required:,.2f} exceeds the general position-notional cap")

    _check("long_option_premium_pct", capital_required,
          effective_limit(absolute=None, percent=policy.max_long_option_premium_pct, equity=equity),
          f"premium+fees ${capital_required:,.2f} exceeds "
          f"{policy.max_long_option_premium_pct if policy.max_long_option_premium_pct is not None else '?'} "
          f"of equity")

    _check("option_planned_risk_pct", planned_risk,
          effective_limit(absolute=None, percent=policy.option_planned_risk_pct, equity=equity),
          f"planned risk ${planned_risk:,.2f} exceeds the option planned-risk cap")

    # Per-candidate stress/theoretical checks against the PORTFOLIO-labelled
    # caps — mirrors option_risk.contract_eligibility()'s existing shape
    # exactly (it checks a single candidate's stress_risk/absolute_max_loss
    # against max_portfolio_option_stress_risk_pct /
    # max_portfolio_theoretical_option_loss_pct, not a true portfolio
    # aggregate at that point).
    _check("option_stress_risk_pct", stress_risk,
          effective_limit(absolute=None, percent=policy.max_portfolio_option_stress_risk_pct,
                          equity=equity),
          f"stress risk ${stress_risk:,.2f} exceeds the portfolio stress-risk cap")

    _check("option_theoretical_loss_pct", absolute_max_loss,
          effective_limit(absolute=None, percent=policy.max_portfolio_theoretical_option_loss_pct,
                          equity=equity),
          f"absolute max loss ${absolute_max_loss:,.2f} exceeds the "
          f"portfolio theoretical-loss cap")

    # True portfolio-aggregate checks — only meaningful if the caller
    # supplied current portfolio state (defaults are 0, a single-trade view).
    _check("max_total_option_premium_pct", current_option_premium_committed + capital_required,
          effective_limit(absolute=None, percent=policy.max_total_option_premium_pct, equity=equity),
          f"total option premium ${current_option_premium_committed + capital_required:,.2f} "
          f"(existing + this candidate) exceeds the total-premium cap")

    _check("portfolio_planned_risk_pct", current_option_planned_risk + planned_risk,
          effective_limit(absolute=None, percent=policy.max_portfolio_planned_risk_pct, equity=equity),
          f"portfolio option planned risk ${current_option_planned_risk + planned_risk:,.2f} "
          f"(existing + this candidate) exceeds the portfolio planned-risk cap")

    if policy.max_open_option_positions is not None:
        new_count = current_open_option_positions + 1
        if new_count > policy.max_open_option_positions:
            violations.append(ConstraintViolation(
                "max_open_option_positions", actual=new_count,
                limit=policy.max_open_option_positions,
                basis=f"{current_open_option_positions} open option position(s) + 1 new "
                      f"vs the {policy.max_open_option_positions}-position limit"))

    violations_t = tuple(violations)
    eligible = not violations_t
    return AccountFitResult(
        instrument=instrument,
        eligible=eligible,
        # Indivisible: exactly the requested candidate, or nothing. Never a
        # fraction, never a searched-for larger N (Layer E's job).
        quantity_allowed=contracts_candidate if eligible else 0,
        capital_required=capital_required if eligible else 0.0,
        planned_risk=planned_risk if eligible else 0.0,
        stress_risk=stress_risk if eligible else None,
        absolute_max_loss=absolute_max_loss if eligible else None,
        binding_constraint=_binding(violations_t) if violations_t else None,
        violations=violations_t,
        assumptions=(
            f"risk numbers via dashboard/option_risk.py:option_risk() "
            f"(confidence={risk.get('confidence')}); planned_risk_basis: "
            f"{risk.get('planned_risk_basis', '')}",
            "no setup-quality/conviction scaling applied to caps (unlike "
            "option_risk.contract_eligibility()'s allocation_fraction) — "
            "Layer B(option)'s only precondition is contract_quality.quality_pass",
            f"evaluated for exactly {contracts_candidate} contract(s); options "
            "are never fractionally sized at this layer",
        ),
        # Structured, not free-text-only (Step 5.1): the SAME value already
        # embedded in `assumptions` above, typed. None (not fabricated) when
        # ineligible — no risk computation's confidence is meaningful for a
        # rejected candidate.
        risk_confidence=RiskConfidence(risk["confidence"]) if eligible else None,
    )
