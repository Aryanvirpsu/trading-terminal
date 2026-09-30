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


def test_reproduces_the_hist001_non_standard_warmup_crash():
    """Real crash found running `hist001.baseline.run_baseline()` with a non-standard warmup window
    (warmup_start=evaluation_start='2024-06-01', evaluation_end='2024-07-20'): NEE was entered, its position
    closed for real, and NEE was then legitimately re-entered later in the same run -- but baseline.py never
    called `close_event()`, so the tracker still considered the FIRST event open and `open_trade()` raised
    on the second, different trade_id. Reproduced here directly against the tracker, independent of any
    warmup date or real dataset -- this crashes for ANY (symbol, direction) pair that closes and legitimately
    re-enters within one run, with or without `reconcile_closed_trades()` called in between."""
    t = EventIdentityTracker()
    first = t.observe(symbol="NEE", direction="LONG", cycle_id="c1", scan_ts="2024-06-03T09:15:00")
    t.open_trade(first.event_id, "sig_e395c3b8bdee7a98")

    # NEE's position later closes for real, and NEE legitimately becomes tradeable again -- the tracker
    # was never told the first event closed, so it hands back the SAME stale event_id.
    still_same = t.observe(symbol="NEE", direction="LONG", cycle_id="c9", scan_ts="2024-06-10T09:15:00")
    assert still_same.event_id == first.event_id and not still_same.is_new_event

    with pytest.raises(ValueError, match=r"already has an open trade"):
        t.open_trade(still_same.event_id, "sig_different_second_trade")


def test_reconcile_closed_trades_lets_a_legitimate_reentry_start_a_new_event():
    """The fix: once the ledger reports a trade's position closed, `reconcile_closed_trades()` closes its
    event, so the NEXT observation of that (symbol, direction) mints a genuinely new, independent event_id
    and the second `open_trade()` no longer collides with the first."""
    t = EventIdentityTracker()
    first = t.observe(symbol="NEE", direction="LONG", cycle_id="c1", scan_ts="2024-06-03T09:15:00")
    t.open_trade(first.event_id, "sig_e395c3b8bdee7a98")
    assert t.open_trades() == {first.event_id: "sig_e395c3b8bdee7a98"}

    # not yet closed -- a no-op, exactly like shadow_log's own per-cycle check when nothing has exited
    t.reconcile_closed_trades(set())
    assert not t.is_closed(first.event_id)

    # the ledger now reports NEE's position closed
    t.reconcile_closed_trades({"sig_e395c3b8bdee7a98"})
    assert t.is_closed(first.event_id)
    assert t.open_trades() == {}

    second = t.observe(symbol="NEE", direction="LONG", cycle_id="c9", scan_ts="2024-06-10T09:15:00")
    assert second.is_new_event is True
    assert second.event_id != first.event_id
    t.open_trade(second.event_id, "sig_different_second_trade")     # no crash: a genuinely new event now
    assert t.event_count() == 2                                     # two independent pieces of evidence


def test_reconcile_closed_trades_is_a_noop_when_nothing_has_closed():
    """Standard-run behavior (a symbol entered once and never exits within the run): reconciliation must
    never touch an event whose trade is still open, regardless of how many times it's called."""
    t = EventIdentityTracker()
    o = t.observe(symbol="DELL", direction="LONG", cycle_id="c1", scan_ts="2026-09-25T09:35:00")
    t.open_trade(o.event_id, "ord_abc123")
    for _ in range(5):
        t.reconcile_closed_trades({"some_unrelated_id"})
    assert not t.is_closed(o.event_id)
    assert t.trade_id_for(o.event_id) == "ord_abc123"
    t.open_trade(o.event_id, "ord_abc123")             # still idempotent, still fine


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
