"""HIST-004's ranking_challenger.py: the four pre-registered ranking rules (Amendment 2), each a pure
function over decision-time-only candidate features."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.historical.hist004.ranking_challenger import (
    rank_by_composite, rank_by_planned_rr, rank_by_quality, rank_random,
)


def _c(event_id, quality, price, stop, target):
    return {"event_id": event_id, "quality": quality, "price": price, "stop": stop, "target": target}


def test_rank_by_quality_picks_the_highest_quality():
    cands = [_c("a", 60.0, 100, 95, 110), _c("b", 80.0, 100, 95, 110), _c("c", 70.0, 100, 95, 110)]
    assert rank_by_quality(cands) == "b"


def test_rank_by_quality_breaks_ties_by_event_id():
    cands = [_c("b", 70.0, 100, 95, 110), _c("a", 70.0, 100, 95, 110)]
    assert rank_by_quality(cands) == "b"          # max() picks the lexicographically-larger id on a tie


def test_rank_by_planned_rr_picks_the_best_reward_to_risk_ratio():
    # a: risk=5, reward=10 -> RR=2.0 ;  b: risk=5, reward=20 -> RR=4.0
    cands = [_c("a", 50.0, 100, 95, 110), _c("b", 50.0, 100, 95, 120)]
    assert rank_by_planned_rr(cands) == "b"


def test_rank_by_planned_rr_ignores_quality_unless_tied():
    cands = [_c("a", 90.0, 100, 95, 110), _c("b", 10.0, 100, 95, 120)]   # b has worse quality but better RR
    assert rank_by_planned_rr(cands) == "b"


def test_rank_by_composite_combines_quality_and_rr():
    # a: quality=100, RR=1.0 -> score=100 ;  b: quality=50, RR=3.0 -> score=150
    cands = [_c("a", 100.0, 100, 95, 100), _c("b", 50.0, 100, 95, 115)]
    assert rank_by_composite(cands) == "b"


def test_rank_by_planned_rr_handles_missing_levels_gracefully():
    cands = [_c("a", 50.0, None, None, None), _c("b", 40.0, 100, 95, 110)]
    assert rank_by_planned_rr(cands) == "b"       # a's RR is 0.0 (no data), b wins on any real RR


def test_rank_random_always_returns_one_of_the_candidates():
    import random
    cands = [_c("a", 1, 1, 1, 1), _c("b", 2, 2, 2, 2), _c("c", 3, 3, 3, 3)]
    rng = random.Random(0)
    picks = {rank_random(cands, rng) for _ in range(50)}
    assert picks <= {"a", "b", "c"}
    assert len(picks) > 1          # with 50 draws from 3 options, seed 0 should hit more than one
