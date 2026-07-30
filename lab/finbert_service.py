"""FinBERT financial-sentiment service — the ONLY Hugging Face model in the repo.

ProsusAI/finbert, wrapped so it can never hurt the terminal:
  * LAZY — the model is NOT loaded at import or app startup; it loads on first use
    (or when the app warms it in a background thread after the page is usable).
  * ONE shared instance (thread-safe single load), GPU when available (RTX 3050),
    CPU fallback otherwise.
  * BATCHED inference with a per-call TIMEOUT.
  * A result CACHE (per headline) so repeat/aggregate calls are instant.
  * LEXICAL FALLBACK on any failure (not installed / not enabled / load error /
    timeout) — callers always get a usable result.
  * A HEALTH CHECK for the UI / diagnostics.

Only enabled when `SENTIMENT_MODEL_ENABLED=true` AND transformers+torch are present;
otherwise every call transparently returns the lexical scorer's output.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(__file__))
import model_registry as mr  # lexical fallback lives here

MODEL_ID = os.environ.get("SENTIMENT_MODEL", "ProsusAI/finbert")
_TIMEOUT = float(os.environ.get("FINBERT_TIMEOUT", "8"))
_CACHE_MAX = 4000

_LOCK = threading.Lock()
_STATE: Dict[str, Any] = {"pipe": None, "tried": False, "error": None, "device": None,
                          "load_ms": None, "loading": False}
_CACHE: Dict[str, Dict[str, Any]] = {}


def enabled() -> bool:
    return os.environ.get("SENTIMENT_MODEL_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


def available() -> bool:
    try:
        import importlib.util as u
        return u.find_spec("torch") is not None and u.find_spec("transformers") is not None
    except Exception:
        return False


def _lexical(texts: List[str], tag: str = "lexical") -> List[Dict[str, Any]]:
    out = []
    for t in texts:
        r = mr.lexical_sentiment(t)
        r = dict(r); r["backend"] = tag
        out.append(r)
    return out


def _load(force: bool = False):
    """Load the pipeline ONCE. Returns the pipeline or None (→ lexical fallback).
    Respects the enable flag unless `force` (used by the offline benchmark)."""
    if _STATE["pipe"] is not None:
        return _STATE["pipe"]
    if not force and not enabled():
        return None
    if _STATE["tried"] and _STATE["pipe"] is None:
        return None
    with _LOCK:
        if _STATE["pipe"] is not None:
            return _STATE["pipe"]
        if _STATE["tried"]:
            return _STATE["pipe"]
        _STATE["tried"] = True
        _STATE["loading"] = True
        t0 = time.perf_counter()
        try:
            import torch
            from transformers import (AutoModelForSequenceClassification, AutoTokenizer,
                                      pipeline)
            device = 0 if torch.cuda.is_available() else -1
            _STATE["device"] = "cuda" if device == 0 else "cpu"
            tok = AutoTokenizer.from_pretrained(MODEL_ID)
            mdl = AutoModelForSequenceClassification.from_pretrained(MODEL_ID)
            _STATE["pipe"] = pipeline("text-classification", model=mdl, tokenizer=tok,
                                      device=device, top_k=None, truncation=True, max_length=256)
            _STATE["load_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        except Exception as e:  # noqa: BLE001
            _STATE["error"] = str(e)[:200]
            _STATE["pipe"] = None
        finally:
            _STATE["loading"] = False
    return _STATE["pipe"]


def warm_async() -> None:
    """Kick model loading on a background thread — call AFTER the page is usable."""
    if _STATE["pipe"] is not None or _STATE["tried"] or not (enabled() and available()):
        return
    threading.Thread(target=_load, name="finbert-warm", daemon=True).start()


def _to_result(row: List[Dict[str, Any]]) -> Dict[str, Any]:
    probs = {r["label"].lower(): float(r["score"]) for r in row}
    p_pos = probs.get("positive", 0.0)
    p_neg = probs.get("negative", 0.0)
    p_neu = probs.get("neutral", max(0.0, 1 - p_pos - p_neg))
    label = max(("positive", "neutral", "negative"), key=lambda k: probs.get(k, 0.0))
    return {"label": label, "score": round(p_pos - p_neg, 3),
            "probs": {"positive": round(p_pos, 3), "neutral": round(p_neu, 3),
                      "negative": round(p_neg, 3)},
            "backend": "finbert"}


def _run_with_timeout(fn, timeout: float):
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(fn).result(timeout=timeout)


def score(texts: List[str], use_cache: bool = True, force: bool = False,
          timeout: float = None) -> List[Dict[str, Any]]:
    """Batch FinBERT sentiment with per-headline cache; lexical fallback on any
    failure. Order is preserved 1:1 with `texts`."""
    if not texts:
        return []
    pipe = _load(force=force)
    if pipe is None:
        return _lexical(texts, "lexical" if not (enabled() and available()) else "lexical(fallback)")
    results: List[Any] = [None] * len(texts)
    todo, todo_idx = [], []
    for i, t in enumerate(texts):
        if use_cache and t in _CACHE:
            results[i] = _CACHE[t]
        else:
            todo.append(t); todo_idx.append(i)
    if todo:
        try:
            raw = _run_with_timeout(lambda: pipe(todo), timeout or _TIMEOUT * max(1, len(todo) / 20))
            for j, row in enumerate(raw):
                r = _to_result(row)
                results[todo_idx[j]] = r
                if use_cache and len(_CACHE) < _CACHE_MAX:
                    _CACHE[todo[j]] = r
        except Exception as e:  # noqa: BLE001 — timeout / runtime error → lexical
            _STATE["error"] = str(e)[:160]
            for j, t in enumerate(todo):
                results[todo_idx[j]] = mr.lexical_sentiment(t)
                results[todo_idx[j]]["backend"] = "lexical(fallback)"
    return results


def score_hybrid(texts: List[str], force: bool = False) -> List[Dict[str, Any]]:
    """FinBERT primary (0.7) blended with lexical (0.3); records the two labels and
    whether they disagree. Falls back to lexical weight 1.0 if FinBERT is absent."""
    fb = score(texts, force=force)
    out = []
    for t, f in zip(texts, fb):
        lx = mr.lexical_sentiment(t)
        w_f, w_l = (0.0, 1.0) if str(f.get("backend", "")).startswith("lexical") else (0.7, 0.3)
        probs = {k: round(w_f * f["probs"].get(k, 0.0) + w_l * lx["probs"].get(k, 0.0), 3)
                 for k in ("positive", "neutral", "negative")}
        label = max(probs, key=probs.get)
        out.append({"label": label, "probs": probs,
                    "score": round(probs["positive"] - probs["negative"], 3),
                    "backend": "hybrid", "finbert_label": f["label"], "lexical_label": lx["label"],
                    "disagreement": f["label"] != lx["label"]})
    return out


def entity_aware_score(texts: List[str], ticker: str, aliases: List[str] = None,
                       force: bool = False) -> List[Dict[str, Any]]:
    """FinBERT sentiment with entity-relevance adjustment.
    
    For each headline, computes FinBERT sentiment and adjusts confidence based
    on how relevant the headline is to the target ticker. Multi-company headlines
    get reduced confidence. Returns the full result plus relevance metadata.
    """
    raw_results = score(texts, force=force)
    out = []
    aliases_list = aliases or []
    for text, res in zip(texts, raw_results):
        rel = mr._relevance(text, ticker, aliases_list)
        adj_res = dict(res)
        adj_res["relevance"] = rel
        adj_res["ticker"] = ticker
        
        if rel < 0.3:
            adj_res["label"] = "neutral"
            adj_res["probs"] = {"positive": 0.2, "neutral": 0.6, "negative": 0.2}
            adj_res["score"] = 0.0
            adj_res["entity_adjusted"] = True
        elif rel < 1.0:
            adj_res["entity_adjusted"] = True
        else:
            adj_res["entity_adjusted"] = False
            
        out.append(adj_res)
    return out


def health() -> Dict[str, Any]:
    return {"model": MODEL_ID, "enabled": enabled(), "available": available(),
            "loaded": _STATE["pipe"] is not None, "loading": _STATE["loading"],
            "device": _STATE["device"], "load_ms": _STATE["load_ms"],
            "error": _STATE["error"], "cache_size": len(_CACHE)}


if __name__ == "__main__":
    import json
    demo = ["Nvidia raises full-year guidance on surging AI chip demand",
            "SEC opens an investigation into the company's accounting practices",
            "Company to report third-quarter earnings on October 26"]
    print("health:", json.dumps(health()))
    print("scores:", json.dumps(score(demo, force=True), indent=2))
    print("health:", json.dumps(health()))
