"""India Service — all business logic for NSE/BSE (Indian stock market) tools.

Mirrors egx_service.py's shape (market overview, sector scan/rotation, index
analysis, stock screener, trade plan, Fibonacci) but resolves sector and index
*membership* LIVE against TradingView's `india` scanner market instead of a
static hardcoded list — see core/data/india_indices.py for why.

All public functions return plain dicts / lists and are independently
testable without the MCP layer.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from tradingview_mcp.core.services.coinlist import load_symbols
from tradingview_mcp.core.services.indicators import (
    compute_metrics,
    extract_extended_indicators,
    compute_stock_score,
    compute_trade_setup,
    compute_trade_quality,
    compute_fibonacci_levels,
    analyze_fibonacci_position,
    detect_trend_for_fibonacci,
)
from tradingview_mcp.core.utils.validators import sanitize_timeframe
from tradingview_mcp.core.data.india_indices import (
    INDIA_INDICES,
    INDEX_DESCRIPTIONS,
    INDEX_QUOTE_SYMBOL,
    get_index_names,
)

# Resilience layer (retry + 60s TTL cache) — shared with every other market.
from tradingview_mcp.core.services.screener_provider import _scan_with_retry

_SCREENER = "india"
_CURRENCY = "INR"

try:
    import tradingview_ta  # noqa: F401  presence check
    from tradingview_mcp.core.services.screener_provider import (
        resilient_get_multiple_analysis as get_multiple_analysis,
    )
    _TA_AVAILABLE = True
except ImportError:
    _TA_AVAILABLE = False

try:
    from tradingview_screener import Query
    from tradingview_screener.column import Column
    _SCREENER_AVAILABLE = True
except ImportError:
    _SCREENER_AVAILABLE = False


# ── Group (sector / index) resolution ──────────────────────────────────────

def _resolve_group(
    group_key: str,
    limit: int = 200,
) -> Tuple[List[str], Dict[str, float], Dict[str, str]]:
    """Resolve a friendly group key (from INDIA_INDICES) or a raw TradingView
    sector name to a live-ranked list of NSE/BSE tickers.

    Returns (tickers, market_cap_by_ticker, sector_by_ticker). Ranked by
    free-float market cap descending — the same signal NSE itself uses for
    index weighting, so this closely tracks true index/sector membership
    without shipping a static list that drifts after reconstitution.
    """
    if not _SCREENER_AVAILABLE:
        return [], {}, {}

    key = group_key.strip().upper().replace(" ", "")
    spec = INDIA_INDICES.get(key)

    q = Query().set_markets(_SCREENER).select("sector", "industry", "market_cap_basic")

    # NOTE: Query.where() REPLACES the filter list on every call rather than
    # ANDing it with previous calls (verified against tradingview_screener's
    # source) — so all filter expressions must be passed in ONE .where() call.
    if spec:
        exch = spec.get("exchange", "NSE")
        filters = [Column("exchange") == exch]
        if spec.get("sector_in"):
            filters.append(Column("sector").isin(spec["sector_in"]))
        if spec.get("industry_in"):
            filters.append(Column("industry").isin(spec["industry_in"]))
        q = q.where(*filters)
        fetch_limit = max(limit, int(spec.get("approx_count", 50)))
    else:
        # Fall back to treating group_key as a raw TradingView sector name
        # (e.g. "Finance", "Health Technology") — case-insensitive match.
        q = q.where(Column("exchange") == "NSE", Column("sector") == group_key.strip())
        fetch_limit = limit

    q = q.order_by("market_cap_basic", ascending=False).limit(fetch_limit)

    cache_key = ("india_group_v1", key, fetch_limit)
    try:
        _, df = _scan_with_retry(q, cache_key=cache_key)
    except Exception:
        return [], {}, {}

    if df is None or df.empty:
        return [], {}, {}

    tickers: List[str] = []
    mcap: Dict[str, float] = {}
    sector: Dict[str, str] = {}
    for _, row in df.iterrows():
        t = row.get("ticker")
        if not t:
            continue
        tickers.append(t)
        mc = row.get("market_cap_basic")
        mcap[t] = float(mc) if mc is not None else 0.0
        sector[t] = row.get("sector") or "Unknown"
    return tickers, mcap, sector


def get_available_groups() -> Dict[str, Any]:
    """List the sector/index group keys usable with scan_india_sector /
    analyze_india_index, plus the raw TradingView sector taxonomy as a
    fallback (any of these 20 names can be passed directly too)."""
    return {
        "index_and_sector_groups": {
            k: INDEX_DESCRIPTIONS.get(k, "") for k in get_index_names()
        },
        "raw_sector_names": [
            "Commercial Services", "Communications", "Consumer Durables",
            "Consumer Non-Durables", "Consumer Services", "Distribution Services",
            "Electronic Technology", "Energy Minerals", "Finance",
            "Health Services", "Health Technology", "Industrial Services",
            "Miscellaneous", "Non-Energy Minerals", "Process Industries",
            "Producer Manufacturing", "Retail Trade", "Technology Services",
            "Transportation", "Utilities",
        ],
        "usage": "Pass any key above as `sector=` or `index=`. Membership is "
                 "resolved live by free-float market-cap ranking within the "
                 "sector/industry filter — an approximation of official NSE "
                 "index files, refreshed every call (60s cache).",
    }


def _batch_ta(symbols: List[str], timeframe: str) -> Dict[str, Any]:
    """Batch-fetch TA indicators for a symbol list, 200 at a time."""
    if not _TA_AVAILABLE or not symbols:
        return {}
    out: Dict[str, Any] = {}
    batch_size = 200
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        try:
            analysis = get_multiple_analysis(screener=_SCREENER, interval=timeframe, symbols=batch)
            out.update(analysis)
        except Exception:
            continue
    return out


# ── Market Overview ─────────────────────────────────────────────────────────

def get_india_market_overview(timeframe: str = "1D", limit: int = 10) -> dict:
    """Comprehensive NSE market overview: top gainers, losers, most active.

    Args:
        timeframe: TradingView interval (default '1D').
        limit:     Stocks per category (max 20).

    Returns:
        Dict with top_gainers, top_losers, most_active, and market_stats.
    """
    if not _TA_AVAILABLE:
        return {"error": "tradingview_ta is missing; run `pip install tradingview-ta`."}

    symbols = load_symbols("nse")
    if not symbols:
        return {"error": "No NSE symbols found. Check coinlist/nse.txt"}

    analysis = _batch_ta(symbols, timeframe)

    all_stocks: List[dict] = []
    for sym, data in analysis.items():
        if data is None:
            continue
        try:
            ind = data.indicators
            metrics = compute_metrics(ind)
            if not metrics:
                continue
            all_stocks.append({
                "symbol": sym,
                "price": metrics.get("price", 0),
                "changePercent": metrics.get("change", 0),
                "volume": ind.get("volume", 0),
                "rsi": round(ind.get("RSI", 0) or 0, 2),
                "bbw": metrics.get("bbw", 0),
                "rating": metrics.get("rating", 0),
                "signal": metrics.get("signal", "N/A"),
            })
        except Exception:
            continue

    if not all_stocks:
        return {"error": "No data returned for NSE stocks", "timeframe": timeframe}

    by_change = sorted(all_stocks, key=lambda x: x["changePercent"], reverse=True)
    by_volume = sorted(all_stocks, key=lambda x: x["volume"] or 0, reverse=True)

    return {
        "exchange": "NSE",
        "timeframe": timeframe,
        "total_analyzed": len(all_stocks),
        "top_gainers": by_change[:limit],
        "top_losers": by_change[-limit:][::-1],
        "most_active": by_volume[:limit],
        "market_stats": {
            "advancing": len([s for s in all_stocks if s["changePercent"] > 0]),
            "declining": len([s for s in all_stocks if s["changePercent"] < 0]),
            "unchanged": len([s for s in all_stocks if s["changePercent"] == 0]),
            "avg_change": (
                round(sum(s["changePercent"] for s in all_stocks) / len(all_stocks), 2)
                if all_stocks else 0
            ),
        },
    }


# ── Sector Scan ──────────────────────────────────────────────────────────────

def scan_india_sector(sector: str = "", timeframe: str = "1D", limit: int = 20) -> dict:
    """Scan NSE stocks by sector/index group, or list available groups.

    Args:
        sector:    Group key (e.g. 'BANKNIFTY', 'NIFTYIT') or raw TradingView
                   sector name (e.g. 'Finance'). Empty string -> list groups.
        timeframe: TradingView interval (default '1D').
        limit:     Max results.

    Returns:
        Sector data dict or available groups list.
    """
    if not sector:
        return get_available_groups()

    if not _TA_AVAILABLE:
        return {"error": "tradingview_ta is missing; run `pip install tradingview-ta`."}

    tickers, mcap, sector_map = _resolve_group(sector, limit=max(limit, 60))
    if not tickers:
        return {"error": f"No constituents resolved for group: {sector}", **get_available_groups()}

    analysis = _batch_ta(tickers, timeframe)

    results: List[dict] = []
    for sym, data in analysis.items():
        if data is None:
            continue
        try:
            ind = data.indicators
            metrics = compute_metrics(ind)
            if not metrics:
                continue
            results.append({
                "symbol": sym,
                "sector": sector_map.get(sym, "Unknown"),
                "price": metrics.get("price", 0),
                "changePercent": metrics.get("change", 0),
                "volume": ind.get("volume", 0),
                "rsi": round(ind.get("RSI", 0) or 0, 2),
                "bbw": metrics.get("bbw", 0),
                "rating": metrics.get("rating", 0),
                "signal": metrics.get("signal", "N/A"),
                "market_cap": mcap.get(sym),
                "bb_upper": round(ind.get("BB.upper", 0) or 0, 4),
                "bb_lower": round(ind.get("BB.lower", 0) or 0, 4),
                "sma20": round(ind.get("SMA20", 0) or 0, 4),
                "ema50": round(ind.get("EMA50", 0) or 0, 4),
            })
        except Exception:
            continue

    results.sort(key=lambda x: x["changePercent"], reverse=True)
    changes = [r["changePercent"] for r in results if r["changePercent"] is not None]
    avg_change = round(sum(changes) / len(changes), 2) if changes else 0

    return {
        "exchange": "NSE/BSE",
        "group": sector.strip().upper(),
        "timeframe": timeframe,
        "total_stocks": len(results),
        "sector_avg_change": avg_change,
        "sector_sentiment": "Bullish" if avg_change > 0.5 else "Bearish" if avg_change < -0.5 else "Neutral",
        "data": results[:limit],
    }


# ── Sector Rotation Scanner ──────────────────────────────────────────────────

def _compute_sector_momentum_score(
    avg_change: float,
    avg_rsi: float,
    breadth_pct: float,
    volume_flow_positive: bool,
    change_rank_pct: float,
) -> int:
    """Compute a 0-100 sector momentum score from four components."""
    change_pts = round(change_rank_pct * 30)

    if 50 <= avg_rsi <= 70:
        rsi_pts = 25
    elif 40 <= avg_rsi < 50 or 70 < avg_rsi <= 80:
        rsi_pts = 15
    elif 30 <= avg_rsi < 40:
        rsi_pts = 10
    elif avg_rsi > 80:
        rsi_pts = 5
    else:
        rsi_pts = 8

    breadth_pts = round(min(breadth_pct, 100) / 100 * 25)
    volume_pts = 20 if volume_flow_positive else 0
    return max(0, min(100, change_pts + rsi_pts + breadth_pts + volume_pts))


def _generate_rotation_signals(ranked_groups: list) -> List[str]:
    signals: List[str] = []
    for s in ranked_groups:
        if s["status"] == "Hot":
            signals.append(
                f"Money rotating INTO {s['display_name']} "
                f"(Hot, {s['avg_change_pct']:+.2f}% avg, "
                f"{s['volume_flow']['signal'].lower()}, "
                f"weight {s['market_cap_weight_pct']}%)"
            )
        elif s["status"] == "Cold":
            signals.append(
                f"Money rotating OUT OF {s['display_name']} "
                f"(Cold, {s['avg_change_pct']:+.2f}% avg, "
                f"{s['volume_flow']['signal'].lower()}, "
                f"weight {s['market_cap_weight_pct']}%)"
            )
    return signals


def run_india_sector_scanner(
    timeframe: str = "1D",
    top_n_sectors: int = 5,
    top_n_stocks: int = 3,
    min_stock_score: int = 60,
) -> dict:
    """Full NSE sector-rotation scanner — ranks the 10 tracked sector/index
    groups and surfaces stock picks. Sector weights are computed live from
    each group's aggregate free-float market cap (not a static table).

    Args:
        timeframe:       TradingView interval (default '1D').
        top_n_sectors:   Number of top groups to surface stock picks for.
        top_n_stocks:    Number of top stocks per highlighted group.
        min_stock_score: Minimum stock score for picks (0-100).

    Returns:
        Weighted market view, group heatmap, top picks, and rotation signals.
    """
    if not _TA_AVAILABLE:
        return {"error": "tradingview_ta is missing; run `pip install tradingview-ta`."}

    group_keys = [k for k in INDIA_INDICES.keys() if k != "NIFTY50" and k != "SENSEX30"]

    group_data: Dict[str, Dict[str, Any]] = {}
    for key in group_keys:
        tickers, mcap, _ = _resolve_group(key, limit=60)
        if not tickers:
            continue
        analysis = _batch_ta(tickers, timeframe)
        stock_rows: List[Dict[str, Any]] = []
        for sym, data in analysis.items():
            if data is None:
                continue
            try:
                ind = data.indicators
                o, c = ind.get("open"), ind.get("close")
                if o and c and o > 0:
                    stock_rows.append({"symbol": sym, "indicators": ind, "change": ((c - o) / o) * 100})
            except Exception:
                continue
        if stock_rows:
            group_data[key] = {
                "stock_rows": stock_rows,
                "total_market_cap": sum(mcap.values()),
            }

    if not group_data:
        return {"error": "No data returned for India sector groups", "timeframe": timeframe}

    all_changes = sorted(r["change"] for g in group_data.values() for r in g["stock_rows"])
    n_total = len(all_changes)

    def _pct_rank(val: float) -> float:
        count_below = sum(1 for c in all_changes if c < val)
        return count_below / n_total if n_total > 0 else 0.5

    stock_scores: Dict[str, Dict[str, Any]] = {}
    for key, g in group_data.items():
        for r in g["stock_rows"]:
            sym = r["symbol"]
            if sym in stock_scores:
                continue
            try:
                pct_rank = _pct_rank(r["change"])
                result = compute_stock_score(r["indicators"], change_pct_rank=pct_rank, currency=_CURRENCY)
                if result:
                    stock_scores[sym] = {"score_result": result, "change": r["change"], "indicators": r["indicators"]}
            except Exception:
                continue

    group_agg: Dict[str, Dict[str, Any]] = {}
    for key, g in group_data.items():
        changes, rsis = [], []
        advancing = declining = 0
        net_volume_flow = 0.0
        picks: List[dict] = []
        for r in g["stock_rows"]:
            chg = r["change"]
            changes.append(chg)
            if chg > 0:
                advancing += 1
            elif chg < 0:
                declining += 1
            ind = r["indicators"]
            rsi = ind.get("RSI")
            if rsi is not None:
                rsis.append(rsi)
            vol = ind.get("volume", 0) or 0
            vol_sma = ind.get("volume.SMA20", 0) or 0
            net_volume_flow += vol - vol_sma
            sc = stock_scores.get(r["symbol"])
            if sc:
                picks.append({"symbol": r["symbol"], "score_result": sc["score_result"], "indicators": ind})

        total = len(g["stock_rows"])
        group_agg[key] = {
            "avg_change": round(sum(changes) / len(changes), 2) if changes else 0.0,
            "avg_rsi": round(sum(rsis) / len(rsis), 2) if rsis else 50.0,
            "advancing": advancing,
            "declining": declining,
            "total_stocks": total,
            "breadth_pct": round(advancing / total * 100, 1) if total else 0.0,
            "net_volume_flow": net_volume_flow,
            "volume_flow_positive": net_volume_flow > 0,
            "total_market_cap": g["total_market_cap"],
            "stock_data": picks,
        }

    sorted_by_change = sorted(group_agg.keys(), key=lambda k: group_agg[k]["avg_change"])
    change_rank_map = {
        k: i / len(sorted_by_change) if len(sorted_by_change) > 1 else 0.5
        for i, k in enumerate(sorted_by_change)
    }

    total_mcap_all = sum(g["total_market_cap"] for g in group_agg.values()) or 1.0

    for key, agg in group_agg.items():
        momentum = _compute_sector_momentum_score(
            avg_change=agg["avg_change"],
            avg_rsi=agg["avg_rsi"],
            breadth_pct=agg["breadth_pct"],
            volume_flow_positive=agg["volume_flow_positive"],
            change_rank_pct=change_rank_map.get(key, 0.5),
        )
        agg["momentum_score"] = momentum
        agg["market_cap_weight_pct"] = round(agg["total_market_cap"] / total_mcap_all * 100, 2)
        if momentum >= 65 and agg["volume_flow_positive"]:
            agg["status"] = "Hot"
        elif momentum >= 50 or (agg["avg_change"] > 0 and agg["breadth_pct"] > 50):
            agg["status"] = "Warming"
        elif momentum >= 35 and not agg["volume_flow_positive"]:
            agg["status"] = "Cooling"
        else:
            agg["status"] = "Cold"

    heatmap: List[dict] = []
    for key in sorted(group_agg.keys(), key=lambda k: group_agg[k].get("momentum_score", 0), reverse=True):
        agg = group_agg[key]
        heatmap.append({
            "group": key,
            "display_name": INDEX_DESCRIPTIONS.get(key, key),
            "market_cap_weight_pct": agg["market_cap_weight_pct"],
            "status": agg["status"],
            "momentum_score": agg.get("momentum_score", 0),
            "avg_change_pct": agg["avg_change"],
            "avg_rsi": agg["avg_rsi"],
            "breadth": {
                "advancing": agg["advancing"],
                "declining": agg["declining"],
                "breadth_pct": agg["breadth_pct"],
            },
            "volume_flow": {
                "net_flow": round(agg["net_volume_flow"]),
                "signal": "Inflow" if agg["volume_flow_positive"] else "Outflow",
            },
            "stocks_analyzed": agg["total_stocks"],
        })

    weighted_change = weighted_rsi = weighted_momentum = 0.0
    for key, agg in group_agg.items():
        w = agg["market_cap_weight_pct"]
        weighted_change += agg["avg_change"] * w
        weighted_rsi += agg["avg_rsi"] * w
        weighted_momentum += agg.get("momentum_score", 0) * w
    total_w = sum(a["market_cap_weight_pct"] for a in group_agg.values()) or 1.0
    weighted_change = round(weighted_change / total_w, 2)
    weighted_rsi = round(weighted_rsi / total_w, 2)
    weighted_momentum = round(weighted_momentum / total_w, 1)

    market_sentiment = (
        "Bullish" if weighted_change > 0.5
        else "Bearish" if weighted_change < -0.5
        else "Neutral"
    )

    top_group_keys = [h["group"] for h in heatmap[:top_n_sectors]]
    group_top_picks: Dict[str, list] = {}
    for key in top_group_keys:
        agg = group_agg[key]
        qualified = [c for c in agg.get("stock_data", []) if c["score_result"]["score"] >= min_stock_score]
        qualified.sort(key=lambda x: x["score_result"]["score"], reverse=True)

        picks: List[dict] = []
        for c in qualified[:top_n_stocks]:
            result = c["score_result"]
            ind = c["indicators"]
            metrics = compute_metrics(ind)
            entry: dict = {
                "symbol": c["symbol"],
                "price": metrics["price"] if metrics else 0,
                "currency": _CURRENCY,
                "stock_score": result["score"],
                "grade": result["grade"],
                "trend_state": result["trend_state"],
                "change_pct": result["change_pct"],
                "signals": result["signals"],
                "penalties": result.get("penalties", []),
                "liquidity": result.get("liquidity", {}),
            }
            if result["score"] >= 70:
                setup = compute_trade_setup(ind)
                if setup:
                    quality = compute_trade_quality(ind, result["score"], setup)
                    entry["trade_setup"] = {
                        "setup_types": setup["setup_types"],
                        "entry_points": setup["entry_points"],
                        "stop_loss": setup["stop_loss"],
                        "stop_distance_pct": setup["stop_distance_pct"],
                        "targets": setup["targets"],
                        "risk_reward": setup["risk_reward"],
                        "supports": setup["supports"],
                        "resistances": setup["resistances"],
                    }
                    entry["trade_quality_score"] = quality["trade_quality_score"]
                    entry["trade_quality"] = quality["quality"]
            picks.append(entry)
        group_top_picks[key] = picks

    return {
        "exchange": "NSE/BSE",
        "timeframe": timeframe,
        "total_groups": len(heatmap),
        "total_stocks_scanned": len(stock_scores),
        "weighted_market_view": {
            "weighted_change_pct": weighted_change,
            "weighted_rsi": weighted_rsi,
            "weighted_momentum": weighted_momentum,
            "market_sentiment": market_sentiment,
        },
        "sector_heatmap": heatmap,
        "sector_top_picks": group_top_picks,
        "rotation_signals": _generate_rotation_signals(heatmap),
        "disclaimer": "For educational/informational purposes only. Not financial advice.",
    }


# ── Index Analysis ───────────────────────────────────────────────────────────

def analyze_india_index(index: str = "NIFTY50", timeframe: str = "1D", limit: int = 30) -> dict:
    """Analyze an India index showing constituent performance with full indicators.

    Args:
        index:     Index name (NIFTY50, BANKNIFTY, SENSEX30, NIFTYIT, NIFTYPHARMA, ...).
        timeframe: TradingView interval (default '1D').
        limit:     Maximum number of stocks to show in detail.

    Returns:
        Index level quote, statistics, sector breakdown, top gainers/losers, all_stocks.
    """
    if not _TA_AVAILABLE:
        return {"error": "tradingview_ta is missing; run `pip install tradingview-ta`."}

    index_key = index.strip().upper().replace(" ", "")
    if index_key not in INDIA_INDICES:
        return {
            "error": f"Unknown index: {index}",
            "available_indices": get_index_names(),
        }

    spec = INDIA_INDICES[index_key]
    tickers, mcap, sector_map = _resolve_group(index_key, limit=max(limit, int(spec.get("approx_count", 50))))
    if not tickers:
        return {"error": f"No constituents resolved for {index_key}", "timeframe": timeframe}

    analysis = _batch_ta(tickers, timeframe)

    all_stocks: List[dict] = []
    for sym, data in analysis.items():
        if data is None:
            continue
        try:
            ind = data.indicators
            metrics = compute_metrics(ind)
            if not metrics:
                continue
            extended = extract_extended_indicators(ind)
            all_stocks.append({
                "symbol": sym,
                "sector": sector_map.get(sym, "Unknown"),
                "market_cap": mcap.get(sym),
                "price": metrics.get("price", 0),
                "changePercent": metrics.get("change", 0),
                "volume": ind.get("volume", 0),
                "rsi": extended["rsi"]["value"],
                "rsi_signal": extended["rsi"]["signal"],
                "sma20": extended["sma"]["sma20"],
                "sma50": extended["sma"]["sma50"],
                "sma200": extended["sma"]["sma200"],
                "macd_crossover": extended["macd"]["crossover"],
                "volume_signal": extended["volume"]["signal"],
                "bbw": metrics.get("bbw", 0),
                "bb_rating": metrics.get("rating", 0),
                "bb_signal": metrics.get("signal", "N/A"),
            })
        except Exception:
            continue

    if not all_stocks:
        return {"error": f"No data returned for {index_key} constituents", "timeframe": timeframe}

    changes = [s["changePercent"] for s in all_stocks]
    avg_change = sum(changes) / len(changes)
    advancing = len([c for c in changes if c > 0])
    declining = len([c for c in changes if c < 0])
    unchanged = len([c for c in changes if c == 0])

    sector_perf: Dict[str, Any] = {}
    for s in all_stocks:
        sec = s["sector"]
        sector_perf.setdefault(sec, {"stocks": 0, "total_change": 0.0})
        sector_perf[sec]["stocks"] += 1
        sector_perf[sec]["total_change"] += s["changePercent"]

    sector_summary = [
        {"sector": sec, "stocks_count": d["stocks"], "avg_change": round(d["total_change"] / d["stocks"], 2)}
        for sec, d in sorted(sector_perf.items(), key=lambda x: x[1]["total_change"] / x[1]["stocks"], reverse=True)
    ]

    by_change = sorted(all_stocks, key=lambda x: x["changePercent"], reverse=True)

    index_level = None
    quote_symbol = INDEX_QUOTE_SYMBOL.get(index_key)
    if quote_symbol:
        try:
            from tradingview_mcp.core.services.yahoo_finance_service import get_price
            from tradingview_mcp.core.utils.validators import normalize_yahoo_symbol
            yq = get_price(normalize_yahoo_symbol(index_key))
            if "error" not in yq:
                index_level = yq
        except Exception:
            pass

    return {
        "index": index_key,
        "description": INDEX_DESCRIPTIONS.get(index_key, ""),
        "index_quote_symbol": quote_symbol,
        "index_level": index_level,
        "timeframe": timeframe,
        "index_stats": {
            "approx_constituents": spec.get("approx_count"),
            "analyzed": len(all_stocks),
            "avg_change": round(avg_change, 2),
            "advancing": advancing,
            "declining": declining,
            "unchanged": unchanged,
            "breadth": round(advancing / len(all_stocks) * 100, 1) if all_stocks else 0,
            "sentiment": "Bullish" if avg_change > 0.5 else "Bearish" if avg_change < -0.5 else "Neutral",
        },
        "sector_breakdown": sector_summary,
        "top_gainers": by_change[:5],
        "top_losers": by_change[-5:][::-1],
        "all_stocks": by_change[:limit],
        "note": "Constituents approximated by free-float market-cap ranking within "
                "sector/industry filters, not NSE's official index file.",
    }


# ── Stock Screener ───────────────────────────────────────────────────────────

def screen_india_stocks(
    timeframe: str = "1D",
    min_score: int = 55,
    index_filter: str = "",
    limit: int = 20,
) -> dict:
    """Production stock ranking engine for NSE — finds strong stocks with setups.

    Args:
        timeframe:    TradingView interval (default '1D').
        min_score:    Minimum stock score to include (0-100).
        index_filter: Filter by group (BANKNIFTY, NIFTYIT, ...); empty = top 600 NSE by market cap.
        limit:        Maximum results.

    Returns:
        Qualified trades, watchlist, grade distribution, and execution rules.
    """
    if not _TA_AVAILABLE:
        return {"error": "tradingview_ta is missing; run `pip install tradingview-ta`."}

    sector_map: Dict[str, str] = {}
    if index_filter:
        symbols, _, sector_map = _resolve_group(index_filter, limit=200)
        if not symbols:
            return {"error": f"Unknown or empty group: {index_filter}", "available": get_index_names()}
        source_label = index_filter.strip().upper()
    else:
        symbols = load_symbols("nse")
        source_label = "Top 600 NSE by market cap"

    if not symbols:
        return {"error": "No NSE symbols found."}

    analysis = _batch_ta(symbols, timeframe)
    raw_results: List[tuple] = []
    for sym, data in analysis.items():
        if data is None:
            continue
        try:
            ind = data.indicators
            o, c = ind.get("open"), ind.get("close")
            if not o or not c or o <= 0:
                continue
            raw_results.append((sym, ind, ((c - o) / o) * 100))
        except Exception:
            continue

    if not raw_results:
        return {"error": "No data returned for NSE stocks", "timeframe": timeframe}

    changes = sorted(r[2] for r in raw_results)
    n = len(changes)

    def _pct_rank(val: float) -> float:
        return sum(1 for c in changes if c < val) / n if n > 0 else 0.5

    scored_stocks: List[dict] = []
    for sym, ind, change in raw_results:
        try:
            pct_rank = _pct_rank(change)
            result = compute_stock_score(ind, change_pct_rank=pct_rank, currency=_CURRENCY)
            if not result or result["score"] < min_score:
                continue
            metrics = compute_metrics(ind)
            if not metrics:
                continue

            vol_sma = ind.get("volume.SMA20")
            liquidity_status = "Pass"
            if vol_sma and vol_sma < 50000:
                liquidity_status = "Fail — Very Low"
                if min_score >= 55:
                    continue

            stock_entry: dict = {
                "symbol": sym,
                "sector": sector_map.get(sym, "Unknown"),
                "price": metrics["price"],
                "stock_score": result["score"],
                "grade": result["grade"],
                "trend_state": result["trend_state"],
                "change_pct": result["change_pct"],
                "score_breakdown": result["breakdown"],
                "signals": result["signals"],
                "penalties": result["penalties"],
                "liquidity_status": liquidity_status,
            }

            if result["score"] >= 70:
                setup = compute_trade_setup(ind)
                if setup:
                    quality = compute_trade_quality(ind, result["score"], setup)
                    stock_entry["trade_setup"] = {
                        "setup_types": setup["setup_types"],
                        "entry_points": setup["entry_points"],
                        "stop_loss": setup["stop_loss"],
                        "stop_distance_pct": setup["stop_distance_pct"],
                        "targets": setup["targets"],
                        "risk_reward": setup["risk_reward"],
                        "supports": setup["supports"],
                        "resistances": setup["resistances"],
                    }
                    stock_entry["trade_quality_score"] = quality["trade_quality_score"]
                    stock_entry["trade_quality"] = quality["quality"]
                    stock_entry["trade_notes"] = quality["notes"]
                    stock_entry["trade_quality_breakdown"] = quality["breakdown"]

            scored_stocks.append(stock_entry)
        except Exception:
            continue

    scored_stocks.sort(key=lambda x: (x["stock_score"], x.get("trade_quality_score", 0)), reverse=True)

    grades: Dict[str, int] = {}
    for s in scored_stocks:
        grades[s["grade"]] = grades.get(s["grade"], 0) + 1

    qualified = [s for s in scored_stocks if s["stock_score"] >= 70 and s.get("trade_quality_score", 0) >= 65]
    watchlist = [s for s in scored_stocks if s["stock_score"] < 70 or s.get("trade_quality_score", 0) < 65]

    return {
        "source": source_label,
        "timeframe": timeframe,
        "min_score": min_score,
        "total_scanned": len(raw_results),
        "total_passed": len(scored_stocks),
        "grade_distribution": grades,
        "qualified_trades": qualified[:limit],
        "qualified_count": len(qualified),
        "watchlist": watchlist[:max(5, limit - len(qualified))],
        "execution_rules": {
            "trade_threshold": "Stock Score >= 70 AND Trade Quality >= 65",
            "risk_reward_min": "R:R to Target 2 >= 2.0 preferred",
            "disclaimer": "For educational/informational purposes only. Not financial advice.",
        },
    }


# ── Trade Plan ────────────────────────────────────────────────────────────────

def generate_india_trade_plan(symbol: str, timeframe: str = "1D") -> dict:
    """Generate a full trade plan for a specific NSE/BSE stock.

    Args:
        symbol:    NSE/BSE stock symbol (e.g. 'RELIANCE'). Prefixed with NSE: if bare.
        timeframe: TradingView interval (default '1D').

    Returns:
        Complete plan: stock score, setup, stop-loss, targets, quality, and S/R.
    """
    if not _TA_AVAILABLE:
        return {"error": "tradingview_ta is missing; run `pip install tradingview-ta`."}

    full_symbol = symbol.upper() if ":" in symbol else f"NSE:{symbol.upper()}"

    try:
        analysis = get_multiple_analysis(screener=_SCREENER, interval=timeframe, symbols=[full_symbol])
    except Exception as exc:
        return {"error": f"Analysis failed: {exc}"}

    if full_symbol not in analysis or analysis[full_symbol] is None:
        return {"error": f"No data found for {full_symbol}"}

    ind = analysis[full_symbol].indicators
    if ind.get("ATR") is None:
        from tradingview_mcp.core.services.screener_provider import fetch_atr_for_ticker
        atr_value = fetch_atr_for_ticker(full_symbol, _SCREENER, timeframe)
        if atr_value is not None:
            ind["ATR"] = atr_value
    metrics = compute_metrics(ind)
    if not metrics:
        return {"error": f"Could not compute metrics for {full_symbol}"}

    score_result = compute_stock_score(ind, currency=_CURRENCY)
    if not score_result:
        return {"error": f"Could not compute stock score for {full_symbol}"}

    setup = compute_trade_setup(ind)
    quality = compute_trade_quality(ind, score_result["score"], setup) if setup else None
    extended = extract_extended_indicators(ind)

    output: dict = {
        "symbol": full_symbol,
        "currency": _CURRENCY,
        "timeframe": timeframe,
        "price": metrics["price"],
        "change_pct": score_result["change_pct"],
        "stock_score": score_result["score"],
        "grade": score_result["grade"],
        "trend_state": score_result["trend_state"],
        "score_breakdown": score_result["breakdown"],
        "signals": score_result["signals"],
        "penalties": score_result["penalties"],
        "liquidity": score_result.get("liquidity", {}),
        "rsi": extended["rsi"],
        "macd": extended["macd"],
        "adx": extended["adx"],
        "volume": extended["volume"],
        "ema": extended["ema"],
        "bollinger_bands": extended["bollinger_bands"],
        "tv_recommendation": extended["tv_recommendation"],
    }

    if setup:
        output["trade_setup"] = {
            "setup_types": setup["setup_types"],
            "entry_points": setup["entry_points"],
            "stop_loss": setup["stop_loss"],
            "stop_distance_pct": setup["stop_distance_pct"],
            "targets": setup["targets"],
            "risk_reward": setup["risk_reward"],
            "supports": setup["supports"],
            "resistances": setup["resistances"],
        }

    if quality:
        output["trade_quality_score"] = quality["trade_quality_score"]
        output["trade_quality"] = quality["quality"]
        output["trade_quality_breakdown"] = quality["breakdown"]
        output["trade_notes"] = quality["notes"]

    ss = score_result["score"]
    tq = quality["trade_quality_score"] if quality else 0
    rr2 = setup["risk_reward"]["to_target_2"] if setup else 0

    if ss >= 70 and tq >= 65 and rr2 and rr2 >= 2.0:
        recommendation = "QUALIFIED — Strong stock with actionable setup"
    elif ss >= 70 and tq >= 50:
        recommendation = "CONDITIONAL — Good stock but setup needs improvement"
    elif ss >= 55:
        recommendation = "WATCHLIST — Monitor for better entry"
    else:
        recommendation = "AVOID — Does not meet momentum/quality criteria"

    output["recommendation"] = recommendation
    output["disclaimer"] = "For educational/informational purposes only. Not financial advice."
    return output


# ── Fibonacci Retracement ────────────────────────────────────────────────────

def analyze_india_fibonacci(symbol: str, lookback: str = "52W", timeframe: str = "1D") -> dict:
    """Fibonacci retracement analysis for an NSE/BSE stock.

    Args:
        symbol:    NSE/BSE stock symbol (e.g. 'RELIANCE').
        lookback:  Period for swing high/low — '1M', '3M', '6M', '52W', 'ALL'.
        timeframe: TradingView interval (default '1D').

    Returns:
        Fibonacci retracement & extension levels, price position, and context.
    """
    if not _TA_AVAILABLE:
        return {"error": "tradingview_ta is missing; run `pip install tradingview-ta`."}

    valid_lookbacks = {"1M", "3M", "6M", "52W", "ALL"}
    if lookback not in valid_lookbacks:
        return {"error": f"Invalid lookback: {lookback}", "valid": sorted(valid_lookbacks)}

    full_symbol = symbol.upper() if ":" in symbol else f"NSE:{symbol.upper()}"

    LOOKBACK_COLUMNS = {
        "1M": ("High.1M", "Low.1M"),
        "3M": ("High.3M", "Low.3M"),
        "6M": ("High.6M", "Low.6M"),
        "52W": ("price_52_week_high", "price_52_week_low"),
        "ALL": ("High.All", "Low.All"),
    }

    swing_high: Optional[float] = None
    swing_low: Optional[float] = None
    swing_source: Optional[str] = None

    if _SCREENER_AVAILABLE:
        try:
            high_col, low_col = LOOKBACK_COLUMNS[lookback]
            q = (
                Query()
                .set_markets(_SCREENER)
                .select("close", high_col, low_col)
                .set_tickers([full_symbol])
            )
            fib_cache_key = ("india_fib_swing_v1", full_symbol, lookback)
            _, df = _scan_with_retry(q, cache_key=fib_cache_key)
            if not df.empty:
                row = df.iloc[0]
                h = row.get(high_col)
                ll = row.get(low_col)
                if h is not None and ll is not None and h > ll:
                    swing_high = float(h)
                    swing_low = float(ll)
                    swing_source = f"screener ({lookback} period high/low)"
        except Exception:
            pass

    try:
        analysis = get_multiple_analysis(screener=_SCREENER, interval=timeframe, symbols=[full_symbol])
    except Exception as exc:
        return {"error": f"Analysis failed: {exc}"}

    if full_symbol not in analysis or analysis[full_symbol] is None:
        return {"error": f"No data found for {full_symbol}"}

    ind = analysis[full_symbol].indicators
    if ind.get("ATR") is None:
        from tradingview_mcp.core.services.screener_provider import fetch_atr_for_ticker
        atr_value = fetch_atr_for_ticker(full_symbol, _SCREENER, timeframe)
        if atr_value is not None:
            ind["ATR"] = atr_value
    close = ind.get("close")
    if not close:
        return {"error": f"No price data for {full_symbol}"}

    if swing_high is None or swing_low is None:
        fib_r3 = ind.get("Pivot.M.Fibonacci.R3")
        fib_s3 = ind.get("Pivot.M.Fibonacci.S3")
        classic_r3 = ind.get("Pivot.M.Classic.R3")
        classic_s3 = ind.get("Pivot.M.Classic.S3")
        h_candidate = fib_r3 or classic_r3
        l_candidate = fib_s3 or classic_s3
        if h_candidate and l_candidate and h_candidate > l_candidate:
            swing_high = float(h_candidate)
            swing_low = float(l_candidate)
            swing_source = "pivot points (R3/S3 fallback)"
        else:
            return {
                "error": "Could not determine swing high/low for Fibonacci calculation",
                "hint": "Period high/low data not available for this symbol",
            }

    swing_range_pct = ((swing_high - swing_low) / swing_low) * 100
    if swing_range_pct < 2:
        return {
            "error": f"Swing range too narrow ({swing_range_pct:.1f}%) for meaningful Fibonacci levels",
            "swing_high": round(swing_high, 2),
            "swing_low": round(swing_low, 2),
        }

    ema50 = ind.get("EMA50")
    ema200 = ind.get("EMA200")
    trend, trend_reasoning = detect_trend_for_fibonacci(close, swing_high, swing_low, ema50, ema200)
    fib_levels = compute_fibonacci_levels(swing_high, swing_low, trend)
    position = analyze_fibonacci_position(close, fib_levels)

    rsi_val = ind.get("RSI")
    atr_val = ind.get("ATR")
    vol = ind.get("volume")
    vol_sma = ind.get("volume.SMA20")
    vol_ratio = round(vol / vol_sma, 2) if vol and vol_sma and vol_sma > 0 else None
    change_pct = (
        round(((close - ind.get("open", close)) / ind.get("open", close)) * 100, 2)
        if ind.get("open") else None
    )

    interp_parts = [f"Price is at {position['retracement_depth_pct']}% retracement of the {trend}."]
    if position.get("key_zone"):
        interp_parts.append(f"Currently in {position['key_zone']}.")
    if position.get("fib_supports"):
        nearest_s = position["fib_supports"][0]
        interp_parts.append(f"Key Fib support at {nearest_s['price']} ({nearest_s['ratio']}).")
    if position.get("fib_resistances"):
        nearest_r = position["fib_resistances"][0]
        interp_parts.append(f"Key Fib resistance at {nearest_r['price']} ({nearest_r['ratio']}).")

    return {
        "symbol": full_symbol,
        "timeframe": timeframe,
        "lookback_period": lookback,
        "price": round(close, 2),
        "change_pct": change_pct,
        "swing_high": round(swing_high, 2),
        "swing_low": round(swing_low, 2),
        "swing_range_pct": round(swing_range_pct, 1),
        "swing_source": swing_source,
        "trend": trend,
        "trend_reasoning": trend_reasoning,
        "retracement_levels": fib_levels["retracement_levels"],
        "extension_levels": fib_levels["extension_levels"],
        "price_position": position,
        "context": {
            "rsi": round(rsi_val, 1) if rsi_val else None,
            "ema50": round(ema50, 2) if ema50 else None,
            "ema200": round(ema200, 2) if ema200 else None,
            "atr": round(atr_val, 2) if atr_val else None,
            "volume_ratio": vol_ratio,
        },
        "interpretation": " ".join(interp_parts),
        "disclaimer": "For educational/informational purposes only. Not financial advice.",
    }
