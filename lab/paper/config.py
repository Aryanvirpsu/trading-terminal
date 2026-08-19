"""Paper-trading configuration, sized for a REAL $500 Robinhood cash account.

The account size is part of the experiment, not a display detail. A $500 cash account
cannot buy one share of many index names, cannot hold five $125 positions and a $100
reserve at once, and cannot afford most option premiums. Testing at $10,000 and scaling
down afterwards would produce a strategy that is unreachable in practice — so every
limit here is an ABSOLUTE DOLLAR amount, evaluated against real buying power.

Percentage caps are still supported and the TIGHTER of (absolute, percentage) always
wins, so the same code is honest at any account size.

Cash account rules enforced: no margin, no leverage, no borrowing, no shorting, no
naked or multi-leg options.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def _i(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return int(default)


def _b(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class AccountConfig:
    """The simulated brokerage account itself."""
    initial_cash: float
    initial_equity: float
    buying_power: float
    margin_enabled: bool
    allow_shorting: bool
    allow_naked_options: bool
    fractional_shares: bool
    fractional_min_notional: float   # smallest fractional order the broker accepts
    ledger: str

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def account() -> AccountConfig:
    from . import db
    cash = _f("PAPER_INITIAL_CASH", 500.0)
    return AccountConfig(
        initial_cash=cash,
        initial_equity=_f("PAPER_INITIAL_EQUITY", cash),
        buying_power=_f("PAPER_BUYING_POWER", cash),
        margin_enabled=_b("PAPER_MARGIN_ENABLED", False),
        allow_shorting=_b("PAPER_ALLOW_SHORTING", False),
        allow_naked_options=_b("PAPER_ALLOW_NAKED_OPTIONS", False),
        # Robinhood supports fractional shares on most listed US equities.
        fractional_shares=_b("PAPER_FRACTIONAL_SHARES", True),
        fractional_min_notional=_f("PAPER_FRACTIONAL_MIN_NOTIONAL", 1.0),
        ledger=db.ledger_name(),
    )


@dataclass(frozen=True)
class RiskConfig:
    # Absolute dollar caps (primary for a small cash account)
    max_loss_per_trade: float
    max_position_notional: float
    min_cash_reserve: float
    max_daily_loss: float
    max_drawdown: float
    # Counts
    max_entries_per_day: int
    max_open_positions: int
    max_positions_per_sector: int
    max_correlated_positions: int
    # Percentage caps — applied IN ADDITION; the tighter of the two always wins
    risk_per_trade_pct: float
    max_sector_exposure_pct: float
    # Cooldown
    cooldown_losses: int
    cooldown_days: int
    # Back-compat alias used by older callers
    starting_equity: float

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def loss_cap(self, equity: float) -> float:
        """Tighter of the absolute per-trade loss cap and the percentage cap."""
        return round(min(self.max_loss_per_trade, equity * self.risk_per_trade_pct), 2)

    def sector_cap(self, equity: float) -> float:
        return round(min(self.max_position_notional * self.max_positions_per_sector,
                         equity * self.max_sector_exposure_pct), 2)


def risk() -> RiskConfig:
    acct_cash = _f("PAPER_INITIAL_CASH", 500.0)
    return RiskConfig(
        # Defaults below are the real-account constraints for a $500 Robinhood cash
        # account. Every one is overridable.
        max_loss_per_trade=_f("PAPER_MAX_LOSS_PER_TRADE", 5.0),
        max_position_notional=_f("PAPER_MAX_POSITION_NOTIONAL", 125.0),
        min_cash_reserve=_f("PAPER_MIN_CASH_RESERVE_USD", 100.0),
        max_daily_loss=_f("PAPER_MAX_DAILY_LOSS_USD", 10.0),
        max_drawdown=_f("PAPER_MAX_DRAWDOWN_USD", 50.0),
        max_entries_per_day=_i("PAPER_MAX_ENTRIES_PER_DAY", 2),
        max_open_positions=_i("PAPER_MAX_OPEN", 3),
        max_positions_per_sector=_i("PAPER_MAX_PER_SECTOR", 1),
        max_correlated_positions=_i("PAPER_MAX_CORRELATED", 2),
        risk_per_trade_pct=_f("PAPER_RISK_PER_TRADE", 0.01),        # 1% of $500 = $5
        max_sector_exposure_pct=_f("PAPER_MAX_SECTOR_EXPOSURE", 0.30),
        cooldown_losses=_i("PAPER_COOLDOWN_LOSSES", 3),
        cooldown_days=_i("PAPER_COOLDOWN_DAYS", 1),
        starting_equity=_f("PAPER_INITIAL_EQUITY", acct_cash),
    )


@dataclass(frozen=True)
class OptionsConfig:
    max_premium_per_trade: float   # total premium incl. spread + fees
    long_only: bool
    allow_spreads: bool
    allow_multi_leg: bool
    min_open_interest: float
    min_volume: float
    max_spread_pct: float
    shadow_only: bool

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def options() -> OptionsConfig:
    return OptionsConfig(
        max_premium_per_trade=_f("PAPER_MAX_OPTION_PREMIUM", 75.0),
        long_only=True,                                   # cash account: long only
        allow_spreads=_b("PAPER_ALLOW_SPREADS", False),
        allow_multi_leg=_b("PAPER_ALLOW_MULTI_LEG", False),
        min_open_interest=_f("PAPER_OPTION_MIN_OI", 250.0),
        min_volume=_f("PAPER_OPTION_MIN_VOL", 25.0),
        max_spread_pct=_f("PAPER_OPTION_MAX_SPREAD_PCT", 10.0),
        shadow_only=_b("PAPER_OPTIONS_SHADOW_ONLY", True),
    )


@dataclass(frozen=True)
class ExecConfig:
    slippage_bps: float
    gap_slippage_bps: float
    fee_per_share: float
    fee_min: float
    fee_pct: float
    spread_bps_default: float
    partial_fill_threshold: float
    partial_fill_ratio: float
    allow_partial_fills: bool
    limit_needs_trade_through: bool
    max_order_age_days: int

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def execution() -> ExecConfig:
    return ExecConfig(
        slippage_bps=_f("PAPER_SLIPPAGE_BPS", 5.0),
        gap_slippage_bps=_f("PAPER_GAP_SLIPPAGE_BPS", 25.0),
        fee_per_share=_f("PAPER_FEE_PER_SHARE", 0.0),      # Robinhood: commission-free
        fee_min=_f("PAPER_FEE_MIN", 0.0),
        fee_pct=_f("PAPER_FEE_PCT", 0.0),
        spread_bps_default=_f("PAPER_SPREAD_BPS", 10.0),
        partial_fill_threshold=_f("PAPER_PARTIAL_THRESHOLD", 0.02),
        partial_fill_ratio=_f("PAPER_PARTIAL_RATIO", 0.5),
        allow_partial_fills=_b("PAPER_ALLOW_PARTIAL", True),
        limit_needs_trade_through=_b("PAPER_LIMIT_TRADE_THROUGH", True),
        max_order_age_days=_i("PAPER_MAX_ORDER_AGE_DAYS", 1),
    )


ACTIVE_STRATEGIES = ("liquid_momentum", "sector_relative_strength", "mean_reversion")


def enabled_strategies() -> tuple:
    raw = os.environ.get("PAPER_STRATEGIES")
    if not raw:
        return ACTIVE_STRATEGIES
    picked = tuple(s.strip() for s in raw.split(",") if s.strip() in ACTIVE_STRATEGIES)
    return picked or ACTIVE_STRATEGIES


def max_finalists() -> int:
    return _i("PAPER_MAX_FINALISTS", 5)


def snapshot() -> Dict[str, Any]:
    return {"account": account().as_dict(), "risk": risk().as_dict(),
            "options": options().as_dict(), "execution": execution().as_dict(),
            "strategies": list(enabled_strategies()), "max_finalists": max_finalists()}
