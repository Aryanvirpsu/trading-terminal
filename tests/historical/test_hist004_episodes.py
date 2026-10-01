"""HIST-004's episodes.py: genuine_choice_episodes(). Proves the deduplication actually works (the same
pair of candidates persisting across many consecutive cycles collapses into ONE episode, not N), that
already-held/already-entered/unchanged-observation candidates never count as live competitors, that
resolving a choice (one candidate entered) correctly ends an episode, and that `scarce` reflects real
under-capacity rather than a parsed note string.

Uses minimal synthetic `result["cycles"]` structures -- these functions are pure, read-only computations
over run_baseline()'s own output shape, same pattern as every other analysis.py-style test in this suite."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.historical.hist004.episodes import genuine_choice_episodes


def _cycle(cycle_id, session_date, evaluated):
    return {"cycle_id": cycle_id, "session_date": session_date, "phase": "evaluation",
           "premarket": {"state": "ok", "evaluated": evaluated}}


def _ev(symbol, event_id, action="TRADEABLE", executed=False, note=None):
    return {"symbol": symbol, "event_id": event_id, "action": action, "executed": executed, "note": note}


def test_two_live_candidates_one_cycle_is_one_episode():
    cycles = [_cycle("c1", "2024-01-02", [_ev("AAA", "evt_a"), _ev("BBB", "evt_b")])]
    eps = genuine_choice_episodes({"cycles": cycles})
    assert len(eps) == 1
    assert set(eps[0].event_ids) == {"evt_a", "evt_b"}
    assert eps[0].cycles_spanned == 1


def test_persisting_same_pair_across_many_cycles_collapses_to_one_episode():
    """The core de-duplication guarantee: the SAME two candidates staying TRADEABLE-but-blocked across 10
    consecutive discovery cycles is ONE genuine choice, not 10 -- this is the exact inflation the naive
    per-cycle choice_events() count suffers from."""
    cycles = [_cycle(f"c{i}", "2024-01-02", [_ev("AAA", "evt_a"), _ev("BBB", "evt_b")]) for i in range(10)]
    eps = genuine_choice_episodes({"cycles": cycles})
    assert len(eps) == 1
    assert eps[0].cycles_spanned == 10
    assert eps[0].first_cycle_id == "c0"
    assert eps[0].last_cycle_id == "c9"


def test_already_held_candidate_is_not_a_live_competitor():
    cycles = [_cycle("c1", "2024-01-02", [
        _ev("AAA", "evt_a"),
        _ev("BBB", "evt_b", note="existing position/open order - no duplicate entry"),
        _ev("CCC", "evt_c"),
    ])]
    eps = genuine_choice_episodes({"cycles": cycles})
    assert len(eps) == 1
    assert set(eps[0].event_ids) == {"evt_a", "evt_c"}      # BBB excluded


def test_already_entered_and_unchanged_observation_notes_are_excluded_too():
    cycles = [_cycle("c1", "2024-01-02", [
        _ev("AAA", "evt_a"),
        _ev("BBB", "evt_b", note="already entered this session - no duplicate entry"),
        _ev("CCC", "evt_c", note="unchanged observation - not re-journaled"),
        _ev("DDD", "evt_d"),
    ])]
    eps = genuine_choice_episodes({"cycles": cycles})
    assert len(eps) == 1
    assert set(eps[0].event_ids) == {"evt_a", "evt_d"}


def test_entries_disabled_bookend_cycles_never_count_as_live():
    cycles = [_cycle("c1", "2024-01-02", [
        _ev("AAA", "evt_a", note="entries disabled (premarket_prep) - observation only"),
        _ev("BBB", "evt_b", note="entries disabled (premarket_prep) - observation only"),
    ])]
    eps = genuine_choice_episodes({"cycles": cycles})
    assert eps == []


def test_a_single_live_candidate_is_not_a_choice():
    cycles = [_cycle("c1", "2024-01-02", [_ev("AAA", "evt_a")])]
    assert genuine_choice_episodes({"cycles": cycles}) == []


def test_resolving_the_choice_ends_the_episode_and_a_new_set_starts_a_new_one():
    cycles = [
        _cycle("c1", "2024-01-02", [_ev("AAA", "evt_a"), _ev("BBB", "evt_b")]),
        _cycle("c2", "2024-01-02", [_ev("AAA", "evt_a"), _ev("BBB", "evt_b")]),
        # AAA gets entered on c3 -- next cycle it becomes "existing position", dropping the live set to just BBB
        _cycle("c3", "2024-01-02", [_ev("AAA", "evt_a", executed=True), _ev("BBB", "evt_b")]),
        _cycle("c4", "2024-01-02", [_ev("AAA", "evt_a", note="existing position/open order - no duplicate entry"),
                                   _ev("BBB", "evt_b"), _ev("CCC", "evt_c")]),
        _cycle("c5", "2024-01-02", [_ev("AAA", "evt_a", note="existing position/open order - no duplicate entry"),
                                   _ev("BBB", "evt_b"), _ev("CCC", "evt_c")]),
    ]
    eps = genuine_choice_episodes({"cycles": cycles})
    assert len(eps) == 2
    assert set(eps[0].event_ids) == {"evt_a", "evt_b"}
    assert eps[0].first_cycle_id == "c1" and eps[0].last_cycle_id == "c3"
    assert eps[0].selected_event_ids == ["evt_a"]
    assert eps[0].scarce is True                        # 1 executed out of 2 live -- capacity was scarce

    assert set(eps[1].event_ids) == {"evt_b", "evt_c"}
    assert eps[1].first_cycle_id == "c4" and eps[1].last_cycle_id == "c5"
    assert eps[1].selected_event_ids == []               # neither ever entered in this synthetic span
    assert eps[1].scarce is True                         # 0 executed out of 2 live


def test_scarce_is_false_when_champion_took_every_live_candidate():
    """If Champion actually entered ALL simultaneously-live candidates (capacity wasn't actually binding),
    this is not evidence of a genuine forced choice -- scarce must be False."""
    cycles = [_cycle("c1", "2024-01-02", [_ev("AAA", "evt_a", executed=True), _ev("BBB", "evt_b", executed=True)])]
    eps = genuine_choice_episodes({"cycles": cycles})
    assert len(eps) == 1
    assert eps[0].scarce is False


def test_different_session_dates_never_merge_into_one_episode():
    cycles = [
        _cycle("c1", "2024-01-02", [_ev("AAA", "evt_a"), _ev("BBB", "evt_b")]),
        _cycle("c2", "2024-01-03", [_ev("AAA", "evt_a"), _ev("BBB", "evt_b")]),   # same set, NEXT day
    ]
    eps = genuine_choice_episodes({"cycles": cycles})
    assert len(eps) == 2
    assert eps[0].session_date == "2024-01-02"
    assert eps[1].session_date == "2024-01-03"


def test_non_tradeable_actions_are_never_live_competitors():
    cycles = [_cycle("c1", "2024-01-02", [
        _ev("AAA", "evt_a"), _ev("BBB", "evt_b", action="MONITOR"), _ev("CCC", "evt_c", action="REJECT"),
    ])]
    eps = genuine_choice_episodes({"cycles": cycles})
    assert eps == []     # only one genuinely-live TRADEABLE candidate (AAA) -- not a choice
