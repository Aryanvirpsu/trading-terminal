"""HIST-004 ranking rules -- see Amendment 2 of HIST_004_PREREGISTRATION.md for why these specific arms
(corrected from the original arm list to what `decision_capture` actually stores, before any result was
computed). Each rule is a pure function: `candidates` is a list of dicts, each at minimum
`{"event_id", "symbol", "quality", "price", "stop", "target"}` (decision-time features only -- no outcome,
no MFE/MAE, no hindsight field), and each rule returns the chosen candidate's `event_id`. Ties break by
`event_id` (deterministic, not insertion order)."""
from __future__ import annotations

import random
from typing import Any, Dict, List, Optional


def _planned_rr(c: Dict[str, Any]) -> float:
    price, stop, target = c.get("price"), c.get("stop"), c.get("target")
    if price is None or stop is None or target is None or price == stop:
        return 0.0
    risk = abs(price - stop)
    reward = abs(target - price)
    return reward / risk if risk else 0.0


def rank_by_quality(candidates: List[Dict[str, Any]]) -> str:
    best = max(candidates, key=lambda c: (c.get("quality") or 0.0, c["event_id"]))
    return best["event_id"]


def rank_by_planned_rr(candidates: List[Dict[str, Any]]) -> str:
    best = max(candidates, key=lambda c: (_planned_rr(c), c.get("quality") or 0.0, c["event_id"]))
    return best["event_id"]


def rank_by_composite(candidates: List[Dict[str, Any]]) -> str:
    def score(c):
        return (c.get("quality") or 0.0) * _planned_rr(c)
    best = max(candidates, key=lambda c: (score(c), c["event_id"]))
    return best["event_id"]


def rank_random(candidates: List[Dict[str, Any]], rng: Optional[random.Random] = None) -> str:
    rng = rng or random.Random()
    return rng.choice(candidates)["event_id"]
