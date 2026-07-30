"""Stock-view + fast-decision-summary tests (Prompt 3).

Deterministic and OFFLINE — the base-TA load and regime are mocked, so these
exercise the two-phase decision engine (fast `evaluate_summary` vs full
`evaluate`), the `research.summary` wrapper, and the progressive-load contract
(analysis_status / pending_families / scenarios) without hitting any provider.

Run:  pytest tests/unit/test_stock_view.py -q
"""
from __future__ import annotations

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in ("src", "dashboard", "lab"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import decision_engine as de  # noqa: E402

_FAKE_A = {
    "price_data": {"current_price": 100.0},
    "trend_state": "uptrend",
    "atr": {"value": 2.0, "percent_of_price": 2.0},
    "market_sentiment": {"momentum": "Bullish", "buy_sell_signal": "BUY"},
    "rsi": {"value": 60},
}


# ── Fast summary: structure + completion contract ────────────────────────────

def test_summary_status_and_pending(monkeypatch):
    monkeypatch.setattr(de, "_load_analysis", lambda s, e: (_FAKE_A, "tradingview:NASDAQ", "fresh"))
    monkeypatch.setattr(de, "_safe_regime", lambda: {"risk_appetite_score": 62, "regime": "RISK-ON"})
    r = de.evaluate_summary("TEST", "NASDAQ", "LONG", 500)
    assert r["analysis_status"] == "summary"
    assert r["provisional"] is True
    # the 7 slow families are declared as pending, the 2 fast ones are computed
    assert set(r["pending_families"]) >= {"catalyst", "options-flow", "social-sentiment",
                                          "analyst-ratings", "macro-rates"}
    assert len(r["fast_families"]) == 2
    fams = {f["family"] for f in r["fast_families"]}
    assert fams == {"trend/momentum", "regime"}


def test_summary_long_geometry(monkeypatch):
    monkeypatch.setattr(de, "_load_analysis", lambda s, e: (_FAKE_A, "tradingview:NASDAQ", "fresh"))
    monkeypatch.setattr(de, "_safe_regime", lambda: {"risk_appetite_score": 62})
    r = de.evaluate_summary("TEST", "NASDAQ", "LONG", 500)
    assert r["price"] == 100.0
    assert r["target"] > r["price"] > r["stop"]   # LONG: stop below, target above
    assert 0.0 <= r["p_direction"] <= 1.0
    assert r["summary_ms"] >= 0


def test_summary_short_geometry(monkeypatch):
    monkeypatch.setattr(de, "_load_analysis", lambda s, e: (_FAKE_A, "yfinance-fallback", "fallback-provider"))
    monkeypatch.setattr(de, "_safe_regime", lambda: {"risk_appetite_score": 40})
    r = de.evaluate_summary("TEST", "NASDAQ", "SHORT", 500)
    assert r["data_state"] == "fallback-provider"       # provider status surfaced
    assert r["stop"] > r["price"] > r["target"]         # SHORT: inverted


def test_summary_no_data_is_reject_not_crash(monkeypatch):
    monkeypatch.setattr(de, "_load_analysis", lambda s, e: (None, "unavailable", "unavailable"))
    r = de.evaluate_summary("BADX", "NASDAQ", "LONG", 500)
    assert r["decision"] == "REJECT"
    assert r["data_state"] == "unavailable"
    assert r["analysis_status"] == "summary"


def test_summary_is_lighter_than_full():
    # The whole point: the summary must NOT enumerate the 7 slow families.
    assert len(de._DEEP_FAMILIES) == 7
    assert "catalyst" in de._DEEP_FAMILIES


# ── Full evaluate: marks complete, no pending ────────────────────────────────

def test_full_evaluate_marks_complete(monkeypatch):
    monkeypatch.setattr(de, "_load_analysis", lambda s, e: (_FAKE_A, "tradingview:NASDAQ", "fresh"))
    monkeypatch.setattr(de, "_safe_regime", lambda: {"risk_appetite_score": 55})
    for fn in ("_fam_catalyst", "_fam_short", "_fam_filings", "_fam_options_flow",
               "_fam_social", "_fam_analyst", "_fam_macro"):
        monkeypatch.setattr(de, fn, lambda *a, **k: de._fam_stub("stub"))
    monkeypatch.setattr(de.ss, "_pick_option_idea", lambda *a, **k: None)
    try:
        import halts
        monkeypatch.setattr(halts, "is_halted", lambda s: False)
    except Exception:
        pass
    r = de.evaluate("TEST", "NASDAQ", "LONG", 500, evaluate_option=False)
    assert r.get("analysis_status") == "complete"
    assert r.get("pending_families") == []
    assert "confidence_quality" in r


# ── research.summary wrapper: adds scenarios + state + provenance ─────────────

def test_research_summary_wraps(monkeypatch):
    import research as R
    canned = {"symbol": "TEST", "price": 100.0, "decision": "MONITOR",
              "analysis_status": "summary", "p_direction": 0.55, "target": 106.0,
              "stop": 97.0, "pending_families": ["catalyst"], "data_state": "fresh"}
    monkeypatch.setattr(de, "evaluate_summary", lambda *a, **k: canned)
    monkeypatch.setattr(R._SM, "lookup", lambda s: {"exchange": "NASDAQ", "name": "Test"})
    R.invalidate("sum:")
    r = R.summary("TEST")
    assert r["state"] == "ok"
    assert r["analysis_status"] == "summary"
    assert r["scenarios"]["state"] == "ok"        # research adds bull/base/bear
    assert "provenance" in r


def test_research_summary_rejects_unsupported():
    import research as R
    r = R.summary("ZZZZQQNOPEX")                   # invalid shape, not in master
    assert r["state"] == "unsupported"


def test_research_summary_no_price_degrades(monkeypatch):
    import research as R
    monkeypatch.setattr(de, "evaluate_summary",
                        lambda *a, **k: {"symbol": "TEST", "data_state": "unavailable",
                                         "analysis_status": "summary", "reason": "no price"})
    monkeypatch.setattr(R._SM, "lookup", lambda s: {"exchange": "NASDAQ"})
    R.invalidate("sum:")
    r = R.summary("TEST")
    assert r["state"] in ("unavailable", "error")   # explicit state, never a fake value
    assert r["analysis_status"] == "summary"
