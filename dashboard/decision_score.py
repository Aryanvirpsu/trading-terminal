"""EXPLAINABLE decision scoring — components, not a mysterious number.

This module does NOT re-judge risk. `options_desk.decide` and `option_risk` remain the
authoritative risk engines; this layer explains the decision and adds the context the
old widget was missing (news, catalysts, historical fit), under one rule:

    CONTEXT ADJUSTS CONFIDENCE. CONTEXT NEVER BYPASSES A HARD RULE.

Concretely, the score is split in two and they are not interchangeable:

    STRUCTURAL (0..58)   technical quality, setup quality, regime, execution
    CONTEXT   (-20..+24) news sentiment, catalysts, historical fit

    decision_score = structural_score + context_score   ->  range -20 .. 82

`decision_score` is the EXACT sum of the seven components. Nothing is added to it and
it is not normalised, so a reader can add up the component bars in the UI and land on
the headline number. It is a DIFFERENT quantity from `setup_score` (the scanner's own
0-100 funnel ranking) and the two are never displayed under the same word.

TRADEABLE requires ALL of: no hard failure, structural ≥ STRUCTURAL_FLOOR, and
decision_score ≥ TRADEABLE_MIN. The structural floor is the mechanism that stops a
euphoric headline from promoting a broken chart — context can add at most +24, which
cannot carry a setup that failed its own structure. And a hard failure short-circuits
everything before any of it is consulted.

HARD FAILURES (never outvoted, in the order the flow evaluates them):

    stale critical data      a verdict older than its inputs is not a verdict
    invalid setup            no objective stop -> no trade, at any score
    liquidity failure        an untradeable instrument is not a trade
    risk policy failure      the account cannot carry it under the active policy

Every component reports {score, max, note, direction}, so the total can be argued with.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in ("..", os.path.join("..", "lab"), os.path.join("..", "src")):
    sys.path.insert(0, os.path.join(_HERE, _p))
sys.path.insert(0, _HERE)


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Bounded maxima. Changing these changes how much each input may ever matter, which is
# the point: the limits are visible and testable rather than emergent.
LIMITS: Dict[str, Tuple[float, float]] = {
    # component            (min, max)
    "technical_quality":      (0.0, 22.0),
    "setup_quality":          (0.0, 16.0),
    "market_regime":          (0.0, 10.0),
    "execution_quality":      (0.0, 10.0),
    # ── context: bounded, signed, and jointly incapable of rescuing a bad setup ──
    "news_sentiment":         (-6.0, 6.0),
    "catalyst_quality":       (-6.0, 6.0),
    "historical_setup_fit":   (-8.0, 12.0),
}
# Reported alongside but NOT summed into the stock score: these answer "which
# instrument", not "is the underlying setup any good".
INSTRUMENT_LIMITS = {"option_quality": (0.0, 10.0), "portfolio_suitability": (0.0, 10.0)}

STRUCTURAL_MAX = sum(hi for k, (_, hi) in LIMITS.items()
                     if k in ("technical_quality", "setup_quality", "market_regime",
                              "execution_quality"))
CONTEXT_MAX = sum(hi for k, (_, hi) in LIMITS.items()
                  if k in ("news_sentiment", "catalyst_quality", "historical_setup_fit"))

CONTEXT_MIN = -sum(abs(lo) for k, (lo, _) in LIMITS.items()
                   if k in ("news_sentiment", "catalyst_quality", "historical_setup_fit"))

# ── THE SCALE ────────────────────────────────────────────────────────────────
# `decision_score` is the EXACT sum of the seven components above. Nothing is added
# to it. An earlier version added a constant +20 "so the scale reads 0-100", which
# was wrong in five separate ways: the headline number no longer equalled the
# components printed underneath it (the one thing this module exists to guarantee),
# the top of the range saturated against the 100 clamp so two different setups could
# both read 100, a setup with zero merit read 20/100, the thresholds were silently
# rebased (a "55 of 100" threshold really meant a raw 35), and the label collided in
# the UI with the scanner's own 0-100 setup score. The constant is gone; the range is
# declared instead.
DECISION_SCORE_MIN = CONTEXT_MIN            # -20.0
DECISION_SCORE_MAX = STRUCTURAL_MAX + CONTEXT_MAX   # 82.0

STRUCTURAL_FLOOR = _f("DECISION_STRUCTURAL_FLOOR", 32.0)      # of STRUCTURAL_MAX (58)
# Thresholds are on the DECISION_SCORE scale (-20..82), not a 0-100 one. These
# defaults are the previous 55/40 rebased by the removed constant, so every verdict
# boundary is exactly where it was — `test_removing_the_constant_did_not_move_any_verdict`
# pins that.
TRADEABLE_MIN = _f("DECISION_SCORE_TRADEABLE_MIN", 35.0)
MONITOR_MIN = _f("DECISION_SCORE_MONITOR_MIN", 20.0)

LABELS = ("TRADEABLE", "MONITOR", "REJECT")

# Names used across code, API and UI, so three different numbers stop being "score":
#   setup_score     scanner.score.total   — the funnel's ranking score, 0..100
#   decision_score  this module           — structural + context, -20..82
#   structural_score / context_score      — its two halves
SCORE_NAMES = ("setup_score", "decision_score", "structural_score", "context_score")


def _clamp(name: str, v: float) -> float:
    lo, hi = LIMITS.get(name, INSTRUMENT_LIMITS.get(name, (0.0, 10.0)))
    return max(lo, min(hi, v))


def _comp(out: List[Dict[str, Any]], name: str, raw: float, note: str,
          direction: str = "neutral") -> None:
    lo, hi = LIMITS.get(name, INSTRUMENT_LIMITS.get(name, (0.0, 10.0)))
    out.append({"component": name, "score": round(_clamp(name, raw), 2),
                "min": lo, "max": hi, "note": note, "direction": direction,
                "capped": raw > hi or raw < lo})


# ── The components ───────────────────────────────────────────────────────────

def _technical(ind: Dict[str, Any], score: Dict[str, Any]) -> Tuple[float, str, str]:
    """Trend structure + participation, drawn from the scanner's own components so the
    two can never disagree about the same chart."""
    comps = {c["component"]: c for c in (score.get("components") or [])}
    struct = comps.get("technical_structure", {})
    vol = comps.get("volume_confirmation", {})
    rs = comps.get("relative_strength", {})
    got = (struct.get("score") or 0) + (vol.get("score") or 0) + (rs.get("score") or 0)
    mx = (struct.get("max") or 14) + (vol.get("max") or 10) + (rs.get("max") or 12)
    pts = 22.0 * (got / mx) if mx else 0.0
    bits = []
    for k in ("above_sma20", "above_sma50", "above_sma200"):
        if ind.get(k):
            bits.append(k.replace("above_sma", "") + "-DMA")
    note = ("above " + "/".join(bits) if bits else "below its moving averages")
    if ind.get("rel_volume") is not None:
        note += f"; {ind['rel_volume']:.1f}x volume"
    if ind.get("rsi14") is not None:
        note += f"; RSI {ind['rsi14']:.0f}"
    direction = "bullish" if pts >= 14 else "bearish" if pts < 8 else "neutral"
    return pts, note, direction


def _setup(setups: List[Dict[str, Any]], levels: Dict[str, Any]) -> Tuple[float, str, str]:
    if not setups:
        return 0.0, "no setup category matched — the evidence for every pattern was absent", "bearish"
    top = setups[0]
    strength = float(top.get("strength") or 0)
    rr = levels.get("rr_target_1") if levels.get("state") == "ok" else None
    pts = 10.0 * min(1.0, strength / 80.0)
    if rr:
        pts += min(6.0, max(0.0, (rr - 1.0) * 3.0))
    note = f"{str(top.get('type','')).replace('_',' ')} (strength {strength:.0f})"
    if rr:
        note += f", {rr:.2f}:1 to target 1"
    return pts, note, "bullish" if pts >= 9 else "neutral"


def _regime(regime: Dict[str, Any]) -> Tuple[float, str, str]:
    trend = regime.get("trend")
    risk = regime.get("risk_appetite")
    vol = regime.get("vol_regime")
    if trend is None and risk is None:
        return 3.0, "regime unknown — no credit assumed", "neutral"
    if trend == "bull" or risk == "risk-on":
        pts, d = 9.0, "bullish"
        note = "index trending higher — a tailwind for long setups"
    elif trend == "bear" or risk == "risk-off":
        pts, d = 2.0, "bearish"
        note = "risk-off tape — a headwind for long setups"
    else:
        pts, d = 5.0, "neutral"
        note = "mixed tape — neither tailwind nor headwind"
    if vol == "high":
        pts -= 1.5
        note += "; elevated volatility regime"
    return pts, note, d


def _execution(desk: Dict[str, Any], ind: Dict[str, Any]) -> Tuple[float, str, str]:
    """Can this actually be got into and out of?"""
    dv = ind.get("dollar_volume_20d")
    pts, bits = 0.0, []
    if dv:
        pts += min(6.0, max(0.0, (dv / 1e6) ** 0.5 / 3.0))
        bits.append(f"${dv/1e9:.1f}B turnover" if dv >= 1e9 else f"${dv/1e6:.0f}M turnover")
    best = (desk or {}).get("best_contract") or {}
    sp = best.get("spread_pct")
    if sp is not None:
        pts += 4.0 if sp <= 5 else 2.0 if sp <= 10 else 0.0
        bits.append(f"{sp:.1f}% chain spread")
    elif dv:
        pts += 2.0
        bits.append("no chain read")
    d = "bullish" if pts >= 7 else "bearish" if pts < 3 else "neutral"
    return pts, "; ".join(bits) or "no liquidity data", d


def _news(sig: Dict[str, Any]) -> Tuple[float, str, str]:
    """Bounded and confidence-weighted. A single euphoric headline with thin coverage
    moves this by a fraction of a point, which is the entire design intent."""
    if not sig or sig.get("state") != "ok" or sig.get("sentiment_score") is None:
        return 0.0, "no directly relevant coverage — scored as absent, not as neutral", "neutral"
    s = float(sig["sentiment_score"])
    conf = float(sig.get("confidence") or 0.0)
    n = int(sig.get("relevant_count") or 0)
    # Sample factor: two stories cannot speak with the authority of ten.
    sample = min(1.0, n / 6.0)
    pts = 6.0 * s * conf * sample
    note = (f"{sig['direction']} {s:+.2f} · confidence {conf:.0%} · "
            f"{n} relevant item(s), {sig.get('direct_count', 0)} directly about the company")
    if sample < 1.0:
        note += f" — contribution scaled to {sample:.0%} for sample size"
    return pts, note, sig.get("direction", "neutral")


def _catalyst(sig: Dict[str, Any]) -> Tuple[float, str, str]:
    if not sig or sig.get("state") != "ok":
        return 0.0, "catalyst data unavailable", "neutral"
    cats = sig.get("catalysts") or []
    if not cats:
        return 0.0, "no identifiable event — drift, not a catalyst", "neutral"
    cs = float(sig.get("catalyst_score") or 0.0)
    pts = 6.0 * cs
    top = cats[0]
    note = (f"{len(cats)} event(s); strongest: {top['type'].replace('_',' ')} "
            f"({top['direction']}, {top['importance']} importance)")
    d = "bullish" if cs > 0.15 else "bearish" if cs < -0.15 else "neutral"
    return pts, note, d


def _history(hist: Dict[str, Any]) -> Tuple[float, str, str]:
    """Historical fit. An INSUFFICIENT SAMPLE contributes ZERO — never a guess, and
    never a penalty for a strategy simply being new on this name."""
    if not hist or hist.get("state") != "ok":
        return 0.0, "historical validation unavailable", "neutral"
    cls = hist.get("classification")
    n = hist.get("sample_size") or 0
    if cls in ("INSUFFICIENT SAMPLE", "NO HISTORY"):
        return 0.0, f"{n} comparable setup(s) — too few to score; contributes nothing", "neutral"
    st = hist.get("stats") or {}
    fit = (hist.get("fit_pct") or 0) / 100.0
    exp = st.get("expectancy_pct") or 0.0
    pf = st.get("profit_factor")
    pf_v = 0.0 if pf is None else (3.0 if pf == float("inf") else float(pf))
    base = {"STRONG HISTORY": 12.0, "MIXED HISTORY": 4.0, "WEAK HISTORY": -8.0}.get(cls, 0.0)
    # Weak history is a full-strength warning; strong history is discounted by how
    # closely the past instances actually resemble today.
    pts = base * (fit if base > 0 else 1.0)
    note = (f"{cls.lower()} over {n} comparable setup(s) at {hist.get('fit_pct')}% fit — "
            f"expectancy {exp:+.1f}%, profit factor {pf_v:.2f}, "
            f"T1 hit {st.get('t1_hit_rate')}%, stop hit {st.get('stop_hit_rate')}%")
    d = "bullish" if base > 4 else "bearish" if base < 0 else "neutral"
    return pts, note, d


# ── Thesis labels ────────────────────────────────────────────────────────────

def _thesis(components: Dict[str, Dict[str, Any]], hist: Dict[str, Any],
            news: Dict[str, Any], desk_dec: Dict[str, Any],
            hard: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def lvl(name, hi, mid, hi_l="STRONG", mid_l="MODERATE", lo_l="WEAK"):
        v = (components.get(name) or {}).get("score") or 0
        return hi_l if v >= hi else mid_l if v >= mid else lo_l

    news_label = {"bullish": "BULLISH", "bearish": "BEARISH",
                  "neutral": "NEUTRAL", "no_data": "NO DATA"}.get(
        (news or {}).get("direction"), "NO DATA")
    cats = (news or {}).get("catalysts") or []
    if not cats:
        cat_label = "NONE"
    else:
        cs = (news or {}).get("catalyst_score") or 0
        cat_label = "POSITIVE" if cs > 0.15 else "NEGATIVE" if cs < -0.15 else "NEUTRAL"
    hcls = (hist or {}).get("classification") or "NO HISTORY"
    hist_label = {"STRONG HISTORY": "STRONG", "MIXED HISTORY": "MIXED",
                  "WEAK HISTORY": "WEAK", "INSUFFICIENT SAMPLE": "INSUFFICIENT",
                  "NO HISTORY": "NO DATA"}.get(hcls, "NO DATA")
    risk_fail = any(h["kind"] in ("risk_policy", "liquidity") for h in hard)
    ps = (desk_dec or {}).get("portfolio_suitability") or {}
    return [
        {"row": "Technical", "value": lvl("technical_quality", 15, 9)},
        {"row": "Setup", "value": lvl("setup_quality", 11, 6)},
        {"row": "Regime", "value": lvl("market_regime", 8, 4,
                                       "SUPPORTIVE", "NEUTRAL", "HOSTILE")},
        {"row": "News/Sentiment", "value": news_label},
        {"row": "Catalysts", "value": cat_label},
        {"row": "Historical Fit", "value": hist_label},
        {"row": "Execution", "value": lvl("execution_quality", 7, 4,
                                          "GOOD", "FAIR", "POOR")},
        {"row": "Risk Fit", "value": "FAIL" if risk_fail else "PASS"},
        {"row": "Option Fit", "value": "PASS" if ps.get("pass") else "FAIL"},
    ]


# ── Hard failures ────────────────────────────────────────────────────────────

def _hard_failures(desk_dec: Dict[str, Any], levels: Dict[str, Any],
                   freshness: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The rules no amount of good news or good history may overturn."""
    out: List[Dict[str, Any]] = []
    fr = freshness or (desk_dec or {}).get("decision_freshness") or {}
    if fr and fr.get("ok") is False:
        out.append({"kind": "stale_data", "detail": "; ".join(fr.get("stale_inputs") or [])
                    or "a critical input is past its freshness window"})
    if (levels or {}).get("state") != "ok":
        out.append({"kind": "invalid_setup",
                    "detail": (levels or {}).get("reason")
                              or "no objective invalidation level — a stop cannot be placed"})
    for b in ((desk_dec or {}).get("blockers") or []):
        low = b.lower()
        if "stale" in low:
            kind = "stale_data"
        elif "liquid" in low or "open interest" in low or "spread" in low:
            kind = "liquidity"
        elif ("risk" in low or "buying power" in low or "policy" in low
              or "cash is the position" in low):
            kind = "risk_policy"
        elif "reward:risk" in low:
            kind = "invalid_setup"
        else:
            continue
        out.append({"kind": kind, "detail": b})
    # Collapse duplicates of the same kind, keeping the first detail.
    seen, uniq = set(), []
    for h in out:
        if h["kind"] in seen:
            continue
        seen.add(h["kind"])
        uniq.append(h)
    return uniq


# ── Public API ───────────────────────────────────────────────────────────────

def assess(*, candidate: Dict[str, Any], desk: Optional[Dict[str, Any]] = None,
           news: Optional[Dict[str, Any]] = None,
           history: Optional[Dict[str, Any]] = None,
           regime: Optional[Dict[str, Any]] = None,
           freshness: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The explainable assessment behind the Trade Decision widget."""
    ind = (candidate or {}).get("indicators") or {}
    levels = (candidate or {}).get("levels") or {}
    setups = (candidate or {}).get("setups") or []
    sc_score = (candidate or {}).get("score") or {}
    desk_dec = ((desk or {}).get("decision") or {}) if desk else {}

    comps: List[Dict[str, Any]] = []
    for name, fn, args in (
        ("technical_quality", _technical, (ind, sc_score)),
        ("setup_quality", _setup, (setups, levels)),
        ("market_regime", _regime, (regime or {},)),
        ("execution_quality", _execution, (desk or {}, ind)),
        ("news_sentiment", _news, (news or {},)),
        ("catalyst_quality", _catalyst, (news or {},)),
        ("historical_setup_fit", _history, (history or {},)),
    ):
        pts, note, direction = fn(*args)
        _comp(comps, name, pts, note, direction)

    by = {c["component"]: c for c in comps}
    structural = sum(by[k]["score"] for k in
                     ("technical_quality", "setup_quality", "market_regime",
                      "execution_quality"))
    context = sum(by[k]["score"] for k in
                  ("news_sentiment", "catalyst_quality", "historical_setup_fit"))
    # The score IS the sum of its parts. No constant, no clamp, no normalisation —
    # a reader adding up the component bars must land on this number exactly.
    decision_score = round(structural + context, 2)

    hard = _hard_failures(desk_dec, levels, freshness)

    # ── the label: hard rules FIRST, and they are not negotiable ──
    if hard:
        label = "REJECT"
        why = f"hard rule failed: {hard[0]['kind'].replace('_', ' ')}"
    elif structural < STRUCTURAL_FLOOR:
        label = "MONITOR"
        why = (f"structural quality {structural:.0f}/{STRUCTURAL_MAX:.0f} is below the "
               f"{STRUCTURAL_FLOOR:.0f} floor — news and history cannot substitute for "
               f"the setup itself")
    elif decision_score >= TRADEABLE_MIN:
        label = "TRADEABLE"
        why = (f"structure {structural:.0f}/{STRUCTURAL_MAX:.0f} clears the floor and "
               f"decision score {decision_score:.1f} clears {TRADEABLE_MIN:.0f}")
    elif decision_score >= MONITOR_MIN:
        label = "MONITOR"
        why = (f"decision score {decision_score:.1f} is between the {MONITOR_MIN:.0f} "
               f"monitor and {TRADEABLE_MIN:.0f} tradeable thresholds")
    else:
        label = "REJECT"
        why = f"decision score {decision_score:.1f} is below the {MONITOR_MIN:.0f} minimum"

    supports, contradicts = _reasons(by, news, history, desk_dec, hard, ind)
    return {
        "state": "ok",
        "label": label, "label_reason": why,
        "instrument": desk_dec.get("instrument"),
        "components": comps,
        "structural_score": round(structural, 2), "structural_max": STRUCTURAL_MAX,
        "structural_floor": STRUCTURAL_FLOOR,
        "context_score": round(context, 2), "context_max": CONTEXT_MAX,
        "context_min": CONTEXT_MIN,
        # THE headline number, and it is the exact sum of `components`.
        "decision_score": decision_score,
        "score_scale": {"name": "decision_score",
                        "min": DECISION_SCORE_MIN, "max": DECISION_SCORE_MAX,
                        "equals": "structural_score + context_score",
                        "note": ("Sum of the seven components exactly — no constant, "
                                 "no normalisation. Distinct from `setup_score`, which "
                                 "is the scanner's own 0-100 ranking score.")},
        "thresholds": {"tradeable": TRADEABLE_MIN, "monitor": MONITOR_MIN,
                       "structural_floor": STRUCTURAL_FLOOR,
                       "scale": "decision_score"},
        "hard_failures": hard,
        "thesis": _thesis(by, history or {}, news or {}, desk_dec, hard),
        "supports": supports, "contradicts": contradicts,
        "invalidation": {
            "price": levels.get("invalidation"),
            "basis": levels.get("invalidation_basis"),
            "note": (f"below {levels.get('invalidation')} the "
                     f"{(setups[0].get('type','setup') if setups else 'setup')}"
                     .replace("_", " ") + " thesis is wrong")
                    if levels.get("invalidation") else "no objective invalidation level",
        },
        "context_cannot_override": (
            "News, catalysts and history adjust confidence only. Their combined "
            f"contribution is bounded to {CONTEXT_MAX:.0f} points and cannot lift a "
            f"setup whose structural score is under {STRUCTURAL_FLOOR:.0f}, nor waive "
            "a stale-data, invalid-setup, liquidity or risk-policy failure."),
    }


def _reasons(by: Dict[str, Dict[str, Any]], news: Optional[Dict[str, Any]],
             hist: Optional[Dict[str, Any]], desk_dec: Dict[str, Any],
             hard: List[Dict[str, Any]], ind: Dict[str, Any]
             ) -> Tuple[List[str], List[str]]:
    """The 2-3 things that matter most on each side, ranked by how much they moved
    the score — not a dump of everything the engine noticed."""
    sup = sorted((c for c in by.values() if c["score"] > 0 and c["direction"] == "bullish"),
                 key=lambda c: -c["score"])
    con = sorted((c for c in by.values() if c["direction"] == "bearish" or c["score"] < 0),
                 key=lambda c: c["score"])
    supports = [c["note"] for c in sup[:3]]
    contradicts = [c["note"] for c in con[:2]]

    for h in hard:
        contradicts.insert(0, f"{h['kind'].replace('_', ' ')}: {h['detail']}")
    rsi = ind.get("rsi14")
    if rsi is not None and rsi >= 70:
        contradicts.append(f"RSI {rsi:.0f} is elevated — extended entries give back more")
    if desk_dec.get("option_ineligible_reason"):
        contradicts.append(desk_dec["option_ineligible_reason"])
    return supports[:3], contradicts[:4]
