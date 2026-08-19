"""ONE canonical snapshot of the SELECTED SYMBOL, assembled for the terminal.

The terminal revolves around a single global symbol. Before this module the frontend
stitched that view together itself from eight endpoints with eight different notions
of "when was this true", which is how a screen came to show `DATA stale` next to
`scanner refreshing` next to a confident OPTION PREFERRED badge.

Three rules this module exists to enforce:

  1. **One compute per fact.** Five underlying units produce everything:

         quote     research.quote                  (live price)
         chart     research.price_history          (bars)
         analysis  scanner.analyse_symbol          (indicators, setups, levels, score)
         desk      options_desk.evaluate_candidate (chain, best contract, verdict, sizing)
         news      research.catalysts              (headlines + earnings)
         account   options_desk.account + paper    (buying power, positions, exposure)

     Sections are VIEWS over those units. `decision` and `options` read the same desk
     result, `technicals` and `levels` the same analysis — so a widget can never
     disagree with the widget next to it, and selecting a symbol costs one pass.

  2. **Nothing blocks on anything else.** Every unit is stale-while-revalidate with a
     non-blocking cold miss, so a slow news provider returns `state:"loading"` for the
     news section while the chart and the chain render. A unit that raises returns
     `state:"error"` for its own sections only.

  3. **Freshness is per-dataset and typed.** Each section carries its OWN freshness
     record from the shared classifier (`lab/freshness.py`) against its OWN window: a
     90-second-old quote is DELAYED, a 90-second-old earnings date is LIVE. One global
     age would describe neither. When a CRITICAL input is stale the decision section is
     gated — the verdict is replaced, never left looking current.

Nothing here places, previews or queues an order.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in ("..", os.path.join("..", "lab"), os.path.join("..", "src")):
    sys.path.insert(0, os.path.join(_HERE, _p))
sys.path.insert(0, _HERE)

import research as _R
import freshness as _fr


# ── Sections ─────────────────────────────────────────────────────────────────
# `unit` is which compute produces it; `kind` is the freshness window it is judged
# against; `critical` marks the inputs a live trade decision may not be stale on.

SECTIONS: Dict[str, Dict[str, Any]] = {
    "quote":      {"unit": "quote",      "kind": "quote",        "critical": True},
    "chart":      {"unit": "chart",      "kind": "quote",        "critical": False},
    "technicals": {"unit": "analysis",   "kind": "levels",       "critical": False},
    "levels":     {"unit": "analysis",   "kind": "levels",       "critical": True},
    # The decision now reads the ASSESSMENT (setup + news + catalysts + history +
    # desk verdict), not the desk alone. The desk still owns the risk verdict.
    "decision":   {"unit": "assessment", "kind": "option_chain", "critical": False},
    "thesis":     {"unit": "assessment", "kind": "option_chain", "critical": False},
    "options":    {"unit": "desk",       "kind": "option_chain", "critical": False},
    "news":       {"unit": "newsig",     "kind": "news",         "critical": False},
    "catalysts":  {"unit": "newsig",     "kind": "news",         "critical": False},
    # Historical validation is technical/regime only and is judged against the
    # `fundamental` window: a strategy's 10-year record does not go stale in an hour.
    "history":    {"unit": "history",    "kind": "fundamental",  "critical": False},
    "risk":       {"unit": "account",    "kind": "account",      "critical": True},
    "position":   {"unit": "account",    "kind": "account",      "critical": False},
}

DEFAULT_SECTIONS = tuple(SECTIONS)

# Per-unit cache windows. These are CACHE lifetimes (how long before we refetch),
# deliberately separate from the FRESHNESS windows above (how old before we warn) —
# conflating the two is how a cache hit came to be reported as "live" data.
# The broker account goes over the Robinhood MCP and measures ~17s cold, so it gets a
# long window: buying power does not move between polls, and SWR serves the previous
# value instantly while it refreshes. The freshness badge still ages it honestly.
QUOTE_TTL = float(os.environ.get("TERMINAL_QUOTE_TTL_S", 5.0))

_UNIT_TTL = {"quote": QUOTE_TTL, "chart": 300.0, "analysis": 120.0,
             "desk": 180.0, "news": 600.0, "newsig": 600.0, "account": 180.0,
             # A 10-year strategy record does not change between polls; it is
             # expensive (re-running the scanner over ~1200 bars) and long-lived.
             "history": 6 * 3600.0, "assessment": 120.0}

# Units whose cold miss must never block a request. Only the quote is allowed to be
# computed inline: it is one cheap call and the header is useless without a price.
# Everything else — including the broker account, which goes over the Robinhood MCP and
# can take ten seconds — fills in on a later poll so the shell paints immediately.
_ASYNC_UNITS = ("analysis", "desk", "news", "newsig", "account", "chart",
                "history", "assessment")


def _market_state() -> Optional[str]:
    try:
        import market_regime as _MR
        return _MR.session_state()["state"]
    except Exception:  # noqa: BLE001
        return None


def _iso_age(ts: Optional[str]) -> Optional[float]:
    """Age in seconds from an ISO timestamp, daily-bar aware."""
    return _fr.bar_age_seconds(ts) if ts else None


# ── The units ────────────────────────────────────────────────────────────────

def _unit_quote(sym: str) -> Dict[str, Any]:
    """The live price. Short TTL on purpose: this is the ONLY thing that has to move
    tick-by-tick, and it is one cheap call — the chart drives its current candle from
    here instead of re-pulling history."""
    return _R.quote(sym, ttl=QUOTE_TTL)


# Timeframe -> (provider range, bar interval, seconds per bar). The interval is
# REPORTED so the chart can say what resolution it is actually drawing and know when a
# live quote rolls the current candle over. Nothing here fabricates a resolution the
# provider does not serve: 1-minute bars exist only for recent days, so the longer
# timeframes stay daily rather than pretending to be intraday.
CHART_TIMEFRAMES: Dict[str, tuple] = {
    "1D": ("1d", "1m", 60),
    "5D": ("5d", "15m", 900),
    "1M": ("1mo", "1d", 86400),
    "3M": ("3mo", "1d", 86400),
    "6M": ("6mo", "1d", 86400),
    "1Y": ("1y", "1d", 86400),
}


def _unit_chart(sym: str, rng: str = "3M") -> Dict[str, Any]:
    """HISTORICAL bars only. The live price is a separate unit (`quote`) so the chart
    never re-pulls three months of history to move the last candle."""
    tf = (rng or "3M").upper()
    prange, interval, secs = CHART_TIMEFRAMES.get(tf, CHART_TIMEFRAMES["3M"])
    b = _R.bars(sym, rng=prange, interval=interval,
                ttl=120.0 if secs < 86400 else 600.0)
    bars = [x for x in (b.get("bars") or []) if x.get("c") is not None]
    if b.get("state") != "ok" or not bars:
        return {"state": b.get("state", "error"), "symbol": sym, "range": tf,
                "interval": interval,
                "reason": b.get("reason") or f"no {interval} history for {tf}"}
    first, last = bars[0]["c"], bars[-1]["c"]
    return {"state": "ok", "symbol": sym, "range": tf,
            "interval": interval, "bar_seconds": secs,
            "provider_range": prange,
            "points": bars, "count": len(bars),
            "change_pct": round((last - first) / first * 100, 2) if first else None,
            "as_of": bars[-1].get("t"),
            "source_timestamp": b.get("source_timestamp"),
            "source": b.get("source"),
            "resolution": f"{interval} bars over {prange}"}


def _unit_analysis(sym: str) -> Dict[str, Any]:
    import scanner as _SC
    # Reuse the funnel's own candidate when the symbol is ALREADY in a cached scan —
    # it carries catalyst/sentiment-enriched scoring and keeps the scanner row and the
    # terminal in agreement. `peek` is deliberate: `scan_cached` would START a universe
    # scan on a miss, and selecting a symbol must never cost 800 names.
    try:
        scan = _R.peek("scan:liquid") or {}
        for c in (scan.get("candidates") or []):
            if c.get("symbol") == sym:
                return {**c, "origin": "scan", "state": "ok",
                        "funnel": {"would_pass": True, "failed_gates": [], "checks": [],
                                   "note": "surfaced by the scan funnel"}}
    except Exception:  # noqa: BLE001
        pass
    return _SC.analyse_symbol(sym)


def _unit_desk(sym: str) -> Dict[str, Any]:
    import options_desk as _OD
    cand = _unit_cached(sym, "analysis", blocking=True)
    if not isinstance(cand, dict) or cand.get("state") != "ok":
        return {"state": "unavailable", "symbol": sym,
                "reason": (cand or {}).get("reason")
                          or "per-symbol analysis unavailable — no chain evaluated"}
    out = _OD.evaluate_candidate(cand)
    out["candidate_origin"] = cand.get("origin", "scan")
    out["funnel"] = cand.get("funnel")
    out["setups"] = cand.get("setups")
    out["score"] = cand.get("score")
    return out


def _unit_news(sym: str) -> Dict[str, Any]:
    return _R.catalysts(sym)


def _unit_newsig(sym: str) -> Dict[str, Any]:
    """The CANONICAL news+catalyst signal the decision consumes — relevance-tiered,
    syndication-deduped, sentiment and catalysts kept apart. Built on the same
    `research.catalysts` payload the raw feed uses, so no second model runs."""
    import news_signal as _NS
    return _NS.compute(sym, raw=_unit_cached(sym, "news", blocking=True))


def _market_regime_now() -> Dict[str, Any]:
    """Today's regime in the SAME vocabulary the history engine produces for past
    dates, so current and historical conditions are compared like with like."""
    import history as _H
    try:
        ctx = _H._load_context()
        idx = ctx.get("spy_idx") or {}
        if idx:
            return _H._regime_at(ctx["spy"], idx, max(idx))
    except Exception:  # noqa: BLE001
        pass
    return {"state": "unknown"}


def _unit_history(sym: str) -> Dict[str, Any]:
    """Historical validation of the strategy that is firing right now."""
    import history as _H
    cand = _unit_cached(sym, "analysis", blocking=True)
    if not isinstance(cand, dict) or cand.get("state") != "ok":
        return {"state": "unavailable", "symbol": sym,
                "classification": "NO HISTORY",
                "reason": (cand or {}).get("reason") or "no live setup to validate"}
    regime = _market_regime_now()
    return _H.evaluate(sym, strategy=cand.get("primary_setup"),
                       current_features=_H.current_features(cand, regime),
                       sector=cand.get("sector"))


def _unit_assessment(sym: str) -> Dict[str, Any]:
    """The explainable decision: bounded components, thesis, supports/contradictions.

    Consumes the authoritative desk verdict rather than re-deciding — risk stays where
    it is. Everything it needs is already cached by the time this runs.
    """
    import decision_score as _DS
    cand = _unit_cached(sym, "analysis", blocking=True)
    if not isinstance(cand, dict) or cand.get("state") != "ok":
        return {"state": "unavailable", "symbol": sym,
                "reason": (cand or {}).get("reason") or "per-symbol analysis unavailable"}
    got = _R.gather({
        "desk": lambda: _unit_cached(sym, "desk", blocking=True),
        "news": lambda: _unit_cached(sym, "newsig", blocking=True),
        "history": lambda: _unit_cached(sym, "history", blocking=True),
    }, timeout=120.0)

    def _ok(k):
        v = got.get(k)
        return v if isinstance(v, dict) and "__err" not in v else {}

    desk = _ok("desk")
    out = _DS.assess(candidate=cand, desk=desk, news=_ok("news"),
                     history=_ok("history"), regime=_market_regime_now())
    # The desk's own verdict travels with the assessment so the widget renders one
    # object and cannot show a score that disagrees with the instrument beside it.
    out["desk"] = (desk or {}).get("decision") or {}
    out["levels"] = cand.get("levels")
    out["setups"] = cand.get("setups")
    out["scanner_score"] = cand.get("score")
    out["candidate_origin"] = cand.get("origin")
    out["funnel"] = cand.get("funnel")
    out["symbol"] = sym

    # Record the snapshot for future validation. The reconstruction in history.py can
    # only see what OHLCV expresses; this captures the news signal, catalysts and
    # regime that existed at this instant, which nothing can recover later. Written
    # BEFORE any outcome is knowable, and never allowed to break the decision.
    try:
        import strategy_store as _SS
        import history as _H
        out["recorded"] = _SS.record(
            symbol=sym, assessment=out, candidate=cand, news=_ok("news"),
            history=_ok("history"), regime=_market_regime_now(),
            features=_H.current_features(cand, _market_regime_now()))
    except Exception as e:  # noqa: BLE001
        out["recorded"] = {"state": "error", "reason": str(e)[:100]}
    return out


def _unit_account(sym: str = "") -> Dict[str, Any]:
    """Broker + paper account state. Deliberately SYMBOL-AGNOSTIC.

    Buying power and the ledger are the same facts whatever ticker is selected, so
    this unit is cached under one key (see `_unit_key`) and the per-symbol position is
    filtered at view time. Keying it by symbol made every ticker change re-pay a ~17s
    Robinhood MCP round-trip for an answer that had not changed.
    """
    import options_desk as _OD
    out: Dict[str, Any] = {"state": "ok", "fetched_at": datetime.now(timezone.utc).isoformat()}
    try:
        out["broker"] = _OD.account()
    except Exception as e:  # noqa: BLE001
        out["broker"] = {"state": "error", "reason": str(e)[:120]}
    try:
        # THE one paper ledger (lab/paper/) — the same object the account strip reads.
        # Never a second balance computed a second way.
        from paper import broker as _pb
        a = _pb.account()
        out["paper"] = {
            "state": "ok",
            "ledger": a.get("ledger"),
            "equity": a.get("equity"), "cash": a.get("cash"),
            "available_cash": a.get("available_cash"),
            "buying_power": a.get("buying_power"),
            "positions_value": a.get("positions_value"),
            "open_positions": a.get("open_positions"),
            "starting_equity": a.get("starting_equity"),
            "unrealized_pnl": a.get("unrealized_pnl"),
            "realized_pnl": a.get("realized_pnl"),
            "drawdown_pct": a.get("drawdown_pct"),
            "capital_utilization_pct": a.get("capital_utilization_pct"),
            "day_loss_used_pct": a.get("day_loss_used_pct"),
            "fractional_shares": a.get("fractional_shares"),
            "margin_enabled": a.get("margin_enabled"),
            "sector_value": a.get("sector_value") or {},
        }
        out["all_positions"] = a.get("open") or []
    except Exception as e:  # noqa: BLE001
        out["paper"] = {"state": "error", "reason": str(e)[:120]}
        out["all_positions"] = []
    return out


_UNITS: Dict[str, Callable[[str], Dict[str, Any]]] = {
    "quote": _unit_quote, "chart": _unit_chart, "analysis": _unit_analysis,
    "desk": _unit_desk, "news": _unit_news, "newsig": _unit_newsig,
    "history": _unit_history, "assessment": _unit_assessment,
    "account": _unit_account,
}


# Units whose answer does not depend on the selected symbol share ONE cache entry.
_GLOBAL_UNITS = ("account",)


def _unit_key(sym: str, unit: str, **kw) -> str:
    extra = ":".join(f"{k}={v}" for k, v in sorted(kw.items()) if v is not None)
    scope = "*" if unit in _GLOBAL_UNITS else sym
    return f"snap:{unit}:{scope}" + (f":{extra}" if extra else "")


def _unit_cached(sym: str, unit: str, *, blocking: bool = False,
                 force: bool = False, **kw) -> Dict[str, Any]:
    """One unit, SWR-cached and in-flight-coalesced.

    `blocking=False` (the default for a request) means a cold miss returns a loading
    marker and computes in the background — the shell paints, the widget fills in.
    """
    key = _unit_key(sym, unit, **kw)
    fn = _UNITS[unit]
    ttl = _UNIT_TTL.get(unit, 60.0)
    if force:
        _R.invalidate(key)

    def _run():
        try:
            return fn(sym, **kw) if kw else fn(sym)
        except Exception as e:  # noqa: BLE001 — a failing unit must not fail its siblings
            return {"state": "error", "symbol": sym, "unit": unit,
                    "reason": f"{type(e).__name__}: {str(e)[:160]}"}

    if blocking or unit not in _ASYNC_UNITS:
        val, cs = _R.swr(key, ttl, _run)
    else:
        val, cs = _R.swr_async(key, ttl, _run, loading={
            "state": "loading", "symbol": sym,
            "reason": f"{unit} computing in the background"})
    if isinstance(val, dict):
        return {**val, "cache_state": cs, "cache_age_s": _R.cache_age(key)}
    return {"state": "error", "reason": "unit returned a non-object"}


# ── Section views over the units ─────────────────────────────────────────────

def _fresh_for(name: str, ts: Optional[str], *, market_state: Optional[str],
               is_fallback: bool = False) -> Dict[str, Any]:
    spec = SECTIONS[name]
    rec = _fr.classify_typed(spec["kind"], _iso_age(ts), is_fallback=is_fallback,
                             market_state=market_state)
    rec["source_timestamp"] = ts
    rec["critical"] = spec["critical"]
    return rec


def _view(name: str, unit_val: Dict[str, Any], *, market_state: Optional[str],
          sym: str) -> Dict[str, Any]:
    """Project one unit onto one section, with that section's own freshness."""
    st = unit_val.get("state")
    if st in ("loading", "error", "unavailable", "insufficient_history"):
        return {"section": name, "state": st, "reason": unit_val.get("reason"),
                "freshness": _fresh_for(name, None, market_state=market_state),
                "cache_state": unit_val.get("cache_state")}

    data: Dict[str, Any] = {}
    ts: Optional[str] = None
    fallback = False

    if name == "quote":
        data = {k: unit_val.get(k) for k in
                ("price", "change_pct", "change", "prev_close", "open", "high", "low",
                 "volume", "currency", "exchange", "source", "state")}
        ts = unit_val.get("source_timestamp")
        fallback = unit_val.get("is_fallback", False)

    elif name == "chart":
        data = {"points": unit_val.get("points") or [], "range": unit_val.get("range"),
                "interval": unit_val.get("interval"),
                "bar_seconds": unit_val.get("bar_seconds"),
                "resolution": unit_val.get("resolution"),
                "count": unit_val.get("count"),
                "change_pct": unit_val.get("change_pct"), "as_of": unit_val.get("as_of")}
        # A daily bar is stamped at its own date; an intraday bar carries a real clock.
        ts = unit_val.get("source_timestamp") or unit_val.get("as_of")

    elif name == "technicals":
        ind = unit_val.get("indicators") or {}
        data = {
            "price": ind.get("price"), "change_pct": ind.get("change_pct"),
            "trend": {"above_sma20": ind.get("above_sma20"),
                      "above_sma50": ind.get("above_sma50"),
                      "above_sma200": ind.get("above_sma200"),
                      "sma20": ind.get("sma20"), "sma50": ind.get("sma50"),
                      "sma200": ind.get("sma200")},
            "atr14": ind.get("atr14"), "atr_pct": ind.get("atr_pct"),
            "rsi14": ind.get("rsi14"),
            "realized_vol_20d": ind.get("realized_vol_20d"),
            "rel_volume": ind.get("rel_volume"),
            "rel_volume_basis": ind.get("rel_volume_basis"),
            "avg_volume_20d": ind.get("avg_volume_20d"),
            "dollar_volume_20d": ind.get("dollar_volume_20d"),
            "support": ind.get("support"), "resistance": ind.get("resistance"),
            "high_20d": ind.get("high_20d"), "low_20d": ind.get("low_20d"),
            "pos_in_20d_range": ind.get("pos_in_20d_range"),
            "chg_5d": ind.get("chg_5d"), "chg_20d": ind.get("chg_20d"),
            "chg_60d": ind.get("chg_60d"),
            "relative_strength": _relative_strength(unit_val),
            "setups": unit_val.get("setups") or [],
            "missing": ind.get("missing") or [],
        }
        ts = ind.get("source_timestamp")

    elif name == "levels":
        lv = unit_val.get("levels") or {}
        data = {**lv, "score": unit_val.get("score"),
                "funnel": unit_val.get("funnel"),
                "sector": unit_val.get("sector_name") or unit_val.get("sector"),
                "sector_rank": unit_val.get("sector_rank"),
                "context": unit_val.get("context")}
        ts = (unit_val.get("indicators") or {}).get("source_timestamp")

    elif name == "decision":
        dec = unit_val.get("desk") or {}
        data = {
            # The authoritative instrument/risk verdict, unchanged.
            "verdict": dec.get("verdict"), "instrument": dec.get("instrument"),
            "reasons_for_stock": dec.get("reasons_for_stock") or [],
            "reasons_for_option": dec.get("reasons_for_option") or [],
            "blockers": dec.get("blockers") or [],
            "contract_quality": dec.get("contract_quality"),
            "portfolio_suitability": dec.get("portfolio_suitability"),
            "instrument_reasons": dec.get("instrument_reasons"),
            "decision_freshness": dec.get("decision_freshness"),
            "sizing": dec.get("sizing"), "horizon": dec.get("horizon"),
            "verification": dec.get("verification"),
            "spread_note": dec.get("spread_note"),
            "option_ineligible_reason": dec.get("option_ineligible_reason"),
            "decided_at": dec.get("decided_at"),
            "disclaimer": dec.get("disclaimer"),
            # The explainable layer.
            "decision": unit_val.get("label"),
            "label_reason": unit_val.get("label_reason"),
            "components": unit_val.get("components") or [],
            "structural_score": unit_val.get("structural_score"),
            "structural_max": unit_val.get("structural_max"),
            "structural_floor": unit_val.get("structural_floor"),
            "context_score": unit_val.get("context_score"),
            "context_max": unit_val.get("context_max"),
            "context_min": unit_val.get("context_min"),
            # Two DIFFERENT numbers, never both called "score":
            #   decision_score = structural + context, exactly (-20..82)
            #   setup_score    = the scanner's own funnel ranking (0..100)
            "decision_score": unit_val.get("decision_score"),
            "score_scale": unit_val.get("score_scale"),
            "setup_score": (unit_val.get("scanner_score") or {}).get("total"),
            "thresholds": unit_val.get("thresholds"),
            "hard_failures": unit_val.get("hard_failures") or [],
            "supports": unit_val.get("supports") or [],
            "contradicts": unit_val.get("contradicts") or [],
            "invalidation": unit_val.get("invalidation"),
            "context_cannot_override": unit_val.get("context_cannot_override"),
            "score": unit_val.get("scanner_score") or {},
            "setups": unit_val.get("setups") or [],
            "levels": unit_val.get("levels") or {},
            "funnel": unit_val.get("funnel"),
            "candidate_origin": unit_val.get("candidate_origin"),
        }
        ts = dec.get("decided_at") or unit_val.get("generated_at")

    elif name == "thesis":
        data = {"rows": unit_val.get("thesis") or [],
                "supports": unit_val.get("supports") or [],
                "contradicts": unit_val.get("contradicts") or [],
                "label": unit_val.get("label"),
                "decision_score": unit_val.get("decision_score")}
        ts = (unit_val.get("desk") or {}).get("decided_at")

    elif name == "history":
        data = {k: unit_val.get(k) for k in
                ("classification", "classification_reason", "fit_pct", "sample_size",
                 "total_instances", "similarity_floor", "window", "horizon_days",
                 "stats", "examples", "methodology", "options_note", "news_note",
                 "strategy", "sector_proxy")}
        ts = None      # a 10-year record has no "as of" beyond the last bar scanned

    elif name == "options":
        best = unit_val.get("best_contract")
        data = {
            "underlying_price": unit_val.get("underlying_price"),
            "best_contract": best,
            "chain": unit_val.get("chain") or [],
            "contracts_analysed": unit_val.get("contracts_analysed"),
            "contracts_passing": unit_val.get("contracts_passing"),
            "expirations": unit_val.get("expirations_considered") or [],
            "provider": unit_val.get("provider"),
            "earnings": unit_val.get("earnings"),
            "rejected_sample": (unit_val.get("rejected_contracts") or [])[:8],
            "session_open": unit_val.get("session_open"),
        }
        ts = (best or {}).get("quote_timestamp") or unit_val.get("generated_at")

    elif name == "news":
        items = [i for i in (unit_val.get("items") or []) if i.get("title")]
        data = {"items": items[:20],
                "direction": unit_val.get("direction"),
                "sentiment_score": unit_val.get("sentiment_score"),
                "confidence": unit_val.get("confidence"),
                "counts": unit_val.get("counts") or {},
                "article_count": unit_val.get("article_count"),
                "relevant_count": unit_val.get("relevant_count"),
                "direct_count": unit_val.get("direct_count"),
                "avg_relevance": unit_val.get("avg_relevance"),
                "tier_breakdown": unit_val.get("tier_breakdown") or {},
                "source_diversity": unit_val.get("source_diversity") or {},
                "news_freshness": unit_val.get("freshness") or {},
                "backend": unit_val.get("backend"),
                "sources_used": unit_val.get("sources_used") or [],
                "reason": unit_val.get("reason")}
        ts = _newest_ts(items)

    elif name == "catalysts":
        data = {"earnings": unit_val.get("earnings"),
                "catalysts": unit_val.get("catalysts") or [],
                "catalyst_score": unit_val.get("catalyst_score"),
                "note": unit_val.get("note")}
        ts = _newest_ts(unit_val.get("catalysts") or [])

    elif name == "risk":
        br = unit_val.get("broker") or {}
        pa = unit_val.get("paper") or {}
        data = {"broker": br, "paper": pa,
                # Broker buying power drives the option-sizing engine; the paper
                # ledger is the simulated account. They are DIFFERENT accounts and are
                # never added together or substituted for one another.
                "buying_power": br.get("buying_power"),
                "broker_equity": br.get("portfolio_value"),
                "paper_equity": pa.get("equity"),
                "read_only": br.get("read_only", True),
                "exposure": pa.get("positions_value"),
                "capital_utilization_pct": pa.get("capital_utilization_pct"),
                "remaining_capacity": pa.get("available_cash"),
                "open_option_positions": br.get("option_positions") or []}
        ts = br.get("fetched_at") or unit_val.get("fetched_at")

    elif name == "position":
        # Filtered HERE, not in the unit — the account unit is shared across symbols.
        allp = unit_val.get("all_positions") or []
        data = {"paper_positions": [p for p in allp
                                    if str(p.get("symbol", "")).upper() == sym],
                "paper_all": allp,
                "broker_positions": [p for p in ((unit_val.get("broker") or {}).get("positions") or [])
                                     if str(p.get("symbol", "")).upper() == sym]}
        ts = unit_val.get("fetched_at")

    return {"section": name, "state": "ok", "data": data,
            "freshness": _fresh_for(name, ts, market_state=market_state,
                                    is_fallback=fallback),
            "cache_state": unit_val.get("cache_state"),
            "cache_age_s": unit_val.get("cache_age_s")}


def _relative_strength(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """RS is the 20-day return against SPY's — reported with the benchmark, never as
    a bare number whose basis the reader has to guess."""
    ind = analysis.get("indicators") or {}
    comps = ((analysis.get("score") or {}).get("components") or [])
    rs = next((c for c in comps if c["component"] == "relative_strength"), None)
    return {"chg_20d": ind.get("chg_20d"), "note": (rs or {}).get("note"),
            "score": (rs or {}).get("score"), "max": (rs or {}).get("max")}


def _newest_ts(items: List[Dict[str, Any]]) -> Optional[str]:
    ts = [i.get("ts") or i.get("timestamp") for i in items
          if i.get("ts") or i.get("timestamp")]
    return max(ts) if ts else None


# ── Public API ───────────────────────────────────────────────────────────────

def snapshot(symbol: str, *, sections: Optional[List[str]] = None,
             rng: str = "3M", force: bool = False,
             blocking: bool = False) -> Dict[str, Any]:
    """The canonical selected-symbol snapshot.

    Returns every requested section with its own state, data, freshness and cache
    state. Sections whose unit is still computing come back `state:"loading"`; a
    section whose unit raised comes back `state:"error"` with the reason — its
    siblings are unaffected.
    """
    sym = (symbol or "").upper().strip()
    if not sym:
        return {"state": "error", "reason": "symbol is required"}
    want = [s for s in (sections or DEFAULT_SECTIONS) if s in SECTIONS]
    if not want:
        want = list(DEFAULT_SECTIONS)

    market_state = _market_state()
    needed_units = {SECTIONS[s]["unit"] for s in want}

    # Fetch every needed unit CONCURRENTLY. Cached units return instantly; async units
    # return a loading marker on a cold miss. One slow provider costs its own section.
    tasks = {u: (lambda u=u: _unit_cached(sym, u, blocking=blocking, force=force,
                                          **({"rng": rng} if u == "chart" else {})))
             for u in needed_units}
    units = _R.gather(tasks, timeout=45.0 if blocking else 6.0)
    for u, v in list(units.items()):
        if not isinstance(v, dict) or "__err" in v:
            units[u] = {"state": "error", "reason": (v or {}).get("__err", "unit failed")
                        if isinstance(v, dict) else "unit failed"}

    out_sections: Dict[str, Any] = {}
    for name in want:
        try:
            out_sections[name] = _view(name, units[SECTIONS[name]["unit"]],
                                       market_state=market_state, sym=sym)
        except Exception as e:  # noqa: BLE001 — presentation must not take siblings down
            out_sections[name] = {
                "section": name, "state": "error",
                "reason": f"{type(e).__name__}: {str(e)[:140]}",
                "freshness": _fresh_for(name, None, market_state=market_state)}

    # Critical-input gating. A verdict may only stand while the data behind it does.
    stale_critical = [
        {"section": n, "label": s["freshness"].get("label"),
         "presentation": s["freshness"].get("presentation")}
        for n, s in out_sections.items()
        if SECTIONS[n]["critical"] and s.get("state") == "ok"
        and s["freshness"].get("blocks_tradeable")]
    loading_critical = [n for n, s in out_sections.items()
                        if SECTIONS[n]["critical"] and s.get("state") == "loading"]

    if stale_critical and "decision" in out_sections:
        d = out_sections["decision"]
        if d.get("state") == "ok":
            d["data"]["gated"] = True
            d["data"]["gate_status"] = "STALE — REFRESH REQUIRED"
            d["data"]["gate_reason"] = [
                f"{c['section']} is {c['label']}" for c in stale_critical]
            # The verdict is REPLACED, not annotated. An OPTION PREFERRED badge sitting
            # beside a stale warning still reads as current guidance at a glance.
            d["data"]["verdict_withheld"] = d["data"].get("verdict")
            d["data"]["instrument_withheld"] = d["data"].get("instrument")
            d["data"]["label_withheld"] = d["data"].get("decision")
            d["data"]["verdict"] = "stale"
            d["data"]["instrument"] = "STALE — REFRESH REQUIRED"
            d["data"]["decision"] = "STALE — REFRESH REQUIRED"
            # Stale data is a HARD failure in the score too, so the explainable layer
            # and the badge cannot tell different stories about the same snapshot.
            d["data"]["hard_failures"] = ([{"kind": "stale_data",
                                            "detail": r} for r in d["data"]["gate_reason"]]
                                          + list(d["data"].get("hard_failures") or []))

    return {
        "state": "ok", "symbol": sym,
        "market": {"state": market_state},
        "sections": out_sections,
        "requested": want,
        "critical_stale": stale_critical,
        "critical_loading": loading_critical,
        "pending": [n for n, s in out_sections.items() if s.get("state") == "loading"],
        "failed": [n for n, s in out_sections.items() if s.get("state") == "error"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "Research only. Nothing here is an order.",
    }


def refresh(symbol: str, *, sections: Optional[List[str]] = None,
            rng: str = "3M", blocking: bool = False) -> Dict[str, Any]:
    """Explicit user refresh for ONE symbol. Drops that symbol's unit caches and
    recomputes — it can never trigger a universe scan.

    Non-blocking by default: the recompute starts in the background and the caller
    polls, which keeps the refresh button responsive. `blocking=True` waits for the
    new values (used by tests and by any caller that needs the result in hand).
    """
    sym = (symbol or "").upper().strip()
    want = [s for s in (sections or DEFAULT_SECTIONS) if s in SECTIONS]
    units = {SECTIONS[s]["unit"] for s in want} or set(_UNITS)
    dropped = 0
    for u in units:
        dropped += _R.invalidate(_unit_key(sym, u, **({"rng": rng} if u == "chart" else {})))
    if "desk" in units:
        dropped += _R.invalidate(f"desk:{sym}")
    if "analysis" in units:
        dropped += _R.invalidate(f"sym:{sym}")
    if "newsig" in units:
        dropped += _R.invalidate(f"newsig:{sym}")
    if "history" in units:
        dropped += _R.invalidate(f"hist:scan:{sym}")
    # The assessment is a pure function of the units above, so any refresh that
    # touches one must drop it too or the widget shows a score built from old inputs.
    if units & {"analysis", "desk", "newsig", "history"}:
        units = units | {"assessment"}
        dropped += _R.invalidate(_unit_key(sym, "assessment"))
    snap = snapshot(sym, sections=want, rng=rng, blocking=blocking)
    snap["refreshed"] = sorted(units)
    snap["cache_entries_dropped"] = dropped
    snap["scope"] = "symbol"
    snap["universe_rescanned"] = False
    return snap
