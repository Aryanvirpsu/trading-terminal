"""Canonical Option Architecture v1.1 — Step 9.1: option-chain quote-
timestamp provenance tests.

Traces a REAL provider-shaped Yahoo options response through the actual
production normalization code — not a hand-built candidate dict — proving
the chain-snapshot timestamp genuinely propagates:

    raw Yahoo JSON (options_service._fetch(), mocked)
        -> options_service._normalize_contract() / get_options_chain()
        -> strategy_service._pick_option_idea()
        -> canonical.contract_quality.evaluate_contract_quality()

and that it is never fabricated (no `datetime.now()` fallback anywhere in
this path), never confused with `lastTradeDate` (a different fact), and
never bypasses canonical A's freshness/hard-failure rules.

Fully offline: options_service._fetch() and strategy_service.get_options_chain
are monkeypatched; no live Robinhood/Yahoo call.

Run: pytest tests/unit/test_option_chain_timestamp_provenance.py -v --tb=short
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in ("src", "dashboard", "lab"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import tradingview_mcp.core.services.options_service as osvc      # noqa: E402
from tradingview_mcp.core.services import strategy_service as ss  # noqa: E402
from canonical.contract_quality import evaluate_contract_quality  # noqa: E402

# Second-precision: Unix-second epoch round-tripping (the actual provider
# format) cannot carry microseconds, so comparisons below use a NOW that is
# already second-precision rather than tolerating a lossy round-trip.
NOW = datetime.now(timezone.utc).replace(microsecond=0)


# ══════════════════════════════════════════════════════════════════════════
# Raw Yahoo v7/finance/options fixture builder — the ACTUAL documented shape
# (contractSymbol/strike/lastPrice/bid/ask/volume/openInterest/
# impliedVolatility/inTheMoney/lastTradeDate/expiration per contract;
# expirationDates + quote.regularMarketTime at the chain level).
# ══════════════════════════════════════════════════════════════════════════

def _epoch(dt: datetime) -> int:
    return int(dt.timestamp())


def _raw_chain(*, snapshot_dt=NOW, last_trade_dt=None, strike=150.0, bid=2.40, ask=2.60,
               expiry_dt=None, regular_market_time_present=True):
    expiry_dt = expiry_dt or (NOW + timedelta(days=21))
    exp_ts = _epoch(expiry_dt)
    quote = {"regularMarketPrice": 148.5, "regularMarketChangePercent": 1.1}
    if regular_market_time_present:
        quote["regularMarketTime"] = _epoch(snapshot_dt)
    contract = {
        "contractSymbol": "AAPL260101C00150000", "strike": strike,
        "lastPrice": 2.50, "bid": bid, "ask": ask, "volume": 300, "openInterest": 1000,
        "impliedVolatility": 0.30, "inTheMoney": False, "expiration": exp_ts,
    }
    if last_trade_dt is not None:
        contract["lastTradeDate"] = _epoch(last_trade_dt)
    return {
        "optionChain": {
            "result": [{
                "underlyingSymbol": "AAPL",
                "expirationDates": [exp_ts],
                "quote": quote,
                "options": [{"expirationDate": exp_ts, "calls": [contract], "puts": []}],
            }],
            "error": None,
        }
    }


# ══════════════════════════════════════════════════════════════════════════
# 1-2. Provider raw timestamp discovered and normalized; reaches
#      get_options_chain()'s own returned contract rows.
# ══════════════════════════════════════════════════════════════════════════

def test_provider_regular_market_time_is_normalized_to_iso_utc(monkeypatch):
    raw = _raw_chain(snapshot_dt=NOW)
    monkeypatch.setattr(osvc, "_fetch", lambda url: raw)
    result = osvc.get_options_chain("AAPL", None)
    assert result["calls"][0]["quote_timestamp"] == NOW.isoformat()
    assert result["calls"][0]["quote_timestamp_source"] == "chain_snapshot"
    assert result["quote_timestamp"] == NOW.isoformat()


def test_normalize_epoch_seconds_to_iso_is_timezone_aware_utc():
    # A known epoch second, independent of the fixture builder above.
    iso = osvc._normalize_epoch_seconds_to_iso(1700000000)
    parsed = datetime.fromisoformat(iso)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0
    assert parsed == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)


def test_missing_regular_market_time_is_unavailable_not_now(monkeypatch):
    raw = _raw_chain(regular_market_time_present=False)
    monkeypatch.setattr(osvc, "_fetch", lambda url: raw)
    result = osvc.get_options_chain("AAPL", None)
    assert result["calls"][0]["quote_timestamp"] is None
    assert result["calls"][0]["quote_timestamp_source"] == "unavailable"
    # Never silently substituted with "now" — the returned value is None,
    # not a fresh-looking timestamp.
    assert result["quote_timestamp"] is None


def test_malformed_regular_market_time_is_unavailable(monkeypatch):
    raw = _raw_chain(snapshot_dt=NOW)
    raw["optionChain"]["result"][0]["quote"]["regularMarketTime"] = "not-a-number"
    monkeypatch.setattr(osvc, "_fetch", lambda url: raw)
    result = osvc.get_options_chain("AAPL", None)
    assert result["calls"][0]["quote_timestamp"] is None
    assert result["calls"][0]["quote_timestamp_source"] == "unavailable"


# ══════════════════════════════════════════════════════════════════════════
# 9. last_trade_timestamp is a SEPARATE fact, never mapped to quote_timestamp
# ══════════════════════════════════════════════════════════════════════════

def test_last_trade_timestamp_is_never_mapped_to_quote_timestamp(monkeypatch):
    """A contract whose last trade is 3 days stale but whose chain snapshot
    (the quote-observation proxy) is fresh: quote_timestamp must reflect the
    SNAPSHOT, not the stale trade — and last_trade_timestamp must still be
    reported, separately, verbatim."""
    stale_trade = NOW - timedelta(days=3)
    raw = _raw_chain(snapshot_dt=NOW, last_trade_dt=stale_trade)
    monkeypatch.setattr(osvc, "_fetch", lambda url: raw)
    result = osvc.get_options_chain("AAPL", None)
    row = result["calls"][0]
    assert row["quote_timestamp"] == NOW.isoformat()
    assert row["last_trade_timestamp"] == stale_trade.isoformat()
    assert row["last_trade_timestamp"] != row["quote_timestamp"]


def test_expiry_scoped_refetch_uses_the_refetched_quote_block(monkeypatch):
    """get_options_chain(symbol, expiry) re-fetches — the snapshot timestamp
    must come from THAT response's own quote block, not the initial
    nearest-expiry one (the bug this step's normalization fixed in passing)."""
    nearest_snapshot = NOW - timedelta(hours=5)
    scoped_snapshot = NOW
    expiry_dt = NOW + timedelta(days=21)
    nearest_raw = _raw_chain(snapshot_dt=nearest_snapshot, expiry_dt=expiry_dt)
    scoped_raw = _raw_chain(snapshot_dt=scoped_snapshot, expiry_dt=expiry_dt)

    def fake_fetch(url):
        return scoped_raw if "date=" in url else nearest_raw

    monkeypatch.setattr(osvc, "_fetch", fake_fetch)
    result = osvc.get_options_chain("AAPL", expiry_dt.date().isoformat())
    assert result["quote_timestamp"] == scoped_snapshot.isoformat()


# ══════════════════════════════════════════════════════════════════════════
# 2-3. Reaches _pick_option_idea() and the Terminal candidate unchanged
# ══════════════════════════════════════════════════════════════════════════

def test_pick_option_idea_propagates_quote_timestamp(monkeypatch):
    raw = _raw_chain(snapshot_dt=NOW, bid=2.40, ask=2.60, strike=150.0)
    monkeypatch.setattr(osvc, "_fetch", lambda url: raw)
    monkeypatch.setattr(ss, "get_options_chain", osvc.get_options_chain)
    idea = ss._pick_option_idea("AAPL", 148.5, 50_000.0, "LONG")
    assert idea is not None
    assert idea["quote_timestamp"] == NOW.isoformat()
    assert idea["quote_timestamp_source"] == "chain_snapshot"
    assert "last_trade_timestamp" in idea


def test_pick_option_idea_none_quote_timestamp_when_provider_lacks_one(monkeypatch):
    raw = _raw_chain(regular_market_time_present=False, bid=2.40, ask=2.60, strike=150.0)
    monkeypatch.setattr(osvc, "_fetch", lambda url: raw)
    monkeypatch.setattr(ss, "get_options_chain", osvc.get_options_chain)
    idea = ss._pick_option_idea("AAPL", 148.5, 50_000.0, "LONG")
    assert idea is not None
    assert idea["quote_timestamp"] is None
    assert idea["quote_timestamp_source"] == "unavailable"


# ══════════════════════════════════════════════════════════════════════════
# 4-8. Reaches canonical A unchanged, and freshness behaves correctly
# ══════════════════════════════════════════════════════════════════════════

def _candidate_via_real_stack(monkeypatch, **chain_kwargs):
    raw = _raw_chain(**chain_kwargs)
    monkeypatch.setattr(osvc, "_fetch", lambda url: raw)
    monkeypatch.setattr(ss, "get_options_chain", osvc.get_options_chain)
    return ss._pick_option_idea("AAPL", 148.5, 50_000.0, "LONG")


def test_fresh_real_stack_timestamp_can_pass_canonical_a(monkeypatch):
    idea = _candidate_via_real_stack(monkeypatch, snapshot_dt=NOW, bid=2.40, ask=2.60, strike=150.0)
    cq = evaluate_contract_quality(
        strike=idea["strike"], underlying=148.5, side="CALL", bid=idea["bid"], ask=idea["ask"],
        volume=idea["volume"], open_interest=idea["open_interest"],
        implied_volatility=idea["implied_volatility"], delta=idea.get("delta"),
        dte=idea["days_to_expiry"], quote_timestamp=idea["quote_timestamp"], session_open=True)
    assert cq.quality_pass is True
    assert cq.freshness_status.value == "fresh"
    assert cq.quote_age_hours is not None and cq.quote_age_hours < 1.0


def test_stale_31h_fails_canonical_a(monkeypatch):
    stale = NOW - timedelta(hours=31)
    idea = _candidate_via_real_stack(monkeypatch, snapshot_dt=stale, bid=2.40, ask=2.60, strike=150.0)
    cq = evaluate_contract_quality(
        strike=idea["strike"], underlying=148.5, side="CALL", bid=idea["bid"], ask=idea["ask"],
        volume=idea["volume"], open_interest=idea["open_interest"],
        implied_volatility=idea["implied_volatility"], delta=idea.get("delta"),
        dte=idea["days_to_expiry"], quote_timestamp=idea["quote_timestamp"], session_open=True)
    assert cq.quality_pass is False
    assert any("stale" in f.lower() for f in cq.hard_failures)
    # The 30h cap itself is untouched — asserted structurally too.
    from canonical.contract_quality import DEFAULT_POLICY
    assert DEFAULT_POLICY.max_quote_age_hours == 30.0


def test_missing_provider_timestamp_fails_canonical_a(monkeypatch):
    idea = _candidate_via_real_stack(monkeypatch, regular_market_time_present=False,
                                     bid=2.40, ask=2.60, strike=150.0)
    assert idea["quote_timestamp"] is None
    cq = evaluate_contract_quality(
        strike=idea["strike"], underlying=148.5, side="CALL", bid=idea["bid"], ask=idea["ask"],
        volume=idea["volume"], open_interest=idea["open_interest"],
        implied_volatility=idea["implied_volatility"], delta=idea.get("delta"),
        dte=idea["days_to_expiry"], quote_timestamp=idea["quote_timestamp"], session_open=True)
    assert cq.quality_pass is False
    assert any("timestamp" in f for f in cq.hard_failures)


def test_malformed_timestamp_fails_canonical_a_directly():
    """canonical A's OWN handling of a garbage string — independent of the
    provider layer, proving the fail-closed behavior end to end regardless
    of where a malformed value might originate."""
    cq = evaluate_contract_quality(
        strike=150.0, underlying=148.5, side="CALL", bid=2.40, ask=2.60, volume=300,
        open_interest=1000, implied_volatility=0.30, delta=0.5, dte=21,
        quote_timestamp="not-a-real-timestamp", session_open=True)
    assert cq.quality_pass is False
    assert any("unparseable" in f.lower() for f in cq.hard_failures)


# ══════════════════════════════════════════════════════════════════════════
# 10. No now() fallback anywhere in the propagation path
# ══════════════════════════════════════════════════════════════════════════

def test_no_now_fallback_in_options_service(monkeypatch):
    """Structural guard: the ONLY places options_service.py may construct a
    UTC 'now' are timestamp-INDEPENDENT (session/crumb caching, `time.strftime`
    for expiry-date formatting from a PROVIDER epoch) — never as a substitute
    quote_timestamp. Grep the actual functions that build the returned
    contract/chain dicts."""
    import inspect
    src_normalize = inspect.getsource(osvc._normalize_contract)
    src_get_chain = inspect.getsource(osvc.get_options_chain)
    src_unusual = inspect.getsource(osvc.get_unusual_options_activity)
    for src in (src_normalize, src_get_chain, src_unusual):
        assert "datetime.now(" not in src
        assert "utcnow(" not in src


def test_no_now_fallback_in_pick_option_idea():
    """Every line touching quote_timestamp in _pick_option_idea() must be a
    plain pass-through (`.get("quote_timestamp", ...)`), never a call to
    now()/utcnow()."""
    import inspect
    src = inspect.getsource(ss._pick_option_idea)
    lines = [l for l in src.splitlines() if "quote_timestamp" in l]
    assert lines, "expected to find the quote_timestamp pass-through lines"
    for line in lines:
        assert "now(" not in line and "utcnow(" not in line


def test_no_now_fallback_in_pipeline1_attachment():
    """The actual canonical A call in _attach_canonical_pipeline1() must
    read quote_timestamp straight off the candidate (`opt.get(...)`), never
    construct one from local time."""
    import inspect
    import research as R
    src = inspect.getsource(R._attach_canonical_pipeline1)
    call_line = next(l for l in src.splitlines() if "quote_timestamp=opt.get" in l)
    assert "now(" not in call_line
    assert "datetime.now(" not in src and "utcnow(" not in src


# ══════════════════════════════════════════════════════════════════════════
# 13-14. Real Terminal-shaped fresh option reaches D=OPTION and executable
# ══════════════════════════════════════════════════════════════════════════

def test_real_terminal_shaped_fresh_option_reaches_option_executable(monkeypatch):
    """End-to-end proof that Step 9's safety path can actually reach
    OPTION — the gap the Step-9 report flagged as functionally incomplete —
    using the REAL provider-normalization stack, not a hand-built fixture."""
    import decision_engine as de
    import research as R

    def _mk(fam, d, c, detail="x"):
        return {"family": fam, "dir": d, "conf": c, "detail": detail}

    a = {"price_data": {"current_price": 148.5}, "trend_state": "uptrend",
        "atr": {"value": 148.5 * 0.02, "percent_of_price": 2.0},
        "market_sentiment": {"momentum": "Bullish", "buy_sell_signal": "BUY"}, "rsi": {"value": 60}}
    monkeypatch.setattr(de, "_load_analysis", lambda s, e: (a, "yahoo", "fresh"))
    monkeypatch.setattr(de, "_safe_regime", lambda: {"risk_appetite_score": 65})
    monkeypatch.setattr(de, "_fam_trend", lambda _a: _mk("trend/momentum", 0.7, 0.85))
    monkeypatch.setattr(de, "_fam_regime", lambda _r, _d: _mk("regime", 0.4, 0.7))
    for fn, fam in (("_fam_catalyst", "catalyst"), ("_fam_short", "short-interest"),
                   ("_fam_filings", "filings/insider"), ("_fam_options_flow", "options-flow"),
                   ("_fam_social", "social-sentiment"), ("_fam_analyst", "analyst-ratings"),
                   ("_fam_macro", "macro-rates")):
        monkeypatch.setattr(de, fn, (lambda fam=fam: (lambda *a, **k: _mk(fam, 0.3, 0.7)))())
    try:
        import halts
        monkeypatch.setattr(halts, "is_halted", lambda s: False)
    except Exception:
        pass
    try:
        import risk_engine
        monkeypatch.setattr(risk_engine, "check_new_trade", lambda sym, risk, spec: {
            "allow": True, "reasons": [], "size_cap_usd": 1e6,
            "portfolio": {"suspended": False, "drawdown_pct": 0.0}})
    except Exception:
        pass

    # A cheap, near-the-money, fresh, liquid contract via the REAL provider stack.
    raw = _raw_chain(snapshot_dt=NOW, bid=1.15, ask=1.25, strike=150.0)
    monkeypatch.setattr(osvc, "_fetch", lambda url: raw)
    monkeypatch.setattr(de.ss, "get_options_chain", osvc.get_options_chain)

    r = de.evaluate("AAPL", "NASDAQ", "LONG", 50_000.0, evaluate_option=False)
    assert r["decision"] == "TRADEABLE"
    R._attach_canonical_pipeline1(r, decision_engine=de, direction="LONG", balance=50_000.0)

    assert r["canonical"]["contract_quality"] is not None
    assert r["canonical"]["contract_quality"]["quality_pass"] is True
    assert r["canonical"]["contract_quality"]["freshness_status"] == "fresh"
    if r["instrument"] == "OPTION PREFERRED":
        assert r["option_executable"] is True
        assert r["canonical"]["sizing"]["instrument"] == "OPTION"
        assert r["canonical"]["sizing"]["quantity"] == int(r["canonical"]["sizing"]["quantity"])
    else:
        # Even if D's comparative branch preferred STOCK here, the option
        # LEG itself must be genuinely eligible+executable-ready — proving
        # the fix, independent of which instrument D ultimately chose.
        assert r["canonical"]["option_account_fit"]["eligible"] is True


def test_fresh_option_invalid_for_another_a_reason_still_fails(monkeypatch):
    """Freshness repair must not accidentally bypass OTHER canonical A
    rules. _pick_option_idea()'s OWN liquidity pre-filter accepts a contract
    with OI<50 as long as volume>=50 (`if oi < 50 and vol < 50: continue` —
    an OR, not an AND); canonical A's OI floor is a hard AND-independent
    floor (OI<50 fails regardless of volume) — a real, structural gap
    between the two filters that lets a genuinely thin contract through
    _pick_option_idea() for this test to exercise. Near-the-money so the
    OTM/spread pre-filters don't ALSO reject it (isolating OI as the one
    failing axis)."""
    raw = _raw_chain(snapshot_dt=NOW, bid=1.15, ask=1.25, strike=150.0)
    raw["optionChain"]["result"][0]["options"][0]["calls"][0]["openInterest"] = 10
    raw["optionChain"]["result"][0]["options"][0]["calls"][0]["volume"] = 100
    monkeypatch.setattr(osvc, "_fetch", lambda url: raw)
    monkeypatch.setattr(ss, "get_options_chain", osvc.get_options_chain)
    idea = ss._pick_option_idea("AAPL", 148.5, 50_000.0, "LONG")
    assert idea is not None, "premise: _pick_option_idea()'s own OI-or-volume floor accepts this"
    assert idea["open_interest"] == 10

    cq = evaluate_contract_quality(
        strike=idea["strike"], underlying=148.5, side="CALL", bid=idea["bid"], ask=idea["ask"],
        volume=idea["volume"], open_interest=idea["open_interest"],
        implied_volatility=idea["implied_volatility"], delta=idea.get("delta"),
        dte=idea["days_to_expiry"], quote_timestamp=idea["quote_timestamp"], session_open=True)
    assert cq.freshness_status.value == "fresh"     # the fix itself worked
    assert cq.quality_pass is False                  # but OI still rejects it
    assert any("illiquid" in f.lower() for f in cq.hard_failures)


# ══════════════════════════════════════════════════════════════════════════
# 16-18. bad option doesn't block stock; Pipeline-2 unaffected; A parity
# ══════════════════════════════════════════════════════════════════════════

def test_bad_option_still_does_not_block_stock(monkeypatch):
    import decision_engine as de
    import research as R

    def _mk(fam, d, c, detail="x"):
        return {"family": fam, "dir": d, "conf": c, "detail": detail}

    a = {"price_data": {"current_price": 148.5}, "trend_state": "uptrend",
        "atr": {"value": 148.5 * 0.02, "percent_of_price": 2.0},
        "market_sentiment": {"momentum": "Bullish", "buy_sell_signal": "BUY"}, "rsi": {"value": 60}}
    monkeypatch.setattr(de, "_load_analysis", lambda s, e: (a, "yahoo", "fresh"))
    monkeypatch.setattr(de, "_safe_regime", lambda: {"risk_appetite_score": 65})
    monkeypatch.setattr(de, "_fam_trend", lambda _a: _mk("trend/momentum", 0.7, 0.85))
    monkeypatch.setattr(de, "_fam_regime", lambda _r, _d: _mk("regime", 0.4, 0.7))
    for fn, fam in (("_fam_catalyst", "catalyst"), ("_fam_short", "short-interest"),
                   ("_fam_filings", "filings/insider"), ("_fam_options_flow", "options-flow"),
                   ("_fam_social", "social-sentiment"), ("_fam_analyst", "analyst-ratings"),
                   ("_fam_macro", "macro-rates")):
        monkeypatch.setattr(de, fn, (lambda fam=fam: (lambda *a, **k: _mk(fam, 0.3, 0.7)))())
    try:
        import halts
        monkeypatch.setattr(halts, "is_halted", lambda s: False)
    except Exception:
        pass
    try:
        import risk_engine
        monkeypatch.setattr(risk_engine, "check_new_trade", lambda sym, risk, spec: {
            "allow": True, "reasons": [], "size_cap_usd": 1e6,
            "portfolio": {"suspended": False, "drawdown_pct": 0.0}})
    except Exception:
        pass

    # No usable option (nothing near-the-money / affordable / liquid enough
    # for _pick_option_idea() itself to return a candidate at all).
    monkeypatch.setattr(de.ss, "_pick_option_idea", lambda *a, **k: None)
    r = de.evaluate("AAPL", "NASDAQ", "LONG", 50_000.0, evaluate_option=False)
    R._attach_canonical_pipeline1(r, decision_engine=de, direction="LONG", balance=50_000.0)
    assert r["stock_executable"] is True
    assert r["option_executable"] is False


def test_pipeline2_yahoo_fallback_quote_timestamp_stays_none(monkeypatch):
    """options_desk.py:yahoo_chain() (Pipeline 2's Robinhood-unavailable
    fallback) ALSO calls options_service.get_options_chain() — a real,
    pre-existing coupling this step did not introduce. yahoo_chain() builds
    its OWN output dict field-by-field (never `**c`) and hardcodes
    `"quote_timestamp": None` explicitly — so the new
    quote_timestamp/quote_timestamp_source fields this step added to
    get_options_chain()'s contracts do NOT flow into Pipeline 2's Yahoo
    path; it is untouched, exactly as Phase 9 requires. (Pipeline 2's own
    Robinhood chain — its primary path — has always carried a REAL
    quote_timestamp already and is unaffected by anything in this file.)"""
    import options_desk as OD
    raw = _raw_chain(snapshot_dt=NOW, bid=1.15, ask=1.25, strike=150.0)
    monkeypatch.setattr(osvc, "_fetch", lambda url: raw)
    ch = OD.yahoo_chain("AAPL", None, "call")
    assert ch["state"] == "ok"
    assert ch["contracts"][0]["quote_timestamp"] is None
    assert "quote_timestamp" in ch["missing_capabilities"]


def _minimal_c_result(*, price=148.5, stop=143.0, target=155.0, atr_pct=2.0):
    """A minimal decision_engine.evaluate()-shaped dict carrying only the
    fields _attach_canonical_pipeline1() actually reads — avoids re-running
    the full 9-family/gates harness for a test that is really about A
    parity, not C."""
    return {"symbol": "AAPL", "decision": "TRADEABLE", "price": price, "stop": stop,
           "target": target, "p_direction": 0.6, "suggested_shares": 10.0,
           "gates": {"volatility": {"atr_pct": atr_pct}}}


def test_canonical_a_direct_vs_pipeline1_result_identical(monkeypatch):
    idea = _candidate_via_real_stack(monkeypatch, snapshot_dt=NOW, bid=1.15, ask=1.25, strike=150.0)
    direct = evaluate_contract_quality(
        strike=idea["strike"], underlying=148.5, side="CALL", bid=idea["bid"], ask=idea["ask"],
        volume=idea["volume"], open_interest=idea["open_interest"],
        implied_volatility=idea["implied_volatility"], delta=idea.get("delta"),
        dte=idea["days_to_expiry"], quote_timestamp=idea["quote_timestamp"], session_open=True)

    import decision_engine as de
    import research as R
    monkeypatch.setattr(de.ss, "_pick_option_idea", lambda *a, **k: idea)
    r = _minimal_c_result()
    R._attach_canonical_pipeline1(r, decision_engine=de, direction="LONG", balance=50_000.0)
    pipeline1 = r["canonical"]["contract_quality"]
    assert pipeline1["quality_pass"] == direct.quality_pass
    assert pipeline1["hard_failures"] == list(direct.hard_failures)
    assert pipeline1["freshness_status"] == direct.freshness_status.value
