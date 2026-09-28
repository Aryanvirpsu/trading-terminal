"""H7 — one canonical event/observation/decision/trade identity system for Historical Lab.

Consolidates the concept the forward runtime's `lab/paper/shadow_log.py:_assign_event()` already
implements (a symbol keeps the SAME `event_id` across repeated observations unless a PRIOR signal from
that event has since closed, so 26 observations of the same setup collapse to one independent sample) into
one small, DB-free, directly testable module usable by both the live replay driver and post-hoc H6/H7
analysis -- rather than re-enabling shadow_log's own sqlite-backed clustering (disabled for historical
replay per H4's own disclosed limitation: it exists to collect live Challenger evidence, not to replay).

The relationship this module enforces:

    one event
        |
        v  (many)
    observations  --(deterministic, 1:1)-->  decisions
        |
        v  (zero or one)
    trade

* `event_id`       — the independent setup. Stable across repeated observations of the same (symbol,
                     direction) until that event's own trade (if any) closes.
* `observation_id` — `f"{cycle_id}:{symbol}"`, one per symbol per discovery cycle -- the same scheme
                     shadow_log already uses, so an event's observations line up with the real cycle
                     schedule (`known_forward/schedule.py`) directly.
* `decision_id`    — the evaluate() call's own identity. Deterministic 1:1 with `observation_id` (one
                     observation always yields exactly one decision) -- kept as a separate field, not
                     folded into `observation_id`, so a FUTURE multi-decision-per-observation mode (e.g.
                     re-evaluating a stale decision mid-cycle) has somewhere to diverge without a schema
                     change.
* `trade_id`       — the real order id, if and only if this event's decision was actually entered by the
                     broker. An event carries at most one open `trade_id` at a time; a caller must call
                     `close_event()` once that trade exits before the SAME (symbol, direction) can start a
                     genuinely new event.

This module counts independent evidence: never count observations, always count events.
"""
from __future__ import annotations

import dataclasses
from typing import Dict, Optional, Set, Tuple


@dataclasses.dataclass(frozen=True)
class ObservationIdentity:
    event_id: str
    observation_id: str
    decision_id: str
    is_new_event: bool             # True iff this observation started a NEW event_id (first of its kind)


def _event_key(symbol: str, direction: str) -> Tuple[str, str]:
    return symbol.upper(), direction.upper()


def _mint_event_id(symbol: str, direction: str, scan_ts: str) -> str:
    stamp = scan_ts[:16].replace(":", "").replace("-", "").replace("T", "")
    return f"evt_{symbol.upper()}_{direction.upper()}_{stamp}"


class EventIdentityTracker:
    """One instance per historical run (or per session). Not thread-safe; a historical replay is
    single-threaded by design (H2's clock is the only source of "now")."""

    def __init__(self) -> None:
        self._active_event: Dict[Tuple[str, str], str] = {}       # (symbol, direction) -> current event_id
        self._open_trade: Dict[str, str] = {}                     # event_id -> trade_id (order_id), while open
        self._closed_events: Set[str] = set()                     # event_ids whose trade has since closed

    def observe(self, *, symbol: str, direction: str, cycle_id: str, scan_ts: str) -> ObservationIdentity:
        """Call once per (symbol, cycle) a real observation/decision occurs for. Returns the identity to
        stamp onto that observation's own record (journal row, shadow row, comparison row, whichever the
        caller is building)."""
        key = _event_key(symbol, direction)
        current = self._active_event.get(key)
        is_new = current is None or current in self._closed_events
        if is_new:
            event_id = _mint_event_id(symbol, direction, scan_ts)
            self._active_event[key] = event_id
        else:
            event_id = current
        observation_id = f"{cycle_id}:{symbol.upper()}"
        decision_id = observation_id            # deterministic 1:1 today; kept distinct on purpose (see module docstring)
        return ObservationIdentity(event_id=event_id, observation_id=observation_id, decision_id=decision_id,
                                   is_new_event=is_new)

    def open_trade(self, event_id: str, trade_id: str) -> None:
        """Record that this event's decision was actually entered by the broker."""
        if event_id in self._open_trade and self._open_trade[event_id] != trade_id:
            raise ValueError(f"event {event_id!r} already has an open trade {self._open_trade[event_id]!r}; "
                             f"an event may carry at most one open trade at a time")
        self._open_trade[event_id] = trade_id

    def close_event(self, event_id: str) -> None:
        """Call once this event's trade has exited (or, for an event that was never traded, once its
        underlying setup is judged to have ended) -- the NEXT observation of the same (symbol, direction)
        will then start a genuinely new event_id rather than continuing this one."""
        self._closed_events.add(event_id)
        self._open_trade.pop(event_id, None)

    def trade_id_for(self, event_id: str) -> Optional[str]:
        return self._open_trade.get(event_id)

    def is_closed(self, event_id: str) -> bool:
        return event_id in self._closed_events

    def event_count(self) -> int:
        """Independent evidence count -- events, never observations."""
        return len(set(self._active_event.values()) | self._closed_events)
