"""ONE authoritative data-freshness classifier — shared by the decision engine,
scanner, individual panels and the global header, so they can never disagree
(the "header says stale 18h while the engine says fresh" bug came from two
different notions of freshness: the scheduled-scan age vs the live-data age).

Freshness is derived from the ACTUAL SOURCE timestamp / age, not the cache-insert
time, and collapses to five states with a confidence penalty each:

    fresh            - live, within the fresh window
    ageing           - a bit old but still usable
    stale            - old; apply a penalty, warn
    critically_stale - too old to trust for a live decision
    fallback         - came from a degraded/fallback provider (regardless of age)

`unknown` is used when no timestamp is available.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

STATES = ("fresh", "ageing", "stale", "critically_stale", "fallback", "unknown")

# Age thresholds (seconds). Anything >= `stale` threshold is critically_stale.
# Tunable via env so intraday vs swing horizons can widen the windows.
def _thresholds() -> Dict[str, float]:
    def _f(name, default):
        try:
            return float(os.environ.get(name, default))
        except Exception:
            return float(default)
    return {"fresh": _f("FRESH_FRESH_S", 120),      # < 2 min  -> fresh
            "ageing": _f("FRESH_AGEING_S", 900),    # < 15 min -> ageing
            "stale": _f("FRESH_STALE_S", 3600)}     # < 60 min -> stale, else critically_stale

# Confidence penalty applied per state (subtracted from a 0..1 confidence).
PENALTY = {"fresh": 0.0, "ageing": 0.05, "stale": 0.15,
           "critically_stale": 0.30, "fallback": 0.20, "unknown": 0.10}


def classify(age_seconds: Optional[float], is_fallback: bool = False,
             thresholds: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """Return a freshness record {state, age_seconds, penalty, label, is_fallback,
    blocks_tradeable}. `is_fallback` forces the `fallback` state regardless of age."""
    th = thresholds or _thresholds()
    if is_fallback:
        state = "fallback"
    elif age_seconds is None:
        state = "unknown"
    else:
        age = max(0.0, float(age_seconds))
        if age < th["fresh"]:
            state = "fresh"
        elif age < th["ageing"]:
            state = "ageing"
        elif age < th["stale"]:
            state = "stale"
        else:
            state = "critically_stale"
    return {"state": state, "age_seconds": (round(age_seconds) if age_seconds is not None else None),
            "penalty": PENALTY.get(state, 0.1), "label": label(state, age_seconds),
            "is_fallback": bool(is_fallback), "blocks_tradeable": blocks_tradeable(state)}


def penalty(state: str) -> float:
    return PENALTY.get(state, 0.1)


def blocks_tradeable(state: str) -> bool:
    """States that must prevent a TRADEABLE verdict (data too old / degraded)."""
    return state in ("stale", "critically_stale", "fallback")


def _human_age(age_seconds: Optional[float]) -> str:
    if age_seconds is None:
        return "unknown age"
    a = max(0.0, float(age_seconds))
    if a < 90:
        return f"{int(a)}s"
    if a < 5400:
        return f"{int(a / 60)}m"
    if a < 172800:
        return f"{int(a / 3600)}h"
    return f"{int(a / 86400)}d"


def label(state: str, age_seconds: Optional[float] = None) -> str:
    names = {"fresh": "fresh", "ageing": "ageing", "stale": "stale",
             "critically_stale": "critically stale", "fallback": "fallback provider",
             "unknown": "unknown"}
    base = names.get(state, state)
    if state in ("ageing", "stale", "critically_stale") and age_seconds is not None:
        return f"{base} ({_human_age(age_seconds)})"
    return base


def worst(*states: str) -> str:
    """Combine several freshness states into the WORST one (for an overall badge)."""
    order = {"fresh": 0, "ageing": 1, "unknown": 2, "stale": 3, "fallback": 4, "critically_stale": 5}
    present = [s for s in states if s]
    return max(present, key=lambda s: order.get(s, 2)) if present else "unknown"
