"""Cache discipline (Prompt 5B): separate LAST-KNOWN / VALID-DECISION / DISPLAY-ONLY.

The SWR cache in `dashboard/research.py` already gives stale-while-revalidate,
request coalescing, circuit breakers, negative caching and provider health. What it
did NOT do is distinguish *why* a value is being served:

    valid-decision  — source is recent enough (within the category freshness limit)
                      to drive a NEW trade decision.
    display-only    — value is stale: fine to RENDER (last-known), but must NOT
                      influence a new actionable decision unless an operator opts in
                      (`DECISION_ALLOW_STALE=true`).
    last-known      — anything we hold, regardless of age (what the page shows).
    unknown         — we have no source timestamp to judge with.

Crucially the decision key is the SOURCE timestamp (when the provider produced the
data), NOT the cache-insertion time — a value re-inserted into the cache is not
magically "fresh". Every served value can be stamped with provenance and logged.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional

sys.path.insert(0, os.path.dirname(__file__))
import freshness as _fr
import providers as _P

TIERS = ("valid-decision", "display-only", "unknown")


def allow_stale_decisions() -> bool:
    return _P.allow_stale_decisions()


def classify(category: str, source_ts: Optional[float], now: Optional[float] = None) -> Dict[str, Any]:
    """Classify a value by its SOURCE age against the category's freshness limit."""
    now = now if now is not None else time.time()
    spec = _P.spec(category)
    limit = spec.freshness_limit_s if spec else None
    if source_ts is None or limit is None:
        return {"tier": "unknown", "decision_valid": allow_stale_decisions(),
                "age_seconds": None, "freshness": _fr.classify(None),
                "limit_s": limit, "reason": "no source timestamp"}
    age = max(0.0, now - float(source_ts))
    within = age <= limit
    valid = within or allow_stale_decisions()
    return {
        "tier": "valid-decision" if within else "display-only",
        "decision_valid": valid,
        "age_seconds": round(age), "limit_s": limit,
        "freshness": _fr.classify(age),
        "reason": ("within freshness limit" if within else
                   ("past freshness limit — display only%s" %
                    ("" if not valid else " (stale override on)"))),
    }


def decision_valid(category: str, source_ts: Optional[float], now: Optional[float] = None) -> bool:
    """Convenience: may this value drive a NEW trade decision?"""
    return classify(category, source_ts, now)["decision_valid"]


# ── Provenance log ────────────────────────────────────────────────────────────

_PROV: Deque[Dict[str, Any]] = deque(maxlen=400)
_LOCK = threading.Lock()


def log(category: str, provider: Optional[str], source_ts: Optional[float],
        cache_state: str = "live", symbol: Optional[str] = None) -> Dict[str, Any]:
    """Record where a served value came from + whether it's decision-valid."""
    rec = classify(category, source_ts)
    entry = {
        "at": datetime.now(timezone.utc).isoformat(), "category": category,
        "provider": provider, "symbol": symbol, "cache_state": cache_state,
        "tier": rec["tier"], "decision_valid": rec["decision_valid"],
        "age_seconds": rec["age_seconds"], "source_ts": source_ts,
    }
    with _LOCK:
        _PROV.append(entry)
    return entry


def recent(n: int = 40) -> List[Dict[str, Any]]:
    with _LOCK:
        return list(_PROV)[-n:]


def summary() -> Dict[str, Any]:
    """Counts by tier + by provider, for the health panel."""
    with _LOCK:
        items = list(_PROV)
    by_tier: Dict[str, int] = {t: 0 for t in TIERS}
    by_provider: Dict[str, int] = {}
    display_only = 0
    for e in items:
        by_tier[e["tier"]] = by_tier.get(e["tier"], 0) + 1
        if e.get("provider"):
            by_provider[e["provider"]] = by_provider.get(e["provider"], 0) + 1
        if e["tier"] == "display-only":
            display_only += 1
    return {"total": len(items), "by_tier": by_tier, "by_provider": by_provider,
            "display_only": display_only, "allow_stale_decisions": allow_stale_decisions()}


def stamp(value: Any, category: str, provider: Optional[str], source_ts: Optional[float],
          cache_state: str = "live", symbol: Optional[str] = None) -> Dict[str, Any]:
    """Wrap a served value with a provenance record AND log it. The decision layer
    reads `provenance.decision_valid`; the UI reads `provenance.tier`/freshness."""
    rec = classify(category, source_ts)
    log(category, provider, source_ts, cache_state, symbol)
    return {"value": value, "provenance": {
        "category": category, "provider": provider, "source_timestamp": source_ts,
        "cache_state": cache_state, "tier": rec["tier"],
        "decision_valid": rec["decision_valid"], "age_seconds": rec["age_seconds"],
        "freshness": rec["freshness"], "limit_s": rec["limit_s"]}}
