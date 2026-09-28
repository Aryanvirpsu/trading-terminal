"""H5 steps 7-9: compare the replay's own output against the frozen `reference.py`, AFTER the fact
(reference.py is never imported by replay.py). Produces the comparison matrix (step 8) with categorized
difference reasons, and the two comparison layers step 7 asks for: mechanical equivalence (chronology,
state, capacity, execution machinery) and decision equivalence (how closely the resulting DECISIONS match).
"""
from __future__ import annotations

import dataclasses
from typing import Any, Dict, List, Optional

from . import reference as ref

DIFFERENCE_REASONS = (
    "PRICE_DATA_DIFFERENCE", "QUOTE_DATA_DIFFERENCE", "CAPABILITY_DIFFERENCE", "EVENT_IDENTITY_DIFFERENCE",
    "ACCOUNT_STATE_DIFFERENCE", "EXECUTION_DIFFERENCE", "SESSION_TIMING_DIFFERENCE", "REPLAY_BUG", "UNKNOWN",
    "MATCH",   # not one of the directive's listed categories, but needed to mark a row with no difference
)


@dataclasses.dataclass
class ComparisonRow:
    symbol: str
    forward_classification: str
    historical_classification: str
    forward_action: str
    historical_action: str
    difference_reason: str
    notes: str

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


def _historical_progression(replay_result: Dict[str, Any], symbol: str) -> List[str]:
    seen: List[str] = []
    for c in replay_result["cycles"]:
        pm = c["premarket"]
        if pm.get("state") != "ok":
            continue
        for ev in pm.get("evaluated", []):
            if ev["symbol"] == symbol and ev.get("action") and (not seen or seen[-1] != ev["action"]):
                seen.append(ev["action"])
    return seen


def _historical_final_action(replay_result: Dict[str, Any], symbol: str) -> str:
    executed_any = False
    last_note = None
    for c in replay_result["cycles"]:
        pm = c["premarket"]
        if pm.get("state") != "ok":
            continue
        for ev in pm.get("evaluated", []):
            if ev["symbol"] != symbol:
                continue
            if ev.get("executed"):
                executed_any = True
            if ev.get("note"):
                last_note = ev["note"]
    if executed_any:
        return "entered"
    if last_note:
        return f"not_entered ({last_note})"
    return "never_a_candidate"


def compare_symbol(replay_result: Dict[str, Any], symbol: str) -> ComparisonRow:
    event = ref.event_by_symbol(symbol)
    forward_classification = event.classification_progression[-1] if event else "UNKNOWN"
    forward_action = event.final_action if event else "UNKNOWN"

    hist_progression = _historical_progression(replay_result, symbol)
    historical_classification = hist_progression[-1] if hist_progression else "never_a_candidate"
    historical_action = _historical_final_action(replay_result, symbol)

    reason, notes = _classify_difference(event, hist_progression, historical_classification)
    return ComparisonRow(symbol=symbol, forward_classification=forward_classification,
                         historical_classification=historical_classification, forward_action=forward_action,
                         historical_action=historical_action, difference_reason=reason, notes=notes)


def _classify_difference(event: Optional[ref.EventReference], hist_progression: List[str],
                         historical_classification: str) -> (str, str):
    if event is None:
        return "UNKNOWN", "symbol not in the frozen reference at all"

    forward_reached_tradeable = "TRADEABLE" in event.classification_progression
    hist_reached_tradeable = "TRADEABLE" in hist_progression

    if not hist_progression:
        # Never even generated a candidate row in the replay (didn't clear score_liquid_momentum /
        # score_sector_rs / score_mean_reversion's own technical entry criteria on this real price series).
        return ("PRICE_DATA_DIFFERENCE",
               "never became a scanner candidate in the replay under the real Yahoo-sourced price/volume "
               "series for this date -- either the real technical setup genuinely differed from whatever "
               "the forward session's live provider mix (60% tradingview/40% yahoo, per the acceptance doc) "
               "saw, or Yahoo's own bars differ enough from that mix to change which strategy scores fire; "
               "not independently verified further in this pass.")

    if forward_reached_tradeable and not hist_reached_tradeable:
        return ("CAPABILITY_DIFFERENCE",
               "reached MONITOR, never TRADEABLE: capped by the `data_quality` SOFT gate at "
               "overall coverage ~0.536, just under the 0.55 minimum -- a STRUCTURAL ceiling under "
               "PRICE_TREND_ONLY_V1 (fundamentals/news/analyst/filings/macro/options categories are "
               "permanently at 0% coverage), not specific to this symbol or date. See "
               "H5_FORWARD_REPRODUCTION.md for the full diagnosis. This means NO symbol can reach "
               "TRADEABLE in this replay today, so downstream capacity/sector/daily-cap mechanics "
               "(the actual point of the DELL/AAPL sector conflict and the TMO/MSFT daily-cap block) "
               "cannot be exercised end-to-end by this specific replay run.")

    if forward_reached_tradeable and hist_reached_tradeable:
        return ("MATCH", "both the forward session and the replay reached TRADEABLE for this symbol.")

    if not forward_reached_tradeable and not hist_reached_tradeable:
        return ("MATCH", "neither the forward session nor the replay reached TRADEABLE for this symbol.")

    return ("UNKNOWN", "replay reached TRADEABLE where the forward session did not -- not expected; "
                       "investigate before trusting this row.")


def build_comparison_matrix(replay_result: Dict[str, Any]) -> List[ComparisonRow]:
    return [compare_symbol(replay_result, sym) for sym in ref.all_symbols()]


def mechanical_equivalence_summary(replay_result: Dict[str, Any]) -> Dict[str, Any]:
    """Step 7's mechanical-equivalence layer: properties independently checked by the H5 test suite
    (test_h5_known_forward_replay.py, test_h5_freshness_and_session_clock.py, test_h4_execution.py's
    isolation tests), summarized here for the report rather than re-derived from `replay_result` alone
    (several, like the lookahead guard, are structural guarantees of HistoricalMarketProvider itself, not
    something a single replay run's output can newly prove or disprove)."""
    cycles_ok = sum(1 for c in replay_result["cycles"] if c["premarket"].get("state") == "ok")
    return {
        "cycles_run": len(replay_result["cycles"]), "cycles_completed_ok": cycles_ok,
        "no_exception_raised": True,
        "lookahead_guard": "structural (HistoricalMarketProvider._visible(); see test_h2_clock_and_lookahead.py)",
        "chronology": "single shared HistoricalClock drove both decision and execution providers in strict "
                      "cycle order; HistoricalClock.set() refuses to move backward",
        "session_rules": "exact 27-cycle schedule (schedule.py) matches the documented forward cadence "
                         "(premarket_prep/regular/post_cutoff, allow_entries per cycle)",
        "state_persistence": "verified by test_replay_capacity_state_persists_across_cycles "
                             "(unchanged-observation idempotency only fires when prior-cycle ledger state "
                             "is genuinely visible to a later cycle)",
        "determinism": "verified by test_replay_is_deterministic_same_run_twice",
        "production_isolation": "verified by test_no_production_ledger_touched_by_the_replay",
    }


def decision_equivalence_summary(rows: List[ComparisonRow]) -> Dict[str, Any]:
    by_reason: Dict[str, int] = {}
    for r in rows:
        by_reason[r.difference_reason] = by_reason.get(r.difference_reason, 0) + 1
    return {"rows": len(rows), "by_difference_reason": by_reason,
           "capability_difference_symbols": [r.symbol for r in rows if r.difference_reason == "CAPABILITY_DIFFERENCE"],
           "unexplained": [r.symbol for r in rows if r.difference_reason == "UNKNOWN"]}
