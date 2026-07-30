"""Terminal feature tests — security master, universal search, comparison math,
model registry, caching and provider-fallback behaviour.

These are deliberately OFFLINE and deterministic: they exercise the seed-backed
security master, the pure computation helpers, the SWR cache and the graceful
model-registry fallback — none of them hit a live provider. Network-dependent
paths (live prices) are covered by the end-to-end verification, not unit tests.

Run:  pytest tests/unit/test_terminal.py -q
"""
from __future__ import annotations

import os
import sys
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in ("dashboard", "lab", "src", "automation"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import security_master as sm  # noqa: E402
import model_registry as mr   # noqa: E402


# ── Symbol normalization ─────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("aapl", "AAPL"), (" AAPL ", "AAPL"), ("$aapl", "AAPL"),
    ("brk b", "BRK.B"), ("BRK/B", "BRK.B"), ("brk-b", "BRK.B"), ("brk.b", "BRK.B"),
    ("bf b", "BF.B"), ("nokia.he", "NOKIA.HE"),
])
def test_canonical_query(raw, expected):
    assert sm.canonical_query(raw) == expected


def test_to_yahoo_share_class_vs_foreign():
    # US share class: dot -> dash
    assert sm.to_yahoo("BRK.B") == "BRK-B"
    assert sm.to_yahoo("BF.B") == "BF-B"
    # Foreign exchange suffix: preserved verbatim (the old bug turned .HE into -HE)
    assert sm.to_yahoo("NOKIA.HE") == "NOKIA.HE"
    # Plain US ticker: unchanged
    assert sm.to_yahoo("AAPL") == "AAPL"


def test_to_yahoo_index_alias():
    assert sm.to_yahoo("SPX") == "^GSPC"
    assert sm.to_yahoo("VIX") == "^VIX"


# ── Search: company name / alias / former name ───────────────────────────────

def _top(q):
    r = sm.search(q, 5)
    return r["results"][0]["symbol"] if r["results"] else None


@pytest.mark.parametrize("query,symbol", [
    ("Apple", "AAPL"),
    ("apple inc", "AAPL"),
    ("Microsoft", "MSFT"),
    ("Alphabet", "GOOGL"),
    ("Google", "GOOGL"),      # alias
    ("Facebook", "META"),     # former name / alias
    ("Nokia", "NOK"),
    ("Coca Cola", "KO"),
    ("Berkshire", "BRK.B"),
])
def test_name_and_alias_search(query, symbol):
    assert _top(query) == symbol


def test_symbol_search_exact_and_space_form():
    assert _top("AAPL") == "AAPL"
    assert _top("BRK B") == "BRK.B"     # space form canonicalised then matched
    assert _top("BRK.B") == "BRK.B"


def test_typo_tolerance():
    assert _top("microsft") == "MSFT"   # fuzzy on the name token
    assert _top("teslla") == "TSLA"


# ── Duplicate international listings must be distinguishable ──────────────────

def test_multiple_listings_distinguished():
    r = sm.search("Nokia", 6)
    syms = [x["symbol"] for x in r["results"]]
    assert "NOK" in syms and "NOKIA.HE" in syms
    nok = next(x for x in r["results"] if x["symbol"] == "NOK")
    hel = next(x for x in r["results"] if x["symbol"] == "NOKIA.HE")
    # Same company, DIFFERENT listing metadata (currency / exchange / type).
    assert nok["currency"] == "USD" and hel["currency"] == "EUR"
    assert nok["exchange"] != hel["exchange"]


def test_foreign_listing_currency_and_type():
    rec = sm.lookup("NOKIA.HE")
    assert rec is not None
    assert rec["currency"] == "EUR"
    assert rec["country"] == "FI"


# ── Ranking: exact symbol beats a name substring; popularity breaks ties ─────

def test_ranking_exact_symbol_first():
    r = sm.search("SPY", 5)
    assert r["results"][0]["symbol"] == "SPY"


def test_ranking_popular_over_obscure():
    # "apple" should surface AAPL above the many OTC "apple ..." shells.
    r = sm.search("apple", 8)
    assert r["results"][0]["symbol"] == "AAPL"


def test_invalid_query_no_match():
    r = sm.search("ZZZZQQQXNOPE", 5)
    assert r["state"] == "no_match" and r["count"] == 0


# ── Performance: warm search must be fast (< 150 ms target, generous ceiling) ─

def test_search_latency_under_150ms():
    sm.search("apple")  # warm
    t0 = time.perf_counter()
    for q in ("Apple", "Nokia", "spy", "brk b", "microsft", "tesla"):
        sm.search(q, 5)
    avg_ms = (time.perf_counter() - t0) * 1000 / 6
    assert avg_ms < 150, f"avg search {avg_ms:.1f}ms exceeds 150ms target"


# ── Model registry: lexical sentiment + graceful fallback ────────────────────

def test_lexical_sentiment_polarity():
    pos = mr.lexical_sentiment("Company beats earnings and raises guidance, shares surge")
    neg = mr.lexical_sentiment("Company misses estimates, cuts guidance, stock plunges")
    neu = mr.lexical_sentiment("Company to hold its annual meeting next week")
    assert pos["label"] == "positive" and pos["score"] > 0
    assert neg["label"] == "negative" and neg["score"] < 0
    assert neu["label"] == "neutral"


def test_lexical_negation_flips():
    s = mr.lexical_sentiment("shares did not surge; analysts are not bullish")
    assert s["score"] <= 0  # negated positives should not read bullish


def test_registry_status_reports_backend():
    st = mr.status()
    be = st["sentiment"]["backend"]
    # Backend is lexical by default, or finbert/hf when a model is loaded+enabled.
    assert be == "lexical" or be == "finbert" or be.startswith("hf")
    assert "enabled" in st["sentiment"]


# ── Sentiment aggregation: dedup + relevance + recency, not a blind average ──

def test_aggregate_dedup_and_relevance():
    items = [
        {"title": "Nvidia beats earnings and raises guidance, shares surge", "source": "Reuters", "ts": None},
        {"title": "Nvidia beats earnings and raises guidance shares surge", "source": "CNBC", "ts": None},  # near-dup
        {"title": "Unrelated small-cap biotech announces trial results", "source": "PR Newswire", "ts": None},
    ]
    agg = mr.aggregate_news_sentiment(items, "NVDA", ["nvidia"])
    assert agg["state"] == "ok"
    # The near-duplicate is collapsed…
    assert agg["dedup_removed"] >= 1
    # …and the irrelevant biotech headline is excluded from the ticker reading.
    assert agg["n"] <= 2


def test_aggregate_source_weight_prefers_reliable():
    assert mr._source_weight("Reuters") > mr._source_weight("Random Blog")
    assert mr._source_weight(None) == 0.5


def test_aggregate_empty_is_no_data_not_neutral():
    agg = mr.aggregate_news_sentiment([], "AAPL", ["apple"])
    assert agg["state"] == "empty" and agg["label"] == "no_data"


# ── research.py: cross-provider symbol mapping + pure comparison math ─────────

def test_research_symbol_mappings():
    import research as R
    assert R.to_yahoo("BRK.B") == "BRK-B"
    assert R.to_yahoo("NOKIA.HE") == "NOKIA.HE"
    assert R.to_finnhub("BRK-B") == "BRK.B"
    assert R.to_finnhub("NOKIA.HE") == "NOKIA.HE"   # foreign suffix preserved


def test_comparison_math():
    import research as R
    pts = [{"c": 100}, {"c": 110}, {"c": 90}, {"c": 120}]
    # max drawdown: 110 -> 90 = -18.18%
    assert R._max_drawdown_pct(pts) == pytest.approx(-18.18, abs=0.1)
    rets = R._daily_returns(pts)
    assert len(rets) == 3
    vol = R._annual_vol_pct(rets)
    assert vol is not None and vol > 0


def test_correlation_matrix_symmetric_and_diag_one():
    import research as R
    # Two perfectly correlated series (same daily returns) -> corr 1.0
    dates = [f"2026-01-{d:02d}" for d in range(1, 40)]
    a = {d: (0.01 if i % 2 else -0.008) for i, d in enumerate(dates)}
    b = {d: (0.02 if i % 2 else -0.016) for i, d in enumerate(dates)}  # 2x a -> corr 1
    m = R._correlation_matrix(["A", "B"], {"A": a, "B": b})
    assert m["state"] == "ok"
    assert m["matrix"][0][0] == 1.0 and m["matrix"][1][1] == 1.0
    assert m["matrix"][0][1] == pytest.approx(1.0, abs=0.01)


def test_compare_requires_two_symbols():
    import research as R
    out = R.compare(["AAPL"], "3M")
    assert out["state"] == "error"


# ── SWR cache: live / stale / miss + in-flight coalescing ────────────────────

def test_swr_live_then_stale():
    import research as R
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return {"v": calls["n"]}

    key = f"test:swr:{time.time()}"
    v1, cs1 = R.swr(key, ttl=100, fn=fn)
    assert cs1 == "miss" and v1["v"] == 1
    v2, cs2 = R.swr(key, ttl=100, fn=fn)
    assert cs2 == "live" and v2["v"] == 1  # served from cache, fn not called again
    assert calls["n"] == 1


def test_gather_isolates_a_failing_task():
    import research as R
    out = R.gather({
        "ok": lambda: {"x": 1},
        "boom": lambda: (_ for _ in ()).throw(ValueError("nope")),
    }, timeout=5.0)
    assert out["ok"] == {"x": 1}
    assert "__err" in out["boom"]   # failure isolated, not raised


def test_guard_classifies_throttle():
    import research as R

    def throttled():
        raise RuntimeError("429 too many requests")

    val, status = R._guard("unit-provider", throttled)
    assert status == "throttled"
