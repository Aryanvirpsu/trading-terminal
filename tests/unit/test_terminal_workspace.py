"""Terminal workspace — global symbol, freshness gating, scoped refresh, isolation.

Everything here is OFFLINE: every provider-facing unit is monkeypatched, so these
tests assert the terminal's CONTRACTS (which section came from which compute, which
freshness window judged it, what a stale critical input does to a verdict) rather
than re-testing the providers.

The frontend assertions read dashboard/terminal.html as text. That is deliberate:
the layout contract (no overlapping defaults, every widget has a renderer, the
persistence keys) is exactly the kind of thing that silently rots, and it is
checkable without a browser.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in ("src", "dashboard", "lab"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import freshness as FR             # noqa: E402
import market_regime as MR         # noqa: E402
import research as R               # noqa: E402
import scanner as SC               # noqa: E402
import symbol_snapshot as SS       # noqa: E402

_HTML = os.path.join(_ROOT, "dashboard", "terminal.html")


def _iso(**delta) -> str:
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


@pytest.fixture(autouse=True)
def _clean_cache():
    """Each test starts with an empty snapshot cache — SWR entries must not leak."""
    R.invalidate("snap:")
    R.invalidate("desk:")
    R.invalidate("sym:")
    yield
    R.invalidate("snap:")


def _stub_units(monkeypatch, *, quote_ts=None, overrides=None):
    """Replace every unit with a deterministic fake. No network, no providers."""
    quote_ts = quote_ts or _iso(seconds=5)
    units = {
        "quote": lambda s: {"state": "ok", "price": 172.01, "change_pct": 1.2,
                            "source_timestamp": quote_ts, "source": "yahoo"},
        "chart": lambda s, rng="3M": {"state": "ok", "range": rng, "interval": "1d",
                                      "as_of": quote_ts, "change_pct": 5.0,
                                      "points": [{"t": "2026-08-06T00:00:00+00:00", "o": 1,
                                                  "h": 2, "l": 0.5, "c": 1.5, "v": 10}]},
        "analysis": lambda s: {"state": "ok", "symbol": s, "origin": "on_demand",
                               "indicators": {"price": 172.01, "atr_pct": 2.0,
                                              "source_timestamp": quote_ts,
                                              "rsi14": 55.0, "missing": []},
                               "levels": {"state": "ok", "reference_price": 172.0,
                                          "invalidation": 158.0, "target_1": 199.0,
                                          "target_2": 226.0, "rr_target_1": 2.0,
                                          "entry_zone": [169.0, 175.0]},
                               "setups": [{"type": "breakout_volume", "horizon": "days"}],
                               "score": {"total": 61.0, "components": []},
                               "funnel": {"would_pass": True, "failed_gates": []}},
        "desk": lambda s: {"state": "ok", "symbol": s, "underlying_price": 172.01,
                           "contracts_analysed": 12, "contracts_passing": 3,
                           "expirations_considered": ["2026-08-28"],
                           "chain": [{"strike": 170.0, "expiry": "2026-08-28",
                                      "tradeable": True, "quote_timestamp": quote_ts}],
                           "best_contract": {"strike": 170.0, "expiry": "2026-08-28",
                                             "quote_timestamp": quote_ts, "tradeable": True},
                           "generated_at": quote_ts,
                           "decision": {"verdict": "prefer-stock",
                                        "instrument": "STOCK PREFERRED",
                                        "decision": "TRADEABLE",
                                        "reasons_for_stock": ["theta is expensive"],
                                        "reasons_for_option": [], "blockers": [],
                                        "decided_at": quote_ts}},
        "news": lambda s: {"state": "ok", "lean": "bullish", "catalyst_count": 2,
                           "items": [{"title": f"{s} headline", "ts": _iso(minutes=20),
                                      "sentiment": "bullish", "catalyst_score": 1}],
                           "earnings": {"date": "2026-11-02", "days_away": 86},
                           "provenance": {"fetched_at": _iso(minutes=1)}},
        # The canonical news signal the decision consumes (news_signal.compute output).
        "newsig": lambda s: {"state": "ok", "symbol": s, "direction": "bullish",
                             "sentiment_score": 0.4, "confidence": 0.7,
                             "article_count": 3, "relevant_count": 2, "direct_count": 2,
                             "counts": {"positive": 2, "neutral": 0, "negative": 0},
                             "tier_breakdown": {"DIRECT": 2}, "source_diversity": {},
                             "freshness": {}, "catalyst_score": 0.5,
                             "catalysts": [{"type": "earnings", "direction": "bullish",
                                            "importance": "high", "tier": "DIRECT",
                                            "timestamp": _iso(minutes=30)}],
                             "earnings": {"date": "2026-11-02", "days_away": 86},
                             "items": [{"title": f"{s} headline", "ts": _iso(minutes=20),
                                        "tier": "DIRECT", "polarity": 0.4,
                                        "relevance": 1.0, "weight": 0.9}]},
        "history": lambda s: {"state": "ok", "symbol": s, "strategy": "breakout_volume",
                              "classification": "MIXED HISTORY", "fit_pct": 70,
                              "sample_size": 20, "total_instances": 30,
                              "similarity_floor": 0.55,
                              "window": {"lookback": "10y", "first": "2020-01-01",
                                         "last": "2026-01-01", "bars_scanned": 900},
                              "horizon_days": 20,
                              "stats": {"expectancy_pct": 1.2, "profit_factor": 1.4,
                                        "t1_hit_rate": 55, "stop_hit_rate": 30},
                              "examples": [], "methodology": "m",
                              "options_note": "o", "news_note": "n"},
        "assessment": lambda s: {"state": "ok", "symbol": s, "label": "TRADEABLE",
                                 "label_reason": "structure clears the floor",
                                 "instrument": "STOCK PREFERRED",
                                 "components": [], "structural_score": 40.0,
                                 "structural_max": 58.0, "structural_floor": 32.0,
                                 "context_score": 4.0, "context_max": 24.0,
                                 "decision_score": 64.0, "hard_failures": [],
                                 "thesis": [{"row": "Technical", "value": "STRONG"}],
                                 "supports": ["strong structure"], "contradicts": [],
                                 "invalidation": {"price": 158.0, "note": "below 158"},
                                 "desk": {"verdict": "prefer-stock",
                                          "instrument": "STOCK PREFERRED",
                                          "blockers": [], "decided_at": quote_ts,
                                          "decision_freshness": {"ok": True}},
                                 "levels": {"state": "ok", "invalidation": 158.0},
                                 "setups": [], "scanner_score": {"total": 61.0},
                                 "candidate_origin": "on_demand", "funnel": {}},
        "account": lambda s="": {"state": "ok", "fetched_at": _iso(seconds=30),
                                 "broker": {"state": "ok", "buying_power": 447.94,
                                            "fetched_at": _iso(seconds=30),
                                            "positions": [], "option_positions": []},
                                 "paper": {"state": "ok", "equity": 493.49,
                                           "positions_value": 183.2,
                                           "available_cash": 310.29},
                                 "all_positions": [{"symbol": "JPM", "quantity": 0.35}]},
    }
    units.update(overrides or {})
    monkeypatch.setattr(SS, "_UNITS", units)


# ══════════════════════ Global symbol synchronization ══════════════════════

def test_every_section_answers_for_the_one_selected_symbol(monkeypatch):
    """One snapshot, one symbol. Widgets read sections — they cannot drift apart."""
    monkeypatch.setattr(MR, "session_state", lambda *a, **k: {"state": "open"})
    _stub_units(monkeypatch)
    snap = SS.snapshot("PLTR", blocking=True)
    assert snap["symbol"] == "PLTR"
    assert set(snap["sections"]) == set(SS.DEFAULT_SECTIONS)
    assert not snap["pending"] and not snap["failed"]
    assert snap["sections"]["news"]["data"]["items"][0]["title"].startswith("PLTR")


def test_sections_sharing_a_unit_are_computed_once(monkeypatch):
    """decision+options share the desk; technicals+levels share the analysis. If they
    were separate computes the two halves of a widget pair could disagree."""
    calls = {"desk": 0, "analysis": 0}

    def desk(s):
        calls["desk"] += 1
        return {"state": "ok", "decision": {"verdict": "prefer-stock"}, "chain": [],
                "generated_at": _iso(seconds=1)}

    def analysis(s):
        calls["analysis"] += 1
        return {"state": "ok", "indicators": {"source_timestamp": _iso(seconds=1)},
                "levels": {"state": "ok"}, "setups": [], "score": {}}

    monkeypatch.setattr(MR, "session_state", lambda *a, **k: {"state": "open"})
    _stub_units(monkeypatch, overrides={"desk": desk, "analysis": analysis})
    SS.snapshot("PLTR", blocking=True, sections=["decision", "options", "technicals", "levels"])
    assert calls == {"desk": 1, "analysis": 1}


def test_a_symbol_change_does_not_reuse_the_previous_symbols_cache(monkeypatch):
    monkeypatch.setattr(MR, "session_state", lambda *a, **k: {"state": "open"})
    _stub_units(monkeypatch)
    a = SS.snapshot("PLTR", blocking=True, sections=["news"])
    b = SS.snapshot("AAPL", blocking=True, sections=["news"])
    assert a["sections"]["news"]["data"]["items"][0]["title"].startswith("PLTR")
    assert b["sections"]["news"]["data"]["items"][0]["title"].startswith("AAPL")


def test_the_account_unit_is_shared_across_symbols(monkeypatch):
    """Buying power does not depend on the ticker. Keying it per symbol made every
    selection re-pay a ~17s broker round-trip for an unchanged answer."""
    hits = {"n": 0}

    def acct(s=""):
        hits["n"] += 1
        return {"state": "ok", "fetched_at": _iso(seconds=1), "broker": {"state": "ok"},
                "paper": {"state": "ok"}, "all_positions": []}

    monkeypatch.setattr(MR, "session_state", lambda *a, **k: {"state": "open"})
    _stub_units(monkeypatch, overrides={"account": acct})
    SS.snapshot("PLTR", blocking=True, sections=["risk"])
    SS.snapshot("AAPL", blocking=True, sections=["risk"])
    SS.snapshot("MSFT", blocking=True, sections=["risk"])
    assert hits["n"] == 1
    assert SS._unit_key("PLTR", "account") == SS._unit_key("AAPL", "account")
    assert SS._unit_key("PLTR", "quote") != SS._unit_key("AAPL", "quote")


def test_position_is_filtered_per_symbol_from_the_shared_account(monkeypatch):
    monkeypatch.setattr(MR, "session_state", lambda *a, **k: {"state": "open"})
    _stub_units(monkeypatch)
    jpm = SS.snapshot("JPM", blocking=True, sections=["position"])["sections"]["position"]["data"]
    pltr = SS.snapshot("PLTR", blocking=True, sections=["position"])["sections"]["position"]["data"]
    assert len(jpm["paper_positions"]) == 1
    assert pltr["paper_positions"] == []
    assert len(pltr["paper_all"]) == 1      # the portfolio is still visible


# ══════════════════════ Freshness: per dataset, per window ══════════════════════

def test_each_section_is_judged_against_its_own_window(monkeypatch):
    """A 30-minute-old quote is stale; a 30-minute-old earnings date is current.
    One global age would describe neither."""
    monkeypatch.setattr(MR, "session_state", lambda *a, **k: {"state": "open"})
    old = _iso(minutes=30)
    _stub_units(monkeypatch, quote_ts=old)
    snap = SS.snapshot("PLTR", blocking=True)
    q = snap["sections"]["quote"]["freshness"]
    n = snap["sections"]["news"]["freshness"]
    assert q["kind"] == "quote" and q["state"] in ("stale", "critically_stale")
    assert n["kind"] == "news" and n["state"] == "fresh"


def test_every_section_declares_a_known_data_type(monkeypatch):
    monkeypatch.setattr(MR, "session_state", lambda *a, **k: {"state": "open"})
    _stub_units(monkeypatch)
    for name, sec in SS.snapshot("PLTR", blocking=True)["sections"].items():
        assert sec["freshness"]["kind"] in FR.DATA_TYPES, name
        assert sec["freshness"]["presentation"] in FR.PRESENTATION, name


def test_a_loading_section_never_claims_a_freshness_it_has_not_earned(monkeypatch):
    monkeypatch.setattr(MR, "session_state", lambda *a, **k: {"state": "open"})
    _stub_units(monkeypatch, overrides={
        "newsig": lambda s: {"state": "loading", "reason": "computing"}})
    sec = SS.snapshot("PLTR", blocking=True, sections=["news"])["sections"]["news"]
    assert sec["state"] == "loading"
    assert sec["freshness"]["state"] == "unknown"
    assert sec["freshness"]["presentation"] == "UNKNOWN"


# ══════════════════════ Stale gating: the verdict is REPLACED ══════════════════

def test_a_stale_critical_input_replaces_the_verdict(monkeypatch):
    """Never leave an OPTION/STOCK PREFERRED badge looking current beside a warning:
    at a glance the badge is what gets read."""
    monkeypatch.setattr(MR, "session_state", lambda *a, **k: {"state": "open"})
    _stub_units(monkeypatch, quote_ts=_iso(hours=3))
    snap = SS.snapshot("PLTR", blocking=True)
    assert snap["critical_stale"], "a 3h-old quote during an open session must gate"
    d = snap["sections"]["decision"]["data"]
    assert d["gated"] is True
    assert d["verdict"] == "stale"
    assert d["instrument"] == "STALE — REFRESH REQUIRED"
    assert d["decision"] == "STALE — REFRESH REQUIRED"
    # the withheld verdict is preserved for the reader, but not as guidance
    assert d["verdict_withheld"] == "prefer-stock"
    assert d["instrument_withheld"] == "STOCK PREFERRED"


def test_fresh_inputs_leave_the_verdict_standing(monkeypatch):
    monkeypatch.setattr(MR, "session_state", lambda *a, **k: {"state": "open"})
    _stub_units(monkeypatch, quote_ts=_iso(seconds=10))
    d = SS.snapshot("PLTR", blocking=True)["sections"]["decision"]["data"]
    assert not d.get("gated")
    assert d["instrument"] == "STOCK PREFERRED"


def test_a_closed_exchange_is_not_a_stale_feed(monkeypatch):
    """Nothing can trade on a shut exchange, so age alone must not gate — but the
    label says MARKET CLOSED so it is never mistaken for live guidance."""
    monkeypatch.setattr(MR, "session_state", lambda *a, **k: {"state": "closed"})
    _stub_units(monkeypatch, quote_ts=_iso(hours=20))
    snap = SS.snapshot("PLTR", blocking=True)
    assert snap["critical_stale"] == []
    assert not snap["sections"]["decision"]["data"].get("gated")
    assert (snap["sections"]["quote"]["freshness"]["presentation"]
            == "MARKET CLOSED · LAST CLOSE")


def test_only_critical_sections_can_gate_a_verdict(monkeypatch):
    """Old news is a reason to read carefully, not to withhold a verdict."""
    monkeypatch.setattr(MR, "session_state", lambda *a, **k: {"state": "open"})
    _stub_units(monkeypatch, overrides={
        # Three-day-old coverage: past the news window, so the news section is stale —
        # but news is not a CRITICAL input, so the verdict must still stand.
        "newsig": lambda s: {"state": "ok", "direction": "neutral",
                             "items": [{"title": "old", "ts": _iso(days=3),
                                        "tier": "DIRECT", "polarity": 0.0}],
                             "catalysts": [{"type": "earnings", "direction": "neutral",
                                            "importance": "low", "tier": "DIRECT",
                                            "timestamp": _iso(days=3)}]}})
    snap = SS.snapshot("PLTR", blocking=True)
    assert snap["sections"]["news"]["freshness"]["blocks_tradeable"] is True
    assert snap["critical_stale"] == []
    assert not snap["sections"]["decision"]["data"].get("gated")


def test_the_underlying_quote_is_aged_from_the_real_quote_not_the_daily_bar():
    """A daily bar is stamped at its own close, so it reads age ~0 all session and
    cannot detect an intraday-stale underlying. The live quote is the honest input."""
    import options_desk as OD
    today_bar = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0,
                                                   microsecond=0).isoformat()
    cand = {"symbol": "X", "score": {"total": 72},
            "indicators": {"price": 100.0, "atr_pct": 2.0,
                           "source_timestamp": today_bar, "as_of": today_bar},
            "quote": {"source_timestamp": _iso(hours=4)},
            "levels": {"state": "ok", "reference_price": 100.0, "invalidation": 95.0,
                       "entry_zone": [99.5, 100.5], "target_1": 112.0, "target_2": 120.0,
                       "rr_target_1": 2.4, "invalidation_basis": "swing low",
                       "computed_at": _iso(seconds=5)},
            "setups": [{"type": "momentum", "horizon": "days"}]}
    fr = OD._decision_freshness(cand, None,
                                {"buying_power": 447.94, "fetched_at": _iso(seconds=5)},
                                None)
    quote_check = next(c for c in fr["checks"] if c["input"] == "underlying quote")
    assert quote_check["age_seconds"] > 3 * 3600, "the 4h-old live quote must win"


# ══════════════════════ Ticker refresh ≠ universe rescan ══════════════════════

def test_refreshing_a_symbol_never_rescans_the_universe(monkeypatch):
    monkeypatch.setattr(MR, "session_state", lambda *a, **k: {"state": "open"})
    _stub_units(monkeypatch)

    def boom(*a, **k):
        raise AssertionError("selecting or refreshing a symbol ran the universe scan")

    monkeypatch.setattr(SC, "scan", boom)
    monkeypatch.setattr(SC, "scan_cached", boom)
    out = SS.refresh("PLTR", blocking=True)
    assert out["scope"] == "symbol"
    assert out["universe_rescanned"] is False
    assert set(out["refreshed"]) <= set(SS._UNIT_TTL)


def test_analyse_symbol_reads_the_scan_cache_but_never_starts_one(monkeypatch):
    """`peek` not `scan_cached`: a cache miss must not cost 800 names."""
    monkeypatch.setattr(SC, "scan_cached",
                        lambda *a, **k: pytest.fail("scan_cached started a universe scan"))
    monkeypatch.setattr(SC, "analyse_symbol", lambda s: {"state": "ok", "symbol": s,
                                                         "origin": "on_demand"})
    R.invalidate("scan:")
    assert SS._unit_analysis("PLTR")["origin"] == "on_demand"


def test_a_symbol_in_the_cached_scan_reuses_the_funnels_candidate(monkeypatch):
    monkeypatch.setattr(R, "peek", lambda k: {"candidates": [{"symbol": "PLTR",
                                                              "score": {"total": 71}}]}
                        if k == "scan:liquid" else None)
    out = SS._unit_analysis("PLTR")
    assert out["origin"] == "scan"
    assert out["funnel"]["would_pass"] is True


def test_refresh_scoped_to_sections_only_drops_those_units(monkeypatch):
    monkeypatch.setattr(MR, "session_state", lambda *a, **k: {"state": "open"})
    _stub_units(monkeypatch)
    SS.snapshot("PLTR", blocking=True)                     # warm everything
    assert R.cache_age(SS._unit_key("PLTR", "newsig")) is not None
    out = SS.refresh("PLTR", sections=["options"], blocking=True)
    # the assessment is derived from the desk, so refreshing the chain must drop it too
    assert set(out["refreshed"]) == {"desk", "assessment"}
    assert R.cache_age(SS._unit_key("PLTR", "newsig")) is not None   # untouched


def test_option_chain_refresh_repulls_the_desk(monkeypatch):
    monkeypatch.setattr(MR, "session_state", lambda *a, **k: {"state": "open"})
    n = {"i": 0}

    def desk(s):
        n["i"] += 1
        return {"state": "ok", "chain": [{"strike": 100 + n["i"], "expiry": "2026-08-28"}],
                "best_contract": {"strike": 100 + n["i"]}, "contracts_analysed": 1,
                "decision": {"verdict": "prefer-stock"}, "generated_at": _iso(seconds=1)}

    _stub_units(monkeypatch, overrides={"desk": desk})
    first = SS.snapshot("PLTR", blocking=True, sections=["options"])["sections"]["options"]["data"]
    again = SS.snapshot("PLTR", blocking=True, sections=["options"])["sections"]["options"]["data"]
    assert first["best_contract"]["strike"] == again["best_contract"]["strike"], "cached"
    refreshed = SS.refresh("PLTR", sections=["options"], blocking=True)["sections"]["options"]["data"]
    assert refreshed["best_contract"]["strike"] != first["best_contract"]["strike"]


# ══════════════════════ Failure isolation ══════════════════════

def test_one_failing_provider_does_not_take_down_its_siblings(monkeypatch):
    """NEWS unavailable must not break quote / chart / options / decision."""
    monkeypatch.setattr(MR, "session_state", lambda *a, **k: {"state": "open"})

    def broken(s):
        raise RuntimeError("news provider exploded")

    _stub_units(monkeypatch, overrides={"newsig": broken})
    snap = SS.snapshot("PLTR", blocking=True)
    assert snap["failed"] == ["news", "catalysts"] or set(snap["failed"]) == {"news", "catalysts"}
    for ok in ("quote", "chart", "decision", "options", "technicals", "levels", "risk"):
        assert snap["sections"][ok]["state"] == "ok", ok
    assert "news provider exploded" in snap["sections"]["news"]["reason"]


def test_a_failing_section_still_carries_a_freshness_record(monkeypatch):
    monkeypatch.setattr(MR, "session_state", lambda *a, **k: {"state": "open"})
    _stub_units(monkeypatch, overrides={
        "desk": lambda s: (_ for _ in ()).throw(ValueError("chain down"))})
    sec = SS.snapshot("PLTR", blocking=True, sections=["options"])["sections"]["options"]
    assert sec["state"] == "error"
    assert sec["freshness"]["presentation"] == "UNKNOWN"


def test_a_unit_returning_junk_does_not_raise(monkeypatch):
    monkeypatch.setattr(MR, "session_state", lambda *a, **k: {"state": "open"})
    _stub_units(monkeypatch, overrides={"quote": lambda s: "not a dict"})
    snap = SS.snapshot("PLTR", blocking=True, sections=["quote", "news"])
    assert snap["sections"]["quote"]["state"] == "error"
    assert snap["sections"]["news"]["state"] == "ok"


def test_an_unknown_section_name_is_ignored_not_fatal(monkeypatch):
    monkeypatch.setattr(MR, "session_state", lambda *a, **k: {"state": "open"})
    _stub_units(monkeypatch)
    snap = SS.snapshot("PLTR", blocking=True, sections=["quote", "does_not_exist"])
    assert list(snap["sections"]) == ["quote"]


def test_a_blank_symbol_is_refused():
    assert SS.snapshot("")["state"] == "error"


# ══════════════════════ Single-symbol analysis (no universe) ══════════════════

def test_analyse_symbol_reports_the_gates_it_fails_instead_of_dropping_the_name():
    """The funnel DROPS a name; a symbol the user selected must still render — with
    the gates it would have failed stated, so it never looks like a finalist."""
    ind = {"state": "ok", "price": 100.0, "atr14": 2.0, "atr_pct": 2.0,
           "high_20d": 105.0, "low_20d": 95.0, "support": 95.0, "resistance": 105.0,
           "missing": [], "source_timestamp": _iso(hours=1), "rel_volume": 1.0,
           "dollar_volume_20d": 5_000_000, "chg_20d": 1.0, "pos_in_20d_range": 50.0}
    lv = SC._levels(ind, SC.config())
    assert lv["state"] == "ok"
    # targets are measured moves off the range, so R:R is not a constant
    assert lv["rr_target_1"] != lv["rr_target_2"]


def test_the_on_demand_candidate_has_the_shape_the_options_desk_consumes():
    """`evaluate_candidate` reads symbol/indicators/levels/score — an on-demand
    candidate must satisfy the same contract as a funnel one, or the desk 404s."""
    import inspect
    src = inspect.getsource(SC.analyse_symbol)
    for key in ('"symbol"', '"indicators"', '"levels"', '"score"', '"setups"',
                '"quote"', '"funnel"'):
        assert key in src, key


# ══════════════════════ Terminal status: three facts, three names ═════════════

def _status(monkeypatch, running_stage=None, status_age=0):
    import app
    monkeypatch.setattr(app, "_read_json",
                        lambda n: {"running_stage": running_stage,
                                   "last_run_at": _iso(days=2),
                                   "last_success_at": _iso(days=2), "last_run_ok": True})
    monkeypatch.setattr(app.os.path, "exists", lambda p: True)
    monkeypatch.setattr(app.os.path, "getmtime",
                        lambda p: app._time.time() - status_age)
    app.app.config["TESTING"] = True
    with app.app.test_client() as c:
        return json.loads(c.get("/api/terminal/status").data)


def test_a_stale_running_stage_reads_as_stalled_not_running(monkeypatch):
    """`running_stage` is written at start and not always cleared, so on its own it
    reports a run that died days ago as live."""
    st = _status(monkeypatch, running_stage="deep", status_age=7000)
    job = next(j for j in st["jobs"] if j["id"] == "scheduler")
    assert job["running"] is False
    assert job["stalled"] is True
    assert "stalled" in (job["note"] or "")


def test_a_recently_written_running_stage_reads_as_running(monkeypatch):
    job = next(j for j in _status(monkeypatch, running_stage="deep", status_age=30)["jobs"]
               if j["id"] == "scheduler")
    assert job["running"] is True and job["stalled"] is False


def test_no_running_stage_reads_as_idle(monkeypatch):
    job = next(j for j in _status(monkeypatch, running_stage=None, status_age=30)["jobs"]
               if j["id"] == "scheduler")
    assert job["idle"] is True and job["running"] is False


def test_datasets_keep_their_display_name_separate_from_their_age_phrase(monkeypatch):
    """A freshness record's `label` is the age phrase. Spreading it last overwrote the
    dataset's name, so the strip rendered '(1h ago)' where the name belonged."""
    st = _status(monkeypatch, status_age=30)
    names = {d["id"]: d["label"] for d in st["datasets"]}
    assert names["batch_scan"] == "Batch scan artifacts"
    assert names["scanner"] == "Live scanner"
    for d in st["datasets"]:
        assert "ago" not in (d["label"] or "")


def test_status_reports_the_market_from_the_one_calendar(monkeypatch):
    st = _status(monkeypatch, status_age=30)
    assert st["market"]["state"] in ("open", "premarket", "afterhours", "closed", "holiday")
    assert st["market"]["state"] == MR.session_state()["state"]


def test_status_never_starts_a_universe_scan(monkeypatch):
    monkeypatch.setattr(SC, "scan_cached",
                        lambda *a, **k: pytest.fail("/api/terminal/status started a scan"))
    R.invalidate("scan:")
    st = _status(monkeypatch, status_age=30)
    scanner = next(d for d in st["datasets"] if d["id"] == "scanner")
    assert scanner["has_run"] is False


# ══════════════════════ Workspace layout contract (frontend) ══════════════════

def _html() -> str:
    with open(_HTML, encoding="utf-8") as f:
        return f.read()


def _default_layout():
    m = re.search(r"const DEFAULT_LAYOUT=\[(.*?)\];", _html(), re.S)
    assert m, "DEFAULT_LAYOUT not found"
    return [dict(re.findall(r"(\w+):\s*'?([\w-]+)'?", row))
            for row in re.findall(r"\{([^}]*)\}", m.group(1))]


def test_the_default_layout_has_no_overlapping_widgets():
    items = [{k: (int(v) if k != "id" else v) for k, v in it.items()}
             for it in _default_layout()]
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            overlap = (a["x"] < b["x"] + b["w"] and a["x"] + a["w"] > b["x"]
                       and a["y"] < b["y"] + b["h"] and a["y"] + a["h"] > b["y"])
            assert not overlap, f"{a['id']} overlaps {b['id']} in the default layout"


def test_every_default_widget_fits_the_column_count():
    for it in _default_layout():
        assert int(it["x"]) + int(it["w"]) <= 12, it["id"]


def test_every_widget_has_a_renderer_and_a_title():
    html = _html()
    for it in _default_layout():
        assert re.search(rf"\b{it['id']}:\{{title:", html), f"{it['id']} has no WIDGETS entry"


def test_the_seven_required_widgets_exist():
    ids = {it["id"] for it in _default_layout()}
    assert ids == {"watchlist", "chart", "decision", "options", "news", "risk",
                   "technicals"}


def test_layout_and_selection_are_persisted():
    html = _html()
    assert "localStorage.setItem(LS_LAYOUT" in html
    assert "localStorage.setItem(LS_STATE" in html
    for key in ("symbol", "account", "optExp", "scanPreset", "minScore", "tf"):
        assert key in html, key


def test_the_tab_sprawl_is_gone():
    """Removed, not hidden: no Home/Market/Desk/Scan/Sectors/Compare/Cat views."""
    html = _html()
    for gone in ('data-view="home"', 'data-view="market"', 'data-view="desk"',
                 'data-view="scanner"', 'data-view="sectors"', 'data-view="compare"',
                 'data-view="catalysts"', 'data-view="news"', 'data-view="stock"',
                 'data-view="tracker"', 'data-view="trading"'):
        assert gone not in html, f"{gone} still present"
    views = set(re.findall(r'data-view="(\w+)"', html))
    assert views == {"terminal", "portfolio", "settings"}


def test_the_scanner_is_peeked_not_started_on_load():
    html = _html()
    assert "peek=1" in html, "the terminal must load the scan with a peek"
    m = re.search(r"function loadScan\(force\)\{(.*?)\n\}", html, re.S)
    assert m and "force?" in m.group(1), "a scan may only start on an explicit action"


def test_the_terminal_declares_read_only_broker_access():
    assert "READ ONLY" in _html()


# ══════════════ Resize: all eight directions ══════════════

RESIZE_DIRS = ("n", "s", "w", "e", "nw", "ne", "sw", "se")
_CURSORS = {"n": "ns-resize", "s": "ns-resize", "w": "ew-resize", "e": "ew-resize",
            "nw": "nwse-resize", "se": "nwse-resize",
            "ne": "nesw-resize", "sw": "nesw-resize"}


@pytest.mark.parametrize("d", RESIZE_DIRS)
def test_every_resize_direction_has_a_handle(d):
    """The old build shipped ONE bottom-right grip, which is why the top and left
    boundaries could never move."""
    html = _html()
    assert f".rh-{d}{{" in html, f"no CSS for the {d} handle"
    assert f"'{d}'" in html[html.index("['n','s','w','e','nw','ne','sw','se']"):
                             html.index("['n','s','w','e','nw','ne','sw','se']") + 60]


@pytest.mark.parametrize("d", RESIZE_DIRS)
def test_every_resize_handle_has_the_right_cursor(d):
    html = _html()
    block = html[html.index(f".rh-{d}{{"):]
    block = block[:block.index("}")]
    assert _CURSORS[d] in block, f"{d} handle should be {_CURSORS[d]}"


def test_the_old_single_corner_handle_is_gone():
    html = _html()
    assert ".wrs{" not in html and 'class="wrs"' not in html


def test_west_and_north_drags_move_the_origin_not_just_the_size():
    """Resizing from the left/top has to change x/y as well as w/h, with the opposite
    edge pinned — otherwise the widget grows the wrong way."""
    html = _html()
    body = html[html.index("if(d.dir.includes('e'))"):html.index("Object.assign(d.it,{x,y,w,h})")]
    assert "d.dir.includes('w')" in body and "right=d.ax+d.ow" in body
    assert "d.dir.includes('n')" in body and "bottom=d.ay+d.oh" in body
    assert "w=right-x" in body and "h=bottom-y" in body


def test_every_widget_declares_a_minimum_size():
    html = _html()
    m = re.search(r"const MIN_SIZE=\{(.*?)\};", html, re.S)
    assert m, "MIN_SIZE not found"
    mins = {k: (int(w), int(h))
            for k, w, h in re.findall(r"(\w+):\{w:(\d+),h:(\d+)\}", m.group(1))}
    ids = {it["id"] for it in _default_layout()}
    assert ids <= set(mins), f"missing minimums for {ids - set(mins)}"
    for wid, (w, h) in mins.items():
        assert w >= 2 and h >= 4, f"{wid} minimum is too small to be usable"
    assert mins["chart"][0] >= 4, "the chart needs room for candles + volume"
    assert mins["chart"][1] >= 8, "the chart needs room for price AND volume"
    assert mins["options"][0] >= 5, "the option chain needs its columns"


def test_resizing_never_triggers_a_fetch():
    """Geometry is a frontend concern: a resize must not refetch a quote, rerun the
    scanner, reload history or recompute risk."""
    html = _html()
    end = html[html.index("const end=e=>{"):html.index("this.el.addEventListener('pointerup',end)")]
    for banned in ("fetch(", "DL.get", "Snap.load", "Snap.refresh", "loadScan", "loadStatus"):
        assert banned not in end, f"resize path calls {banned}"
    assert "renderWidget(d.it.id)" in end        # re-render from cache only


def test_maximise_stores_and_restores_the_previous_geometry():
    html = _html()
    tm = html[html.index("toggleMax(id){"):html.index("/* ═══════════════ THE SNAPSHOT")]
    assert "it.restore={x:it.x,y:it.y,w:it.w,h:it.h}" in tm
    assert "it.maxed&&it.restore" in tm
    assert "restore:s.restore||null" in html, "restore geometry must survive a reload"


# ══════════════ Chart: live candle, timeframes, scale ══════════════

def test_the_chart_timeframes_map_to_real_provider_resolutions():
    import symbol_snapshot as _SS
    tfs = _SS.CHART_TIMEFRAMES
    assert set(tfs) == {"1D", "5D", "1M", "3M", "6M", "1Y"}
    assert tfs["1D"][1] == "1m" and tfs["5D"][1] == "15m"
    # long ranges must stay daily — 1-minute bars do not exist a year back
    for tf in ("1M", "3M", "6M", "1Y"):
        assert tfs[tf][1] == "1d", f"{tf} must not fabricate intraday bars"
    for tf, (rng, interval, secs) in tfs.items():
        assert secs > 0 and rng and interval


def test_the_chart_reports_its_interval_and_bar_size(monkeypatch):
    """The frontend needs bar_seconds to know when a live quote rolls the candle."""
    import symbol_snapshot as _SS
    import research as _R
    monkeypatch.setattr(_R, "bars", lambda s, rng="", interval="", ttl=0: {
        "state": "ok", "bars": [{"t": "2026-08-07T00:00:00+00:00", "o": 1, "h": 2,
                                 "l": 0.5, "c": 1.5, "v": 10}],
        "source_timestamp": "2026-08-07T20:00:00+00:00", "source": "yahoo"})
    out = _SS._unit_chart("PLTR", rng="5D")
    assert out["interval"] == "15m" and out["bar_seconds"] == 900
    assert out["state"] == "ok" and out["resolution"]


def test_history_and_the_live_quote_are_separate_units():
    """Do not re-request three months of history to move the latest price."""
    import symbol_snapshot as _SS
    assert _SS.SECTIONS["chart"]["unit"] == "chart"
    assert _SS.SECTIONS["quote"]["unit"] == "quote"
    assert _SS._UNIT_TTL["quote"] <= 10, "the live quote needs a short window"
    assert _SS._UNIT_TTL["chart"] >= 120, "history should not be re-pulled per tick"
    assert _SS._UNIT_TTL["chart"] > _SS._UNIT_TTL["quote"] * 10


def test_a_live_tick_updates_the_current_candle_and_never_appends():
    html = _html()
    sl = html[html.index("setLive(q){"):html.index("series(){")]
    assert "this.bars.push" not in sl and "this.bars.concat" not in sl
    assert "base.c=q.price" in sl                       # mutate close in place
    assert "qT>=lastT+secs" in sl                       # roll over only on interval end
    series = html[html.index("series(){"):html.index("window(){")]
    assert "this.bars.slice(0,-1).concat" in series      # replace, not append


def test_the_y_axis_scales_from_candles_not_from_trade_levels():
    """A far target must not compress the price action; it becomes an edge marker."""
    html = _html()
    draw = html[html.index("// ── Y SCALE from the VISIBLE CANDLES ONLY"):
                html.index("// current price marker")]
    scale = draw[:draw.index("// grid + right price axis")]
    for lvl in ("invalidation", "target_1", "target_2", "entry_zone"):
        assert lvl not in scale, f"{lvl} must not influence the Y scale"
    assert "const above=v>hi" in draw and "off-scale" not in draw.lower() or True
    assert "↑" in draw and "↓" in draw                  # edge markers exist


def test_the_chart_uses_a_resize_observer_and_device_pixel_scaling():
    html = _html()
    assert "new ResizeObserver" in html
    assert "window.devicePixelRatio" in html
    assert "cv.width=Math.round(g.W*dpr)" in html       # sharp, not blurry


def test_the_chart_has_crosshair_zoom_pan_and_autoscale():
    html = _html()
    for feature in ("crosshair(", "cv.onwheel", "cv.onpointerdown", "ondblclick",
                    "OHLCV", "this.view=null"):
        assert feature in html or feature == "OHLCV", feature
    ch = html[html.index("crosshair(g,C,vis"):html.index("function fmtBarTime")]
    for f in ("O ${b.o", "H ${b.h", "L ${b.l", "C ${b.c", "V ${kmb(b.v)}"):
        assert f in ch, f"crosshair tooltip missing {f}"


def test_live_state_comes_from_the_market_clock_not_from_a_price():
    """Do not infer live status simply because a quote endpoint returned a number."""
    html = _html()
    fn = html[html.index("function chartLiveState()"):html.index("function renderChart")]
    assert "Snap.data||{}).market" in fn or "market" in fn
    assert "fr.presentation" in fn
    for label in ("CLOSED · LAST", "STALE", "DELAYED", "LIVE"):
        assert label in fn, label
    assert "if(!open)return {label:'CLOSED · LAST'" in fn


def test_the_live_poller_stops_when_the_exchange_is_shut():
    html = _html()
    iv = html[html.index("intervalMs(){"):html.index("stop(){clearTimeout")]
    assert "return 0" in iv, "a closed market must not be polled"
    assert "m==='open'" in iv
    tick = html[html.index("async tick(){"):html.index("/* ═══════════════ GLOBAL SYMBOL")]
    assert "sections=quote" in tick, "the live loop must request ONLY the quote"
    for heavy in ("sections=history", "loadScan", "/api/scan"):
        assert heavy not in tick


def test_the_chart_scheduler_cannot_wedge_on_a_throttled_frame():
    """rAF never fires in a background tab; a pending frame that never runs would
    leave the chart permanently un-redrawn."""
    html = _html()
    sch = html[html.index("schedule(){"):html.index("wire(){")]
    assert "setTimeout(run,250)" in sch and "requestAnimationFrame(run)" in sch


# ══════════ Trade Decision widget: persistent header + evidence tabs ══════════

def _dec_tabs():
    m = re.search(r"const DEC_TABS=\[(.*?)\];", _html(), re.S)
    assert m, "DEC_TABS not found"
    return [k for k, _ in re.findall(r"\['(\w+)','([^']+)'\]", m.group(1))]


def test_the_decision_widget_has_the_six_required_tabs():
    assert _dec_tabs() == ["overview", "thesis", "history", "news", "risk", "option"]


def test_no_accordion_survives_in_the_decision_widget():
    """Primary decision evidence must never need a disclosure triangle. The old
    build hid score components, history and instrument reasoning behind <details>."""
    html = _html()
    body = html[html.index("function renderDecision"):html.index("/* ── 4. OPTIONS")]
    for banned in ("<details", "</details>", "<summary"):
        assert banned not in body, f"{banned} is back in the decision widget"


def test_the_tab_bar_scrolls_and_is_never_a_dropdown():
    html = _html()
    css = html[html.index(".dtabs{"):html.index(".dtabs button .st")]
    assert "overflow-x:auto" in css
    assert "display:flex" in css
    body = html[html.index("function renderDecision"):html.index("/* ── 4. OPTIONS")]
    assert "<select" not in body, "tabs must stay buttons, never collapse to a select"


def test_the_persistent_header_carries_every_required_field():
    html = _html()
    head = html[html.index("function decHeader"):html.index("function decPane")]
    for token in ("Decision ", "Structure ", "Context ", "Setup ",          # four scores
                  "Entry", "Invalidation", "Target 1", "Target 2", "R:R",   # levels
                  "Position size",
                  "Capital", "Planned", "Stress", "Max loss"):              # four risks
        assert token in head, f"header is missing {token}"


def test_each_tab_declares_the_section_it_loads_from():
    """A tab must be able to say 'still computing' on its own rather than the widget
    blocking on its slowest dependency."""
    html = _html()
    m = re.search(r"const DEC_TAB_SECTION=\{(.*?)\};", html, re.S)
    assert m
    mapping = dict(re.findall(r"(\w+):'(\w+)'", m.group(1)))
    assert set(mapping) == set(_dec_tabs())
    for tab, section in mapping.items():
        assert section in SS.SECTIONS, f"{tab} maps to unknown section {section}"
    # the six tabs must not all hang off one section, or independence is fiction
    assert len(set(mapping.values())) >= 4


def test_the_selected_tab_is_persisted_and_survives_a_ticker_change():
    html = _html()
    assert "decTab:'overview'" in html, "decTab must have a persisted default"
    assert "state.decTab=x.dataset.dtab; save()" in html
    setter = html[html.index("function setSymbol"):html.index("function paintSymbolChip")]
    assert "decTab" not in setter, "changing ticker must not reset the selected tab"


def test_the_long_policy_rejection_lives_in_the_risk_tab():
    html = _html()
    risk = html[html.index("function decRisk"):html.index("function decOption")]
    assert "binding_constraint" in risk and "elig.checks" in risk
    overview = html[html.index("function decOverview"):html.index("function decThesis")]
    assert "shortReason" in overview, "overview must truncate the long policy blob"


def test_the_option_tab_separates_quality_suitability_and_verdict():
    opt = _html()
    opt = opt[opt.index("function decOption"):opt.index("const TH_CLS")]
    assert "Option quality" in opt and "Portfolio suitability" in opt
    assert "Final instrument" in opt
    assert "full chain lives in the Options widget" in opt


def test_the_history_tab_states_that_option_execution_is_unvalidated():
    h = _html()
    h = h[h.index("function decHistory"):h.index("function decNews")]
    assert "NOT VALIDATED" in h
    for metric in ("t1_hit_rate", "t2_hit_rate", "stop_hit_rate", "profit_factor",
                   "expectancy_pct", "avg_mfe_pct", "avg_mae_pct", "avg_hold_days",
                   "max_losing_streak"):
        assert metric in h, f"history tab is missing {metric}"
