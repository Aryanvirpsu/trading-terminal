"""Genuine choice-episode characterization -- the corrected definition established during Medium/Full
(`HIST_001_PREREGISTRATION.md` A2.9): "a genuine choice event requires two or more truly newly-eligible
(not already-held) candidates competing for a genuinely scarce, available slot at that instant." The naive
raw-episode count (`choice_events()` in `hist001/analysis.py`, one row per CYCLE with >=2 TRADEABLE
candidates) massively inflates the apparent sample: a candidate that stays TRADEABLE-but-blocked for 10
consecutive 15-minute discovery cycles is ONE real choice, not 10 -- this module collapses that persistence
into a single episode.

SKIP phrases below are the exact, real note strings `lab/paper/workflow.py::premarket()` attaches to an
evaluated record BEFORE it is considered "genuinely live" this cycle -- a candidate already held, already
entered this session, or an unchanged repeat of a prior observation is excluded from the live-candidate set
(it is not a NEW competitor, and including it would double-count the same underlying decision)."""
from __future__ import annotations

import dataclasses
from typing import Any, Dict, List, Optional

_SKIP_NOTE_PREFIXES = (
    "existing position/open order",
    "already entered this session",
    "unchanged observation",
    "entries disabled",
)


def _is_genuinely_live(ev: Dict[str, Any]) -> bool:
    if ev.get("action") != "TRADEABLE":
        return False
    note = ev.get("note") or ""
    return not any(note.startswith(p) for p in _SKIP_NOTE_PREFIXES)


@dataclasses.dataclass
class ChoiceEpisode:
    episode_id: str
    session_date: str
    first_cycle_id: str
    last_cycle_id: str
    cycles_spanned: int
    event_ids: List[str]
    symbols: List[str]
    candidate_detail_by_event: Dict[str, Dict[str, Any]]    # event_id -> decision-time detail at FIRST live cycle
    selected_event_ids: List[str]       # event_id(s) that were ever actually executed during the episode's span
    selected_symbols: List[str]
    scarce: bool                        # True iff fewer candidates were entered than were simultaneously live

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


def genuine_choice_episodes(result: Dict[str, Any]) -> List[ChoiceEpisode]:
    """Walks every evaluation-phase cycle in chronological order. A cycle contributes a "live candidate set"
    -- the event_ids of every genuinely-live TRADEABLE evaluation this cycle (see `_is_genuinely_live`).
    Consecutive cycles (same session_date) whose live sets are IDENTICAL collapse into one episode; a set
    change (a candidate resolves, a new one joins, one drops below TRADEABLE) starts a new episode. Only
    sets with >=2 members are reported -- a single live candidate is not a choice.

    `scarce` is true when, across the episode's full span, FEWER distinct event_ids were ever actually
    executed than were simultaneously live -- i.e. Champion could not (or did not) take all of them, which
    is itself the evidence that capacity was the binding constraint (Champion never deliberately holds back
    capacity it has free; see canonical_bridge.py's own executable-gate design) -- never inferred from
    parsing a specific capacity-block note string, which is read-only funnel information computed
    separately and not reused here."""
    eval_cycles = [c for c in result["cycles"] if c["phase"] == "evaluation" and c["premarket"].get("state") == "ok"]

    episodes: List[ChoiceEpisode] = []
    current_set: Optional[frozenset] = None
    current_session_date: Optional[str] = None
    current_cycles: List[Dict[str, Any]] = []

    def _flush():
        if current_set is None or len(current_set) < 2:
            return
        event_ids = sorted(current_set)
        detail_by_event: Dict[str, Dict[str, Any]] = {}
        symbols: List[str] = []
        executed_events: set = set()
        for c in current_cycles:
            for ev in c["premarket"].get("evaluated", []):
                eid = ev.get("event_id")
                if eid not in current_set:
                    continue
                if eid not in detail_by_event:
                    detail_by_event[eid] = {"symbol": ev.get("symbol"), "quality": None, "note": ev.get("note"),
                                            "decision_id": ev.get("decision_id")}
                    symbols.append(ev.get("symbol"))
                if ev.get("executed"):
                    executed_events.add(eid)
        episodes.append(ChoiceEpisode(
            episode_id=f"ep_{current_cycles[0]['cycle_id']}", session_date=current_session_date,
            first_cycle_id=current_cycles[0]["cycle_id"], last_cycle_id=current_cycles[-1]["cycle_id"],
            cycles_spanned=len(current_cycles), event_ids=event_ids, symbols=symbols,
            candidate_detail_by_event=detail_by_event, selected_event_ids=sorted(executed_events),
            selected_symbols=[detail_by_event[e]["symbol"] for e in sorted(executed_events)],
            scarce=len(executed_events) < len(current_set)))

    for c in eval_cycles:
        live = {ev["event_id"] for ev in c["premarket"].get("evaluated", [])
               if _is_genuinely_live(ev) and ev.get("event_id")}
        live_set = frozenset(live) if len(live) >= 2 else None
        session_date = c["session_date"]

        if live_set == current_set and session_date == current_session_date and current_set is not None:
            current_cycles.append(c)
            continue

        _flush()
        current_set = live_set
        current_session_date = session_date
        current_cycles = [c] if live_set is not None else []

    _flush()
    return episodes
