"""Provider-independent MODEL REGISTRY.

The terminal must never hard-depend on a multi-gigabyte ML stack, yet must be
able to USE one when it's present. This registry is the seam:

  * Every model slot (sentiment today; embeddings / forecast declared for later)
    is chosen by ENVIRONMENT VARIABLES and FAILS GRACEFULLY.
  * The default backend is a deterministic, dependency-free **lexical** financial
    sentiment scorer (Loughran-McDonald-style polarity + the catalyst keyword
    engine). It always works, offline, in milliseconds.
  * If `transformers` (+`torch`) is installed AND `SENTIMENT_MODEL_ENABLED=true`,
    the sentiment slot upgrades to a Hugging Face model (default ProsusAI/finbert)
    loaded LAZILY and ONCE (never on import, never per-call, never re-downloaded).
    Any failure silently falls back to lexical — a model outage can't blank the UI.

Aggregation is done HERE, not by naive averaging: per-headline polarity is
combined with recency decay, source reliability, ticker relevance and duplicate
detection, exactly as the brief requires ("Do not average unrelated headlines").

Environment:
    SENTIMENT_MODEL_ENABLED = true|false     (default false -> lexical)
    SENTIMENT_MODEL         = hf model id     (default ProsusAI/finbert)
    EMBEDDINGS_MODEL        = hf model id     (declared; used by RAG when wired)
    FORECAST_MODEL          = hf model id     (declared; benchmarked separately)
"""
from __future__ import annotations

import math
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# ── Config (env-driven, versioned) ───────────────────────────────────────────
REGISTRY_VERSION = "1.0.0"


def _envbool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


CONFIG = {
    "sentiment": {
        "enabled": _envbool("SENTIMENT_MODEL_ENABLED", False),
        "model": os.environ.get("SENTIMENT_MODEL", "ProsusAI/finbert"),
        "slot": "financial-sentiment",
    },
    "embeddings": {
        "enabled": _envbool("EMBEDDINGS_MODEL_ENABLED", False),
        "model": os.environ.get("EMBEDDINGS_MODEL", "FinLang/finance-embeddings-investopedia"),
        "fallback": "sentence-transformers/all-MiniLM-L6-v2",
        "slot": "semantic-search",
    },
    "forecast": {
        "enabled": _envbool("FORECAST_MODEL_ENABLED", False),
        "model": os.environ.get("FORECAST_MODEL", "ibm-granite/granite-timeseries-ttm-r3"),
        "slot": "time-series",
    },
}

# ── Lexical financial sentiment (always-available fallback) ───────────────────
# A compact, finance-tuned polarity lexicon. Not a replacement for FinBERT, but a
# calibrated, explainable default that never fails or downloads anything.
_POS = {
    "beat", "beats", "beat expectations", "surge", "surges", "soar", "soars", "jump",
    "jumps", "rally", "rallies", "record", "upgrade", "upgrades", "raised", "raises",
    "outperform", "buy", "strong", "growth", "profit", "profitable", "gains", "gain",
    "tops", "exceeds", "bullish", "rebound", "recovery", "approval", "approved",
    "wins", "win", "expansion", "boost", "boosts", "optimistic", "beat estimates",
    "guidance raise", "dividend", "buyback", "accelerate", "momentum", "breakout",
    "all-time high", "upside", "outperforms", "beat street",
}
_NEG = {
    "miss", "misses", "missed", "plunge", "plunges", "tumble", "tumbles", "slump",
    "slumps", "drop", "drops", "fall", "falls", "downgrade", "downgrades", "cut",
    "cuts", "underperform", "sell", "weak", "loss", "losses", "warn", "warns",
    "warning", "bearish", "lawsuit", "probe", "investigation", "recall", "layoff",
    "layoffs", "bankruptcy", "default", "fraud", "decline", "declines", "slashed",
    "guidance cut", "profit warning", "misses estimates", "sinks", "slides",
    "downside", "halted", "delisting", "short seller", "selloff", "sell-off",
}
# Negators flip nearby polarity.
_NEGATORS = {"not", "no", "never", "without", "fails", "failed", "fail"}

# Source reliability priors (0-1). Unknown sources default to 0.5.
_SOURCE_WEIGHT = {
    "reuters": 1.0, "bloomberg": 1.0, "the wall street journal": 0.95, "wsj": 0.95,
    "financial times": 0.95, "cnbc": 0.85, "barron's": 0.85, "barrons": 0.85,
    "marketwatch": 0.8, "the motley fool": 0.55, "motley fool": 0.55, "seeking alpha": 0.6,
    "yahoo finance": 0.7, "associated press": 0.9, "ap": 0.9, "forbes": 0.7,
    "business insider": 0.65, "investor's business daily": 0.8, "zacks": 0.6,
    "benzinga": 0.6, "the street": 0.6, "thestreet": 0.6,
}


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def lexical_sentiment(text: str) -> Dict[str, Any]:
    """Return {label, score in [-1,1], probs{positive,neutral,negative}} for one text."""
    toks = _tokenize(text)
    low = " " + " ".join(toks) + " "
    pos = neg = 0
    # phrase hits first (bigrams matter: "guidance cut" is bearish)
    for phrase in _POS:
        if " " in phrase and f" {phrase} " in low:
            pos += 2
    for phrase in _NEG:
        if " " in phrase and f" {phrase} " in low:
            neg += 2
    for i, t in enumerate(toks):
        neg_ctx = i > 0 and toks[i - 1] in _NEGATORS
        if t in _POS:
            if neg_ctx:
                neg += 1
            else:
                pos += 1
        elif t in _NEG:
            if neg_ctx:
                pos += 1
            else:
                neg += 1
    total = pos + neg
    if total == 0:
        return {"label": "neutral", "score": 0.0,
                "probs": {"positive": 0.25, "neutral": 0.5, "negative": 0.25},
                "backend": "lexical"}
    score = (pos - neg) / total
    p_pos = pos / (total + 1)
    p_neg = neg / (total + 1)
    p_neu = max(0.0, 1 - p_pos - p_neg)
    label = "positive" if score > 0.15 else "negative" if score < -0.15 else "neutral"
    return {"label": label, "score": round(score, 3),
            "probs": {"positive": round(p_pos, 3), "neutral": round(p_neu, 3),
                      "negative": round(p_neg, 3)},
            "backend": "lexical"}


# ── Optional Hugging Face sentiment (lazy, single instance, never on import) ──
_HF_LOCK = threading.Lock()
_HF = {"tried": False, "pipe": None, "error": None, "model": None}


def _hf_pipe():
    """Load the HF sentiment pipeline once. Returns the pipeline or None. Any
    import/download/runtime failure is captured and turns into a lexical fallback."""
    cfg = CONFIG["sentiment"]
    if not cfg["enabled"]:
        return None
    if _HF["pipe"] is not None or _HF["tried"]:
        return _HF["pipe"]
    with _HF_LOCK:
        if _HF["tried"]:
            return _HF["pipe"]
        _HF["tried"] = True
        try:
            from transformers import pipeline  # type: ignore
            _HF["pipe"] = pipeline("text-classification", model=cfg["model"],
                                   top_k=None, truncation=True)
            _HF["model"] = cfg["model"]
        except Exception as e:  # noqa: BLE001
            _HF["error"] = str(e)[:160]
            _HF["pipe"] = None
    return _HF["pipe"]


def _hf_sentiment(texts: List[str]) -> Optional[List[Dict[str, Any]]]:
    pipe = _hf_pipe()
    if pipe is None:
        return None
    try:
        raw = pipe(texts)
        out = []
        for row in raw:
            probs = {r["label"].lower(): float(r["score"]) for r in row}
            p_pos = probs.get("positive", 0.0)
            p_neg = probs.get("negative", 0.0)
            p_neu = probs.get("neutral", max(0.0, 1 - p_pos - p_neg))
            score = p_pos - p_neg
            label = "positive" if score > 0.15 else "negative" if score < -0.15 else "neutral"
            out.append({"label": label, "score": round(score, 3),
                        "probs": {"positive": round(p_pos, 3), "neutral": round(p_neu, 3),
                                  "negative": round(p_neg, 3)},
                        "backend": "hf:" + (_HF["model"] or "?")})
        return out
    except Exception as e:  # noqa: BLE001 — never let a model error blank the panel
        _HF["error"] = str(e)[:160]
        return None


def sentiment(texts: List[str]) -> List[Dict[str, Any]]:
    """Batch sentiment for many texts. Routes through the FinBERT SERVICE (lazy,
    cached, timeout, health, GPU-aware) when it is enabled + available, else the
    lexical scorer. Order and length are preserved 1:1 with the input."""
    if not texts:
        return []
    try:
        import finbert_service as _fb   # lazy import avoids a circular dependency
        if _fb.enabled() and _fb.available():
            out = _fb.score(texts)
            if out and len(out) == len(texts):
                return out
    except Exception:  # noqa: BLE001 — never let the model layer blank the panel
        pass
    # Legacy in-module HF path (kept for back-compat) then lexical.
    hf = _hf_sentiment(texts)
    if hf is not None and len(hf) == len(texts):
        return hf
    return [lexical_sentiment(t) for t in texts]


# ── Aggregation: recency + source reliability + relevance + dedup ────────────

def _title_sim(a: str, b: str) -> float:
    ta, tb = set(_tokenize(a)), set(_tokenize(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _dedup(items: List[Dict[str, Any]], threshold: float = 0.72) -> List[Dict[str, Any]]:
    """Drop near-duplicate headlines (same story from many outlets) by Jaccard
    title similarity, keeping the most reliable source."""
    kept: List[Dict[str, Any]] = []
    for it in items:
        title = it.get("title", "")
        dup_of = None
        for k in kept:
            if _title_sim(title, k.get("title", "")) >= threshold:
                dup_of = k
                break
        if dup_of is None:
            kept.append(it)
        else:
            dup_of["_dupes"] = dup_of.get("_dupes", 0) + 1
            if _source_weight(it.get("source")) > _source_weight(dup_of.get("source")):
                dup_of["title"], dup_of["source"], dup_of["url"] = title, it.get("source"), it.get("url")
    return kept


def _source_weight(source: Optional[str]) -> float:
    if not source:
        return 0.5
    return _SOURCE_WEIGHT.get(source.strip().lower(), 0.5)


def _recency_weight(ts_iso: Optional[str], now: float, half_life_h: float = 48.0) -> float:
    """Exponential recency decay; undated headlines get a neutral 0.5."""
    if not ts_iso:
        return 0.5
    try:
        t = datetime.fromisoformat(ts_iso.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.5
    age_h = max(0.0, (now - t) / 3600.0)
    return math.pow(0.5, age_h / half_life_h)


def _count_ticker_like(text: str) -> int:
    """Count how many unique uppercase 2-5 letter words look like stock tickers."""
    exclude = {"THE", "AND", "FOR", "INC", "CORP", "LTD", "PLC", "LLC", "ETF"}
    tokens = set(re.findall(r'\b[A-Z]{2,5}\b', text))
    return len(tokens - exclude)


def _relevance(title: str, ticker: str, aliases: List[str]) -> float:
    """1.0 if the ticker/company is clearly the subject, lower if only mentioned."""
    title = title or ""
    low = title.lower()

    if ticker:
        # Multi-company discount
        if _count_ticker_like(title) >= 2:
            if ticker.upper() in set(re.findall(r'\b[A-Z]{2,5}\b', title)) or f"${ticker.upper()}" in title:
                return 0.7

        # $TICKER style mentions
        if f"${ticker.upper()}" in title:
            return 1.0

        # Word-boundary matching for ticker
        if re.search(r'\b' + re.escape(ticker.lower()) + r'\b', low):
            return 1.0

    for a in ([ticker] + (aliases or [])):
        if a and a.lower() in low:
            # subject if near the start of the headline, else a passing mention
            return 1.0 if low.find(a.lower()) < len(low) * 0.5 else 0.6
            
    return 0.2


def aggregate_news_sentiment(items: List[Dict[str, Any]], ticker: str,
                             aliases: Optional[List[str]] = None) -> Dict[str, Any]:
    """Turn raw headlines into ONE ticker-relevant sentiment reading.

    Each headline: model polarity × recency × source reliability × relevance.
    Duplicates collapsed first. Returns an explainable aggregate with the top
    contributing headlines — never a blind average of unrelated news.
    """
    aliases = aliases or []
    now = time.time()
    items = [it for it in items if it.get("title")]
    if not items:
        return {"state": "empty", "score": None, "label": "no_data", "n": 0,
                "backend": _current_backend()}
    deduped = _dedup(items)
    titles = [it["title"] for it in deduped]
    sents = sentiment(titles)

    backend = sents[0].get("backend", "lexical") if sents else "lexical"
    model_is_finbert = "finbert" in str(backend)
    num = den = 0.0
    agg_probs = {"positive": 0.0, "neutral": 0.0, "negative": 0.0}
    rel_sum = 0.0
    disagreements = 0
    scored = []
    for it, se in zip(deduped, sents):
        rel = _relevance(it["title"], ticker, aliases)
        if rel < 0.4:
            continue
        rw = _recency_weight(it.get("ts"), now)
        sw = _source_weight(it.get("source"))
        w = rel * rw * sw
        num += se["score"] * w
        den += w
        rel_sum += rel
        for k in agg_probs:
            agg_probs[k] += float((se.get("probs") or {}).get(k, 0.0)) * w
        # Lexical vs FinBERT disagreement (only meaningful when the model is FinBERT).
        lex_label = lexical_sentiment(it["title"])["label"] if model_is_finbert else se["label"]
        if model_is_finbert and lex_label != se["label"]:
            disagreements += 1
        scored.append({"title": it["title"][:140], "source": it.get("source"),
                       "url": it.get("url"), "ts": it.get("ts"),
                       "sentiment": se["label"], "polarity": se["score"],
                       "probs": se.get("probs"), "lexical_label": lex_label,
                       "disagreement": bool(model_is_finbert and lex_label != se["label"]),
                       "relevance": round(rel, 2), "recency_w": round(rw, 2),
                       "source_w": round(sw, 2), "weight": round(w, 3),
                       "dupes": it.get("_dupes", 0)})
    if den == 0:
        return {"state": "empty", "score": None, "label": "no_data", "n": 0,
                "backend": backend}
    score = num / den
    agg_probs = {k: round(v / den, 3) for k, v in agg_probs.items()}
    label = "bullish" if score > 0.12 else "bearish" if score < -0.12 else "neutral"
    n = len(scored)
    agree = sum(1 for s in scored if (s["polarity"] > 0) == (score > 0)) / n if n else 0
    conf = round(min(0.85, 0.3 + 0.4 * agree + min(0.15, n * 0.01)), 2)
    scored.sort(key=lambda s: s["weight"], reverse=True)
    pos = sum(1 for s in scored if s["sentiment"] == "positive")
    neg = sum(1 for s in scored if s["sentiment"] == "negative")
    return {"state": "ok", "score": round(score, 3), "label": label, "conf": conf,
            "n": n, "positive": pos, "negative": neg, "neutral": n - pos - neg,
            "dir": round(max(-0.5, min(0.5, score)), 3),
            "probs": agg_probs,                          # aggregate pos/neu/neg probabilities
            "avg_relevance": round(rel_sum / n, 2),
            "coverage": round(n / max(1, len(deduped)), 2),   # relevant / total considered
            "model_disagreements": disagreements,        # lexical vs FinBERT
            "top": scored[:6], "backend": backend,
            "dedup_removed": len(items) - len(deduped)}


def _current_backend() -> str:
    if CONFIG["sentiment"]["enabled"] and _HF["pipe"] is not None:
        return "hf:" + (_HF["model"] or CONFIG["sentiment"]["model"])
    if CONFIG["sentiment"]["enabled"] and _HF["error"]:
        return "lexical (hf failed: %s)" % _HF["error"][:60]
    return "lexical"


# ── Health / status ──────────────────────────────────────────────────────────

def status() -> Dict[str, Any]:
    fb_health = {}
    try:
        import finbert_service as _fb
        fb_health = _fb.health()
    except Exception:
        pass
    active = fb_health.get("model") if fb_health.get("loaded") else _current_backend()
    return {
        "version": REGISTRY_VERSION,
        "sentiment": {
            "enabled": CONFIG["sentiment"]["enabled"],
            "model": CONFIG["sentiment"]["model"],
            "backend": ("finbert" if fb_health.get("loaded") else _current_backend()),
            "active_model": active,
            "transformers_installed": _transformers_present(),
            "finbert": fb_health,          # lazy/loaded/device/load_ms/error/cache_size
        },
        "embeddings": {**CONFIG["embeddings"], "backend": "not wired (RAG offline)"},
        "forecast": {**CONFIG["forecast"], "backend": "baseline (see MODEL_EVALUATION.md)"},
        "at": datetime.now(timezone.utc).isoformat(),
    }


def _transformers_present() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("transformers") is not None
    except Exception:
        return False


if __name__ == "__main__":
    import json
    print(json.dumps(status(), indent=2))
    demo = [
        {"title": "Nvidia beats earnings and raises guidance, shares surge", "source": "Reuters", "ts": None},
        {"title": "Analyst downgrades Nvidia on valuation concerns", "source": "CNBC", "ts": None},
        {"title": "Nvidia unveils new AI chip at conference", "source": "The Motley Fool", "ts": None},
    ]
    print(json.dumps(aggregate_news_sentiment(demo, "NVDA", ["nvidia"]), indent=2))
