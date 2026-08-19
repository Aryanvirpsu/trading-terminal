"""Terminal intelligence — news→decision wiring, bounded scoring, historical validation.

OFFLINE and deterministic. Price series are synthetic and shaped so that a look-ahead
bug would be impossible to miss: the "future" half of every series behaves nothing like
the past, so any feature that leaked it would change measurably.
"""
from __future__ import annotations

import math
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in ("src", "dashboard", "lab"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import decision_score as DS      # noqa: E402
import history as H              # noqa: E402
import news_signal as NS         # noqa: E402
import research as R             # noqa: E402
import scanner as SC             # noqa: E402


def _iso(**d) -> str:
    return (datetime.now(timezone.utc) - timedelta(**d)).isoformat()


def _series(n=600, start=100.0, drift=0.0015, vol=0.012, seed=7):
    """Deterministic pseudo-random OHLCV. No numpy, no randomness across runs."""
    bars, px, s = [], start, seed
    base = datetime(2020, 1, 1, tzinfo=timezone.utc)
    for i in range(n):
        s = (1103515245 * s + 12345) % (2 ** 31)
        r = ((s / (2 ** 31)) - 0.5) * 2 * vol + drift
        o = px
        px = max(1.0, px * (1 + r))
        hi = max(o, px) * 1.004
        lo = min(o, px) * 0.996
        bars.append({"t": (base + timedelta(days=i)).isoformat(),
                     "o": round(o, 4), "h": round(hi, 4), "l": round(lo, 4),
                     "c": round(px, 4), "v": 2_000_000 + (s % 500_000)})
    return bars


# ══════════════════════ 1. ANTI-LOOK-AHEAD (mandatory) ══════════════════════

def test_features_at_bar_i_ignore_everything_after_bar_i():
    """THE test. Identical prefixes must produce identical features no matter what
    follows them — so a crash after bar i cannot change the signal at bar i."""
    bars = _series(500)
    i = 300
    calm = bars[:i + 1] + [dict(b, c=b["c"], h=b["h"], l=b["l"]) for b in bars[i + 1:]]
    crash = bars[:i + 1] + [dict(b, o=b["o"] * 0.3, h=b["h"] * 0.3,
                                 l=b["l"] * 0.3, c=b["c"] * 0.3)
                            for b in bars[i + 1:]]
    a = H._indicators_at("X", calm, i)
    b = H._indicators_at("X", crash, i)
    assert a["state"] == "ok"
    for k in ("price", "sma20", "sma50", "sma200", "atr14", "rsi14", "chg_20d",
              "high_20d", "low_20d", "rel_volume", "pos_in_20d_range"):
        assert a[k] == b[k], f"{k} leaked information from after bar {i}"


def test_the_prefix_slice_physically_excludes_the_future():
    bars = _series(400)
    pref = H._prefix(bars, 250)
    assert pref[-1] is bars[250]
    assert len(pref) <= H.WINDOW
    assert all(b in bars[:251] for b in pref)


def test_regime_at_a_date_uses_only_bars_up_to_that_date():
    spy = _series(500, seed=11)
    idx = H._index_by_date(spy)
    date = str(spy[300]["t"])[:10]
    wrecked = spy[:301] + [dict(b, c=b["c"] * 0.2, h=b["h"] * 0.2, l=b["l"] * 0.2)
                           for b in spy[301:]]
    assert H._regime_at(spy, idx, date) == H._regime_at(wrecked, H._index_by_date(wrecked), date)


def test_an_instance_without_a_complete_forward_window_is_dropped():
    """Truncating the horizon would bias recent signals toward 'never exited'."""
    bars = _series(400)
    lv = {"reference_price": bars[398]["c"], "invalidation": bars[398]["c"] * 0.9,
          "target_1": bars[398]["c"] * 1.1, "target_2": bars[398]["c"] * 1.2}
    assert H._outcome(bars, 398, lv, horizon=20) is None
    lv2 = {"reference_price": bars[300]["c"], "invalidation": bars[300]["c"] * 0.9,
           "target_1": bars[300]["c"] * 1.1, "target_2": bars[300]["c"] * 1.2}
    assert H._outcome(bars, 300, lv2, horizon=20) is not None


def test_a_bar_touching_both_stop_and_target_is_recorded_as_a_stop():
    """Daily bars cannot order the two. Assuming the favourable one flatters every
    number in the table, so the pessimistic reading is the only defensible one."""
    bars = _series(200)
    i = 100
    entry = bars[i]["c"]
    bars[i + 1] = dict(bars[i + 1], o=entry, h=entry * 1.20, l=entry * 0.80, c=entry)
    out = H._outcome(bars, i, {"reference_price": entry, "invalidation": entry * 0.9,
                               "target_1": entry * 1.1, "target_2": entry * 1.15},
                     horizon=20)
    assert out["exit_reason"] == "stop"
    assert out["hit_stop"] is True and out["hit_t1"] is False


def test_the_engine_states_that_historical_news_is_unavailable():
    """Back-filling today's sentiment labels onto old headlines would be look-ahead of
    the purest kind. The engine must say so rather than manufacture it."""
    assert "Historical news unavailable" in H.NEWS_NOTE
    assert "not" in H.OPTIONS_NOTE.lower() and "validated" in H.OPTIONS_NOTE.lower()
    for token in ("sentiment", "news"):
        assert token in H.NEWS_NOTE.lower()


def test_no_option_chain_history_is_synthesised():
    src = open(os.path.join(_ROOT, "dashboard", "history.py"), encoding="utf-8").read()
    for forbidden in ("implied_volatility", "black_scholes", "bs_greeks", "option_chain"):
        assert forbidden not in src, f"history.py must not reconstruct {forbidden}"


# ══════════════════════ 2. Outcome + aggregation correctness ═════════════════

def test_outcome_measures_target_stop_mfe_and_mae():
    bars = _series(200)
    i, entry = 100, None
    entry = bars[i]["c"]
    for k in range(1, 6):
        bars[i + k] = dict(bars[i + k], o=entry, h=entry * (1 + 0.02 * k),
                           l=entry * 0.99, c=entry * (1 + 0.015 * k))
    out = H._outcome(bars, i, {"reference_price": entry, "invalidation": entry * 0.90,
                               "target_1": entry * 1.05, "target_2": entry * 1.5},
                     horizon=20)
    assert out["hit_t1"] is True and out["exit_reason"] == "target_1"
    assert out["mfe_pct"] > 0 and out["mae_pct"] <= 0
    assert out["return_pct"] == pytest.approx(5.0, abs=0.01)


def test_overlapping_signals_are_not_counted_as_independent_trades():
    assert H.MIN_GAP >= 2
    src = open(os.path.join(_ROOT, "dashboard", "history.py"), encoding="utf-8").read()
    assert "i - last_i < MIN_GAP" in src


def test_a_tiny_sample_never_gets_a_verdict():
    stats = H._stats([{"return_pct": 5.0, "hit_t1": True, "hit_t2": False,
                       "hit_stop": False, "mfe_pct": 6.0, "mae_pct": -1.0,
                       "held_days": 3, "r_multiple": 1.5, "exit_reason": "target_1",
                       "ret_5d": 3.0, "ret_10d": 4.0, "ret_20d": 5.0}] * 4)
    cls, why = H._classify(stats, 4)
    assert cls == "INSUFFICIENT SAMPLE"
    assert "4" in why and str(H.MIN_SAMPLE) in why


def test_a_losing_history_is_classified_weak():
    outs = [{"return_pct": -4.0, "hit_t1": False, "hit_t2": False, "hit_stop": True,
             "mfe_pct": 1.0, "mae_pct": -5.0, "held_days": 4, "r_multiple": -1.0,
             "exit_reason": "stop", "ret_5d": -3.0, "ret_10d": -4.0, "ret_20d": -5.0}] * 20
    cls, _ = H._classify(H._stats(outs), 20)
    assert cls == "WEAK HISTORY"


def test_similarity_rewards_matching_regime_and_structure():
    a = {"atr_pct": 3.0, "rsi14": 65, "rel_volume": 1.5, "pos_in_20d_range": 90,
         "chg_20d": 12, "rel_strength_20d": 8, "above_sma20": True, "above_sma50": True,
         "above_sma200": True, "regime_trend": "bull", "vol_regime": "normal",
         "sector_rank": 2}
    assert H.similarity(a, a) == pytest.approx(1.0, abs=0.01)
    opposite = {**a, "regime_trend": "bear", "above_sma50": False, "above_sma200": False,
                "rsi14": 25, "rel_strength_20d": -15}
    assert H.similarity(a, opposite) < 0.6


def test_missing_features_neither_credit_nor_penalise():
    a = {"atr_pct": 3.0, "regime_trend": "bull"}
    b = {"atr_pct": 3.0, "regime_trend": "bull", "rsi14": None}
    assert H.similarity(a, b) == pytest.approx(1.0, abs=0.01)


# ══════════════════════ 3. News relevance + catalysts ═══════════════════════

def test_a_ticker_roundup_outweighs_nothing():
    """The spec's own example: a list headline must not carry company-news weight."""
    roundup = NS.classify_relevance("SA analyst upgrades/downgrades: AAPL, PLTR, SNDK, AMZN",
                                    "PLTR", aliases=["palantir"])
    direct = NS.classify_relevance("Palantir wins $500M Army contract", "PLTR",
                                   aliases=["palantir"])
    assert direct["tier"] == "DIRECT"
    assert roundup["tier"] in ("INDIRECT_SECTOR", "MARKET_WIDE", "LOW")
    assert NS.TIER_WEIGHT[roundup["tier"]] <= 0.35
    assert NS.TIER_WEIGHT[direct["tier"]] == 1.0


def test_a_multi_company_list_is_a_list_even_when_named_first():
    r = NS.classify_relevance("Benzinga Bulls And Bears: Palantir, Marvell, AppLovin",
                              "PLTR", aliases=["palantir"])
    assert r["tier"] != "DIRECT"


def test_market_wide_headlines_do_not_read_as_company_news():
    r = NS.classify_relevance("Stock Market Today: futures rise as Fed holds rates",
                              "PLTR", aliases=["palantir"])
    assert r["tier"] in ("MARKET_WIDE", "LOW")


def test_catalyst_matching_is_word_bounded():
    """A substring test made 'ban' fire inside 'Bank of America' and tagged a bullish
    analyst note as a regulatory event."""
    assert NS.classify_catalyst("Palantir Stock Soars -- Bank of America Sees Upside") is None
    reg = NS.classify_catalyst("SEC opens investigation into Palantir")
    assert reg and reg["type"] == "regulatory"


def test_catalysts_are_typed_directional_and_ranked():
    c = NS.classify_catalyst("Palantir wins $500M Army contract")
    assert c["type"] == "contract" and c["direction"] == "bullish"
    assert c["importance"] == "high"
    bad = NS.classify_catalyst("Palantir cuts full-year guidance")
    assert bad["type"] == "guidance" and bad["direction"] == "bearish"


def test_a_headline_with_no_event_produces_no_catalyst():
    assert NS.classify_catalyst("Palantir shares drift in quiet trading") is None


def test_syndicated_copies_are_collapsed():
    items = [{"title": "Palantir wins big Army contract worth 500 million dollars",
              "source": "reuters"},
             {"title": "Palantir wins big Army contract worth 500 million dollars today",
              "source": "yahoo"},
             {"title": "Completely unrelated market wrap for Tuesday", "source": "cnbc"}]
    out = NS._dedup_syndicated(items)
    assert len(out) == 2
    assert out[0].get("_dupes") == 1


def test_sentiment_and_catalysts_are_separate_readings():
    raw = {"state": "ok", "items": [
        {"title": "Palantir announces new AI platform launch", "polarity": 0.0,
         "sentiment": "neutral", "ts": _iso(hours=2), "source": "reuters"}],
        "earnings": {"date": "2026-11-02"}}
    sig = NS.compute("PLTR", raw=raw)
    assert sig["direction"] == "neutral"                 # tone is flat
    assert sig["catalysts"] and sig["catalysts"][0]["type"] == "product"   # event exists
    assert "never averaged" in sig["note"]


def test_confidence_is_earned_not_assumed():
    one = NS.compute("PLTR", raw={"state": "ok", "items": [
        {"title": "Palantir surges on strong results", "polarity": 0.9,
         "sentiment": "bullish", "ts": _iso(hours=1), "source": "yahoo"}]})
    # Six DISTINCT stories — near-identical titles would (correctly) be collapsed as
    # syndicated copies of one story, which is itself tested above.
    many = NS.compute("PLTR", raw={"state": "ok", "items": [
        {"title": t, "polarity": 0.9, "sentiment": "bullish",
         "ts": _iso(hours=1), "source": src}
        for t, src in (
            ("Palantir surges on strong results", "reuters"),
            ("Palantir lands major federal defence agreement", "bloomberg"),
            ("Analysts raise Palantir price targets across the board", "cnbc"),
            ("Palantir commercial bookings accelerate sharply", "yahoo"),
            ("Institutional investors add to Palantir positions", "barrons"),
            ("Palantir expands European partnership network", "wsj"))]})
    assert many["confidence"] > one["confidence"]
    assert one["confidence"] <= 0.90


def test_no_relevant_coverage_is_reported_as_absent_not_neutral():
    sig = NS.compute("PLTR", raw={"state": "ok", "items": [
        {"title": "Stock Market Today: Dow futures slip", "polarity": 0.4,
         "sentiment": "bullish", "ts": _iso(hours=1), "source": "cnbc"}]})
    assert sig["direction"] == "no_data"
    assert sig["sentiment_score"] is None
    assert sig["confidence"] == 0.0


# ══════════════════════ 4. Bounded scoring & hard rules ═════════════════════

def _candidate(**over):
    base = {
        "symbol": "PLTR", "primary_setup": "relative_strength_momentum",
        "indicators": {"price": 172.0, "atr_pct": 3.0, "rsi14": 60.0,
                       "rel_volume": 1.8, "above_sma20": True, "above_sma50": True,
                       "above_sma200": True, "dollar_volume_20d": 5e9,
                       "chg_20d": 20.0, "pos_in_20d_range": 90.0, "missing": []},
        "levels": {"state": "ok", "reference_price": 172.0, "invalidation": 158.0,
                   "target_1": 199.0, "target_2": 226.0, "rr_target_1": 2.0,
                   "invalidation_basis": "1.5 x ATR(14)"},
        "setups": [{"type": "relative_strength_momentum", "strength": 80,
                    "horizon": "multi-day swing"}],
        "score": {"total": 78.0, "components": [
            {"component": "technical_structure", "score": 12.0, "max": 14},
            {"component": "volume_confirmation", "score": 8.0, "max": 10},
            {"component": "relative_strength", "score": 10.0, "max": 12}]},
    }
    base.update(over)
    return base


def _desk(**over):
    d = {"decision": {"verdict": "prefer-stock", "instrument": "STOCK PREFERRED",
                      "blockers": [], "reasons_for_stock": [], "reasons_for_option": [],
                      "portfolio_suitability": {"pass": False},
                      "decision_freshness": {"ok": True}},
         "best_contract": {"spread_pct": 2.0}}
    d["decision"].update(over)
    return d


_BULL_NEWS = {"state": "ok", "direction": "bullish", "sentiment_score": 0.95,
              "confidence": 0.9, "relevant_count": 12, "direct_count": 10,
              "catalysts": [{"type": "contract", "direction": "bullish",
                             "importance": "high", "tier": "DIRECT",
                             "timestamp": _iso(hours=2)}],
              "catalyst_score": 1.0}
_BEAR_NEWS = {**_BULL_NEWS, "direction": "bearish", "sentiment_score": -0.95,
              "catalysts": [{"type": "regulatory", "direction": "bearish",
                             "importance": "high", "tier": "DIRECT",
                             "timestamp": _iso(hours=2)}],
              "catalyst_score": -1.0}
_STRONG_HIST = {"state": "ok", "classification": "STRONG HISTORY", "fit_pct": 90,
                "sample_size": 40, "stats": {"expectancy_pct": 6.0, "profit_factor": 2.5,
                                             "t1_hit_rate": 64, "stop_hit_rate": 20}}
_WEAK_HIST = {"state": "ok", "classification": "WEAK HISTORY", "fit_pct": 85,
              "sample_size": 40, "stats": {"expectancy_pct": -3.0, "profit_factor": 0.7,
                                           "t1_hit_rate": 30, "stop_hit_rate": 55}}
_THIN_HIST = {"state": "ok", "classification": "INSUFFICIENT SAMPLE", "fit_pct": 80,
              "sample_size": 3, "stats": {}}


# ── the +20 regression ──────────────────────────────────────────────────────
# An earlier build computed `total = structural + context + 20`, so PLTR displayed
# 75.6 while the components printed underneath it summed to 55.6. These pin the fix.

def test_the_decision_score_is_exactly_the_sum_of_its_components():
    """The headline number must equal the bars a reader can add up. No constant, no
    normalisation, no clamp."""
    for news, hist in ((_BULL_NEWS, _STRONG_HIST), (_BEAR_NEWS, _WEAK_HIST),
                       ({}, {}), (_BULL_NEWS, _THIN_HIST)):
        a = DS.assess(candidate=_candidate(), desk=_desk(), news=news, history=hist,
                      regime={"trend": "bull"})
        parts = sum(c["score"] for c in a["components"])
        assert a["decision_score"] == pytest.approx(parts, abs=0.01)
        assert a["decision_score"] == pytest.approx(
            a["structural_score"] + a["context_score"], abs=0.01)


def test_pltrs_reported_score_matches_its_own_arithmetic():
    """The exact case that was wrong: structural 43.9 + context 11.7 displayed as 75.6."""
    a = DS.assess(candidate=_candidate(), desk=_desk(), news=_BULL_NEWS,
                  history=_STRONG_HIST, regime={"trend": "bull"})
    assert a["decision_score"] != pytest.approx(
        a["structural_score"] + a["context_score"] + 20.0, abs=0.01)


def test_no_hidden_constant_is_added_to_the_score():
    """A zero-everything assessment must score zero, not a floor."""
    empty = _candidate(indicators={"missing": []}, setups=[],
                       levels={"state": "unavailable", "reason": "no ATR"},
                       score={"total": 0.0, "components": []})
    a = DS.assess(candidate=empty, desk={"decision": {}}, news={}, history={},
                  regime={})
    assert a["decision_score"] == pytest.approx(
        sum(c["score"] for c in a["components"]), abs=0.01)
    src = open(os.path.join(_ROOT, "dashboard", "decision_score.py"), encoding="utf-8").read()
    assert "+ 20.0" not in src and "min(100.0" not in src


def test_the_score_scale_is_declared_and_reachable_at_both_ends():
    a = DS.assess(candidate=_candidate(), desk=_desk(), news=_BULL_NEWS,
                  history=_STRONG_HIST, regime={"trend": "bull"})
    sc = a["score_scale"]
    assert sc["min"] == DS.CONTEXT_MIN
    assert sc["max"] == DS.STRUCTURAL_MAX + DS.CONTEXT_MAX
    # the top of the range must be REACHABLE, not clamped away
    assert sc["max"] == 82.0 and sc["min"] == -20.0
    assert sc["equals"] == "structural_score + context_score"
    assert DS.DECISION_SCORE_MIN <= a["decision_score"] <= DS.DECISION_SCORE_MAX


def test_removing_the_constant_did_not_move_any_verdict():
    """Thresholds were rebased by exactly the removed constant, so every boundary sits
    where it did before."""
    assert DS.TRADEABLE_MIN == 55.0 - 20.0
    assert DS.MONITOR_MIN == 40.0 - 20.0
    a = DS.assess(candidate=_candidate(), desk=_desk(), news=_BULL_NEWS,
                  history=_STRONG_HIST, regime={"trend": "bull"})
    old_style_total = a["structural_score"] + a["context_score"] + 20.0
    assert (a["decision_score"] >= DS.TRADEABLE_MIN) == (old_style_total >= 55.0)
    assert (a["decision_score"] >= DS.MONITOR_MIN) == (old_style_total >= 40.0)
    assert a["label"] == "TRADEABLE"


def test_the_two_scores_are_never_called_the_same_thing():
    """`setup_score` (scanner, 0-100) and `decision_score` (-20..82) are different
    quantities on different scales and must be labelled distinctly everywhere."""
    a = DS.assess(candidate=_candidate(), desk=_desk(), news=_BULL_NEWS,
                  history=_STRONG_HIST, regime={"trend": "bull"})
    assert "total" not in a, "the ambiguous name `total` must be gone"
    html = open(os.path.join(_ROOT, "dashboard", "terminal.html"), encoding="utf-8").read()
    assert "Decision ${num(d.decision_score,1)}" in html
    assert "Setup ${num(d.setup_score,1)}" in html
    snap = open(os.path.join(_ROOT, "dashboard", "symbol_snapshot.py"), encoding="utf-8").read()
    assert '"decision_score"' in snap and '"setup_score"' in snap


def test_every_component_is_bounded():
    a = DS.assess(candidate=_candidate(), desk=_desk(), news=_BULL_NEWS,
                  history=_STRONG_HIST, regime={"trend": "bull"})
    for c in a["components"]:
        assert c["min"] <= c["score"] <= c["max"], c["component"]


def test_news_and_history_cannot_rescue_a_structurally_broken_setup():
    """The headline requirement: one euphoric article must not make a bad setup
    TRADEABLE."""
    broken = _candidate(
        indicators={"price": 5.0, "atr_pct": 0.4, "rsi14": 30.0, "rel_volume": 0.3,
                    "above_sma20": False, "above_sma50": False, "above_sma200": False,
                    "dollar_volume_20d": 2e6, "chg_20d": -20.0, "missing": ["atr14"]},
        setups=[], score={"total": 20.0, "components": []})
    a = DS.assess(candidate=broken, desk=_desk(), news=_BULL_NEWS,
                  history=_STRONG_HIST, regime={"trend": "bull"})
    assert a["label"] != "TRADEABLE"
    assert a["structural_score"] < a["structural_floor"]
    assert "cannot substitute" in a["label_reason"]


def test_the_context_budget_is_smaller_than_the_structural_floor():
    """Structural: it must be arithmetically impossible for context to carry a setup
    that failed its own structure."""
    assert DS.CONTEXT_MAX < DS.STRUCTURAL_FLOOR


def test_a_hard_failure_beats_every_favourable_input():
    for blocker, kind in (("DECISION STALE — REFRESH REQUIRED: quote is stale", "stale_data"),
                          ("open interest 4 is thin — liquidity gate failed", "liquidity"),
                          ("risk policy configuration error: bad profile", "risk_policy")):
        a = DS.assess(candidate=_candidate(), desk=_desk(blockers=[blocker]),
                      news=_BULL_NEWS, history=_STRONG_HIST, regime={"trend": "bull"})
        assert a["label"] == "REJECT", blocker
        assert any(h["kind"] == kind for h in a["hard_failures"]), blocker


def test_an_invalid_setup_is_rejected_at_any_score():
    a = DS.assess(candidate=_candidate(levels={"state": "unavailable",
                                               "reason": "no ATR — cannot place a stop"}),
                  desk=_desk(), news=_BULL_NEWS, history=_STRONG_HIST,
                  regime={"trend": "bull"})
    assert a["label"] == "REJECT"
    assert a["hard_failures"][0]["kind"] == "invalid_setup"


def test_an_insufficient_history_sample_contributes_exactly_zero():
    a = DS.assess(candidate=_candidate(), desk=_desk(), news={},
                  history=_THIN_HIST, regime={"trend": "bull"})
    h = next(c for c in a["components"] if c["component"] == "historical_setup_fit")
    assert h["score"] == 0.0
    assert "too few" in h["note"]


def test_absent_news_scores_zero_rather_than_neutral_credit():
    a = DS.assess(candidate=_candidate(), desk=_desk(), news={},
                  history=_STRONG_HIST, regime={"trend": "bull"})
    n = next(c for c in a["components"] if c["component"] == "news_sentiment")
    assert n["score"] == 0.0
    assert "not as neutral" in n["note"]


def test_news_contribution_scales_with_sample_size():
    thin = DS.assess(candidate=_candidate(), desk=_desk(),
                     news={**_BULL_NEWS, "relevant_count": 1, "direct_count": 1},
                     history={}, regime={"trend": "bull"})
    thick = DS.assess(candidate=_candidate(), desk=_desk(), news=_BULL_NEWS,
                      history={}, regime={"trend": "bull"})
    a = next(c for c in thin["components"] if c["component"] == "news_sentiment")["score"]
    b = next(c for c in thick["components"] if c["component"] == "news_sentiment")["score"]
    assert a < b


# ══════════════════════ 5. The four required conflict cases ═════════════════

def test_case_technical_bullish_plus_news_bearish():
    a = DS.assess(candidate=_candidate(), desk=_desk(), news=_BEAR_NEWS,
                  history=_STRONG_HIST, regime={"trend": "bull"})
    good = DS.assess(candidate=_candidate(), desk=_desk(), news=_BULL_NEWS,
                     history=_STRONG_HIST, regime={"trend": "bull"})
    assert a["decision_score"] < good["decision_score"], "bearish news must lower the score"
    assert a["context_score"] < good["context_score"]
    thesis = {r["row"]: r["value"] for r in a["thesis"]}
    assert thesis["News/Sentiment"] == "BEARISH"
    assert thesis["Technical"] in ("STRONG", "MODERATE")


def test_case_technical_bullish_plus_history_weak():
    a = DS.assess(candidate=_candidate(), desk=_desk(), news=_BULL_NEWS,
                  history=_WEAK_HIST, regime={"trend": "bull"})
    good = DS.assess(candidate=_candidate(), desk=_desk(), news=_BULL_NEWS,
                     history=_STRONG_HIST, regime={"trend": "bull"})
    assert a["decision_score"] < good["decision_score"]
    h = next(c for c in a["components"] if c["component"] == "historical_setup_fit")
    assert h["score"] < 0, "a losing history must subtract, not merely fail to add"
    assert {r["row"]: r["value"] for r in a["thesis"]}["Historical Fit"] == "WEAK"


def test_case_news_bullish_plus_technical_invalid():
    a = DS.assess(candidate=_candidate(levels={"state": "unavailable",
                                               "reason": "no ATR"}),
                  desk=_desk(), news=_BULL_NEWS, history=_STRONG_HIST,
                  regime={"trend": "bull"})
    assert a["label"] == "REJECT"
    assert any(h["kind"] == "invalid_setup" for h in a["hard_failures"])


def test_case_history_strong_plus_stale_quote():
    a = DS.assess(candidate=_candidate(), desk=_desk(), news=_BULL_NEWS,
                  history=_STRONG_HIST, regime={"trend": "bull"},
                  freshness={"ok": False, "stale_inputs": ["underlying quote is stale (3h)"]})
    assert a["label"] == "REJECT"
    assert a["hard_failures"][0]["kind"] == "stale_data"
    assert "quote" in a["hard_failures"][0]["detail"]


# ══════════════════════ 6. Thesis + explainability surface ══════════════════

def test_the_thesis_answers_every_required_row():
    a = DS.assess(candidate=_candidate(), desk=_desk(), news=_BULL_NEWS,
                  history=_STRONG_HIST, regime={"trend": "bull"})
    rows = {r["row"] for r in a["thesis"]}
    assert {"Technical", "News/Sentiment", "Catalysts", "Historical Fit",
            "Execution", "Risk Fit"} <= rows


def test_supports_and_contradictions_are_short_and_ranked():
    a = DS.assess(candidate=_candidate(), desk=_desk(), news=_BULL_NEWS,
                  history=_STRONG_HIST, regime={"trend": "bull"})
    assert 0 < len(a["supports"]) <= 3
    assert len(a["contradicts"]) <= 4


def test_the_invalidation_is_always_stated():
    a = DS.assess(candidate=_candidate(), desk=_desk(), news={}, history={},
                  regime={"trend": "bull"})
    assert a["invalidation"]["price"] == 158.0
    assert "158" in a["invalidation"]["note"]


def test_an_elevated_rsi_is_surfaced_as_a_watch_item():
    a = DS.assess(candidate=_candidate(indicators={**_candidate()["indicators"],
                                                   "rsi14": 74.0}),
                  desk=_desk(), news=_BULL_NEWS, history=_STRONG_HIST,
                  regime={"trend": "bull"})
    assert any("RSI" in c for c in a["contradicts"])


# ══════════════════════ 7. News actually reaches the decision ═══════════════

def test_the_authoritative_decision_now_consumes_a_news_signal():
    """Before this work `options_desk.decide` contained no reference to news at all;
    news reached a verdict only through two scanner-score components."""
    src = open(os.path.join(_ROOT, "dashboard", "decision_score.py"), encoding="utf-8").read()
    assert "news_sentiment" in src and "catalyst_quality" in src
    snap = open(os.path.join(_ROOT, "dashboard", "symbol_snapshot.py"), encoding="utf-8").read()
    assert "news_signal" in snap and "_unit_assessment" in snap
    assert '"decision":   {"unit": "assessment"' in snap.replace("'", '"')


def test_the_decision_section_carries_its_components_to_the_ui():
    """Every piece of evidence must reach the widget. It now lives in tabs rather than
    accordions, so assert the CONTENT is rendered, not the old <details> labels."""
    html = open(os.path.join(_ROOT, "dashboard", "terminal.html"), encoding="utf-8").read()
    body = html[html.index("function renderDecision"):html.index("/* ── 4. OPTIONS")]
    for token in ("Why this setup", "Watch", "hard_failures",
                  "context_cannot_override",          # overview
                  "d.components", "s.evidence", "invalidation_basis",
                  "target_1_basis",                   # thesis
                  "options_note", "news_note",        # history
                  "catalyst_score", "tier_reason",    # news
                  "binding_constraint",               # risk
                  "contract_quality", "portfolio_suitability"):   # option
        assert token in body, token
    # and the components themselves are still shipped by the API
    snap = open(os.path.join(_ROOT, "dashboard", "symbol_snapshot.py"), encoding="utf-8").read()
    assert '"components": unit_val.get("components")' in snap


# ══════════════════════ 8. Strategy snapshot store ══════════════════════════

def test_the_store_writes_before_any_outcome_can_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGY_STORE_DB", str(tmp_path / "s.db"))
    import importlib
    import strategy_store as ST
    importlib.reload(ST)
    a = DS.assess(candidate=_candidate(), desk=_desk(), news=_BULL_NEWS,
                  history=_STRONG_HIST, regime={"trend": "bull"})
    r = ST.record(symbol="PLTR", assessment=a, candidate=_candidate(),
                  news=_BULL_NEWS, history=_STRONG_HIST, regime={"trend": "bull"})
    assert r["state"] == "ok" and r["written"] is True
    rows = ST.open_snapshots()
    assert len(rows) == 1
    assert rows[0]["outcome"] is None
    assert rows[0]["entry"] == 172.0 and rows[0]["stop"] == 158.0
    assert rows[0]["strategy"] == "relative_strength_momentum"
    # one row per symbol/strategy/session — a re-render is not a new signal
    again = ST.record(symbol="PLTR", assessment=a, candidate=_candidate())
    assert again["written"] is False
    assert len(ST.open_snapshots()) == 1


def test_a_store_failure_never_breaks_a_decision(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGY_STORE_DB", str(tmp_path / "nope" / "x" / "s.db"))
    import importlib
    import strategy_store as ST
    importlib.reload(ST)
    monkeypatch.setattr(ST, "_conn", lambda: (_ for _ in ()).throw(RuntimeError("disk")))
    out = ST.record(symbol="PLTR", assessment={"label": "TRADEABLE"})
    assert out["state"] == "error"      # reported, not raised
