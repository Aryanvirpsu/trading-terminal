"""Scanner-performance + resilience tests (Prompt 2).

These are deterministic and OFFLINE — they exercise the circuit breaker, negative
cache, the shared options grader, the single-exchange decision-engine load, the
regime cache state and the preset universe builder WITHOUT hitting any live
provider. They guard the fixes from BASELINE.md against regression.

Run:  pytest tests/unit/test_scanner_perf.py -q
"""
from __future__ import annotations

import os
import sys
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in ("src", "dashboard", "lab"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

from tradingview_mcp.core.services import screener_provider as sp  # noqa: E402
from tradingview_mcp.core.services.options_grading import grade_contract  # noqa: E402


# ── Circuit breaker: opens, fast-fails, NEVER sleeps 15s in the request path ──

def _reset_breaker():
    sp._breaker_on_success()
    with sp._NEG_LOCK:
        sp._NEG_CACHE.clear()


def test_breaker_opens_after_threshold():
    _reset_breaker()
    assert sp._breaker_allows() is True
    for _ in range(sp._breaker_threshold()):
        sp._record_ta_failure()
    assert sp.breaker_status()["state"] == "open"
    assert sp._breaker_allows() is False           # fast-fail gate closed
    _reset_breaker()


def test_breaker_open_is_non_blocking():
    """The whole point of the rewrite: an open breaker must fast-fail, not sleep
    a 15s cooldown inside the request."""
    _reset_breaker()
    for _ in range(sp._breaker_threshold()):
        sp._record_ta_failure()
    t0 = time.perf_counter()
    for _ in range(50):
        sp._breaker_allows()
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.5, f"breaker gate blocked for {elapsed:.2f}s — must be instant"
    _reset_breaker()


def test_breaker_closes_on_success():
    _reset_breaker()
    for _ in range(sp._breaker_threshold()):
        sp._record_ta_failure()
    assert sp.breaker_status()["state"] == "open"
    sp._breaker_on_success()
    assert sp.breaker_status()["state"] == "closed"
    assert sp._breaker_allows() is True


def test_breaker_half_open_after_reset():
    _reset_breaker()
    for _ in range(sp._breaker_threshold()):
        sp._record_ta_failure()
    assert sp._breaker_allows() is False
    # Simulate the reset window elapsing (avoids a real sleep; reset has a 1s floor).
    with sp._BREAKER_LOCK:
        sp._BREAKER["opened_at"] = time.time() - (sp._breaker_reset_s() + 1.0)
    assert sp._breaker_allows() is True             # transitioned to half_open
    assert sp.breaker_status()["state"] == "half_open"
    _reset_breaker()


def test_negative_cache_roundtrip(monkeypatch):
    monkeypatch.setenv("TRADINGVIEW_MCP_NEG_TTL_S", "30")
    key = ("ta_multi_v1", "america", "1D", ("NASDAQ:TESTX",))
    assert sp._neg_get(key) is False
    sp._neg_set(key)
    assert sp._neg_get(key) is True
    with sp._NEG_LOCK:
        sp._NEG_CACHE.clear()


def test_throttled_error_is_runtimeerror():
    assert issubclass(sp.TVThrottledError, RuntimeError)


# ── Shared options grader: one formula, full fields, real rejections ─────────

def _c(**kw):
    base = {"strike": 100.0, "bid": 2.0, "ask": 2.1, "last_price": 2.05,
            "volume": 500, "open_interest": 1500, "implied_volatility": 0.3,
            "delta": 0.5}
    base.update(kw)
    return base


def test_grade_good_contract_is_A_and_tradeable():
    g = grade_contract(_c(), 100.0, "CALL", 10)
    assert g["grade"] in ("A", "B")
    assert g["tradeable"] is True
    # Every field the scanner promised is present and populated.
    for k in ("bid", "ask", "mid", "spread_dollars", "spread_pct", "volume",
              "open_interest", "liquidity_score", "grade", "breakeven", "dte"):
        assert g[k] is not None


def test_grade_zero_bid_rejected():
    g = grade_contract(_c(bid=0, ask=2.0), 100.0, "CALL", 10)
    assert g["tradeable"] is False
    assert "two-sided" in (g["rejection"] or "")


def test_grade_wide_spread_rejected():
    g = grade_contract(_c(bid=1.0, ask=3.0), 100.0, "CALL", 10)   # ~100% spread
    assert g["tradeable"] is False
    assert "wide spread" in (g["rejection"] or "")


def test_grade_low_oi_rejected():
    g = grade_contract(_c(open_interest=5, volume=2), 100.0, "CALL", 10)
    assert g["tradeable"] is False


def test_grade_missing_greeks_flagged():
    g = grade_contract(_c(delta=None, implied_volatility=None), 100.0, "CALL", 10)
    assert any("delta" in f for f in g["fails"])
    assert any("IV" in f for f in g["fails"])


def test_grade_never_returns_unexplained_none():
    """The BASELINE.md gap: a graded contract must never have spread/liquidity
    silently None — they are numbers or the contract is rejected with a reason."""
    g = grade_contract(_c(), 100.0, "CALL", 10)
    assert isinstance(g["liquidity_score"], (int, float))
    assert g["spread_pct"] is not None


# ── Decision engine: single-exchange hint (no NASDAQ<->NYSE double hit) ───────

def test_tv_exchange_hint():
    import decision_engine as de
    assert de._tv_exchange_hint("NASDAQ") == "NASDAQ"
    assert de._tv_exchange_hint("NYSE") == "NYSE"
    assert de._tv_exchange_hint("NYSE Arca") == "AMEX"
    assert de._tv_exchange_hint("Cboe BZX") == "AMEX"
    assert de._tv_exchange_hint("") == "NASDAQ"     # sane default, single venue


# ── Market regime: cache state is reported; warm serve is instant ────────────

def test_regime_cache_state_field():
    from tradingview_mcp.core.services import strategy_service as ss
    # Prime with a synthetic cached value so we don't hit the network.
    ss._REGIME_CACHE["r"] = (time.time(), {"regime": "TEST", "risk_appetite_score": 50})
    t0 = time.perf_counter()
    r = ss.market_regime()
    dt = time.perf_counter() - t0
    assert r["cache_state"] == "fresh"
    assert dt < 0.05, "warm regime must be instant"
    ss._REGIME_CACHE.pop("r", None)


# ── Preset scan universe from the security master ────────────────────────────

def test_scan_universe_presets():
    import research as R
    liquid = R.build_scan_universe("liquid")
    etf = R.build_scan_universe("etf")
    spec = R.build_scan_universe("small_cap_speculative")
    assert liquid["size"] >= 50 and all(":" in t for t in liquid["tickers"])
    assert etf["size"] >= 10
    # ETF preset should be dominated by ETPs (SPY/QQQ-style), not the base stocks.
    assert any(t.endswith(":SPY") or t.endswith(":QQQ") for t in etf["tickers"])
    assert spec["speculative"] is True
    assert liquid["speculative"] is False


def test_scan_universe_bounded():
    import research as R
    u = R.build_scan_universe("liquid", limit=40)
    assert u["size"] <= 40   # never deep-scans the whole 30k master


def test_to_tv_ticker_skips_otc_and_foreign():
    import research as R
    assert R._to_tv_ticker({"symbol": "AAPL", "exchange": "NASDAQ"}) == "NASDAQ:AAPL"
    assert R._to_tv_ticker({"symbol": "XXX", "exchange": "OTC"}) is None
    assert R._to_tv_ticker({"symbol": "NOKIA.HE", "exchange": "Helsinki"}) is None
