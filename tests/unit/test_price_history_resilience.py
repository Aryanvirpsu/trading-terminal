"""research.price_history() resilience — Yahoo primary / Finnhub secondary.

lab/providers.py has always declared the "candles" category as
primary=yahoo, secondary=finnhub (CATEGORIES["candles"]) but nothing ever
implemented that fallback: research.price_history() hardcoded yfinance with
no secondary, so a single Yahoo outage took the whole paper scanner dark
(strategies.scan() -> sector_map() -> price_history() for every sector ETF
and every candidate symbol). This file proves the fallback added to close
that gap behaves correctly in each direction, and that it never fabricates
or passes through bad data.

Fully offline: yfinance and finnhub_data are monkeypatched; no network call,
no API key required. Each test uses its own symbol to avoid the module's
real SWR cache carrying a result across tests in the same process.
"""
from __future__ import annotations

import os
import sys
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in ("src", "dashboard", "lab"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import research as R          # noqa: E402
import finnhub_data as FH      # noqa: E402


def _good_yahoo_points(n=60):
    return [{"t": f"2026-08-{(i % 28) + 1:02d}T00:00:00-04:00", "o": 100.0 + i,
             "h": 101.0 + i, "l": 99.0 + i, "c": 100.5 + i, "v": 1_000_000}
            for i in range(n)]


def _good_finnhub_payload(n=60):
    now = int(time.time())
    return {"c": [100.5 + i for i in range(n)], "o": [100.0 + i for i in range(n)],
            "h": [101.0 + i for i in range(n)], "l": [99.0 + i for i in range(n)],
            "v": [1_000_000] * n, "t": [now - (n - i) * 86400 for i in range(n)]}


# ══════════════════════════════════════════════════════════════════════════
# A — primary succeeds, fallback never called
# ══════════════════════════════════════════════════════════════════════════

def test_primary_succeeds_fallback_never_called(monkeypatch):
    monkeypatch.setattr(R, "_yahoo_hist", lambda sym, period, interval: _good_yahoo_points())
    called = {"finnhub": False}
    monkeypatch.setattr(R, "_finnhub_hist", lambda *a, **k: called.__setitem__("finnhub", True) or [])

    h = R.price_history("TESTA", "3M")
    assert h["state"] == "ok"
    assert len(h["points"]) == 60
    assert h["provenance"]["source"] == "Yahoo daily history"
    assert called["finnhub"] is False, "fallback must not be invoked when the primary succeeds"


# ══════════════════════════════════════════════════════════════════════════
# B — primary throttled/errors, fallback supplies valid bars
# ══════════════════════════════════════════════════════════════════════════

def test_primary_throttled_fallback_supplies_bars(monkeypatch):
    def _boom(sym, period, interval):
        raise RuntimeError("HTTP Error 429: Too Many Requests")
    monkeypatch.setattr(R, "_yahoo_hist", _boom)
    monkeypatch.setattr(FH, "candles", lambda sym, days=100, resolution="D": _good_finnhub_payload())

    h = R.price_history("TESTB", "3M")
    assert h["state"] == "ok", h
    assert len(h["points"]) == 60
    assert h["provenance"]["source"] == "Finnhub daily history (Yahoo fallback)"
    assert h["tried"] == ["yahoo:unavailable", "finnhub:ok"]
    # Every point still carries real OHLCV, not a placeholder.
    for p in h["points"]:
        assert p["c"] > 0 and p["h"] >= p["l"]


# ══════════════════════════════════════════════════════════════════════════
# C — primary and fallback both fail: explicit unavailable, nothing fabricated
# ══════════════════════════════════════════════════════════════════════════

def test_both_providers_fail_is_explicit_unavailable(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("no data")
    monkeypatch.setattr(R, "_yahoo_hist", _boom)
    monkeypatch.setattr(FH, "candles", lambda *a, **k: {"error": "no key"})

    h = R.price_history("TESTC", "3M")
    assert h["state"] == "empty"
    assert h.get("points") is None
    assert h["reason"] == "no price history from any provider"
    assert h["tried"] == ["yahoo:unavailable", "finnhub:unavailable"]


def test_scanner_bars_helper_returns_none_not_fabricated(monkeypatch):
    """The actual paper-scanner consumer (lab/paper/strategies.py:_bars())
    must see a clean None, never a partially-fabricated bar set, when both
    providers fail."""
    from paper import strategies as strat
    monkeypatch.setattr(R, "_yahoo_hist", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(FH, "candles", lambda *a, **k: {"error": "no key"})
    assert strat._bars("TESTC2") is None


# ══════════════════════════════════════════════════════════════════════════
# D — cache: an identical request within the window does not re-hit the provider
# ══════════════════════════════════════════════════════════════════════════

def test_repeated_request_within_cache_window_hits_provider_once(monkeypatch):
    calls = {"n": 0}

    def _count(sym, period, interval):
        calls["n"] += 1
        return _good_yahoo_points()
    monkeypatch.setattr(R, "_yahoo_hist", _count)

    h1 = R.price_history("TESTD", "3M")
    h2 = R.price_history("TESTD", "3M")
    assert h1["state"] == "ok" and h2["state"] == "ok"
    assert calls["n"] == 1, "second call within the 300s SWR window must reuse the cached bars"
    assert h2["provenance"]["cache_state"] in ("live", "refreshing")


# ══════════════════════════════════════════════════════════════════════════
# E — malformed fallback data is rejected, never reaches strategy math
# ══════════════════════════════════════════════════════════════════════════

def test_malformed_fallback_bars_are_dropped_not_passed_through(monkeypatch):
    monkeypatch.setattr(R, "_yahoo_hist", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))

    def _bad_payload(sym, days=100, resolution="D"):
        now = int(time.time())
        # one genuinely good bar, two malformed (negative close; high < low)
        return {"c": [100.0, -5.0, 50.0], "o": [99.0, 10.0, 51.0],
                "h": [101.0, 11.0, 49.0], "l": [98.0, 9.0, 52.0],
                "v": [1000, 1100, 1200], "t": [now - 172800, now - 86400, now]}
    monkeypatch.setattr(FH, "candles", _bad_payload)

    h = R.price_history("TESTE", "3M")
    assert h["state"] == "ok"
    assert len(h["points"]) == 1, "only the one structurally valid bar may survive"
    assert h["points"][0]["c"] == 100.0


def test_incomplete_fallback_payload_is_unavailable_not_empty_bars(monkeypatch):
    monkeypatch.setattr(R, "_yahoo_hist", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(FH, "candles", lambda *a, **k: {"c": [1.0], "t": [1]})  # missing o/h/l

    h = R.price_history("TESTE2", "3M")
    assert h["state"] == "empty"


# ══════════════════════════════════════════════════════════════════════════
# F — paper safety: provider resilience never touches broker/live-execution config
# ══════════════════════════════════════════════════════════════════════════

def test_no_live_execution_path_introduced():
    assert os.environ.get("ROBINHOOD_TRADING_ENABLED", "false").lower() == "false"
    assert os.environ.get("BROKER_PROVIDER", "none").lower() == "none"
    import inspect
    src = inspect.getsource(R.price_history) + inspect.getsource(R._finnhub_hist)
    for banned in ("submit_entry", "place_order", "robinhood", "BROKER_PROVIDER"):
        assert banned not in src, f"price-history resilience code must never touch execution: found {banned!r}"
