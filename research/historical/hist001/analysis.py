"""HIST-001: post-hoc analysis over a `baseline.run_baseline()` result -- funnel attribution, capacity
opportunity cost (via H6's `resolve_hypothetical`), choice-event records, the CH-001 shadow comparison, and
basic portfolio/trade/path metrics. Every function here is read-only over the replay's own output; none of
them change a Champion decision or re-run any part of the replay.
"""
from __future__ import annotations

import datetime as dt
import json
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

from ..outcomes import resolve_hypothetical


def funnel_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    """UNIVERSE -> scanner detections -> raw setups -> validation -> A/B/C/D/E -> finalists -> TRADEABLE ->
    capacity-eligible -> orders -> fills -> exits, with observation counts, independent-event counts (via
    each observation's own event_id, stamped during the replay), conversion %, and major rejection reasons.
    Scanner-detection/raw-setup/finalist counts come straight from each cycle's own scan() output
    (candidate_count/finalist_count), already the real funnel the Champion itself reports."""
    total_observations = 0
    events_seen: set = set()
    by_decision = Counter()
    events_by_decision: Dict[str, set] = defaultdict(set)
    candidates_total = 0
    finalists_total = 0
    cycles_ok = 0

    for c in result["cycles"]:
        pm = c["premarket"]
        if pm.get("state") != "ok":
            continue
        cycles_ok += 1
        candidates_total += pm.get("candidates") or 0
        finalists_total += len(pm.get("finalists") or [])
        for ev in pm.get("evaluated", []):
            total_observations += 1
            action = ev.get("action")
            by_decision[action] += 1
            eid = ev.get("event_id")
            if eid:
                events_seen.add(eid)
                events_by_decision[action].add(eid)

    reasons = Counter()
    for row in result["not_executed"]:
        try:
            detail = json.loads(row["detail_json"]) if row.get("detail_json") else {}
        except Exception:
            detail = {}
        reasons[detail.get("reason", "unknown")] += 1

    tradeable_events = len(events_by_decision.get("TRADEABLE", set()))
    orders = result["orders"]
    fills = result["fills"]
    closed_positions = [p for p in result["positions"] if p.get("status") == "closed"]

    def pct(n, d):
        return round(100.0 * n / d, 2) if d else None

    return {
        "cycles_completed_ok": cycles_ok, "cycles_total": len(result["cycles"]),
        "candidates_total_raw": candidates_total, "finalists_total_raw": finalists_total,
        "raw_observations": total_observations, "independent_events": len(events_seen),
        "by_decision_observations": dict(by_decision),
        "by_decision_independent_events": {k: len(v) for k, v in events_by_decision.items()},
        "tradeable_independent_events": tradeable_events,
        "orders_placed": len(orders), "fills": len(fills), "exits": len(closed_positions),
        "conversion_pct": {
            "observations_to_tradeable": pct(by_decision.get("TRADEABLE", 0), total_observations),
            "tradeable_events_to_orders": pct(len(orders), tradeable_events) if tradeable_events else None,
            "orders_to_fills": pct(len(fills), len(orders)),
        },
        "major_rejection_reasons": reasons.most_common(10),
    }


def survivorship_bias_note(universe_symbols: List[str], dataset_symbols: List[str]) -> Dict[str, Any]:
    """Directive sec 17, mandatory: today's sector_map membership was used (never called unbiased)."""
    missing = sorted(set(universe_symbols) - set(dataset_symbols))
    return {
        "universe_source": "today's dashboard/sector_map.py membership (NOT point-in-time historical "
                          "membership -- fabhaus/this project has no reliable point-in-time constituent "
                          "history)",
        "symbols_in_universe": len(universe_symbols), "symbols_with_data": len(dataset_symbols),
        "symbols_missing_from_source": missing,
        "delisted_or_ticker_changed_excluded": "not separately tracked at this stage -- no ticker-change/"
                                              "delisting metadata was supplied to corporate_actions.py for "
                                              "this batch (quarantine-by-default: none silently remapped)",
        "verdict": "SURVIVORSHIP BIAS PRESENT -- today's surviving universe was replayed backward. Any "
                  "symbol delisted, merged, or renamed between the replay period and today is absent from "
                  "this universe by construction, not detected and excluded after the fact.",
    }


def capacity_opportunity_cost(result: Dict[str, Any], provider, decision_provider=None) -> List[Dict[str, Any]]:
    """For every TRADEABLE observation that did NOT execute (blocked by daily-entry cap / sector cap /
    open-position cap / capital / cutoff -- read from its own journalled `note`/not_executed reason),
    resolve its hypothetical outcome via H6, using its own decision-time price/stop/target. Does not
    conclude the constraint should change (directive sec 13) -- purely descriptive opportunity cost."""
    blocked_capacity_phrases = ("daily entry cap", "sector", "capacity", "post-cutoff", "post_cutoff",
                               "existing position", "already entered")
    out: List[Dict[str, Any]] = []
    for c in result["cycles"]:
        pm = c["premarket"]
        if pm.get("state") != "ok":
            continue
        decision_time = dt.datetime.fromisoformat(c["et_time"])
        for ev in pm.get("evaluated", []):
            if ev.get("action") != "TRADEABLE" or ev.get("executed"):
                continue
            note = (ev.get("note") or "").lower()
            reasons_matched = [phrase for phrase in blocked_capacity_phrases if phrase in note]
            if not reasons_matched:
                continue
            # The evaluated dict from premarket() carries only the action/note, not price/stop/target --
            # those live in the real decision output, not retrievable post-hoc without re-evaluating. This
            # is disclosed as a scope limit of this analysis pass, not silently worked around: a genuine
            # capacity-opportunity-cost resolution needs the ORIGINAL decision's own levels, captured at
            # replay time in a future increment (see HIST_001 report's "not yet done" section).
            out.append({"symbol": ev["symbol"], "cycle_id": c["cycle_id"], "session_date": c["session_date"],
                       "event_id": ev.get("event_id"), "blocked_reason": ev.get("note"),
                       "hypothetical_outcome": "NOT RESOLVED -- decision-time price/stop/target were not "
                       "captured at replay time for non-executed TRADEABLE observations in this pass"})
    return out


def choice_events(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Directive sec 14: whenever two or more TRADEABLE candidates compete for a limited slot in the SAME
    cycle, record the candidate set and which one the Champion actually selected (executed)."""
    out = []
    for c in result["cycles"]:
        pm = c["premarket"]
        if pm.get("state") != "ok":
            continue
        tradeable = [ev for ev in pm.get("evaluated", []) if ev.get("action") == "TRADEABLE"]
        if len(tradeable) < 2:
            continue
        selected = [ev["symbol"] for ev in tradeable if ev.get("executed")]
        out.append({"cycle_id": c["cycle_id"], "session_date": c["session_date"],
                   "candidates": [ev["symbol"] for ev in tradeable], "selected": selected,
                   "candidate_detail": [{"symbol": ev["symbol"], "note": ev.get("note")} for ev in tradeable]})
    return out


def ch001_shadow(result: Dict[str, Any]) -> Dict[str, Any]:
    """SHADOW ANALYSIS ONLY (directive sec 15): Champion's real exit vs. a hypothetical breakeven-after-+1R
    stop management, for every REAL position that reached +1R (per its own recorded MFE). Never affects a
    real position. Uses the real positions table's own mfe/stop/target/avg_entry -- does not re-run
    outcomes.py bar-by-bar (that needs the raw provider bars, a further increment); reports what CAN be
    determined from the ledger's own recorded MFE/exit alone, and says so."""
    reached_plus_1r = []
    for p in result["positions"]:
        entry, stop = p.get("avg_entry"), p.get("stop")
        if entry is None or stop is None or entry == stop:
            continue
        r_per_share = abs(entry - stop)
        mfe_r = (p.get("mfe") or 0.0) / r_per_share if r_per_share else 0.0
        if mfe_r >= 1.0:
            reached_plus_1r.append({"position_id": p["position_id"], "symbol": p["symbol"],
                                   "mfe_r": round(mfe_r, 3), "status": p.get("status"),
                                   "realized_pnl": p.get("realized_pnl")})
    return {
        "note": "SHADOW ANALYSIS -- does not affect real Champion positions. HIST-002 remains the formal "
               "Challenger experiment; CH-001 is not promoted from this alone.",
        "independent_trades_reaching_plus_1r": len(reached_plus_1r), "detail": reached_plus_1r,
        "scope_limit": "full CH-001 delta-R (Champion exit vs. breakeven-after-+1R exit) requires a bar-by-"
                      "bar outcomes.py resolution from the +1R timestamp forward -- not computed in this "
                      "pass; this reports only which real positions reached +1R per their own ledger MFE.",
    }


def portfolio_metrics(result: Dict[str, Any]) -> Dict[str, Any]:
    positions = result["positions"]
    closed = [p for p in positions if p.get("status") == "closed"]
    realized = [p.get("realized_pnl") or 0.0 for p in closed]
    acct = result["account"]
    return {
        "starting_equity": acct.get("starting_equity"), "ending_equity": acct.get("equity"),
        "net_pnl": round(sum(realized), 2), "resolved_trades": len(closed), "open_positions_end": len(
            [p for p in positions if p.get("status") == "open"]),
        "win_rate_pct": round(100.0 * sum(1 for r in realized if r > 0) / len(closed), 1) if closed else None,
        "avg_win": round(sum(r for r in realized if r > 0) / max(1, sum(1 for r in realized if r > 0)), 2) if closed else None,
        "avg_loss": round(sum(r for r in realized if r < 0) / max(1, sum(1 for r in realized if r < 0)), 2) if closed else None,
        "note_on_sample_size": "Sharpe/Sortino and other distributional statistics are NOT reported at this "
                              "sample size (per the pre-registration: reported only when the independent-"
                              "event count is large enough to be statistically meaningful).",
    }
