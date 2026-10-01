"""HIST-003 Section B: identifies the literal marginal-slot trades for a higher-capacity arm against the
2/day control, and separately counts how often that slot was genuinely *available* (blocked by the daily
cap specifically, not by some other gate) under the control. Read-only over already-produced
`run_baseline()` results -- never re-runs or re-decides anything.
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Dict, List


def entries_by_day(result: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Entry orders for this result, grouped by session_date, sorted chronologically within each day."""
    entries = [o for o in result["orders"] if o.get("intent") == "entry"]
    per_day: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for o in entries:
        per_day[o["session_date"]].append(o)
    return {d: sorted(os, key=lambda o: o["created_at"]) for d, os in per_day.items()}


def marginal_slot_trades(variant_result: Dict[str, Any], control_result: Dict[str, Any],
                         slot_ordinal: int) -> List[Dict[str, Any]]:
    """Days where `variant_result` placed exactly `slot_ordinal` entries that `control_result` never did on
    any day (i.e. days reaching a NEW max entry count for this account). Returns the closed-position record
    for the slot_ordinal-th chronological entry on each such day."""
    variant_days = entries_by_day(variant_result)
    control_max = max((len(os) for os in entries_by_day(control_result).values()), default=0)
    if slot_ordinal <= control_max:
        return []

    positions_by_signal = {p.get("signal_id"): p for p in variant_result["positions"]}
    out = []
    for day, orders in variant_days.items():
        if len(orders) >= slot_ordinal:
            nth = orders[slot_ordinal - 1]
            pos = positions_by_signal.get(nth.get("signal_id"))
            if pos:
                out.append({"session_date": day, "symbol": nth["symbol"], **pos})
    return out


def days_with_genuine_cap_block(control_result: Dict[str, Any]) -> Dict[str, List[str]]:
    """Distinct session dates where the CONTROL arm's own daily-entry-cap genuinely rejected a TRADEABLE
    candidate (not_executed reason contains "daily entry cap") -- i.e. days where relaxing the cap had a
    real opportunity to act on, as opposed to days where the control simply had nothing more to buy."""
    by_day: Dict[str, List[str]] = defaultdict(list)
    for row in control_result["not_executed"]:
        try:
            detail = json.loads(row.get("detail_json") or "{}")
        except Exception:
            detail = {}
        if "daily entry cap" in str(detail.get("reason", "")).lower():
            by_day[str(row.get("at") or "")[:10]].append(row.get("entity_id"))
    return dict(by_day)


def slot_conversion_summary(variant_result: Dict[str, Any], control_result: Dict[str, Any],
                            slot_ordinal: int) -> Dict[str, Any]:
    """How many of the control's genuine cap-blocked days actually converted into a slot_ordinal-th entry
    once the cap was relaxed, vs. how many remained capped by a different gate."""
    blocked_days = days_with_genuine_cap_block(control_result)
    variant_days = entries_by_day(variant_result)
    converted = [d for d in blocked_days if len(variant_days.get(d, [])) >= slot_ordinal]
    not_converted = [d for d in blocked_days if d not in converted]
    trades = marginal_slot_trades(variant_result, control_result, slot_ordinal)
    pnls = [t.get("realized_pnl") or 0.0 for t in trades if t.get("status") == "closed"]
    return {
        "slot_ordinal": slot_ordinal,
        "control_days_with_genuine_cap_block": len(blocked_days),
        "converted_to_actual_entry": len(converted),
        "converted_days": sorted(converted),
        "blocked_by_a_different_gate_even_after_relaxing": sorted(not_converted),
        "marginal_trades": len(trades),
        "marginal_net_pnl": round(sum(pnls), 2) if pnls else None,
        "marginal_avg_pnl": round(sum(pnls) / len(pnls), 2) if pnls else None,
        "marginal_win_rate_pct": round(100.0 * sum(1 for p in pnls if p > 0) / len(pnls), 1) if pnls else None,
    }
