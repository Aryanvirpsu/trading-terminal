"""research/historical/hist003/slot_analysis.py: identifies the literal marginal-slot trades for a
higher-capacity arm against a lower-capacity control, and counts how often the daily cap itself (vs. some
other gate) was the thing standing between the control and an extra entry."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from research.historical.hist003.slot_analysis import (  # noqa: E402
    days_with_genuine_cap_block, entries_by_day, marginal_slot_trades, slot_conversion_summary)


def _order(session_date, symbol, created_at, signal_id):
    return {"intent": "entry", "session_date": session_date, "symbol": symbol, "created_at": created_at,
           "signal_id": signal_id}


def _position(signal_id, symbol, realized_pnl, status="closed"):
    return {"signal_id": signal_id, "symbol": symbol, "status": status, "realized_pnl": realized_pnl}


def _not_executed_row(at, reason, entity_id="sig_x"):
    import json
    return {"at": at, "entity_id": entity_id, "detail_json": json.dumps({"reason": reason})}


def test_entries_by_day_groups_and_sorts_chronologically():
    result = {"orders": [
        _order("2024-01-02", "B", "2024-01-02T14:00:00+00:00", "sig_b"),
        _order("2024-01-02", "A", "2024-01-02T13:00:00+00:00", "sig_a"),
        _order("2024-01-03", "C", "2024-01-03T13:00:00+00:00", "sig_c"),
    ]}
    by_day = entries_by_day(result)
    assert [o["symbol"] for o in by_day["2024-01-02"]] == ["A", "B"]
    assert [o["symbol"] for o in by_day["2024-01-03"]] == ["C"]


def test_marginal_slot_trades_only_counts_days_exceeding_the_controls_own_max():
    control = {"orders": [_order("2024-01-02", "A", "2024-01-02T13:00:00+00:00", "sig_a"),
                          _order("2024-01-02", "B", "2024-01-02T14:00:00+00:00", "sig_b")]}
    variant = {"orders": [_order("2024-01-02", "A", "2024-01-02T13:00:00+00:00", "sig_a"),
                          _order("2024-01-02", "B", "2024-01-02T14:00:00+00:00", "sig_b"),
                          _order("2024-01-02", "C", "2024-01-02T15:00:00+00:00", "sig_c"),
                          _order("2024-01-03", "D", "2024-01-03T13:00:00+00:00", "sig_d")],
               "positions": [_position("sig_a", "A", 1.0), _position("sig_b", "B", 2.0),
                            _position("sig_c", "C", -5.0), _position("sig_d", "D", 3.0)]}
    trades = marginal_slot_trades(variant, control, slot_ordinal=3)
    assert len(trades) == 1
    assert trades[0]["symbol"] == "C"
    assert trades[0]["realized_pnl"] == -5.0


def test_marginal_slot_trades_returns_nothing_when_control_already_reaches_that_ordinal():
    control = {"orders": [_order("2024-01-02", "A", "2024-01-02T13:00:00+00:00", "sig_a"),
                          _order("2024-01-02", "B", "2024-01-02T14:00:00+00:00", "sig_b"),
                          _order("2024-01-02", "C", "2024-01-02T15:00:00+00:00", "sig_c")]}
    variant = {"orders": control["orders"], "positions": []}
    assert marginal_slot_trades(variant, control, slot_ordinal=3) == []


def test_days_with_genuine_cap_block_filters_to_the_daily_cap_reason_only():
    control = {"not_executed": [
        _not_executed_row("2024-01-02T14:00:00+00:00", "daily entry cap reached (2 per day, incl. persisted entries)"),
        _not_executed_row("2024-01-03T14:00:00+00:00", "stock leg not canonically executable"),
        _not_executed_row("2024-01-05T14:00:00+00:00", "daily entry cap reached (2 per day, incl. persisted entries)"),
    ]}
    blocked = days_with_genuine_cap_block(control)
    assert set(blocked.keys()) == {"2024-01-02", "2024-01-05"}


def test_slot_conversion_summary_separates_converted_from_still_blocked_by_other_gate():
    control = {
        "orders": [_order("2024-01-02", "A", "2024-01-02T13:00:00+00:00", "sig_a"),
                  _order("2024-01-02", "B", "2024-01-02T14:00:00+00:00", "sig_b"),
                  _order("2024-01-05", "E", "2024-01-05T13:00:00+00:00", "sig_e"),
                  _order("2024-01-05", "F", "2024-01-05T14:00:00+00:00", "sig_f")],
        "not_executed": [
            _not_executed_row("2024-01-02T14:00:00+00:00",
                              "daily entry cap reached (2 per day, incl. persisted entries)"),
            _not_executed_row("2024-01-05T14:00:00+00:00",
                              "daily entry cap reached (2 per day, incl. persisted entries)"),
        ],
    }
    variant = {
        "orders": control["orders"] + [_order("2024-01-02", "C", "2024-01-02T15:00:00+00:00", "sig_c")],
        "positions": [_position("sig_c", "C", -3.0)],
        "not_executed": [],
    }
    summary = slot_conversion_summary(variant, control, slot_ordinal=3)
    assert summary["control_days_with_genuine_cap_block"] == 2
    assert summary["converted_days"] == ["2024-01-02"]
    assert summary["blocked_by_a_different_gate_even_after_relaxing"] == ["2024-01-05"]
    assert summary["marginal_trades"] == 1
    assert summary["marginal_net_pnl"] == -3.0
    assert summary["marginal_win_rate_pct"] == 0.0
