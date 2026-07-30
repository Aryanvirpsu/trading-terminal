"""Multi-provider engine + consensus + coverage + cache-discipline + sector-map tests
(Prompt 5B). All OFFLINE and deterministic — providers are monkeypatched, no network.

Covers the required outage/agreement scenarios:
  * TradingView completely offline / disabled   -> test_tv_disabled_*, test_evaluate_*
  * Yahoo unavailable                            -> test_price_consensus_yahoo_down
  * Conflicting prices                           -> test_consensus_conflict
  * Stale cache (source-timestamp based)         -> test_cache_policy_stale_display_only
  * Missing options                              -> test_momentum_ok_without_options
  * Missing social data                          -> test_missing_social_little_effect
  * Multiple providers agreeing                  -> test_consensus_agree
  * Sector classification missing                -> test_sector_unknown_key
  * Sector-map rendering                         -> test_sector_map_shape
"""
from __future__ import annotations

import os
import sys
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in ("src", "dashboard", "lab"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import providers as P          # noqa: E402
import data_quality as DQ      # noqa: E402
import cache_policy as CP      # noqa: E402
import freshness as FR         # noqa: E402


# ── 1. Feature flag: TradingView is opt-in and demoted ───────────────────────

def test_tv_flag_default_and_off(monkeypatch):
    monkeypatch.delenv("TRADINGVIEW_ENABLED", raising=False)
    assert P.tv_enabled() is True
    monkeypatch.setenv("TRADINGVIEW_ENABLED", "false")
    assert P.tv_enabled() is False
    inv = P.provider_inventory()
    assert inv["tradingview_role"] == "optional technical-signal confirmation only"
    # TradingView is never a primary or secondary in the spec table
    for c in P.CATEGORIES.values():
        assert c.primary != "tradingview" and c.secondary != "tradingview"


def test_matrix_covers_all_categories():
    rows = P.matrix_rows()
    names = {r["category"] for r in rows}
    assert {"price", "candles", "fundamentals", "options", "news",
            "analyst", "filings", "macro", "sector"} <= names
    for r in rows:
        assert r["primary"] and "freshness_limit_s" in r and "confidence_weight" in r


# ── 2. Consensus: agree / conflict / provider-down / all-fail ────────────────

def test_consensus_agree():
    now = time.time()
    r = P.consensus([P.Reading("finnhub", 100.0, now - 30),
                     P.Reading("yahoo", 100.3, now - 5)], category="price", tol=0.01)
    assert r["value"] == 100.3 and r["provider"] == "yahoo"     # freshest wins
    assert r["agreeing_providers"] == ["finnhub"]
    assert r["conflicting_providers"] == []
    assert r["state"] == "ok" and r["confidence"] >= 0.9


def test_consensus_conflict():
    now = time.time()
    r = P.consensus([P.Reading("finnhub", 100.0, now - 30),
                     P.Reading("yahoo", 112.0, now - 5)], category="price", tol=0.01)
    assert r["state"] == "conflict"
    assert "finnhub" in r["conflicting_providers"]
    assert r["confidence"] < 0.8                                 # disagreement penalised
    # never silently overwrite — the losing provider is surfaced, not dropped
    assert r["value"] is not None


def test_price_consensus_yahoo_down(monkeypatch):
    # Yahoo unavailable, Finnhub valid -> Finnhub carries; no crash, decent confidence.
    monkeypatch.setattr(P, "_yahoo_price", lambda s: P.Reading("yahoo", None, ok=False, error="timeout"))
    monkeypatch.setattr(P, "_finnhub_price", lambda s: P.Reading("finnhub", 55.0, time.time() - 10))
    monkeypatch.setenv("TRADINGVIEW_ENABLED", "false")
    r = P.price_consensus("XYZ")
    assert r["value"] == 55.0 and r["provider"] == "finnhub"
    assert r["tradingview_used"] is False and r["state"] == "ok"


def test_consensus_all_fail():
    r = P.consensus([P.Reading("finnhub", None, ok=False, error="no key"),
                     P.Reading("yahoo", None, ok=False, error="timeout")], category="price")
    assert r["value"] is None and r["state"] == "unavailable"
    assert r["confidence"] == 0.0 and r["tried"]


def test_tv_failure_does_not_lower_quality_when_others_valid(monkeypatch):
    monkeypatch.setenv("TRADINGVIEW_ENABLED", "true")
    monkeypatch.setattr(P, "_yahoo_price", lambda s: P.Reading("yahoo", 200.0, time.time() - 5))
    monkeypatch.setattr(P, "_finnhub_price", lambda s: P.Reading("finnhub", 200.2, time.time() - 20))
    monkeypatch.setattr(P, "_tv_price", lambda s, e="NASDAQ": P.Reading("tradingview", None, ok=False, error="429"))
    r = P.price_consensus("ABC")
    assert r["value"] in (200.0, 200.2)
    assert r["confidence"] >= 0.9          # a failed TV confirm must NOT tank confidence


# ── 3. Coverage-based data quality (strategy-aware) ──────────────────────────

def _full_momentum_cov(with_options=True):
    cov = {
        "price": DQ.category_coverage("price", {"price": 100.0}, confidence=0.9),
        "candles": DQ.category_coverage("candles", {"closes": [1], "highs": [1], "lows": [1]}, confidence=0.9),
        "news": DQ.category_coverage("news", {"headlines": [1]}, confidence=0.6),
        "analyst": DQ.category_coverage("analyst", {"recommendation": 1}, confidence=0.6),
        "sector": DQ.category_coverage("sector", {"sector": "Tech"}, confidence=0.7),
    }
    cov["options"] = DQ.category_coverage(
        "options", {"bid": 1.0, "ask": 1.1, "open_interest": 900} if with_options else {}, confidence=0.7)
    return cov


def test_momentum_ok_without_options():
    m = DQ.assess(_full_momentum_cov(with_options=False), profile="momentum")
    assert m["sufficient"] is True          # missing options must NOT fail a momentum stock
    assert "options" not in m["blocking_gaps"]


def test_options_profile_requires_option_chain():
    o = DQ.assess(_full_momentum_cov(with_options=False), profile="options")
    assert o["sufficient"] is False
    assert "options" in o["blocking_gaps"]
    assert "options" in o["missing_fields"]  # exact missing fields reported


def test_analysis_needs_fundamentals():
    cov = _full_momentum_cov()
    cov.pop("options", None)
    a = DQ.assess(cov, profile="analysis")   # fundamentals never supplied -> blocking
    assert a["sufficient"] is False and "fundamentals" in a["blocking_gaps"]


def test_missing_social_little_effect():
    # social is IGNORED in momentum — dropping it barely moves the score.
    base = DQ.assess(_full_momentum_cov(), profile="momentum")["overall"]
    with_social = _full_momentum_cov()
    with_social["social"] = DQ.category_coverage("social", {"messages": [1]}, confidence=0.5)
    withs = DQ.assess(with_social, profile="momentum")["overall"]
    assert abs(withs - base) < 0.02


def test_stale_coverage_is_degraded():
    fresh = DQ.category_coverage("price", {"price": 100.0}, confidence=0.9)["coverage"]
    stale = DQ.category_coverage("price", {"price": 100.0}, stale=True, confidence=0.9)["coverage"]
    assert stale < fresh


# ── 4. Cache discipline: valid-decision vs display-only (source-ts based) ─────

def test_cache_policy_valid_when_fresh(monkeypatch):
    monkeypatch.delenv("DECISION_ALLOW_STALE", raising=False)
    rec = CP.classify("price", time.time() - 60)      # price limit 900s
    assert rec["tier"] == "valid-decision" and rec["decision_valid"] is True


def test_cache_policy_stale_display_only(monkeypatch):
    monkeypatch.delenv("DECISION_ALLOW_STALE", raising=False)
    rec = CP.classify("price", time.time() - 5000)    # past the 900s limit
    assert rec["tier"] == "display-only"
    assert rec["decision_valid"] is False             # stale cannot drive a NEW decision


def test_cache_policy_stale_override(monkeypatch):
    monkeypatch.setenv("DECISION_ALLOW_STALE", "true")
    rec = CP.classify("price", time.time() - 5000)
    assert rec["tier"] == "display-only" and rec["decision_valid"] is True   # opt-in


def test_cache_policy_uses_source_not_cache_time():
    # A value re-inserted "now" but whose SOURCE is old is still display-only.
    old_source = time.time() - 5000
    assert CP.decision_valid("price", old_source) is False


def test_provenance_log_and_summary():
    CP._PROV.clear()
    CP.log("price", "yahoo", time.time() - 30)
    CP.log("price", "finnhub", time.time() - 9000)     # stale
    s = CP.summary()
    assert s["total"] == 2 and s["by_tier"]["display-only"] >= 1


# ── 5. Full evaluate with TradingView OFF still works ────────────────────────

_FAKE_A = {"price_data": {"current_price": 100.0}, "trend_state": "uptrend",
           "atr": {"value": 2.5, "percent_of_price": 2.5},
           "market_sentiment": {"momentum": "Bullish", "buy_sell_signal": "BUY"},
           "rsi": {"value": 60}}


def test_evaluate_works_with_tv_disabled(monkeypatch):
    import decision_engine as de
    monkeypatch.setenv("TRADINGVIEW_ENABLED", "false")
    monkeypatch.setattr(de, "_load_analysis", lambda s, e: (_FAKE_A, "yahoo", "fresh"))
    monkeypatch.setattr(de, "_safe_regime", lambda: {"risk_appetite_score": 62})
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
    assert r["data_state"] == "fresh" and r["data_source"] == "yahoo"
    assert r["freshness"]["state"] == "fresh"          # Yahoo primary is FRESH, not fallback
    assert "data_quality" in r and r["data_quality"]["profile"] == "momentum"
    assert r["decision"] in ("TRADEABLE", "MONITOR", "REJECT")


def test_load_analysis_leads_with_yahoo(monkeypatch):
    import decision_engine as de
    import fallback_ta
    monkeypatch.setattr(fallback_ta, "analysis", lambda s: dict(_FAKE_A, symbol=s))
    # even if TV would work, Yahoo (primary) must win and be labelled fresh
    a, src, st = de._load_analysis("TEST", "NASDAQ")
    assert src == "yahoo" and st == "fresh"


def test_load_analysis_tv_secondary_when_yahoo_down(monkeypatch):
    import decision_engine as de
    import fallback_ta
    monkeypatch.setattr(fallback_ta, "analysis", lambda s: {"error": "yf down"})
    monkeypatch.setenv("TRADINGVIEW_ENABLED", "true")
    monkeypatch.setattr(de.ss, "_analyze_cached", lambda s, e, i: dict(_FAKE_A))
    a, src, st = de._load_analysis("TEST", "NASDAQ")
    assert st == "secondary-provider" and src.startswith("tradingview")
    assert de._engine_freshness(st)["state"] == "ageing"   # mild penalty, not blocking
    assert not FR.blocks_tradeable(de._engine_freshness(st)["state"])


def test_load_analysis_unavailable_when_all_down(monkeypatch):
    import decision_engine as de
    import fallback_ta
    monkeypatch.setattr(fallback_ta, "analysis", lambda s: {"error": "yf down"})
    monkeypatch.setenv("TRADINGVIEW_ENABLED", "false")
    a, src, st = de._load_analysis("TEST", "NASDAQ")
    assert a is None and st == "unavailable"


# ── 6. Options quality is separate; a weak option never rejects the stock ────

def test_option_ungradeable_keeps_stock(monkeypatch):
    import decision_engine as de
    # option present but missing microstructure -> ungradeable, stock decision intact
    og = de._grade_option_chain({"bid": 1.0}, {"spread_pct": None}, None, 45, 70)
    assert og["gradeable"] is False and og["preference"] == "prefer-stock"


def test_option_graded_when_microstructure_present():
    import decision_engine as de
    opt = {"bid": 1.0, "ask": 1.05, "open_interest": 1200, "days_to_expiry": 21, "volume": 300}
    liq = {"spread_pct": 4.8}
    ob = {"ev_per_contract": 25.0}
    og = de._grade_option_chain(opt, liq, ob, 45, 70)
    assert og["gradeable"] is True and og["chain_quality"] is not None
    assert og["preference"] in ("prefer-stock", "prefer-option")


def test_no_option_is_stock_only():
    import decision_engine as de
    og = de._grade_option_chain(None, None, None, 45, 70)
    assert og["preference"] == "stock-only" and og["gradeable"] is False


# ── 7. Sector map ────────────────────────────────────────────────────────────

def test_sector_unknown_key():
    import sector_map as S
    assert S.sector_tile("not_a_sector")["state"] == "unsupported"
    assert S.sector_detail("not_a_sector")["state"] == "unsupported"


def test_sector_map_shape(monkeypatch):
    import sector_map as S
    # stub the ETF performance + constituent quotes so no network is touched
    monkeypatch.setattr(S, "_perf_from_history", lambda sym: {
        "symbol": sym, "state": "ok", "price": 100.0, "perf_1d": 1.0, "perf_5d": 2.0,
        "perf_1m": 3.0, "rel_volume": 1.1, "momentum_pct": 2.0, "volatility_pct": 15.0,
        "above_sma20": True, "above_sma50": True, "as_of": "2026-07-29T20:00:00+00:00"})
    monkeypatch.setattr(S, "_constituent_quotes", lambda tk: [
        {"symbol": t, "price": 10.0, "change_pct": (1.0 if i % 2 == 0 else -0.5)}
        for i, t in enumerate(tk)])
    # bypass SWR so we get the computed map synchronously
    monkeypatch.setattr(S._R, "swr_async", lambda k, ttl, fn, loading=None: (fn(), "miss"))
    m = S.sector_map("cap")
    assert m["state"] == "ok" and m["tradingview_used"] is False
    assert len(m["sectors"]) == len(S.SECTORS)
    t0 = m["sectors"][0]
    for k in ("name", "etf", "perf_1d", "breadth", "opportunity_count", "top", "bottom", "freshness"):
        assert k in t0
    assert t0["rs_vs_spy_1m"] is not None        # relative strength vs SPY computed


def test_sector_tile_degrades_when_etf_down(monkeypatch):
    import sector_map as S
    monkeypatch.setattr(S, "_perf_from_history", lambda sym: {"symbol": sym, "state": "throttled"})
    monkeypatch.setattr(S, "_constituent_quotes", lambda tk: [])
    t = S.sector_tile("technology")
    assert t["state"] == "throttled"             # typed state, never a fake number
    assert t["perf_1d"] is None
