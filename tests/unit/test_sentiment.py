"""Unit tests for sentiment scoring and model registry."""
from __future__ import annotations

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "lab"))

import model_registry as mr

def test_lexical_sentiment_positive():
    """Test that a clear positive headline gets a positive label."""
    res = mr.lexical_sentiment('Apple beats earnings expectations')
    assert res['label'] == 'positive'
    assert 'score' in res
    assert 'probs' in res
    assert 'backend' in res
    assert res['backend'] == 'lexical'
    assert sum(res['probs'].values()) == pytest.approx(1.0)

def test_lexical_sentiment_negative():
    """Test that a clear negative headline gets a negative label."""
    res = mr.lexical_sentiment('Stock tumbles after earnings miss')
    assert res['label'] == 'negative'

def test_lexical_sentiment_neutral():
    """Test that a clear neutral headline gets a neutral label."""
    res = mr.lexical_sentiment('Company to report earnings on Friday')
    assert res['label'] == 'neutral'

def test_lexical_sentiment_negation():
    """Test that negations are handled correctly."""
    res = mr.lexical_sentiment('Not a good result')
    assert res['label'] != 'positive'

def test_lexical_sentiment_phrase_matching():
    """Test that multi-word phrases match correctly."""
    res = mr.lexical_sentiment('guidance cut')
    assert res['label'] == 'negative'

def test_relevance_exact_ticker_match():
    """Test that an exact ticker match in the first half of the string gets a high relevance."""
    assert mr._relevance("AAPL reports earnings", "AAPL", ["Apple"]) == 1.0

def test_relevance_alias_match():
    """Test that an alias match gets a decent relevance."""
    assert mr._relevance("Apple reports earnings", "AAPL", ["Apple"]) >= 0.6

def test_relevance_no_match():
    """Test that no match gets a low relevance."""
    assert mr._relevance("Some other company reports earnings", "AAPL", ["Apple"]) <= 0.4

def test_aggregate_news_sentiment_empty():
    """Test aggregating an empty list."""
    res = mr.aggregate_news_sentiment([], "AAPL")
    assert res['state'] == 'empty'

def test_aggregate_news_sentiment_single_positive():
    """Test aggregating a single positive headline."""
    items = [{"title": "Apple beats earnings expectations", "source": "Reuters", "ts": None}]
    res = mr.aggregate_news_sentiment(items, "AAPL", ["Apple"])
    assert res['state'] == 'ok'
    assert res['label'] == 'bullish'
    assert 'score' in res
    assert 'conf' in res
    assert 'top' in res

def test_aggregate_news_sentiment_mix():
    """Test aggregating a mix of headlines."""
    items = [
        {"title": "Apple beats earnings expectations", "source": "Reuters", "ts": None},
        {"title": "Apple stock tumbles on supply chain issues", "source": "Bloomberg", "ts": None}
    ]
    res = mr.aggregate_news_sentiment(items, "AAPL", ["Apple"])
    assert res['state'] == 'ok'
    assert 'score' in res
    assert 'conf' in res
    assert 'top' in res
    
def test_aggregate_news_sentiment_dedup():
    """Test that deduplication works during aggregation."""
    items = [
        {"title": "Apple beats earnings expectations", "source": "Reuters", "ts": None},
        {"title": "Apple beats earnings expectations", "source": "Bloomberg", "ts": None}
    ]
    res = mr.aggregate_news_sentiment(items, "AAPL", ["Apple"])
    assert res['dedup_removed'] == 1

def test_dedup_exact_titles():
    """Test deduping exact duplicate titles."""
    items = [
        {"title": "Same title here", "source": "Reuters"},
        {"title": "Same title here", "source": "Bloomberg"}
    ]
    res = mr._dedup(items)
    assert len(res) == 1

def test_dedup_different_titles():
    """Test that different titles are not deduped."""
    items = [
        {"title": "First title here", "source": "Reuters"},
        {"title": "Second title here", "source": "Bloomberg"}
    ]
    res = mr._dedup(items)
    assert len(res) == 2
