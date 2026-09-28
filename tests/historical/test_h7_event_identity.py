"""H7 acceptance: one canonical event/observation/decision/trade identity system -- repeated observations
of the same setup collapse to one event, a new event only starts after the prior one closes, trades
associate to exactly one event at a time, and independent-evidence counting counts events, never
observations (the exact sample-size-inflation bug the forward runtime's own shadow_log exists to prevent).
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.historical.event_identity import EventIdentityTracker


def test_repeated_observations_of_the_same_setup_share_one_event_id():
    t = EventIdentityTracker()
    ids = [t.observe(symbol="AMD", direction="LONG", cycle_id=f"c{i}", scan_ts=f"2026-09-25T09:{35+i*5:02d}:00")
          for i in range(5)]
    event_ids = {o.event_id for o in ids}
    assert len(event_ids) == 1
    assert ids[0].is_new_event is True
    assert all(not o.is_new_event for o in ids[1:])
    assert t.event_count() == 1


def test_observation_and_decision_ids_are_per_cycle_and_symbol():
    t = EventIdentityTracker()
    o = t.observe(symbol="dell", direction="LONG", cycle_id="2026-09-25T1205", scan_ts="2026-09-25T12:05:00")
    assert o.observation_id == "2026-09-25T1205:DELL"          # symbol upper-cased
    assert o.decision_id == o.observation_id                    # deterministic 1:1 today


def test_different_symbols_or_directions_never_share_an_event():
    t = EventIdentityTracker()
    dell = t.observe(symbol="DELL", direction="LONG", cycle_id="c1", scan_ts="2026-09-25T09:35:00")
    meta = t.observe(symbol="META", direction="LONG", cycle_id="c1", scan_ts="2026-09-25T09:35:00")
    dell_short = t.observe(symbol="DELL", direction="SHORT", cycle_id="c1", scan_ts="2026-09-25T09:35:00")
    assert len({dell.event_id, meta.event_id, dell_short.event_id}) == 3
    assert t.event_count() == 3


def test_a_new_event_starts_only_after_the_prior_one_closes():
    t = EventIdentityTracker()
    first = t.observe(symbol="DELL", direction="LONG", cycle_id="c1", scan_ts="2026-09-25T09:35:00")
    still_same = t.observe(symbol="DELL", direction="LONG", cycle_id="c2", scan_ts="2026-09-25T09:50:00")
    assert still_same.event_id == first.event_id and not still_same.is_new_event

    t.close_event(first.event_id)
    new_event = t.observe(symbol="DELL", direction="LONG", cycle_id="c3", scan_ts="2026-09-25T14:00:00")
    assert new_event.event_id != first.event_id
    assert new_event.is_new_event is True
    assert t.event_count() == 2                       # the closed one still counts as independent evidence


def test_trade_association_and_one_open_trade_per_event():
    t = EventIdentityTracker()
    o = t.observe(symbol="DELL", direction="LONG", cycle_id="c1", scan_ts="2026-09-25T12:05:00")
    assert t.trade_id_for(o.event_id) is None
    t.open_trade(o.event_id, "ord_abc123")
    assert t.trade_id_for(o.event_id) == "ord_abc123"
    t.open_trade(o.event_id, "ord_abc123")             # idempotent re-assertion of the SAME trade is fine

    with pytest.raises(ValueError):
        t.open_trade(o.event_id, "ord_different")      # a second, DIFFERENT trade on the same open event


def test_closing_an_event_clears_its_open_trade():
    t = EventIdentityTracker()
    o = t.observe(symbol="DELL", direction="LONG", cycle_id="c1", scan_ts="2026-09-25T12:05:00")
    t.open_trade(o.event_id, "ord_abc123")
    t.close_event(o.event_id)
    assert t.trade_id_for(o.event_id) is None
    assert t.is_closed(o.event_id)


def test_reproduces_the_forward_sessions_own_observation_to_event_collapse():
    """The acceptance doc's own headline number: 133 raw observations -> 12 independent events, e.g. AMD
    26 observations -> 1 event, DELL 26 -> 1, CRM 25 -> 1. No trade closes mid-session for any of these
    (both real positions were carried overnight per the doc), so every symbol should collapse to exactly
    one event across the whole day."""
    from research.historical.known_forward.reference import REFERENCE_EVENTS

    t = EventIdentityTracker()
    for event in REFERENCE_EVENTS:
        for i in range(event.observations):
            t.observe(symbol=event.symbol, direction="LONG", cycle_id=f"c{i}",
                     scan_ts=f"2026-09-25T{9 + i // 4:02d}:{(i % 4) * 15:02d}:00")
    assert t.event_count() == len(REFERENCE_EVENTS) == 12
