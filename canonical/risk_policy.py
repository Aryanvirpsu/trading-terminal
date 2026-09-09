"""Canonical Option Architecture v1.1 — generalized RiskPolicy schema.

One schema, multiple profiles, one cap-combining formula. Architectural
invariant this module exists to enforce: **profiles may differ in VALUES, never
in FORMULA.** There is no `if profile.name == "Strategy500": ...` branch
anywhere in `effective_limit()` — see `test_no_profile_identity_branch_in_cap_math`
in the accompanying test file, which greps this module's own source to prove it.

Percentage convention (declared once, applies to every `*_pct` field on
`RiskPolicy`): a FRACTION of equity, matching lab/paper/config.py's existing
convention — `0.01` means 1%, `1.0` means 100%. This is a DELIBERATE choice: the
two legacy sources this module mirrors disagree with each other —
lab/paper/config.py already uses the fraction convention (`risk_per_trade_pct:
_f("PAPER_RISK_PER_TRADE", 0.01)`), while dashboard/option_risk.py's PROFILES
dict uses percentage-POINTS (`"stock_planned_risk_pct": 1.0` meaning 1%). Every
DashboardPolicy field sourced from the latter is divided by 100 at construction
time, with the raw source value quoted in a comment beside the conversion, so
the conversion itself is auditable.

This module is PURE and NETWORK-FREE: no import of lab.paper.config,
dashboard.option_risk, or any other legacy module, so there is no runtime
coupling back to them. Every profile's values were read from source THIS
SESSION (see the provenance comments on each field) and hardcoded here as a
point-in-time mirror — if the legacy source changes, this module does not
silently drift with it, which is the correct property for something Step 4's
AccountFit will treat as authoritative going forward.

ADDITIVE ONLY. Nothing in the repository is migrated to call this module.
`option_risk.contract_eligibility()`, `option_risk.option_risk()`,
`lab/paper/risk.py`, `lab/paper/config.py`, `strategy_service._size_option()`,
`decision_engine.evaluate()`, and `instrument_choice()` are all unmodified and
remain authoritative for CURRENT production behavior.

RiskPolicy carries LIMITS AND POLICY VALUES ONLY. It must never carry runtime/
account/trade state — no equity, buying power, positions, quotes, premium,
entry, stop, or planned-risk number, and no instrument preference. Those
belong to Step 4's AccountFit. See
test_risk_policy_carries_no_runtime_state for the structural test that pins
this down (every field name is checked against a forbidden-state blocklist).

Deliberately OUT OF SCOPE for this schema (verified present in the legacy
sources but a different KIND of thing than a risk-limit cap):
  * option_risk.py's STRESS_DEFAULTS (gap_atr_multiple, iv_crush_pct,
    spread_widen_multiple, max_model_calibration_error,
    min_credible_planned_loss_pct, fast/slow-path timing fractions) — these
    are option-REPRICING MODEL calibration constants consumed inside
    option_risk.option_risk()'s stress-scenario math, not account risk limits.
  * options_desk.config()'s min_reward_risk (1.8) — a trade-QUALITY / setup
    filter, not a risk budget.
  * options_desk.config()'s max_premium_per_position (SIZE_MAX_PREMIUM,
    $300 default) and max_daily_loss_pct (SIZE_MAX_DAILY_LOSS_PCT, 3.0%
    default) — both defined in options_desk.config() but, verified by
    grepping dashboard/options_desk.py this session, never actually READ by
    any check (no `cfg["max_premium_per_position"]` or
    `cfg["max_daily_loss_pct"]` lookup exists anywhere in the file). Carrying
    an unenforced number into a schema meant to back a future authoritative
    gate would misrepresent what's actually in force today.
  * lab/paper/config.py's max_entries_per_day, max_correlated_positions,
    cooldown_losses, cooldown_days — real, active Strategy-500 controls, but
    pacing/cooldown behavior rather than a capital risk-limit cap. Left for a
    future schema revision if Step 4 needs them; NOT represented here.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

# ══════════════════════════════════════════════════════════════════════════
# The schema
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class RiskPolicy:
    """Limits and policy values only — see module docstring for the runtime-
    state exclusion this is pinned against. `None` on any limit field means
    genuinely unbounded on that axis, never zero."""

    name: str
    source: str   # human-readable provenance: which legacy config this mirrors

    # ── per-trade ─────────────────────────────────────────────────────────
    max_loss_per_trade: Optional[float]        # $
    risk_per_trade_pct: Optional[float]        # fraction of equity

    # ── position exposure ────────────────────────────────────────────────
    max_position_notional: Optional[float]         # $
    max_position_notional_pct: Optional[float]     # fraction of equity
    max_portfolio_exposure_pct: Optional[float]    # fraction of equity, all open positions
    max_sector_exposure_pct: Optional[float]       # fraction of equity, one sector's positions

    # ── concentration / account ──────────────────────────────────────────
    max_open_positions: Optional[int]
    max_positions_per_sector: Optional[int]
    max_daily_loss: Optional[float]        # $
    max_daily_loss_pct: Optional[float]    # fraction
    max_drawdown: Optional[float]          # $
    max_drawdown_pct: Optional[float]      # fraction
    min_cash_reserve: Optional[float]      # $ — capital that must stay unallocated

    # ── option-specific ───────────────────────────────────────────────────
    option_planned_risk_pct: Optional[float]                   # fraction, per contract
    max_long_option_premium_pct: Optional[float]                # fraction, per contract
    max_total_option_premium_pct: Optional[float]                # fraction, portfolio-wide
    max_portfolio_planned_risk_pct: Optional[float]              # fraction, portfolio-wide
    max_portfolio_option_stress_risk_pct: Optional[float]        # fraction, portfolio-wide
    max_portfolio_theoretical_option_loss_pct: Optional[float]   # fraction, portfolio-wide
    max_open_option_positions: Optional[int]

    # ── execution capability ─────────────────────────────────────────────
    fractional_shares: bool

    # Field names ending "_pct" — used by validation and by the runtime-state
    # structural test to confirm every percentage field is covered.
    _PCT_FIELDS = (
        "risk_per_trade_pct", "max_position_notional_pct", "max_portfolio_exposure_pct",
        "max_sector_exposure_pct",
        "max_daily_loss_pct", "max_drawdown_pct", "option_planned_risk_pct",
        "max_long_option_premium_pct", "max_total_option_premium_pct",
        "max_portfolio_planned_risk_pct", "max_portfolio_option_stress_risk_pct",
        "max_portfolio_theoretical_option_loss_pct",
    )
    _DOLLAR_FIELDS = (
        "max_loss_per_trade", "max_position_notional", "max_daily_loss",
        "max_drawdown", "min_cash_reserve",
    )
    _COUNT_FIELDS = ("max_open_positions", "max_positions_per_sector", "max_open_option_positions")

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("RiskPolicy.name must be nonempty")
        if not self.source or not self.source.strip():
            raise ValueError("RiskPolicy.source must be nonempty (provenance is mandatory)")
        for f in self._PCT_FIELDS:
            v = getattr(self, f)
            if v is not None and v < 0:
                raise ValueError(f"{f} must be nonnegative (fraction convention: 0.01 = 1%), got {v}")
        for f in self._DOLLAR_FIELDS:
            v = getattr(self, f)
            if v is not None and v < 0:
                raise ValueError(f"{f} must be nonnegative, got {v}")
        for f in self._COUNT_FIELDS:
            v = getattr(self, f)
            if v is not None and v <= 0:
                raise ValueError(f"{f} must be positive when supplied, got {v}")


# ══════════════════════════════════════════════════════════════════════════
# Canonical cap-combining helper — the ONE formula every profile shares
# ══════════════════════════════════════════════════════════════════════════

class BindingSource(str, Enum):
    ABSOLUTE = "absolute"
    PERCENTAGE = "percentage"
    BOTH = "both"           # equal caps — deterministic, named rather than
                             # arbitrarily preferring one side
    UNBOUNDED = "unbounded"


@dataclass(frozen=True)
class LimitResult:
    limit: Optional[float]           # None only when genuinely unbounded
    binding_source: BindingSource


def effective_limit(*, absolute: Optional[float], percent: Optional[float],
                    equity: float) -> LimitResult:
    """effective_cap = min(absolute, equity * percent) — the "tighter
    constraint wins" pattern already used by lab/paper/config.py:
    RiskConfig.loss_cap() and RiskConfig.sector_cap(), generalized so every
    RiskPolicy field goes through this ONE function regardless of which
    profile it came from. A missing side is infinity, never zero.
    """
    if absolute is not None and absolute < 0:
        raise ValueError(f"absolute cap must be nonnegative, got {absolute}")
    if percent is not None and percent < 0:
        raise ValueError(f"percentage cap must be nonnegative, got {percent}")
    if equity < 0:
        raise ValueError(f"equity must be nonnegative, got {equity}")

    pct_limit = (equity * percent) if percent is not None else None

    if absolute is None and pct_limit is None:
        return LimitResult(None, BindingSource.UNBOUNDED)
    if absolute is not None and pct_limit is None:
        return LimitResult(round(absolute, 2), BindingSource.ABSOLUTE)
    if absolute is None and pct_limit is not None:
        return LimitResult(round(pct_limit, 2), BindingSource.PERCENTAGE)

    a, p = round(absolute, 2), round(pct_limit, 2)
    if a == p:
        return LimitResult(a, BindingSource.BOTH)
    return LimitResult(min(a, p), BindingSource.ABSOLUTE if a < p else BindingSource.PERCENTAGE)


def per_trade_loss_cap(policy: RiskPolicy, equity: float) -> LimitResult:
    """Convenience wrapper: the policy's per-trade loss cap at a given
    equity, via the one shared formula."""
    return effective_limit(absolute=policy.max_loss_per_trade,
                           percent=policy.risk_per_trade_pct, equity=equity)


def position_notional_cap(policy: RiskPolicy, equity: float) -> LimitResult:
    return effective_limit(absolute=policy.max_position_notional,
                           percent=policy.max_position_notional_pct, equity=equity)


def daily_loss_cap(policy: RiskPolicy, equity: float) -> LimitResult:
    return effective_limit(absolute=policy.max_daily_loss,
                           percent=policy.max_daily_loss_pct, equity=equity)


def drawdown_cap(policy: RiskPolicy, equity: float) -> LimitResult:
    return effective_limit(absolute=policy.max_drawdown,
                           percent=policy.max_drawdown_pct, equity=equity)


# ══════════════════════════════════════════════════════════════════════════
# Profile 1 — Strategy500Policy
#
# Source: lab/paper/config.py — AccountConfig (fractional_shares) and
# RiskConfig.risk() (everything else), read from source this session
# (2026-08-26). Values quoted are the env-var DEFAULTS as of that read; each
# is independently overridable at runtime via the same env vars in the real
# lab/paper account, which this profile deliberately does not read (no
# runtime coupling — see module docstring).
# ══════════════════════════════════════════════════════════════════════════

STRATEGY_500_POLICY = RiskPolicy(
    name="Strategy500Policy",
    source="lab/paper/config.py: AccountConfig + RiskConfig.risk() defaults, verified 2026-08-26",

    # RiskConfig.max_loss_per_trade default 5.0 (PAPER_MAX_LOSS_PER_TRADE)
    max_loss_per_trade=5.0,
    # RiskConfig.risk_per_trade_pct default 0.01 (PAPER_RISK_PER_TRADE) —
    # already fraction convention; source comment: "1% of $500 = $5"
    risk_per_trade_pct=0.01,

    # RiskConfig.max_position_notional default 125.0 (PAPER_MAX_POSITION_NOTIONAL)
    max_position_notional=125.0,
    max_position_notional_pct=None,   # no percentage-of-equity variant exists in lab/paper

    max_portfolio_exposure_pct=None,  # lab/paper has no general (all-sector) exposure %

    # RiskConfig.max_sector_exposure_pct default 0.30 (PAPER_MAX_SECTOR_EXPOSURE) —
    # already fraction convention. Added in Step 4 after discovering it while
    # generalizing lab/paper/risk.py's check_entry() (max_sector_exposure check,
    # via RiskConfig.sector_cap()) for canonical B(stock); missed in the
    # original Step 3 field list.
    max_sector_exposure_pct=0.30,

    # RiskConfig.max_open_positions default 3 (PAPER_MAX_OPEN)
    max_open_positions=3,
    # RiskConfig.max_positions_per_sector default 1 (PAPER_MAX_PER_SECTOR)
    max_positions_per_sector=1,
    # RiskConfig.max_daily_loss default 10.0 (PAPER_MAX_DAILY_LOSS_USD)
    max_daily_loss=10.0,
    max_daily_loss_pct=None,          # absolute-only in lab/paper
    # RiskConfig.max_drawdown default 50.0 (PAPER_MAX_DRAWDOWN_USD)
    max_drawdown=50.0,
    max_drawdown_pct=None,            # absolute-only in lab/paper
    # RiskConfig.min_cash_reserve default 100.0 (PAPER_MIN_CASH_RESERVE_USD)
    min_cash_reserve=100.0,

    # lab/paper/config.py has no option-specific policy fields at all today —
    # represented explicitly as None, not fabricated. This profile is a
    # representation of CURRENT stock-only policy, not authorization to
    # size or execute options; Strategy-500 options remain shadow-only
    # regardless of what a future profile revision might add here.
    option_planned_risk_pct=None,
    max_long_option_premium_pct=None,
    max_total_option_premium_pct=None,
    max_portfolio_planned_risk_pct=None,
    max_portfolio_option_stress_risk_pct=None,
    max_portfolio_theoretical_option_loss_pct=None,
    max_open_option_positions=None,

    # AccountConfig.fractional_shares default True (PAPER_FRACTIONAL_SHARES) —
    # "Robinhood supports fractional shares on most listed US equities."
    fractional_shares=True,
)


# ══════════════════════════════════════════════════════════════════════════
# Profile 2 — DashboardPolicy
#
# Source: TWO legacy config surfaces, both actively read by
# dashboard/options_desk.py's evaluate_candidate()/decide() pipeline today
# (Pipeline 2 — Scanner -> Desk -> Tracker), verified this session
# (2026-08-26):
#   * dashboard/options_desk.py: config() — the STOCK-side sizing block
#     ("risk / sizing (Phase 9)"), confirmed via grep to actually be READ at
#     evaluate_candidate() lines 764-768 (risk_budget/notional_cap/
#     exposure_room) and lines 750-753 (position/sector counts).
#   * dashboard/option_risk.py: policy("BALANCED") — the OPTION-side profile.
#     BALANCED is confirmed the current default: active_profile() returns
#     "BALANCED" whenever RISK_PROFILE is unset.
# Percentage-point values from option_risk.py's PROFILES dict are divided by
# 100 to match this schema's fraction convention (see module docstring); the
# raw source value is quoted beside each conversion.
# ══════════════════════════════════════════════════════════════════════════

DASHBOARD_POLICY = RiskPolicy(
    name="DashboardPolicy",
    source=("dashboard/options_desk.py: config() (stock sizing) + "
            "dashboard/option_risk.py: policy('BALANCED') (option policy), "
            "verified 2026-08-26"),

    # options_desk.config()["max_risk_dollars"] default 0.0 — THEIR code
    # treats 0.0 as its own "no absolute cap" sentinel (see the inline
    # comment at that definition: "0 = no absolute cap"). This schema's
    # sentinel for unbounded is None, not 0.0 — 0.0 here would mean "zero
    # dollars of risk allowed," the opposite of the source's intent. This is
    # the exact gap v1.1 identified: DashboardPolicy has percentage-oriented
    # limits but no real absolute per-trade cap. NOT fixed here — left
    # unbounded, exactly as it is today, as an explicit human policy decision
    # (v1.1 correction 6 / this module's docstring).
    max_loss_per_trade=None,
    # options_desk.config()["max_account_risk_pct"] default 1.0 (percentage-
    # points, SIZE_MAX_ACCOUNT_RISK_PCT) -> 1.0 / 100 = 0.01. Confirmed via
    # grep this is the ACTIVE stock risk_budget source (evaluate_candidate()
    # line 764); option_risk.py's PROFILES also defines a
    # "stock_planned_risk_pct" (also 1.0 in BALANCED) but it is confirmed
    # display-only (profile_table()) — never read for stock sizing anywhere.
    risk_per_trade_pct=0.01,

    max_position_notional=None,   # no absolute $ notional cap on the dashboard side
    # options_desk.config()["max_position_pct"] default 20.0 (percentage-
    # points, SIZE_MAX_POSITION_PCT) -> 0.20
    max_position_notional_pct=0.20,
    # options_desk.config()["max_total_exposure_pct"] default 60.0
    # (percentage-points, SIZE_MAX_TOTAL_EXPOSURE_PCT) -> 0.60
    max_portfolio_exposure_pct=0.60,
    max_sector_exposure_pct=None,  # no dashboard-side sector-exposure %-of-equity
                                    # control exists today (only the per-sector
                                    # position COUNT cap below)

    # options_desk.config()["max_concurrent_positions"] default 5 (SIZE_MAX_POSITIONS)
    max_open_positions=5,
    # options_desk.config()["max_positions_per_sector"] default 2 (SIZE_MAX_PER_SECTOR)
    max_positions_per_sector=2,
    max_daily_loss=None,
    max_daily_loss_pct=None,   # options_desk.config() defines
                                # SIZE_MAX_DAILY_LOSS_PCT (3.0%) but it is
                                # verified UNUSED — no check anywhere reads
                                # cfg["max_daily_loss_pct"]. Representing an
                                # unenforced number as an enforced canonical
                                # limit would misstate current policy, so
                                # this is left unbounded rather than set to
                                # the dead default.
    max_drawdown=None,
    max_drawdown_pct=None,     # no dashboard-side drawdown control exists today
    min_cash_reserve=None,     # no dashboard-side cash-reserve floor exists today

    # option_risk.py PROFILES["BALANCED"], percentage-points -> fraction:
    option_planned_risk_pct=0.04,                      # "option_planned_risk_pct": 4.0
    max_long_option_premium_pct=0.10,                  # "max_long_option_premium_pct": 10.0
    max_total_option_premium_pct=0.20,                 # "max_total_option_premium_pct": 20.0
    max_portfolio_planned_risk_pct=0.06,                # "max_portfolio_planned_risk_pct": 6.0
    max_portfolio_option_stress_risk_pct=0.12,          # "max_portfolio_option_stress_risk_pct": 12.0
    max_portfolio_theoretical_option_loss_pct=0.20,     # "max_portfolio_theoretical_option_loss_pct": 20.0
    max_open_option_positions=2,                        # "max_open_option_positions": 2

    # options_desk.config()["fractional_shares"] default True (SIZE_FRACTIONAL)
    fractional_shares=True,
)


# ══════════════════════════════════════════════════════════════════════════
# Future live-execution profile — deliberately impossible to instantiate with
# permissive defaults. No values are populated anywhere in this module; the
# only way to get an object back is to build one explicitly, which this
# function refuses to do.
# ══════════════════════════════════════════════════════════════════════════

def future_live_policy() -> RiskPolicy:
    """There is no live-execution risk profile. This function exists only as
    a documented, deliberately-unusable placeholder so a future caller finds
    an explicit refusal here instead of silently inheriting paper/dashboard
    values (v1.1: "never infer live risk appetite from paper/dashboard
    values"; live execution is, per the project's own framing, "a different,
    much bigger decision than anything in this repo today"). Always raises."""
    raise NotImplementedError(
        "No live-execution RiskPolicy exists. Live risk appetite must never be "
        "inferred from Strategy500Policy or DashboardPolicy — populating this "
        "requires an explicit, separate, human policy decision (see Canonical "
        "Option Architecture v1.1, shadow-mode promotion gate)."
    )
