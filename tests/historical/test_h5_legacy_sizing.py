"""H5 directive #11: the pre-322e325 sizing shim reproduces the documented defect (realised stop loss can
exceed the $5 planned budget) standalone, without touching or reverting any current, fixed file."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.historical.legacy_sizing import historical_as_executed_quantity, realized_stop_loss


def test_historical_as_executed_sizes_off_the_reference_distance_not_the_executable_fill():
    """Reference entry 560.00, executable (ask+slippage) 565.6853 -- a materially worse fill than the
    reference the OLD formula sized against, exactly the DELL-shaped scenario 322e325's commit message
    describes. The old formula must size using |entry-stop| (the reference distance) with NO adjustment
    for the worse executable price."""
    entry_reference, stop, executable = 560.00, 512.00, 565.6853
    result = historical_as_executed_quantity(
        equity=504.66, entry=entry_reference, stop=stop, max_loss_per_trade=5.0, risk_per_trade_pct=0.01,
        max_position_notional=125.0, fill_price=executable, buying_power=400.0)
    assert result["mode"] == "historical_as_executed"
    expected_qty = round(5.0 / (entry_reference - stop), 6)     # budget / RAW reference distance
    assert result["quantity"] == pytest.approx(expected_qty, rel=1e-9)
    assert result["planned_risk"] == pytest.approx(round(expected_qty * (entry_reference - stop), 2))


def test_realized_stop_loss_exceeds_the_planned_budget_the_way_322e325_documented():
    """This is the exact defect 322e325 fixed: sizing off the reference distance while the ACTUAL fill is
    worse than reference means the realised loss, computed the way the broker's own fill simulator models
    a stop fill, comes in above the $5.00 budget the position was approved for."""
    entry_reference, stop, executable = 560.00, 512.00, 565.6853
    sized = historical_as_executed_quantity(
        equity=504.66, entry=entry_reference, stop=stop, max_loss_per_trade=5.0, risk_per_trade_pct=0.01,
        max_position_notional=125.0, fill_price=executable, buying_power=400.0)
    realized = realized_stop_loss(sized["quantity"], executable_entry=executable, stop=stop,
                                  exit_slippage_bps=5.0, fee_per_share=0.0)
    assert realized > sized["risk_budget"], (
        f"expected the historical-as-executed formula to overrun its own ${sized['risk_budget']} budget "
        f"(realised {realized}) -- if this ever stops being true the shim no longer models the documented bug")


def test_current_fixed_formula_does_not_overrun_the_budget_on_the_same_inputs():
    """Side-by-side counterfactual: the REAL, current risk.position_size() on the identical inputs must
    keep the realised loss within budget -- proving the shim's defect is specific to the OLD formula, not
    an artifact of these particular numbers."""
    from lab.paper import risk

    entry_reference, stop, executable = 560.00, 512.00, 565.6853
    current = risk.position_size(equity=504.66, entry=entry_reference, stop=stop, fill_price=executable,
                                 buying_power=400.0)
    realized = realized_stop_loss(current["quantity"], executable_entry=executable, stop=stop,
                                  exit_slippage_bps=5.0, fee_per_share=0.0)
    assert realized <= current["risk_budget"] + 1e-6


def test_zero_or_negative_inputs_are_rejected_not_silently_sized():
    result = historical_as_executed_quantity(equity=500.0, entry=0.0, stop=0.0, max_loss_per_trade=5.0,
                                             risk_per_trade_pct=0.01, max_position_notional=125.0)
    assert result["quantity"] == 0.0 and not result["affordable"]
