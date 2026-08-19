"""ONE canonical, symbol-level news + catalyst signal for the decision engine.

Before this module the News widget DISPLAYED headlines and the authoritative verdict
(`options_desk.decide`) never read a single one — news reached a decision only through
two components of `scanner.score` (news_sentiment max 4, catalyst_quality max 8) that
were computed for scan FINALISTS only. This module produces the signal the decision
actually consumes, and it keeps two questions apart that were previously collapsed:

    SENTIMENT  what is the tone of the information available right now?
    CATALYST   is there an identifiable EVENT capable of moving the stock?

A stock can have glowing sentiment and no catalyst (drift), or a hard catalyst with
neutral tone (an earnings date). They are reported separately and never averaged.

RELEVANCE TIERING is the other half of the job. "Palantir wins $500M contract" and
"SA analyst upgrades/downgrades: AAPL, PLTR, SNDK, AMZN" both mention PLTR; treating
them as equal evidence is how a ticker roundup came to move a verdict. Every item is
placed in one of four tiers and weighted accordingly:

    DIRECT          the company is the subject
    INDIRECT_SECTOR the sector/peer group is the subject
    MARKET_WIDE     the market is the subject; this ticker is in a list
    LOW             a passing mention

No new sentiment model is introduced. Polarity comes from whatever
`lab/model_registry.sentiment` is configured to use (FinBERT when enabled, lexical
otherwise) via `research.catalysts`, which has already scored and deduplicated the
items — this module re-weights that output, it does not re-run it.
"""
from __future__ import annotations

import math
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in ("..", os.path.join("..", "lab"), os.path.join("..", "src")):
    sys.path.insert(0, os.path.join(_HERE, _p))
sys.path.insert(0, _HERE)

import research as _R


# ── Relevance tiers ──────────────────────────────────────────────────────────

TIERS = ("DIRECT", "INDIRECT_SECTOR", "MARKET_WIDE", "LOW")

# How much each tier is allowed to move the aggregate. A market-wide roundup that
# happens to list the ticker carries a twentieth of the weight of company news.
TIER_WEIGHT = {"DIRECT": 1.0, "INDIRECT_SECTOR": 0.35, "MARKET_WIDE": 0.10, "LOW": 0.05}

_MARKET_WORDS = ("stock market today", "market today", "futures", "s&p 500", "sp500",
                 "nasdaq composite", "dow jones", "wall street", "fed ", "fomc",
                 "inflation", "cpi", "jobs report", "rate cut", "rate hike",
                 "market wrap", "premarket movers", "stocks to watch", "movers")
_ROUNDUP_WORDS = ("upgrades/downgrades", "analyst roundup", "top stocks", "best stocks",
                  "stocks to buy", "watchlist", "movers", "biggest gainers",
                  "biggest losers", "roundup", "bulls and bears", "bulls & bears",
                  "and more", "among others", "here's what", "stocks in focus")

# "Benzinga Bulls And Bears: Palantir, Marvell, AppLovin" names three companies and
# no uppercase tickers, so counting ticker-shaped tokens alone read it as company news
# about whichever name happened to sit before the halfway mark. A comma-separated run
# of capitalised names — with or without a leading colon — is a list, not a subject.
_LIST_RE = re.compile(r"(?:[A-Z][\w.&'-]+)(?:\s*,\s*(?:[A-Z][\w.&'-]+)){2,}")


def _looks_like_a_list(title: str) -> bool:
    if _LIST_RE.search(title or ""):
        return True
    head, _, tail = (title or "").partition(":")
    return bool(tail) and tail.count(",") >= 2
_SECTOR_WORDS = ("sector", "peers", "rivals", "industry", "competitors", "chipmakers",
                 "software stocks", "defense stocks", "ai stocks", "bank stocks")


def _tickers_in(text: str) -> int:
    exclude = {"THE", "AND", "FOR", "INC", "CORP", "LTD", "PLC", "LLC", "ETF", "CEO",
               "CFO", "IPO", "USA", "US", "AI", "EPS", "GDP", "NYSE", "SEC", "FDA"}
    return len(set(re.findall(r"\b[A-Z]{2,5}\b", text or "")) - exclude)


def classify_relevance(title: str, symbol: str, *, aliases: Optional[List[str]] = None,
                       model_relevance: Optional[float] = None) -> Dict[str, Any]:
    """Place ONE headline in a relevance tier, with the reason it landed there."""
    t = title or ""
    low = t.lower()
    sym = (symbol or "").upper()
    aliases = [a for a in (aliases or []) if a]
    n_tickers = _tickers_in(t)

    named = bool(re.search(rf"\b{re.escape(sym)}\b", t)) or f"${sym}" in t
    alias_hit = next((a for a in aliases if a and a.lower() in low), None)
    # Subject position: a company named in the first half of a headline is usually
    # what the headline is ABOUT; named at the end it is usually a list member.
    subject_early = False
    if alias_hit:
        subject_early = low.find(alias_hit.lower()) < max(1, len(low) * 0.5)
    elif named:
        m = re.search(rf"\b{re.escape(sym)}\b", t)
        subject_early = bool(m) and m.start() < max(1, len(t) * 0.5)

    is_roundup = (any(w in low for w in _ROUNDUP_WORDS) or n_tickers >= 4
                  or _looks_like_a_list(t))
    is_market = any(w in low for w in _MARKET_WORDS)
    is_sector = any(w in low for w in _SECTOR_WORDS)
    # A list headline is a list even when this company happens to be named first.
    if is_roundup and not (named and f"${sym}" in t):
        return {"tier": "MARKET_WIDE" if is_market else "INDIRECT_SECTOR",
                "reason": f"multi-company list headline — {sym} is one of several names",
                "tickers_in_headline": n_tickers}

    if not named and not alias_hit:
        return {"tier": "LOW", "reason": "the symbol and its company name are absent",
                "tickers_in_headline": n_tickers}
    if is_roundup and not subject_early:
        return {"tier": "MARKET_WIDE" if is_market else "LOW",
                "reason": f"list/roundup headline naming {n_tickers} tickers — "
                          f"{sym} is a list member, not the subject",
                "tickers_in_headline": n_tickers}
    if is_market and not subject_early:
        return {"tier": "MARKET_WIDE",
                "reason": "market-level headline; the ticker appears inside it",
                "tickers_in_headline": n_tickers}
    if is_sector and not subject_early:
        return {"tier": "INDIRECT_SECTOR",
                "reason": "the sector or peer group is the subject",
                "tickers_in_headline": n_tickers}
    if n_tickers >= 2 and not subject_early:
        return {"tier": "INDIRECT_SECTOR",
                "reason": f"{n_tickers} companies share the headline",
                "tickers_in_headline": n_tickers}
    if subject_early or (model_relevance is not None and model_relevance >= 0.9):
        return {"tier": "DIRECT", "reason": "the company is the subject of the headline",
                "tickers_in_headline": n_tickers}
    return {"tier": "LOW", "reason": "passing mention", "tickers_in_headline": n_tickers}


# ── Catalysts: an EVENT, not a tone ──────────────────────────────────────────

CATALYST_TYPES = ("earnings", "guidance", "analyst", "contract", "product",
                  "regulatory", "m_and_a", "management", "macro_sector")

# (type, importance, patterns). Order matters — the first match wins, so specific
# event language beats generic. Nothing is inferred: a headline that matches no
# pattern produces NO catalyst rather than a guessed one.
_CATALYST_RULES: List[Tuple[str, str, Tuple[str, ...]]] = [
    ("earnings",   "high",   ("earnings", "q1 results", "q2 results", "q3 results",
                              "q4 results", "quarterly results", "beats estimates",
                              "misses estimates", "reports revenue", "eps of")),
    ("guidance",   "high",   ("guidance", "outlook", "forecast raised", "forecast cut",
                              "raises full-year", "lowers full-year", "guides")),
    ("m_and_a",    "high",   ("acquire", "acquisition", "merger", "buyout", "takeover",
                              "to buy", "stake in", "divest")),
    ("regulatory", "high",   ("sec", "doj", "ftc", "antitrust", "investigation",
                              "lawsuit", "fda approval", "subpoena", "fined", "probe",
                              "sanction", "export curb", "ban", "banned")),
    ("contract",   "high",   ("contract", "deal worth", "awarded", "wins ", "signs ",
                              "partnership", "agreement with", "selects")),
    ("management", "medium", ("ceo", "cfo", "steps down", "resigns", "appoints",
                              "names new", "board of directors")),
    ("product",    "medium", ("launch", "unveil", "announces new", "introduces",
                              "rollout", "releases")),
    ("analyst",    "medium", ("upgrade", "downgrade", "price target", "initiates coverage",
                              "reiterates", "buy rating", "sell rating", "overweight",
                              "underweight")),
    ("macro_sector", "low",  ("sector", "industry", "peers", "tariff", "rates",
                              "inflation")),
]

_BULLISH = ("beats", "raises", "wins", "awarded", "upgrade", "approval", "record",
            "surges", "soars", "jumps", "rallies", "expands", "outperform", "overweight",
            "buy rating", "partnership", "acquire")
_BEARISH = ("misses", "cuts", "lowers", "downgrade", "lawsuit", "probe", "investigation",
            "falls", "plunges", "sinks", "drops", "recall", "delay", "resigns",
            "steps down", "fine", "underweight", "sell rating", "ban")


_PAT_CACHE: Dict[str, "re.Pattern[str]"] = {}


def _matches(low: str, pats: Tuple[str, ...]) -> bool:
    """WORD-BOUNDARY matching. A plain substring test made "ban" fire on "Bank of
    America" and tagged a bullish analyst note as a regulatory event — the kind of
    error that is invisible until it moves a verdict."""
    key = "|".join(pats)
    rx = _PAT_CACHE.get(key)
    if rx is None:
        rx = _PAT_CACHE[key] = re.compile(
            "|".join(r"\b" + re.escape(p.strip()) + (r"\b" if p.strip().isalpha() else "")
                     for p in pats))
    return bool(rx.search(low))


def classify_catalyst(title: str, *, polarity: Optional[float] = None
                      ) -> Optional[Dict[str, Any]]:
    """Identify an EVENT in a headline, or return None. Never infers one."""
    low = (title or "").lower()
    for ctype, importance, pats in _CATALYST_RULES:
        if _matches(low, pats):
            bull = sum(1 for w in _BULLISH if w in low)
            bear = sum(1 for w in _BEARISH if w in low)
            if bull > bear:
                direction = "bullish"
            elif bear > bull:
                direction = "bearish"
            elif polarity is not None and abs(polarity) > 0.25:
                direction = "bullish" if polarity > 0 else "bearish"
            else:
                direction = "neutral"
            return {"type": ctype, "direction": direction, "importance": importance}
    return None


# ── Freshness / diversity helpers ────────────────────────────────────────────

def _age_hours(ts: Optional[str]) -> Optional[float]:
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - t).total_seconds() / 3600.0)
    except Exception:  # noqa: BLE001
        return None


def _recency_weight(ts: Optional[str], half_life_h: float = 36.0) -> float:
    a = _age_hours(ts)
    return 0.45 if a is None else math.pow(0.5, a / half_life_h)


def _source_diversity(sources: List[str]) -> Dict[str, Any]:
    """Five stories from one outlet are one story. Diversity discounts confidence."""
    clean = [s.strip().lower() for s in sources if s]
    uniq = sorted(set(clean))
    n = len(clean)
    ratio = (len(uniq) / n) if n else 0.0
    return {"sources": len(uniq), "items": n, "ratio": round(ratio, 2),
            "names": uniq[:8],
            "note": ("single-source coverage — treat as one story" if len(uniq) <= 1 and n > 1
                     else f"{len(uniq)} independent outlets")}


# ── The canonical signal ─────────────────────────────────────────────────────

DIRECTIONS = ("bullish", "bearish", "neutral", "no_data")


def compute(symbol: str, *, raw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """ONE news+catalyst reading for a symbol.

    `raw` is a `research.catalysts()` payload; it is fetched when not supplied. That
    payload has already run the configured sentiment model and deduplicated
    syndicated copies, so this function re-weights rather than re-scores.
    """
    sym = (symbol or "").upper().strip()
    if not sym:
        return {"state": "error", "reason": "symbol is required"}
    if raw is None:
        raw = _R.catalysts(sym)
    if not isinstance(raw, dict) or raw.get("state") != "ok":
        return {"state": (raw or {}).get("state", "error"), "symbol": sym,
                "reason": (raw or {}).get("reason") or "news provider unavailable",
                "direction": "no_data", "sentiment_score": None, "confidence": 0.0,
                "article_count": 0, "catalysts": [], "items": []}

    try:
        aliases = _R._aliases_for(sym)
    except Exception:  # noqa: BLE001
        aliases = []

    items = [i for i in (raw.get("items") or []) if i.get("title")]
    # `research.catalysts` dedups on an exact-prefix key, which misses a syndicated
    # story re-headlined by another outlet. Collapse those too, keeping the earliest.
    items = _dedup_syndicated(items)

    scored: List[Dict[str, Any]] = []
    num = den = 0.0
    for it in items:
        rel = classify_relevance(it.get("title"), sym, aliases=aliases,
                                 model_relevance=it.get("relevance"))
        pol = it.get("polarity")
        if pol is None:
            pol = {"bullish": 0.5, "bearish": -0.5}.get(it.get("sentiment"), 0.0)
        tw = TIER_WEIGHT[rel["tier"]]
        rw = _recency_weight(it.get("ts"))
        w = tw * rw
        cat = classify_catalyst(it.get("title"), polarity=pol)
        rec = {"title": it.get("title"), "url": it.get("url"),
               "source": it.get("source") or it.get("provider"),
               "ts": it.get("ts"), "age_hours": _age_hours(it.get("ts")),
               "polarity": round(float(pol), 3),
               "sentiment": it.get("sentiment"),
               "tier": rel["tier"], "tier_reason": rel["reason"],
               "relevance": round(tw, 2), "recency_weight": round(rw, 2),
               "weight": round(w, 3), "catalyst": cat,
               "model_relevance": it.get("relevance"),
               "duplicates": it.get("_dupes", 0)}
        scored.append(rec)
        # Only DIRECT and sector items move the number. A market-wide roundup is
        # context, not evidence about this company.
        if rel["tier"] in ("DIRECT", "INDIRECT_SECTOR"):
            num += float(pol) * w
            den += w

    scored.sort(key=lambda r: (-r["weight"], r["age_hours"] if r["age_hours"] is not None else 1e9))
    direct = [r for r in scored if r["tier"] == "DIRECT"]
    relevant = [r for r in scored if r["tier"] in ("DIRECT", "INDIRECT_SECTOR")]

    if den <= 0 or not relevant:
        return {"state": "ok", "symbol": sym, "direction": "no_data",
                "sentiment_score": None, "confidence": 0.0,
                "article_count": len(items), "relevant_count": 0, "direct_count": 0,
                "reason": "no directly relevant coverage — every item is a market-wide "
                          "or passing mention",
                "items": scored[:20], "catalysts": _catalyst_list(scored),
                "earnings": raw.get("earnings"),
                "counts": {"positive": 0, "neutral": 0, "negative": 0},
                "source_diversity": _source_diversity([r["source"] for r in scored]),
                "freshness": _set_freshness(scored), "backend": raw.get("model_backend"),
                "catalyst_score": 0.0}

    score = num / den
    direction = "bullish" if score > 0.12 else "bearish" if score < -0.12 else "neutral"
    pos = sum(1 for r in relevant if r["polarity"] > 0.15)
    neg = sum(1 for r in relevant if r["polarity"] < -0.15)
    neu = len(relevant) - pos - neg

    div = _source_diversity([r["source"] for r in relevant])
    fresh = _set_freshness(relevant)
    agree = (sum(1 for r in relevant if (r["polarity"] > 0) == (score > 0))
             / len(relevant)) if relevant else 0.0
    # Confidence is EARNED: agreement, sample, independent sourcing, recency and how
    # much of the evidence is about the company rather than the market.
    conf = (0.20
            + 0.30 * agree
            + 0.15 * min(1.0, len(direct) / 4.0)
            + 0.15 * min(1.0, div["ratio"])
            + 0.20 * (fresh.get("recency_factor") or 0.0))
    conf = round(max(0.0, min(0.90, conf)), 2)

    cats = _catalyst_list(scored)
    return {
        "state": "ok", "symbol": sym,
        "sentiment_score": round(score, 3),
        "direction": direction,
        "confidence": conf,
        "article_count": len(items),
        "relevant_count": len(relevant),
        "direct_count": len(direct),
        "avg_relevance": round(sum(r["relevance"] for r in relevant) / len(relevant), 2),
        "counts": {"positive": pos, "neutral": neu, "negative": neg},
        "source_diversity": div,
        "freshness": fresh,
        "tier_breakdown": {t: sum(1 for r in scored if r["tier"] == t) for t in TIERS},
        "catalysts": cats,
        "catalyst_score": _catalyst_score(cats),
        "earnings": raw.get("earnings"),
        "items": scored[:20],
        "backend": raw.get("model_backend"),
        "sources_used": raw.get("sources_used") or [],
        "note": "Sentiment is the TONE of current coverage. Catalysts are identifiable "
                "events and are reported separately — they are never averaged together.",
    }


def _dedup_syndicated(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse the same story carried by several outlets (token-overlap match)."""
    def toks(s):
        return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) > 3}

    kept: List[Dict[str, Any]] = []
    for it in items:
        t = toks(it.get("title"))
        dup = None
        for k in kept:
            kt = toks(k.get("title"))
            if not t or not kt:
                continue
            if len(t & kt) / len(t | kt) >= 0.6:
                dup = k
                break
        if dup is None:
            kept.append(dict(it))
        else:
            dup["_dupes"] = dup.get("_dupes", 0) + 1
    return kept


def _catalyst_list(scored: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Typed events, most important and most relevant first. Deduplicated by type."""
    out: List[Dict[str, Any]] = []
    rank = {"high": 3, "medium": 2, "low": 1}
    for r in scored:
        c = r.get("catalyst")
        # Only company-level coverage can establish a company catalyst.
        if not c or r["tier"] not in ("DIRECT", "INDIRECT_SECTOR"):
            continue
        out.append({**c, "headline": r["title"], "source": r["source"],
                    "timestamp": r["ts"], "age_hours": r["age_hours"],
                    "tier": r["tier"], "url": r["url"],
                    "confirmed_by": 1 + (r.get("duplicates") or 0)})
    out.sort(key=lambda c: (-rank.get(c["importance"], 0),
                            c["age_hours"] if c["age_hours"] is not None else 1e9))
    seen, dedup = set(), []
    for c in out:
        if c["type"] in seen:
            continue
        seen.add(c["type"])
        dedup.append(c)
    return dedup[:6]


def _catalyst_score(cats: List[Dict[str, Any]]) -> float:
    """0..1 — is there an identifiable event capable of moving the stock? Signed by
    direction so a bearish catalyst cannot read as a positive."""
    if not cats:
        return 0.0
    w = {"high": 1.0, "medium": 0.55, "low": 0.2}
    total = 0.0
    for c in cats[:4]:
        base = w.get(c["importance"], 0.2)
        if c["tier"] != "DIRECT":
            base *= 0.5
        rec = _recency_weight(c.get("timestamp"), half_life_h=72.0)
        sign = {"bullish": 1.0, "bearish": -1.0, "neutral": 0.35}[c["direction"]]
        total += base * rec * sign
    return round(max(-1.0, min(1.0, total)), 3)


def _set_freshness(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    ages = [r["age_hours"] for r in items if r.get("age_hours") is not None]
    if not ages:
        return {"newest_hours": None, "median_hours": None, "dated": 0,
                "undated": len(items), "recency_factor": 0.0,
                "note": "no headline carries a timestamp — recency cannot be verified"}
    ages.sort()
    newest = ages[0]
    return {"newest_hours": round(newest, 1),
            "median_hours": round(ages[len(ages) // 2], 1),
            "dated": len(ages), "undated": len(items) - len(ages),
            "recency_factor": round(math.pow(0.5, newest / 24.0), 2),
            "note": f"newest relevant story {newest:.1f}h old"}


def cached(symbol: str, ttl: float = 600.0) -> Dict[str, Any]:
    sym = (symbol or "").upper().strip()
    return _R.cached(f"newsig:{sym}", ttl, lambda: compute(sym))
