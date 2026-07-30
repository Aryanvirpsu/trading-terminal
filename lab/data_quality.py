"""Coverage-based data quality (Prompt 5B).

The OLD data-quality number was the mean confidence of the evidence families, so a
throttled TradingView (one provider) dragged the WHOLE stock's quality down even when
Yahoo/Finnhub had perfectly valid price, fundamentals, news and analyst data. That is
backwards.

Here data quality is CATEGORY COVERAGE, weighted and STRATEGY-AWARE:

  * Each category (price/candles, fundamentals, news, options, filings, analyst,
    sector/macro, social) contributes coverage in [0,1] with a confidence weight.
  * A strategy PROFILE declares which categories are REQUIRED vs OPTIONAL vs IGNORED.
      - momentum : needs price/candles; options & filings optional; social ~ignored.
      - analysis : needs price + fundamentals; options optional.
      - options  : REQUIRES a live, liquid option chain (bid/ask/spread/OI/vol/DTE/Greeks).
  * The score is the weighted coverage over the categories the strategy CARES about.
    A missing OPTIONAL category lowers the score only a little; a missing REQUIRED
    category caps the score hard (and is reported as a blocking gap).
  * We report per-category quality AND the exact missing fields — never a bare number.

A category built only from a STALE source (past its freshness limit) counts as
degraded coverage, not full coverage, so stale display data cannot inflate quality
for a NEW decision.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(__file__))
import providers as _P


# ── Strategy profiles: required / optional / ignored categories ───────────────

# weight multiplier applied to a category's base confidence_weight, by role.
_REQUIRED, _OPTIONAL, _IGNORED = "required", "optional", "ignored"

PROFILES: Dict[str, Dict[str, str]] = {
    # Stock momentum scan: price action is everything; options/filings are nice to
    # have; social barely matters. Missing options must NOT fail a momentum stock.
    "momentum": {
        "price": _REQUIRED, "candles": _REQUIRED, "fundamentals": _OPTIONAL,
        "news": _OPTIONAL, "analyst": _OPTIONAL, "filings": _OPTIONAL,
        "sector": _OPTIONAL, "macro": _OPTIONAL, "options": _OPTIONAL,
        "social": _IGNORED,
    },
    # Share analysis: price + fundamentals are required; an option chain is optional.
    "analysis": {
        "price": _REQUIRED, "candles": _REQUIRED, "fundamentals": _REQUIRED,
        "news": _OPTIONAL, "analyst": _OPTIONAL, "filings": _OPTIONAL,
        "sector": _OPTIONAL, "macro": _OPTIONAL, "options": _OPTIONAL,
        "social": _IGNORED,
    },
    # Options trade: a live, liquid option chain is REQUIRED on top of the stock.
    "options": {
        "price": _REQUIRED, "candles": _REQUIRED, "options": _REQUIRED,
        "fundamentals": _OPTIONAL, "news": _OPTIONAL, "analyst": _OPTIONAL,
        "filings": _IGNORED, "sector": _OPTIONAL, "macro": _IGNORED,
        "social": _IGNORED,
    },
}

_ROLE_MULT = {_REQUIRED: 1.0, _OPTIONAL: 0.5, _IGNORED: 0.0}


def profiles() -> List[str]:
    return list(PROFILES)


# ── Category coverage evaluation ──────────────────────────────────────────────

def category_coverage(category: str, present: Dict[str, Any], *,
                      stale: bool = False, confidence: Optional[float] = None) -> Dict[str, Any]:
    """Coverage of ONE category from the fields actually present.

    `present`  : the field->value map the provider returned (missing/None = absent).
    `stale`    : the value came from past the category freshness limit (display-only).
    `confidence`: optional provider/consensus confidence to fold in.
    """
    spec = _P.spec(category)
    req = list(spec.required_fields) if spec else list(present.keys())
    have = [f for f in req if present.get(f) not in (None, "", [], {})]
    missing = [f for f in req if f not in have]
    cov = (len(have) / len(req)) if req else (1.0 if present else 0.0)
    if stale:
        cov *= 0.5                                   # stale = degraded coverage
    if confidence is not None:
        cov *= (0.5 + 0.5 * max(0.0, min(1.0, confidence)))
    return {"category": category, "coverage": round(cov, 3),
            "have": have, "missing": missing, "stale": bool(stale),
            "weight": (spec.confidence_weight if spec else 0.5)}


def assess(coverages: Dict[str, Dict[str, Any]], *, profile: str = "momentum") -> Dict[str, Any]:
    """Overall, coverage-based, strategy-aware data quality.

    `coverages`: category -> the dict returned by `category_coverage` (or at least
                 {coverage, missing, weight, stale}).
    Returns overall score (0-1), per-category detail, the strategy profile's
    required/optional roles, the blocking (missing-required) gaps, and whether the
    data is sufficient for THAT strategy.
    """
    roles = PROFILES.get(profile, PROFILES["momentum"])
    cats: Dict[str, Any] = {}
    num = den = 0.0
    blocking: List[str] = []
    for cat, role in roles.items():
        mult = _ROLE_MULT[role]
        cov_rec = coverages.get(cat) or {"coverage": 0.0, "missing": list(
            (_P.spec(cat).required_fields if _P.spec(cat) else [])), "weight":
            (_P.spec(cat).confidence_weight if _P.spec(cat) else 0.5), "stale": False}
        w = cov_rec.get("weight", 0.5) * mult
        cov = cov_rec.get("coverage", 0.0)
        cats[cat] = {**cov_rec, "role": role, "effective_weight": round(w, 3)}
        if role == _IGNORED:
            continue
        num += w * cov
        den += w
        if role == _REQUIRED and cov < 0.5:
            blocking.append(cat)
    overall = round((num / den) if den else 0.0, 3)
    # A missing REQUIRED category caps the headline score so it can't read "green".
    if blocking:
        overall = round(min(overall, 0.5), 3)
    missing_fields = {c: cats[c]["missing"] for c in cats
                      if cats[c].get("missing") and roles.get(c) != _IGNORED}
    return {
        "profile": profile, "overall": overall,
        "sufficient": (not blocking),
        "blocking_gaps": blocking,
        "categories": cats,
        "missing_fields": missing_fields,
        "explain": ("data sufficient for %s" % profile) if not blocking else
                   ("missing required: " + ", ".join(blocking)),
    }
