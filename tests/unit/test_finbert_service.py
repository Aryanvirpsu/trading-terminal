"""Unit tests for the FinBERT sentiment service."""
from __future__ import annotations

import os
import sys
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "lab"))

import finbert_service as fb

def test_enabled_default(monkeypatch):
    """Test that the model is disabled by default."""
    monkeypatch.delenv("SENTIMENT_MODEL_ENABLED", raising=False)
    assert fb.enabled() is False

def test_enabled_true(monkeypatch):
    """Test that the model can be enabled."""
    monkeypatch.setenv("SENTIMENT_MODEL_ENABLED", "true")
    assert fb.enabled() is True

def test_enabled_false(monkeypatch):
    """Test that the model can be disabled explicitly."""
    monkeypatch.setenv("SENTIMENT_MODEL_ENABLED", "false")
    assert fb.enabled() is False

def test_enabled_1(monkeypatch):
    """Test that the model can be enabled with '1'."""
    monkeypatch.setenv("SENTIMENT_MODEL_ENABLED", "1")
    assert fb.enabled() is True

def test_available():
    """Test that the model dependencies are available."""
    assert fb.available() is True

def test_health_structure():
    """Test that the health endpoint returns the correct structure."""
    res = fb.health()
    assert set(res.keys()) == {"model", "enabled", "available", "loaded", "loading", "device", "load_ms", "error", "cache_size"}

def test_lexical_fallback():
    """Test the lexical fallback helper."""
    res = fb._lexical(["Apple beats earnings"])
    assert len(res) == 1
    assert res[0]["backend"] == "lexical"
    assert "label" in res[0]
    assert "score" in res[0]

def test_score_disabled(monkeypatch):
    """Test scoring when the model is disabled."""
    monkeypatch.setenv("SENTIMENT_MODEL_ENABLED", "false")
    res = fb.score(["Apple beats earnings"])
    assert len(res) == 1
    assert res[0]["backend"].startswith("lexical")
    assert "label" in res[0]
    assert "score" in res[0]
    assert "probs" in res[0]

@pytest.mark.slow
def test_score_force():
    """Test scoring with force=True to load the actual model."""
    res = fb.score(["Nvidia beats earnings expectations", "Company goes bankrupt"], force=True)
    assert len(res) == 2
    assert res[0]["backend"] == "finbert"
    
    # Check probabilities structure
    assert "positive" in res[0]["probs"]
    assert "neutral" in res[0]["probs"]
    assert "negative" in res[0]["probs"]
    
    # Known positive should not be negative
    assert res[0]["label"] in ("positive", "neutral")
    
    # Known negative should not be positive
    assert res[1]["label"] in ("negative", "neutral")

@pytest.mark.slow
def test_score_hybrid_force():
    """Test hybrid scoring with force=True."""
    res = fb.score_hybrid(["Market remains steady"], force=True)
    assert len(res) == 1
    assert res[0]["backend"] == "hybrid"
    assert "finbert_label" in res[0]
    assert "lexical_label" in res[0]
    assert "disagreement" in res[0]

@pytest.mark.slow
def test_cache_behavior():
    """Test that the cache speeds up subsequent requests."""
    text = "Some random headline about earnings report that we want to cache"
    
    # First call - cache miss
    t0 = time.perf_counter()
    res1 = fb.score([text], force=True)
    t1 = time.perf_counter()
    
    # Second call - cache hit
    t2 = time.perf_counter()
    res2 = fb.score([text], force=True)
    t3 = time.perf_counter()
    
    assert res1 == res2
    assert (t3 - t2) < (t1 - t0)
