"""H5 blocker/directive #11 -- execution-risk equivalence, historical-as-executed mode.

The 2026-09-25 forward session ran on code as of commit `9b5c30e` (per
`docs/UBUNTU_LIVE_ACCEPTANCE_01.md`'s "image avdi-paper:9b5c30e..."), BEFORE `322e325`
("Risk sizing: the approved dollar risk must survive executable fill pricing", 2026-09-25 19:56 -0400,
i.e. landed the evening AFTER that session -- the forward trades themselves used the pre-fix formula).
`h1/historical-lab`'s current `lab/paper/risk.py`/`canonical/account_fit.py` already have that fix. The
directive requires this NOT be rewritten -- "Do not rewrite history" -- so this module reimplements the OLD
(pre-`322e325`) sizing formula, standalone, for comparison only. It is NEVER imported by `execution.py` or
wired into any live decision path; it exists to answer "what would the OLD formula have produced against
this exact historical replay's own inputs", side by side with the CURRENT-corrected result the real,
unmodified `risk.position_size()`/`stock_account_fit()` already produce.

Old formula (risk.py, pre-322e325): `per_share_risk = abs(entry - stop)` (the raw reference distance,
never adjusted for the executable fill), `q_risk = budget / per_share_risk`, and the final quantity rounded
to 6dp with ordinary `round()` (not floored) -- meaning the realised stop loss at the executable fill could
exceed the planned budget, exactly the defect `322e325`'s commit message documents evidence for (DELL
realised $5.277 vs. the $5.00 budget; META $5.296).

Two modes are produced from the SAME decision (entry/stop/quote), never mixed:
  * `historical_as_executed_quantity()` -- the pre-322e325 formula, for validating the historical
    reproduction is faithful to what ACTUALLY happened on 2026-09-25.
  * the real, current `risk.position_size()` -- a counterfactual showing what current AVDI would do,
    called directly by the H5 replay/report, not duplicated here.
P&L from the two must never be pooled into one figure; the H5 report keeps them in separate columns.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def historical_as_executed_quantity(equity: float, entry: float, stop: float, *,
                                    max_loss_per_trade: float, risk_per_trade_pct: float,
                                    max_position_notional: float, fill_price: Optional[float] = None,
                                    buying_power: Optional[float] = None,
                                    fractional_shares: bool = True,
                                    fractional_min_notional: float = 1.0) -> Dict[str, Any]:
    """The pre-322e325 `risk.position_size()` formula, verbatim in its risk arithmetic (raw reference
    per-share distance, `round()` not `floor()`), reimplemented standalone so it can run without mutating
    or reverting any file in the current, fixed codebase. Callers pass the exact same equity/entry/stop/
    fill_price/buying_power the CURRENT `risk.position_size()` call received, for a fair side-by-side."""
    px = fill_price if fill_price else entry
    per_share = abs(entry - stop)                      # <- the bug: no executable-risk adjustment at all
    budget = round(min(max_loss_per_trade, equity * risk_per_trade_pct), 2)
    if per_share <= 0 or px <= 0:
        return {"quantity": 0.0, "risk_budget": budget, "risk_per_share": 0.0, "planned_risk": 0.0,
                "notional": 0.0, "affordable": False, "binding_constraint": "invalid_prices",
                "mode": "historical_as_executed"}

    bp = buying_power if buying_power is not None else 0.0
    q_risk = budget / per_share
    q_notional = max_position_notional / px
    q_cash = bp / px
    qty = min(q_risk, q_notional, q_cash)
    binding = ["risk", "notional", "cash"][[q_risk, q_notional, q_cash].index(qty)]

    if not fractional_shares:
        qty = float(int(qty))
        if qty < 1:
            return {"quantity": 0.0, "risk_budget": budget, "risk_per_share": round(per_share, 4),
                    "planned_risk": 0.0, "notional": 0.0, "affordable": False,
                    "binding_constraint": "cannot_afford_one_share", "mode": "historical_as_executed"}
    else:
        qty = round(qty, 6)                              # <- the other half of the bug: rounds, doesn't floor
        if qty * px < fractional_min_notional:
            return {"quantity": 0.0, "risk_budget": budget, "risk_per_share": round(per_share, 4),
                    "planned_risk": 0.0, "notional": 0.0, "affordable": False,
                    "binding_constraint": "below_min_notional", "mode": "historical_as_executed"}

    notional = round(qty * px, 2)
    return {"quantity": qty, "risk_budget": budget, "risk_per_share": round(per_share, 4),
            "planned_risk": round(qty * per_share, 2), "notional": notional, "share_price": round(px, 2),
            "affordable": notional <= bp + 1e-9 and qty > 0, "binding_constraint": binding,
            "mode": "historical_as_executed"}


def realized_stop_loss(quantity: float, executable_entry: float, stop: float, *,
                       exit_slippage_bps: float, fee_per_share: float) -> float:
    """What a position of this size ACTUALLY loses if the stop fills the way the paper broker's fill
    simulator models a stop fill (stop * (1 - exit_slippage), plus round-trip fees) -- used to show, for
    the historical-as-executed quantity, how far the realised loss exceeds the $5 budget it was sized
    against (the exact defect 322e325 fixed)."""
    fill = stop * (1.0 - exit_slippage_bps / 1e4)
    per_share_loss = (executable_entry - fill) + 2.0 * fee_per_share
    return round(quantity * per_share_loss, 4)
