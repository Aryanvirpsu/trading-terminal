"""End to end: the time on an execution quote is when the PROVIDER observed the price, never when we fetched it.

Why it matters: a stale price stamped with fetch time looks fresh to every freshness check downstream (the entry
gate, `quote_age_s_max`, and any age limit on a stop fill). In production TradingView, which is served from a 10 minute
in-process analysis cache, supplied about 40 % of the logged decision quotes while stamping `time.time()`.
"""
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for folder in ("src", "dashboard", "lab"):
    sys.path.insert(0, str(ROOT / folder))

import finnhub_data
import research
from paper import workflow


@pytest.fixture(autouse=True)
def feeds(monkeypatch):
    from tradingview_mcp.core.services import strategy_service as strategy
    from tradingview_mcp.core.services import yahoo_finance_service as yahoo
    monkeypatch.setenv("TRADINGVIEW_ENABLED", "true")
    monkeypatch.setattr(research, "price_history", lambda *a: {"state": "empty"})     # quote-only: no bar range
    monkeypatch.setattr(finnhub_data, "quote", lambda symbol: {"error": "no key"})
    # a cached TradingView analysis: it has a price but NO observation time
    monkeypatch.setattr(strategy, "_analyze_cached", lambda *a: {
        "timestamp": "real-time", "price_data": {"current_price": 35.0}})
    return yahoo


def yahoo_says(monkeypatch, yahoo, price, observed):
    monkeypatch.setattr(yahoo, "get_price", lambda symbol: {
        "price": price, "source_timestamp": observed, "timestamp": "fetched just now"})


def test_the_quote_carries_the_providers_observation_time_not_the_fetch_time(monkeypatch, feeds):
    observed = time.time() - 600                        # the last trade Yahoo reports is ten minutes old
    yahoo_says(monkeypatch, feeds, 40.0, observed)
    q = workflow.quote_for("BAC")
    assert q is not None and q.provider == "yahoo" and q.last == 40.0
    assert q.source_ts == observed
    assert time.time() - q.source_ts >= 599             # so an age limit can see that it is stale


def test_a_cached_tradingview_price_cannot_win_with_a_fetch_time_stamp(monkeypatch, feeds):
    yahoo_says(monkeypatch, feeds, 40.0, time.time() - 5)
    q = workflow.quote_for("BAC")
    assert q.provider == "yahoo" and q.last == 40.0     # before the fix the later fetch (TradingView, 35.0) won


@pytest.mark.parametrize("observed", [None, 0, float("nan"), "invalid"])
def test_no_provider_observation_time_means_no_executable_quote(monkeypatch, feeds, observed):
    yahoo_says(monkeypatch, feeds, 40.0, observed)      # Yahoo has a price but no time; TradingView never has one
    assert workflow.quote_for("BAC") is None


def test_tradingview_alone_is_not_an_executable_quote(monkeypatch, feeds):
    monkeypatch.setattr(feeds, "get_price", lambda symbol: {"error": "down"})
    assert workflow.quote_for("BAC") is None
