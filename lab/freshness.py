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


# Per-DATA-TYPE freshness requirements. A single global age is wrong: a quote that is
# 10 minutes old during the session is dangerous, while an earnings date that is 10
# minutes old is simply current. Each entry feeds the SAME shared classifier, so the
# vocabulary (fresh/ageing/stale/…) stays identical everywhere — only the windows move.
# All overridable per type via FRESH_<TYPE>_{FRESH,AGEING,STALE}_S.
_DATA_TYPE_DEFAULTS: Dict[str, Dict[str, float]] = {
    # market-sensitive: tight
    "quote":        {"fresh": 60,        "ageing": 300,       "stale": 900},
    "option_chain": {"fresh": 120,       "ageing": 600,       "stale": 1800},
    "account":      {"fresh": 300,       "ageing": 1800,      "stale": 7200},
    "levels":       {"fresh": 300,       "ageing": 1800,      "stale": 7200},
    # slow-moving: hours to days is normal and NOT a fault
    "scanner":      {"fresh": 8 * 3600,  "ageing": 24 * 3600, "stale": 72 * 3600},
    "news":         {"fresh": 3600,      "ageing": 6 * 3600,  "stale": 24 * 3600},
    "fundamental":  {"fresh": 24 * 3600, "ageing": 7 * 86400, "stale": 30 * 86400},
}

DATA_TYPES = tuple(_DATA_TYPE_DEFAULTS)


def thresholds_for(kind: str) -> Dict[str, float]:
    """Freshness windows for one data type. Unknown types fall back to the tight
    default set — erring toward calling something stale, never toward pretending."""
    base = _DATA_TYPE_DEFAULTS.get(kind)
    if base is None:
        return _thresholds()
    up = kind.upper()

    def _f(edge: str, default: float) -> float:
        try:
            return float(os.environ.get(f"FRESH_{up}_{edge.upper()}_S", default))
        except Exception:
            return float(default)
    return {e: _f(e, v) for e, v in base.items()}


# How a freshness record should be PRESENTED, which is not the same question as how
# old it is. Market-closed data at its last close is correct and complete; calling it
# "stale (28h)" on a Sunday describes a working system as a broken one.
PRESENTATION = ("LIVE", "DELAYED", "MARKET CLOSED · LAST CLOSE", "STALE · REFRESH FAILED",
                "UNKNOWN")


def presentation(state: str, *, market_state: Optional[str] = None,
                 is_fallback: bool = False) -> str:
    """Map (freshness state, market state) onto what the UI should actually say.

    `market_state` is the authoritative session state from market_regime.session_state()
    — 'open', 'premarket', 'afterhours', 'closed', 'holiday'. It is passed IN rather
    than derived from a quote timestamp: inferring the session from data age is what
    let a stale feed masquerade as a closed market and vice versa.
    """
    if state == "unknown":
        return "UNKNOWN"
    live_session = market_state in ("open", "premarket", "afterhours")
    if not live_session and market_state is not None:
        # Exchange is shut. Old data is EXPECTED, not a failure — unless the source
        # itself failed, which `is_fallback` reports independently of age.
        return "STALE · REFRESH FAILED" if is_fallback else "MARKET CLOSED · LAST CLOSE"
    if is_fallback:
        return "STALE · REFRESH FAILED"
    if state == "fresh":
        return "LIVE"
    if state == "ageing":
        return "DELAYED"
    return "STALE · REFRESH FAILED"

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


def classify_typed(kind: str, age_seconds: Optional[float], *,
                   is_fallback: bool = False,
                   market_state: Optional[str] = None) -> Dict[str, Any]:
    """Classify against ONE data type's windows and say how it should be presented.

    Adds `kind`, `presentation` and `market_state` to the standard record, and — when
    the exchange is shut — clears `blocks_tradeable` for age alone. Nothing can be
    traded on a closed exchange anyway, and flagging Friday's close as a data fault all
    weekend trained the eye to ignore the one badge that matters intraday. A genuine
    provider failure (`is_fallback`) still blocks, closed or not.
    """
    rec = classify(age_seconds, is_fallback=is_fallback,
                   thresholds=thresholds_for(kind))
    rec["kind"] = kind
    rec["market_state"] = market_state
    rec["presentation"] = presentation(rec["state"], market_state=market_state,
                                       is_fallback=is_fallback)
    if market_state is not None and market_state not in ("open", "premarket", "afterhours"):
        rec["blocks_tradeable"] = bool(is_fallback)
        rec["label"] = ("provider refresh failed" if is_fallback
                        else f"market closed — last close ({_human_age(age_seconds)} ago)"
                        if age_seconds is not None else "market closed")
    return rec


def bar_age_seconds(as_of: Optional[str], session_close_hour: int = 16) -> Optional[float]:
    """Age of a DAILY bar, measured from the session close it represents.

    Yahoo stamps a daily bar with midnight of the bar's date, so naively ageing it
    against `now` overstates staleness by up to 16 hours: yesterday's close, the
    freshest datum that can possibly exist pre-market, read as "30h old". Anything
    intraday (a timestamp with a non-zero clock component) is aged as-is.
    """
    if not as_of:
        return None
    try:
        from datetime import datetime, timedelta, timezone as _tz
        dt = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_tz.utc)
        if (dt.hour, dt.minute, dt.second) == (0, 0, 0):
            dt = dt + timedelta(hours=session_close_hour)   # bar date -> its close
        age = (datetime.now(_tz.utc) - dt.astimezone(_tz.utc)).total_seconds()
        return max(0.0, age)
    except Exception:
        return None


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
