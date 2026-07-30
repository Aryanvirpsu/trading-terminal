"""FinBERT sentiment-service tests (Prompt 5A).

These NEVER load the real 440 MB model — a fake pipeline exercises the service's
batching / cache / timeout / fallback / entity-relevance logic deterministically,
and the disabled path is verified to stay lexical. The accuracy comparison itself
lives in `FINBERT_BENCHMARK.md` (run separately).

Run:  pytest tests/unit/test_finbert_sentiment.py -q
"""
from __future__ import annotations

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in ("lab",):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import finbert_service as fb  # noqa: E402
import model_registry as mr   # noqa: E402


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("SENTIMENT_MODEL_ENABLED", raising=False)
    fb._STATE.update({"pipe": None, "tried": False, "error": None, "device": None,
                      "load_ms": None, "loading": False})
    fb._CACHE.clear()
    yield
    fb._STATE.update({"pipe": None, "tried": False})
    fb._CACHE.clear()


class _FakePipe:
    """Returns FinBERT-shaped output ([{label,score},...] per text) without a model."""
    def __call__(self, texts):
        rows = []
        for t in texts:
            low = t.lower()
            if any(w in low for w in ("beat", "surge", "raises", "record", "jump")):
                rows.append([{"label": "positive", "score": 0.80}, {"label": "neutral", "score": 0.15},
                             {"label": "negative", "score": 0.05}])
            elif any(w in low for w in ("miss", "sue", "investigation", "plunge", "recall", "fine")):
                rows.append([{"label": "negative", "score": 0.80}, {"label": "neutral", "score": 0.15},
                             {"label": "positive", "score": 0.05}])
            else:
                rows.append([{"label": "neutral", "score": 0.80}, {"label": "positive", "score": 0.10},
                             {"label": "negative", "score": 0.10}])
        return rows


def _use_fake():
    fb._STATE.update({"pipe": _FakePipe(), "tried": True, "device": "cpu", "error": None})


# ── Availability + disabled path ─────────────────────────────────────────────

def test_transformers_available():
    assert fb.available() is True          # torch + transformers were installed


def test_disabled_returns_lexical():
    # No enable flag → no model load, lexical output.
    out = fb.score(["Company beats earnings and raises guidance"])
    assert out and out[0]["backend"] == "lexical"
    assert fb._STATE["pipe"] is None       # never loaded


def test_score_empty():
    assert fb.score([]) == []


def test_health_shape():
    h = fb.health()
    for k in ("model", "enabled", "available", "loaded", "device", "cache_size"):
        assert k in h
    assert h["model"] == "ProsusAI/finbert"


# ── FinBERT scoring via the fake pipe ────────────────────────────────────────

def test_finbert_scoring_and_probs(monkeypatch):
    _use_fake(); monkeypatch.setenv("SENTIMENT_MODEL_ENABLED", "true")
    out = fb.score(["Nvidia beats earnings and raises guidance"])
    assert out[0]["backend"] == "finbert"
    assert out[0]["label"] == "positive"
    assert abs(sum(out[0]["probs"].values()) - 1.0) < 0.05


def test_finbert_batch_order_preserved(monkeypatch):
    _use_fake(); monkeypatch.setenv("SENTIMENT_MODEL_ENABLED", "true")
    texts = ["Company beats estimates", "Firm to hold annual meeting", "SEC opens investigation"]
    out = fb.score(texts)
    assert [o["label"] for o in out] == ["positive", "neutral", "negative"]


def test_finbert_cache(monkeypatch):
    _use_fake(); monkeypatch.setenv("SENTIMENT_MODEL_ENABLED", "true")
    fb.score(["a cached headline"])
    assert "a cached headline" in fb._CACHE
    # second call served from cache (fake pipe would still work, but cache is hit)
    assert fb.score(["a cached headline"])[0]["backend"] == "finbert"


def test_finbert_timeout_falls_back_to_lexical(monkeypatch):
    class _Hang:
        def __call__(self, texts):
            import time; time.sleep(5); return []
    fb._STATE.update({"pipe": _Hang(), "tried": True})
    monkeypatch.setenv("SENTIMENT_MODEL_ENABLED", "true")
    out = fb.score(["a headline"], timeout=0.2, use_cache=False)
    assert out[0]["backend"] == "lexical(fallback)"      # timeout → lexical, never blocks


def test_hybrid_blends_and_flags_disagreement(monkeypatch):
    _use_fake(); monkeypatch.setenv("SENTIMENT_MODEL_ENABLED", "true")
    out = fb.score_hybrid(["Company beats earnings and raises guidance"])
    assert out[0]["backend"] == "hybrid"
    assert set(("finbert_label", "lexical_label", "disagreement", "probs")) <= set(out[0])
    assert abs(sum(out[0]["probs"].values()) - 1.0) < 0.05


def test_entity_aware_lowers_irrelevant(monkeypatch):
    _use_fake(); monkeypatch.setenv("SENTIMENT_MODEL_ENABLED", "true")
    out = fb.entity_aware_score(["An unrelated biotech announces trial results"], "NVDA", ["nvidia"])
    assert out[0]["relevance"] < 0.4
    assert out[0]["label"] == "neutral"                  # forced neutral when irrelevant


# ── Registry routing + aggregate fields + relevance ──────────────────────────

def test_registry_sentiment_lexical_when_disabled():
    out = mr.sentiment(["Company beats earnings"])
    assert out[0]["backend"] == "lexical"


def test_aggregate_has_new_fields_and_excludes_irrelevant():
    items = [
        {"title": "Nvidia beats earnings and raises guidance", "source": "Reuters", "ts": None},
        {"title": "Nvidia unveils a new AI chip", "source": "CNBC", "ts": None},
        {"title": "Analyst downgrades Intel on weak demand", "source": "Bloomberg", "ts": None},
    ]
    agg = mr.aggregate_news_sentiment(items, "NVDA", ["nvidia"])
    assert agg["state"] == "ok"
    for k in ("probs", "coverage", "avg_relevance", "model_disagreements"):
        assert k in agg
    assert agg["n"] == 2 and agg["coverage"] < 1.0        # Intel headline excluded
    assert abs(sum(agg["probs"].values()) - 1.0) < 0.05


def test_relevance_multicompany_discount():
    # A two-ticker headline mentioning the target is discounted below a solo mention.
    solo = mr._relevance("nvidia beats earnings", "NVDA", ["nvidia"])
    multi = mr._relevance("NVDA soars while INTC struggles", "NVDA", ["nvidia"])
    assert solo == 1.0
    assert multi <= 0.7


def test_aggregate_empty_is_no_data():
    agg = mr.aggregate_news_sentiment([], "AAPL", ["apple"])
    assert agg["state"] == "empty" and agg["label"] == "no_data"


# ── Benchmark harness metrics ────────────────────────────────────────────────

def test_benchmark_metrics_math():
    import sentiment_benchmark as sb
    preds = [{"label": "positive", "probs": {"positive": 0.9, "neutral": 0.05, "negative": 0.05}},
             {"label": "negative", "probs": {"positive": 0.05, "neutral": 0.05, "negative": 0.9}},
             {"label": "neutral", "probs": {"positive": 0.1, "neutral": 0.8, "negative": 0.1}},
             {"label": "positive", "probs": {"positive": 0.6, "neutral": 0.2, "negative": 0.2}}]
    golds = ["positive", "negative", "neutral", "negative"]   # last one wrong
    m = sb._metrics(preds, golds)
    assert m["n"] == 4 and m["accuracy"] == 0.75
    assert 0.0 <= m["macro_f1"] <= 1.0
    assert m["brier"] >= 0.0
