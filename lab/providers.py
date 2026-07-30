"""Normalized multi-provider data layer + per-field consensus (Prompt 5B).

The terminal no longer treats TradingView as a primary data source. TradingView is
DEMOTED to an OPTIONAL technical-signal confirmation; when it is throttled, disabled
(`TRADINGVIEW_ENABLED=false`) or simply unavailable, every page and scan stays fully
useful on the free primaries (Yahoo/yfinance, Finnhub, EDGAR, FRED, Robinhood MCP,
StockTwits, Google News, the local security master).

This module is the single source of truth for:

  * The **category spec** — for each data category (quotes, candles, fundamentals,
    options, news, analyst, filings, macro, sector) the primary + secondary
    provider, whether TradingView is an optional confirm, the timeout, the cache
    TTL, the FRESHNESS LIMIT (max source age still valid to drive a NEW trade
    decision), the required fields that make the category "covered", and a
    confidence weight.  `DATA_PROVIDER_MATRIX.md` is generated from this table.

  * **Per-field consensus** — given the same field from several providers, return
    one record `{value, provider, source_timestamp, freshness, confidence,
    agreeing_providers, conflicting_providers}`.  On disagreement we prefer the
    freshest valid source, apply a confidence penalty, and SURFACE the conflict —
    never silently overwrite.  A TradingView failure never lowers the whole
    stock's quality when another provider has valid data.

Nothing here places an order or mutates account state.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(__file__))
import freshness as _fr  # the ONE shared freshness classifier


# ── Feature flags ─────────────────────────────────────────────────────────────

def tv_enabled() -> bool:
    """TradingView is opt-IN as a confirmation layer. Default ON for continuity, but
    the whole terminal must work with this OFF — that is an acceptance target."""
    return os.environ.get("TRADINGVIEW_ENABLED", "true").strip().lower() not in (
        "0", "false", "no", "off")


def allow_stale_decisions() -> bool:
    """Whether a stale (past-freshness-limit) value may still drive a NEW decision.
    Off by default: stale data can render the page but not create an actionable call."""
    return os.environ.get("DECISION_ALLOW_STALE", "false").strip().lower() in (
        "1", "true", "yes", "on")


# ── Category spec ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CategorySpec:
    name: str
    primary: str
    secondary: Optional[str]
    tv_confirm: bool          # TradingView is an OPTIONAL confirmation only
    timeout_s: float
    ttl_s: float              # cache TTL (stale-while-revalidate window start)
    freshness_limit_s: float  # max SOURCE age still valid for a NEW trade decision
    required_fields: Tuple[str, ...]
    confidence_weight: float  # how much this category counts toward overall quality

    def as_row(self) -> Dict[str, Any]:
        return {
            "category": self.name, "primary": self.primary,
            "secondary": self.secondary or "—",
            "tv_confirm": "optional" if self.tv_confirm else "no",
            "timeout_s": self.timeout_s, "ttl_s": self.ttl_s,
            "freshness_limit_s": self.freshness_limit_s,
            "required_fields": list(self.required_fields),
            "confidence_weight": self.confidence_weight,
        }


# The authoritative table. TradingView appears ONLY as an optional confirm, never a
# primary or secondary. Freshness limits are per-category: an intraday quote is only
# decision-valid for minutes; fundamentals/filings stay valid for a day+.
CATEGORIES: Dict[str, CategorySpec] = {
    "price": CategorySpec(
        "price", primary="finnhub", secondary="yahoo", tv_confirm=True,
        timeout_s=6, ttl_s=8, freshness_limit_s=900,
        required_fields=("price",), confidence_weight=1.0),
    "candles": CategorySpec(
        "candles", primary="yahoo", secondary="finnhub", tv_confirm=True,
        timeout_s=8, ttl_s=300, freshness_limit_s=6 * 3600,
        required_fields=("closes", "highs", "lows"), confidence_weight=1.0),
    "fundamentals": CategorySpec(
        "fundamentals", primary="finnhub", secondary="alphavantage", tv_confirm=False,
        timeout_s=8, ttl_s=6 * 3600, freshness_limit_s=3 * 86400,
        required_fields=("market_cap",), confidence_weight=0.7),
    "options": CategorySpec(
        "options", primary="yahoo", secondary=None, tv_confirm=False,
        timeout_s=8, ttl_s=60, freshness_limit_s=1800,
        required_fields=("bid", "ask", "open_interest"), confidence_weight=0.6),
    "news": CategorySpec(
        "news", primary="google-news", secondary="finnhub", tv_confirm=False,
        timeout_s=8, ttl_s=180, freshness_limit_s=6 * 3600,
        required_fields=("headlines",), confidence_weight=0.6),
    "analyst": CategorySpec(
        "analyst", primary="finnhub", secondary=None, tv_confirm=False,
        timeout_s=8, ttl_s=6 * 3600, freshness_limit_s=7 * 86400,
        required_fields=("recommendation",), confidence_weight=0.5),
    "filings": CategorySpec(
        "filings", primary="edgar", secondary=None, tv_confirm=False,
        timeout_s=8, ttl_s=7 * 86400, freshness_limit_s=30 * 86400,
        required_fields=("cik",), confidence_weight=0.4),
    "macro": CategorySpec(
        "macro", primary="fred", secondary=None, tv_confirm=False,
        timeout_s=8, ttl_s=6 * 3600, freshness_limit_s=2 * 86400,
        required_fields=("series",), confidence_weight=0.4),
    "sector": CategorySpec(
        "sector", primary="security-master", secondary="finnhub", tv_confirm=False,
        timeout_s=4, ttl_s=24 * 3600, freshness_limit_s=30 * 86400,
        required_fields=("sector",), confidence_weight=0.4),
    "social": CategorySpec(
        "social", primary="stocktwits", secondary=None, tv_confirm=False,
        timeout_s=6, ttl_s=180, freshness_limit_s=6 * 3600,
        required_fields=("messages",), confidence_weight=0.15),
}


def spec(category: str) -> Optional[CategorySpec]:
    return CATEGORIES.get(category)


def matrix_rows() -> List[Dict[str, Any]]:
    return [c.as_row() for c in CATEGORIES.values()]


# ── Per-field consensus ───────────────────────────────────────────────────────

@dataclass
class Reading:
    """One provider's reading of a field."""
    provider: str
    value: Any
    source_ts: Optional[float] = None   # epoch seconds of the SOURCE, not cache-time
    ok: bool = True
    error: Optional[str] = None


def _agree(a: Any, b: Any, tol: float) -> bool:
    """Numeric agreement within a relative tolerance; exact for non-numerics."""
    try:
        fa, fb = float(a), float(b)
    except (TypeError, ValueError):
        return a == b
    if fa == 0 and fb == 0:
        return True
    denom = max(abs(fa), abs(fb)) or 1e-9
    return abs(fa - fb) / denom <= tol


def consensus(readings: List[Reading], *, category: str = "price",
              tol: float = 0.005) -> Dict[str, Any]:
    """Combine several provider readings of ONE field into a consensus record.

    Rules (from the brief):
      * Prefer the FRESHEST valid source (then the primary's ordering).
      * When providers DISAGREE, apply a confidence penalty and surface the split.
      * NEVER silently overwrite — losers are reported as conflicting/agreeing.
      * A failed provider (ok=False) is ignored for the value but recorded.
    """
    valid = [r for r in readings if r.ok and r.value is not None]
    if not valid:
        errs = [f"{r.provider}:{(r.error or 'no data')[:24]}" for r in readings]
        return {"value": None, "provider": None, "source_timestamp": None,
                "freshness": _fr.classify(None), "confidence": 0.0,
                "agreeing_providers": [], "conflicting_providers": [],
                "state": "unavailable", "tried": errs}

    now = time.time()
    # Freshest valid reading wins the value (source_ts None => treat as oldest).
    winner = max(valid, key=lambda r: (r.source_ts if r.source_ts is not None else -1))
    agreeing, conflicting = [], []
    for r in valid:
        if r is winner:
            continue
        (agreeing if _agree(winner.value, r.value, tol) else conflicting).append(r.provider)

    age = (now - winner.source_ts) if winner.source_ts is not None else None
    fresh = _fr.classify(age)
    spc = CATEGORIES.get(category)
    within_limit = (age is None) or (spc is None) or (age <= spc.freshness_limit_s)

    # Confidence: base on freshness (1 - penalty), lifted by agreement, cut by conflict
    # and by being past the category freshness limit.
    n_support = 1 + len(agreeing)
    n_total = n_support + len(conflicting)
    agree_ratio = n_support / n_total if n_total else 1.0
    conf = (1.0 - fresh["penalty"]) * (0.6 + 0.4 * agree_ratio)
    if conflicting:
        conf *= 0.8                       # visible penalty for disagreement
    if not within_limit:
        conf *= 0.5                       # past the decision-freshness limit
    conf = round(max(0.0, min(1.0, conf)), 3)

    return {
        "value": winner.value, "provider": winner.provider,
        "source_timestamp": winner.source_ts, "age_seconds": (round(age) if age is not None else None),
        "freshness": fresh, "within_freshness_limit": within_limit,
        "confidence": conf,
        "agreeing_providers": agreeing, "conflicting_providers": conflicting,
        "state": "conflict" if conflicting else "ok",
    }


# ── Price consensus (the flagship: Finnhub + Yahoo [+ TradingView confirm]) ───

def _finnhub_price(symbol: str) -> Reading:
    try:
        import finnhub_data
        q = finnhub_data.quote(symbol)
        if isinstance(q, dict) and q.get("c"):
            ts = q.get("t")
            return Reading("finnhub", round(float(q["c"]), 4),
                           source_ts=(float(ts) if ts else time.time()))
        return Reading("finnhub", None, ok=False, error=(q or {}).get("error", "no quote"))
    except Exception as e:
        return Reading("finnhub", None, ok=False, error=str(e))


def _yahoo_price(yahoo_symbol: str) -> Reading:
    try:
        from tradingview_mcp.core.services.yahoo_finance_service import get_price
        p = get_price(yahoo_symbol)
        if isinstance(p, dict) and p.get("price") and "error" not in p:
            # Yahoo's regularMarketPrice is ~real-time; stamp as now (its own ts is
            # the fetch time). market_state lets callers know if the tape is live.
            return Reading("yahoo", round(float(p["price"]), 4), source_ts=time.time())
        return Reading("yahoo", None, ok=False, error=(p or {}).get("error", "no price"))
    except Exception as e:
        return Reading("yahoo", None, ok=False, error=str(e))


def _tv_price(symbol: str, exchange: str = "NASDAQ") -> Reading:
    """OPTIONAL confirmation only — never called unless TradingView is enabled AND
    the primaries are already in hand or explicitly requested."""
    try:
        from tradingview_mcp.core.services import strategy_service as ss
        a = ss._analyze_cached(symbol, exchange, "1D")
        pd = (a or {}).get("price_data") or {}
        if pd.get("current_price"):
            return Reading("tradingview", round(float(pd["current_price"]), 4),
                           source_ts=time.time())
        return Reading("tradingview", None, ok=False, error="no tv price")
    except Exception as e:
        return Reading("tradingview", None, ok=False, error=str(e))


def price_consensus(symbol: str, *, yahoo_symbol: Optional[str] = None,
                    finnhub_symbol: Optional[str] = None, exchange: str = "NASDAQ",
                    with_tv: Optional[bool] = None,
                    extra: Optional[List[Reading]] = None) -> Dict[str, Any]:
    """Live-price consensus across the free primaries (+ optional TV confirm).

    `extra` lets a caller inject already-known readings (e.g. a Robinhood MCP
    position mark) without this module importing the broker layer."""
    fin_sym = finnhub_symbol or symbol
    yah_sym = yahoo_symbol or symbol
    readings: List[Reading] = [_finnhub_price(fin_sym), _yahoo_price(yah_sym)]
    if extra:
        readings.extend(extra)
    use_tv = tv_enabled() if with_tv is None else (with_tv and tv_enabled())
    if use_tv:
        readings.append(_tv_price(symbol, exchange))   # optional confirmation
    out = consensus(readings, category="price", tol=0.01)   # 1% price tolerance
    out["tradingview_used"] = bool(use_tv)
    out["providers_queried"] = [r.provider for r in readings]
    return out


# ── Provider inventory (for the health panel + matrix doc) ────────────────────

def provider_inventory() -> Dict[str, Any]:
    """Which categories each provider serves, and TradingView's demoted role."""
    inv: Dict[str, List[str]] = {}
    for c in CATEGORIES.values():
        inv.setdefault(c.primary, []).append(f"{c.name}(primary)")
        if c.secondary:
            inv.setdefault(c.secondary, []).append(f"{c.name}(secondary)")
        if c.tv_confirm:
            inv.setdefault("tradingview", []).append(f"{c.name}(confirm)")
    return {"providers": inv, "tradingview_enabled": tv_enabled(),
            "tradingview_role": "optional technical-signal confirmation only"}
