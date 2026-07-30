"""
TradingView MCP Server — routing layer only.

Each @mcp.tool() handler is responsible for:
  1. Validating / sanitising parameters
  2. Delegating to the appropriate service module
  3. Returning the result

No business logic lives here. All computation is in core/services/*.
"""
from __future__ import annotations

import argparse
import os
from typing import Optional

from mcp.server.fastmcp import FastMCP

# ── Service imports ────────────────────────────────────────────────────────────
from tradingview_mcp.core.services.coinlist import load_symbols
from tradingview_mcp.core.services.screener_service import (
    fetch_bollinger_analysis,
    fetch_trending_analysis,
    analyze_coin,
    scan_consecutive_candles,
    scan_advanced_candle_patterns_single_tf,
    fetch_multi_timeframe_patterns,
    run_multi_timeframe_analysis,
)
from tradingview_mcp.core.services.scanner_service import (
    volume_breakout_scan,
    volume_confirmation_analyze,
    smart_volume_scan,
)
from tradingview_mcp.core.services.multi_agent_service import run_multi_agent_analysis
from tradingview_mcp.core.services.egx_service import (
    get_egx_market_overview,
    scan_egx_sector,
    run_egx_sector_scanner,
    analyze_egx_index,
    screen_egx_stocks,
    generate_egx_trade_plan,
    analyze_egx_fibonacci,
)
from tradingview_mcp.core.services.india_service import (
    get_india_market_overview,
    scan_india_sector,
    run_india_sector_scanner,
    analyze_india_index,
    screen_india_stocks,
    generate_india_trade_plan,
    analyze_india_fibonacci,
)
from tradingview_mcp.core.services.nse_options_service import (
    get_nse_option_chain,
    get_nse_unusual_options_activity,
)
from tradingview_mcp.core.services.paper_trading_service import (
    paper_trade as _paper_trade,
    get_paper_portfolio,
    get_paper_trade_history,
    paper_option_trade as _paper_option_trade,
    get_paper_option_portfolio,
    get_paper_option_trade_history,
    place_order as _place_order,
    get_broker_status,
    get_broker_positions,
    get_broker_holdings,
)
from tradingview_mcp.core.broker import axisdirect as _axisdirect
from tradingview_mcp.core.broker import robinhood as _robinhood
from tradingview_mcp.core.services import strategy_service as _strategy
from tradingview_mcp.core.services.sentiment_service import analyze_sentiment
from tradingview_mcp.core.services.news_service import fetch_news_summary
from tradingview_mcp.core.services.yahoo_finance_service import (
    get_price,
    get_market_snapshot,
)
from tradingview_mcp.core.services.bitcoin_market_service import get_bitcoin_market_pulse
from tradingview_mcp.core.services.extended_hours_service import get_extended_hours_price
from tradingview_mcp.core.services.options_service import (
    get_options_chain,
    get_unusual_options_activity,
)
from tradingview_mcp.core.services.futures_service import (
    get_futures_overview,
    get_futures_movers,
    get_futures_category_snapshot,
    get_futures_watchlist,
)
from tradingview_mcp.core.services.backtest_service import (
    run_backtest,
    compare_strategies as _compare_strategies,
    walk_forward_backtest,
)
from tradingview_mcp.core.utils.validators import (
    sanitize_timeframe,
    sanitize_exchange,
    normalize_tradingview_symbol,
    normalize_yahoo_symbol,
)
from tradingview_mcp.core.errors import (
    BatchExecutionError,
    ErrorCode,
    make_error,
)

try:
    import tradingview_screener  # noqa: F401
    TRADINGVIEW_SCREENER_AVAILABLE = True
except ImportError:
    TRADINGVIEW_SCREENER_AVAILABLE = False


# ── MCP server instance ────────────────────────────────────────────────────────

mcp = FastMCP(
    name="TradingView Multi-Market Screener",
    instructions=(
        "Multi-market screener backed by TradingView. "
        "Supports crypto exchanges (KuCoin, Binance, Bybit, MEXC, etc.), stock markets "
        "(EGX, BIST, NASDAQ, NYSE, Bursa Malaysia, HKEX, SSE, SZSE, TWSE, TPEX, NSE/BSE India), "
        "and futures markets (CME, COMEX, NYMEX, CBOT — equity index, energy, metals, "
        "agriculture, rates, forex, crypto futures). "
        "Tools: top_gainers, top_losers, bollinger_scan, coin_analysis, multi_agent_analysis, "
        "volume_breakout_scanner, futures_market_overview, futures_top_movers, "
        "futures_category_snapshot, futures_watchlist, egx_market_overview, "
        "india_market_overview, india_sector_scan, india_sector_scanner, india_index_analysis, "
        "india_stock_screener, india_trade_plan, india_fibonacci_retracement, nse_option_chain, "
        "nse_options_unusual_activity, paper_trade, paper_portfolio, paper_trade_history, "
        "place_order, paper_option_trade, paper_option_portfolio, paper_option_trade_history, "
        "strategy_setup_account, strategy_find_trades, strategy_status, strategy_log_trade, "
        "strategy_close_trade, strategy_manage_positions, strategy_daily_run, market_tracker "
        "(a $500 momentum/breakout strategy engine that screens liquid US stocks + options, sizes "
        "to a Balanced risk model, manages exits, journals trades, and reads whole-market regime "
        "risk-on/off to avoid bad trades), "
        "broker_status, broker_positions, broker_holdings, "
        "axisdirect_login_start, axisdirect_login_complete, robinhood_login, and more. "
        "Use exchange='NSE' with the generic tools (coin_analysis, top_gainers, "
        "multi_agent_analysis, combined_analysis, multi_timeframe_analysis, "
        "backtest_strategy with a .NS/.BO symbol) for India too. Live order execution "
        "defaults to off (paper trading only); BROKER_PROVIDER=axisdirect or "
        "BROKER_PROVIDER=robinhood enables real orders once configured — Robinhood has "
        "NO paper mode, every live order there is real money immediately."
    ),
)


# ── Screener tools ─────────────────────────────────────────────────────────────

@mcp.tool()
def top_gainers(exchange: str = "KUCOIN", timeframe: str = "15m", limit: int = 25) -> list[dict] | dict:
    """Return top gainers for an exchange and timeframe using Bollinger Band analysis.

    Args:
        exchange: Exchange name — crypto: KUCOIN, BINANCE, BYBIT, MEXC; stocks: EGX, BIST, NASDAQ, NYSE, BURSA, HKEX, SSE, SZSE, TWSE, TPEX
        timeframe: One of 5m, 15m, 1h, 4h, 1D, 1W, 1M
        limit: Number of rows to return (max 50)

    Returns:
        list[dict] on success. On total upstream failure returns a structured
        error envelope: ``{"error": {"code": "ALL_BATCHES_FAILED", ...}}``.
    """
    exchange = sanitize_exchange(exchange, "KUCOIN")
    timeframe = sanitize_timeframe(timeframe, "15m")
    limit = max(1, min(limit, 50))
    try:
        rows = fetch_trending_analysis(exchange, timeframe=timeframe, limit=limit)
    except BatchExecutionError as e:
        return make_error(
            ErrorCode.ALL_BATCHES_FAILED, str(e),
            batches_attempted=e.batches_attempted,
            batches_failed=e.batches_failed,
            first_error=e.first_error,
        )
    return [{"symbol": r["symbol"], "changePercent": r["changePercent"], "indicators": dict(r["indicators"])} for r in rows]


@mcp.tool()
def top_losers(exchange: str = "KUCOIN", timeframe: str = "15m", limit: int = 25) -> list[dict] | dict:
    """Return top losers for an exchange and timeframe. Supports crypto (KUCOIN, BINANCE, MEXC) and stocks (EGX, BIST, NASDAQ).

    Returns ``list[dict]`` on success, or an error envelope on total upstream
    failure (``{"error": {"code": "ALL_BATCHES_FAILED", ...}}``).
    """
    exchange = sanitize_exchange(exchange, "KUCOIN")
    timeframe = sanitize_timeframe(timeframe, "15m")
    limit = max(1, min(limit, 50))
    try:
        rows = fetch_trending_analysis(exchange, timeframe=timeframe, limit=limit)
    except BatchExecutionError as e:
        return make_error(
            ErrorCode.ALL_BATCHES_FAILED, str(e),
            batches_attempted=e.batches_attempted,
            batches_failed=e.batches_failed,
            first_error=e.first_error,
        )
    rows.sort(key=lambda x: x["changePercent"])
    return [{"symbol": r["symbol"], "changePercent": r["changePercent"], "indicators": dict(r["indicators"])} for r in rows[:limit]]


@mcp.tool()
def bollinger_scan(exchange: str = "KUCOIN", timeframe: str = "4h", bbw_threshold: float = 0.04, limit: int = 50) -> list[dict]:
    """Scan for assets with low Bollinger Band Width (squeeze detection). Works with crypto and stocks.

    Args:
        exchange: Exchange — crypto: KUCOIN, BINANCE, BYBIT, MEXC; stocks: EGX, BIST, NASDAQ, NYSE, BURSA, HKEX, SSE, SZSE, TWSE, TPEX
        timeframe: One of 5m, 15m, 1h, 4h, 1D, 1W, 1M
        bbw_threshold: Maximum BBW value to filter (default 0.04)
        limit: Number of rows to return (max 100)
    """
    exchange = sanitize_exchange(exchange, "KUCOIN")
    timeframe = sanitize_timeframe(timeframe, "4h")
    limit = max(1, min(limit, 100))
    rows = fetch_bollinger_analysis(exchange, timeframe=timeframe, bbw_filter=bbw_threshold, limit=limit)
    return [{"symbol": r["symbol"], "changePercent": r["changePercent"], "indicators": dict(r["indicators"])} for r in rows]


@mcp.tool()
def rating_filter(exchange: str = "KUCOIN", timeframe: str = "5m", rating: int = 2, limit: int = 25) -> list[dict] | dict:
    """Filter coins by Bollinger Band rating.

    Args:
        exchange: Exchange name like KUCOIN, BINANCE, BYBIT, MEXC, etc.
        timeframe: One of 5m, 15m, 1h, 4h, 1D, 1W, 1M
        rating: BB rating (-3 to +3): -3=Strong Sell, -2=Sell, -1=Weak Sell, 1=Weak Buy, 2=Buy, 3=Strong Buy
        limit: Number of rows to return (max 50)

    Returns ``list[dict]`` on success, or an error envelope on total upstream
    failure (``{"error": {"code": "ALL_BATCHES_FAILED", ...}}``).
    """
    exchange = sanitize_exchange(exchange, "KUCOIN")
    timeframe = sanitize_timeframe(timeframe, "5m")
    rating = max(-3, min(3, rating))
    limit = max(1, min(limit, 50))
    try:
        rows = fetch_trending_analysis(exchange, timeframe=timeframe, filter_type="rating", rating_filter=rating, limit=limit)
    except BatchExecutionError as e:
        return make_error(
            ErrorCode.ALL_BATCHES_FAILED, str(e),
            batches_attempted=e.batches_attempted,
            batches_failed=e.batches_failed,
            first_error=e.first_error,
        )
    return [{"symbol": r["symbol"], "changePercent": r["changePercent"], "indicators": dict(r["indicators"])} for r in rows]


# ── Coin / asset analysis ──────────────────────────────────────────────────────

@mcp.tool()
def coin_analysis(symbol: str, exchange: str = "KUCOIN", timeframe: str = "15m") -> dict:
    """Get detailed analysis for a specific asset (coin or stock) on specified exchange and timeframe.

    Args:
        symbol: Symbol — crypto: "BTCUSDT", "ETHUSDT"; stocks: "COMI" (EGX), "THYAO" (BIST), "600519" (SSE), "300251" (SZSE), "2330" (TWSE), "3105" (TPEX)
        exchange: Exchange — crypto: KUCOIN, BINANCE, MEXC; stocks: EGX, BIST, NASDAQ, NYSE, BURSA, HKEX, SSE, SZSE, TWSE, TPEX
        timeframe: Time interval (5m, 15m, 1h, 4h, 1D, 1W, 1M)

    Returns:
        Detailed analysis with all indicators and metrics
    """
    exchange = sanitize_exchange(exchange, "KUCOIN")
    timeframe = sanitize_timeframe(timeframe, "15m")
    return analyze_coin(symbol, exchange, timeframe)


# ── Candle pattern tools ───────────────────────────────────────────────────────

@mcp.tool()
def consecutive_candles_scan(
    exchange: str = "KUCOIN",
    timeframe: str = "15m",
    pattern_type: str = "bullish",
    candle_count: int = 3,
    min_growth: float = 2.0,
    limit: int = 20,
) -> dict:
    """Scan for coins with consecutive growing/shrinking candles pattern.

    Args:
        exchange: Exchange name (BINANCE, KUCOIN, etc.)
        timeframe: Time interval (5m, 15m, 1h, 4h)
        pattern_type: "bullish" (growing candles) or "bearish" (shrinking candles)
        candle_count: Number of consecutive candles to check (2-5)
        min_growth: Minimum growth percentage for each candle
        limit: Maximum number of results to return
    """
    exchange = sanitize_exchange(exchange, "KUCOIN")
    timeframe = sanitize_timeframe(timeframe, "15m")
    candle_count = max(2, min(5, candle_count))
    min_growth = max(0.5, min(20.0, min_growth))
    limit = max(1, min(50, limit))
    return scan_consecutive_candles(exchange, timeframe, pattern_type, candle_count, min_growth, limit)


@mcp.tool()
def advanced_candle_pattern(
    exchange: str = "KUCOIN",
    base_timeframe: str = "15m",
    pattern_length: int = 3,
    min_size_increase: float = 10.0,
    limit: int = 15,
) -> dict:
    """Advanced candle pattern analysis using multi-timeframe data.

    Args:
        exchange: Exchange name (BINANCE, KUCOIN, etc.)
        base_timeframe: Base timeframe for analysis (5m, 15m, 1h, 4h)
        pattern_length: Number of consecutive periods to analyse (2-4)
        min_size_increase: Minimum percentage increase in candle size
        limit: Maximum number of results to return
    """
    exchange = sanitize_exchange(exchange, "KUCOIN")
    base_timeframe = sanitize_timeframe(base_timeframe, "15m")
    pattern_length = max(2, min(4, pattern_length))
    min_size_increase = max(5.0, min(50.0, min_size_increase))
    limit = max(1, min(30, limit))

    symbols = load_symbols(exchange)
    if not symbols:
        return {"error": f"No symbols found for exchange: {exchange}", "exchange": exchange}
    symbols = symbols[: min(limit * 2, 100)]

    if TRADINGVIEW_SCREENER_AVAILABLE:
        try:
            results = fetch_multi_timeframe_patterns(exchange, symbols, base_timeframe, pattern_length, min_size_increase)
            return {
                "exchange": exchange,
                "base_timeframe": base_timeframe,
                "pattern_length": pattern_length,
                "min_size_increase": min_size_increase,
                "method": "multi-timeframe",
                "total_found": len(results),
                "data": results[:limit],
            }
        except Exception:
            pass  # Fall through to single-timeframe fallback

    return scan_advanced_candle_patterns_single_tf(exchange, symbols, base_timeframe, pattern_length, min_size_increase, limit)


# ── Volume scanner tools ───────────────────────────────────────────────────────

@mcp.tool()
def volume_breakout_scanner(
    exchange: str = "KUCOIN",
    timeframe: str = "15m",
    volume_multiplier: float = 2.0,
    price_change_min: float = 3.0,
    limit: int = 25,
) -> list[dict] | dict:
    """Detect coins with volume breakout + price breakout.

    Args:
        exchange: Exchange name like KUCOIN, BINANCE, BYBIT, MEXC, etc.
        timeframe: One of 5m, 15m, 1h, 4h, 1D, 1W, 1M
        volume_multiplier: How many times the volume should be above normal level (default 2.0)
        price_change_min: Minimum price change percentage (default 3.0)
        limit: Number of rows to return (max 50)

    Returns ``list[dict]`` on success, or an error envelope on total upstream
    failure (``{"error": {"code": "ALL_BATCHES_FAILED", ...}}``). The empty
    list now strictly means "no matches today"; rate-limit cliffs surface
    explicitly.
    """
    exchange = sanitize_exchange(exchange, "KUCOIN")
    timeframe = sanitize_timeframe(timeframe, "15m")
    volume_multiplier = max(1.5, min(10.0, volume_multiplier))
    price_change_min = max(1.0, min(20.0, price_change_min))
    limit = max(1, min(limit, 50))
    try:
        return volume_breakout_scan(exchange, timeframe, volume_multiplier, price_change_min, limit)
    except BatchExecutionError as e:
        return make_error(
            ErrorCode.ALL_BATCHES_FAILED, str(e),
            batches_attempted=e.batches_attempted,
            batches_failed=e.batches_failed,
            first_error=e.first_error,
        )


@mcp.tool()
def volume_confirmation_analysis(symbol: str, exchange: str = "KUCOIN", timeframe: str = "15m") -> dict:
    """Detailed volume confirmation analysis for a specific coin.

    Args:
        symbol: Coin symbol (e.g., BTCUSDT)
        exchange: Exchange name
        timeframe: Time frame for analysis
    """
    exchange = sanitize_exchange(exchange, "KUCOIN")
    timeframe = sanitize_timeframe(timeframe, "15m")
    return volume_confirmation_analyze(symbol, exchange, timeframe)


@mcp.tool()
def smart_volume_scanner(
    exchange: str = "KUCOIN",
    min_volume_ratio: float = 2.0,
    min_price_change: float = 2.0,
    rsi_range: str = "any",
    limit: int = 20,
) -> list[dict] | dict:
    """Smart volume + technical analysis combination scanner.

    Args:
        exchange: Exchange name
        min_volume_ratio: Minimum volume multiplier (default 2.0)
        min_price_change: Minimum price change percentage (default 2.0)
        rsi_range: "oversold" (<30), "overbought" (>70), "neutral" (30-70), "any"
        limit: Number of results (max 30)

    Returns ``list[dict]`` on success, or an error envelope on total upstream
    failure (``{"error": {"code": "ALL_BATCHES_FAILED", ...}}``) — inherited
    from the inner ``volume_breakout_scan`` call.
    """
    exchange = sanitize_exchange(exchange, "KUCOIN")
    min_volume_ratio = max(1.2, min(10.0, min_volume_ratio))
    min_price_change = max(0.5, min(20.0, min_price_change))
    limit = max(1, min(limit, 30))
    try:
        return smart_volume_scan(exchange, min_volume_ratio, min_price_change, rsi_range, limit)
    except BatchExecutionError as e:
        return make_error(
            ErrorCode.ALL_BATCHES_FAILED, str(e),
            batches_attempted=e.batches_attempted,
            batches_failed=e.batches_failed,
            first_error=e.first_error,
        )


# ── Multi-agent analysis ───────────────────────────────────────────────────────

@mcp.tool()
def multi_agent_analysis(symbol: str, exchange: str = "KUCOIN", timeframe: str = "15m") -> dict:
    """Run a multi-agent debate (Technical, Sentiment, Risk) for a specific symbol.

    Args:
        symbol: Symbol — crypto: "BTCUSDT"; stocks: "COMI" (EGX), "THYAO" (BIST), "600519" (SSE), "300251" (SZSE), "2330" (TWSE), "3105" (TPEX), "GDX" (AMEX)
        exchange: Exchange — crypto: KUCOIN, BINANCE, MEXC; stocks: EGX, BIST, NASDAQ, NYSE, AMEX, NYSEARCA, PCX, SSE, SZSE, TWSE, TPEX
        timeframe: Time interval (5m, 15m, 1h, 4h, 1D, 1W)

    Returns:
        A structured debate between 3 AI agents culminating in a final trading decision.
    """
    exchange = sanitize_exchange(exchange, "KUCOIN")
    timeframe = sanitize_timeframe(timeframe, "15m")
    full_symbol = normalize_tradingview_symbol(symbol, exchange)
    return run_multi_agent_analysis(full_symbol, exchange, timeframe)


# ── EGX market tools ───────────────────────────────────────────────────────────

@mcp.tool()
def egx_market_overview(timeframe: str = "1D", limit: int = 10) -> dict:
    """Get a comprehensive overview of the Egyptian Exchange (EGX) market.

    Args:
        timeframe: One of 5m, 15m, 1h, 4h, 1D, 1W, 1M (default 1D for stocks)
        limit: Number of stocks per category (max 20)
    """
    timeframe = sanitize_timeframe(timeframe, "1D")
    limit = max(1, min(limit, 20))
    return get_egx_market_overview(timeframe, limit)


@mcp.tool()
def egx_sector_scan(sector: str = "", timeframe: str = "1D", limit: int = 20) -> dict:
    """Scan EGX stocks by sector. Shows available sectors if none specified.

    Args:
        sector: Sector name (banks, healthcare_and_pharma, real_estate, etc.)
                Leave empty to list all sectors.
        timeframe: One of 5m, 15m, 1h, 4h, 1D, 1W, 1M
        limit: Max results per sector (max 50)
    """
    timeframe = sanitize_timeframe(timeframe, "1D")
    limit = max(1, min(limit, 50))
    return scan_egx_sector(sector, timeframe, limit)


@mcp.tool()
def egx_sector_scanner(
    timeframe: str = "1D",
    top_n_sectors: int = 5,
    top_n_stocks: int = 3,
    min_stock_score: int = 60,
) -> dict:
    """Sector rotation scanner for EGX — identifies hot/cold sectors and top picks.

    Args:
        timeframe: One of 5m, 15m, 1h, 4h, 1D, 1W, 1M (default 1D)
        top_n_sectors: Number of top sectors to show stock picks for (1-18, default 5)
        top_n_stocks: Number of top stocks per highlighted sector (1-10, default 3)
        min_stock_score: Minimum stock score for picks (0-100, default 60)
    """
    timeframe = sanitize_timeframe(timeframe, "1D")
    top_n_sectors = max(1, min(18, top_n_sectors))
    top_n_stocks = max(1, min(10, top_n_stocks))
    min_stock_score = max(0, min(100, min_stock_score))
    return run_egx_sector_scanner(timeframe, top_n_sectors, top_n_stocks, min_stock_score)


@mcp.tool()
def egx_index_analysis(index: str = "EGX30", timeframe: str = "1D", limit: int = 30) -> dict:
    """Analyse an EGX index showing constituent performance with full indicators.

    Args:
        index: EGX30, EGX70, EGX100, SHARIAH33, EGX35LV, TAMAYUZ
        timeframe: One of 5m, 15m, 1h, 4h, 1D, 1W, 1M (default 1D)
        limit: Number of stocks to show in detail (max 100)
    """
    timeframe = sanitize_timeframe(timeframe, "1D")
    limit = max(1, min(limit, 100))
    return analyze_egx_index(index, timeframe, limit)


@mcp.tool()
def egx_stock_screener(
    timeframe: str = "1D",
    min_score: int = 55,
    index_filter: str = "",
    limit: int = 20,
) -> dict:
    """Production stock ranking engine for EGX — finds strong stocks with actionable setups.

    Args:
        timeframe: One of 5m, 15m, 1h, 4h, 1D, 1W, 1M (default 1D)
        min_score: Minimum stock score to include (0-100, default 55)
        index_filter: Filter by index — EGX30, EGX70, EGX100, SHARIAH33, EGX35LV, TAMAYUZ
        limit: Number of results (max 50)
    """
    timeframe = sanitize_timeframe(timeframe, "1D")
    min_score = max(0, min(100, min_score))
    limit = max(1, min(50, limit))
    return screen_egx_stocks(timeframe, min_score, index_filter, limit)


@mcp.tool()
def egx_trade_plan(symbol: str, timeframe: str = "1D") -> dict:
    """Generate a full trade plan for a specific EGX stock.

    Args:
        symbol: EGX stock symbol (e.g., "COMI", "TMGH", "FWRY")
        timeframe: One of 5m, 15m, 1h, 4h, 1D, 1W, 1M (default 1D)
    """
    timeframe = sanitize_timeframe(timeframe, "1D")
    return generate_egx_trade_plan(symbol, timeframe)


@mcp.tool()
def egx_fibonacci_retracement(symbol: str, lookback: str = "52W", timeframe: str = "1D") -> dict:
    """Fibonacci retracement analysis for EGX stocks.

    Args:
        symbol: EGX stock symbol (e.g., "COMI", "TMGH", "FWRY")
        lookback: Period for swing high/low — "1M", "3M", "6M", "52W", "ALL" (default 52W)
        timeframe: Analysis timeframe (5m, 15m, 1h, 4h, 1D, 1W, 1M — default 1D)
    """
    timeframe = sanitize_timeframe(timeframe, "1D")
    lookback = lookback.strip().upper()
    return analyze_egx_fibonacci(symbol, lookback, timeframe)


# ── India (NSE/BSE) market tools ────────────────────────────────────────────────

@mcp.tool()
def india_market_overview(timeframe: str = "1D", limit: int = 10) -> dict:
    """Get a comprehensive overview of the Indian stock market (NSE), top 600 by market cap.

    Args:
        timeframe: One of 5m, 15m, 1h, 4h, 1D, 1W, 1M (default 1D for stocks)
        limit: Number of stocks per category (max 20)
    """
    timeframe = sanitize_timeframe(timeframe, "1D")
    limit = max(1, min(limit, 20))
    return get_india_market_overview(timeframe, limit)


@mcp.tool()
def india_sector_scan(sector: str = "", timeframe: str = "1D", limit: int = 20) -> dict:
    """Scan NSE/BSE stocks by sector or index group. Shows available groups if none specified.

    Args:
        sector: Group key (BANKNIFTY, NIFTYIT, NIFTYPHARMA, NIFTYAUTO, NIFTYFMCG, NIFTYMETAL,
                NIFTYREALTY, NIFTYFINSERVICE) or a raw TradingView sector name (e.g. "Finance").
                Leave empty to list all available groups.
        timeframe: One of 5m, 15m, 1h, 4h, 1D, 1W, 1M
        limit: Max results (max 100)
    """
    timeframe = sanitize_timeframe(timeframe, "1D")
    limit = max(1, min(limit, 100))
    return scan_india_sector(sector, timeframe, limit)


@mcp.tool()
def india_sector_scanner(
    timeframe: str = "1D",
    top_n_sectors: int = 5,
    top_n_stocks: int = 3,
    min_stock_score: int = 60,
) -> dict:
    """Sector rotation scanner for NSE — identifies hot/cold sector groups and top picks.
    Sector weights are computed live from aggregate free-float market cap.

    Args:
        timeframe: One of 5m, 15m, 1h, 4h, 1D, 1W, 1M (default 1D)
        top_n_sectors: Number of top groups to show stock picks for (1-9, default 5)
        top_n_stocks: Number of top stocks per highlighted group (1-10, default 3)
        min_stock_score: Minimum stock score for picks (0-100, default 60)
    """
    timeframe = sanitize_timeframe(timeframe, "1D")
    top_n_sectors = max(1, min(9, top_n_sectors))
    top_n_stocks = max(1, min(10, top_n_stocks))
    min_stock_score = max(0, min(100, min_stock_score))
    return run_india_sector_scanner(timeframe, top_n_sectors, top_n_stocks, min_stock_score)


@mcp.tool()
def india_index_analysis(index: str = "NIFTY50", timeframe: str = "1D", limit: int = 30) -> dict:
    """Analyse an India index (NIFTY50, BANKNIFTY, SENSEX30, NIFTYIT, NIFTYPHARMA, NIFTYAUTO,
    NIFTYFMCG, NIFTYMETAL, NIFTYREALTY, NIFTYFINSERVICE) showing constituent performance.
    Constituents are approximated live by free-float market-cap ranking within each
    group's sector/industry filter (not NSE's official index file).

    Args:
        index: Index/group name (default NIFTY50)
        timeframe: One of 5m, 15m, 1h, 4h, 1D, 1W, 1M (default 1D)
        limit: Number of stocks to show in detail (max 100)
    """
    timeframe = sanitize_timeframe(timeframe, "1D")
    limit = max(1, min(limit, 100))
    return analyze_india_index(index, timeframe, limit)


@mcp.tool()
def india_stock_screener(
    timeframe: str = "1D",
    min_score: int = 55,
    index_filter: str = "",
    limit: int = 20,
) -> dict:
    """Production stock ranking engine for NSE — finds strong stocks with actionable setups.

    Args:
        timeframe: One of 5m, 15m, 1h, 4h, 1D, 1W, 1M (default 1D)
        min_score: Minimum stock score to include (0-100, default 55)
        index_filter: Filter by group — BANKNIFTY, NIFTYIT, NIFTYPHARMA, etc. (empty = top 600 NSE)
        limit: Number of results (max 50)
    """
    timeframe = sanitize_timeframe(timeframe, "1D")
    min_score = max(0, min(100, min_score))
    limit = max(1, min(50, limit))
    return screen_india_stocks(timeframe, min_score, index_filter, limit)


@mcp.tool()
def india_trade_plan(symbol: str, timeframe: str = "1D") -> dict:
    """Generate a full trade plan for a specific NSE/BSE stock.

    Args:
        symbol: NSE/BSE stock symbol (e.g., "RELIANCE", "TCS", "HDFCBANK")
        timeframe: One of 5m, 15m, 1h, 4h, 1D, 1W, 1M (default 1D)
    """
    timeframe = sanitize_timeframe(timeframe, "1D")
    return generate_india_trade_plan(symbol, timeframe)


@mcp.tool()
def india_fibonacci_retracement(symbol: str, lookback: str = "52W", timeframe: str = "1D") -> dict:
    """Fibonacci retracement analysis for NSE/BSE stocks.

    Args:
        symbol: NSE/BSE stock symbol (e.g., "RELIANCE", "TCS", "INFY")
        lookback: Period for swing high/low — "1M", "3M", "6M", "52W", "ALL" (default 52W)
        timeframe: Analysis timeframe (5m, 15m, 1h, 4h, 1D, 1W, 1M — default 1D)
    """
    timeframe = sanitize_timeframe(timeframe, "1D")
    lookback = lookback.strip().upper()
    return analyze_india_fibonacci(symbol, lookback, timeframe)


@mcp.tool()
def nse_option_chain(symbol: str, expiry: Optional[str] = None) -> dict:
    """NSE India options chain (calls + puts) for an index or F&O-eligible stock, with PCR and max pain.

    Use this for "what's the NIFTY option chain?", "BANKNIFTY PCR right now?", "max pain for
    RELIANCE this expiry?". Data comes from NSE India's own public (unofficial) website API —
    it has no key but is protected by aggressive bot-detection (Akamai). It works well from most
    residential/office networks in India; from cloud/datacenter networks it may intermittently
    return a clear NSE_BLOCKED_OR_UNAVAILABLE error rather than data. If you see that error
    repeatedly, this network is being blocked by NSE — the reliable fix is a licensed broker API
    (Zerodha Kite Connect / Upstox / Angel One), not a different scraping approach.

    Args:
        symbol: Index (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50) or an F&O-eligible
            NSE stock symbol (e.g. RELIANCE).
        expiry: Optional expiry date in NSE's format (e.g. '26-Jun-2026'). Omit for nearest expiry;
            the response's `available_expiries` lists valid values to retry with.

    Returns:
        underlying_value, requested_expiry, available_expiries, pcr_oi (put/call OI ratio),
        max_pain (strike + writer loss), strikes: list of {strike, CE, PE} with OI/volume/IV/LTP.
        On failure: {"error": {"code", "message"}}.
    """
    return get_nse_option_chain(symbol, expiry)


@mcp.tool()
def nse_options_unusual_activity(symbol: str, top_n: int = 10, min_volume: int = 100) -> dict:
    """Top NSE F&O strikes by volume/OI ratio, with OI build-up classification.

    Use this for "any unusual options activity in BANKNIFTY?", "where is smart money
    positioned in NIFTY before expiry?". Classifies each active strike as Long Buildup /
    Short Buildup / Long Unwinding / Short Covering — standard NSE F&O desk shorthand
    combining price direction with open-interest direction. Same NSE data-source caveats
    as nse_option_chain apply (see that tool's docstring).

    Args:
        symbol: Index or F&O-eligible NSE stock symbol.
        top_n: How many strikes to return, ranked by V/OI descending (default 10).
        min_volume: Filter floor for today's volume, drops illiquid noise (default 100).
    """
    top_n = max(1, min(50, top_n))
    min_volume = max(0, min_volume)
    return get_nse_unusual_options_activity(symbol, top_n, min_volume)


# ── Paper trading + order routing ───────────────────────────────────────────────

@mcp.tool()
def paper_trade(
    symbol: str,
    quantity: float,
    side: str,
    exchange: str = "NSE",
    price: Optional[float] = None,
    user_id: str = "default",
) -> dict:
    """Execute a SIMULATED (paper) BUY or SELL — no real money, no real broker involved.

    Fills at the current live market price unless you pass an explicit price. Tracks a
    local portfolio per user_id (SQLite, ~/.tradingview_mcp_data/portfolio.db), each
    starting at $10,000 notional. Use this to practice strategies or test trade ideas
    from the other India/screener/backtest tools before ever considering real capital.

    Args:
        symbol: Stock symbol (e.g. "RELIANCE", "AAPL"). Bare NSE/BSE symbols get .NS/.BO
            appended automatically for price lookup when exchange is nse/bse.
        quantity: Number of shares (> 0).
        side: "BUY" or "SELL".
        exchange: "NSE", "BSE", or any exchange yahoo_price understands (default NSE).
        price: Optional fill price override. Omit to fill at the live market price.
        user_id: Which paper account to trade under (e.g. an email). Defaults to a
            single shared "default" account if omitted.
    """
    return _paper_trade(symbol, quantity, side, exchange=exchange, price=price, user_id=user_id)


@mcp.tool()
def paper_portfolio(exchange: str = "NSE", user_id: str = "default") -> dict:
    """Current simulated portfolio: cash balance, open positions, and live unrealized P&L.

    Args:
        exchange: Exchange used to mark open positions to market (default NSE).
        user_id: Which paper account to look up (e.g. an email). Defaults to "default".
    """
    return get_paper_portfolio(exchange=exchange, user_id=user_id)


@mcp.tool()
def paper_trade_history(limit: int = 50, user_id: str = "default") -> dict:
    """Past simulated fills (BUY/SELL), newest first, with realized P&L per SELL.

    Args:
        limit: Max trades to return (default 50, max 500).
        user_id: Which paper account to look up (e.g. an email). Defaults to "default".
    """
    return get_paper_trade_history(limit=limit, user_id=user_id)


@mcp.tool()
def paper_option_trade(
    symbol: str,
    option_type: str,
    strike: float,
    expiry: str,
    quantity: float,
    side: str,
    premium: Optional[float] = None,
    user_id: str = "default",
) -> dict:
    """Execute a SIMULATED options BUY (open/add) or SELL (close) — no real money.

    Fills at the live option chain premium (last trade, or bid/ask midpoint if the
    strike hasn't traded recently) unless you pass one explicitly. US equity options
    only, 100-share contract multiplier. No naked/short writing — SELL requires an
    existing long position from a prior BUY, same simple model as stock paper trading.
    Note: backtesting for options was intentionally NOT built — there's no free source
    of historical option chains, only live snapshots, so it would only ever be a
    theoretical approximation. Use this tool to test option strategies forward in real
    time against real live premiums instead.

    Args:
        symbol: Underlying stock/ETF symbol (e.g. "AAPL", "SPY").
        option_type: "CALL" or "PUT".
        strike: Strike price — must match an existing strike on the live chain.
        expiry: Expiry date (YYYY-MM-DD) — must match one of the chain's
            available_expiries; call stock_options_chain first to see valid dates.
        quantity: Number of contracts (> 0).
        side: "BUY" or "SELL".
        premium: Optional fill price override (per share, pre-100x-multiplier).
        user_id: Which paper account to trade under (e.g. an email). Defaults to "default".
    """
    return _paper_option_trade(symbol, option_type, strike, expiry, quantity, side, premium=premium, user_id=user_id)


@mcp.tool()
def paper_option_portfolio(user_id: str = "default") -> dict:
    """Current simulated option positions: cash balance, open contracts, and live
    mark-to-market unrealized P&L per contract (re-fetches the live chain for each).

    Args:
        user_id: Which paper account to look up (e.g. an email). Defaults to "default".
    """
    return get_paper_option_portfolio(user_id=user_id)


@mcp.tool()
def paper_option_trade_history(limit: int = 50, user_id: str = "default") -> dict:
    """Past simulated option fills (BUY/SELL), newest first, with realized P&L per SELL.

    Args:
        limit: Max trades to return (default 50, max 500).
        user_id: Which paper account to look up (e.g. an email). Defaults to "default".
    """
    return get_paper_option_trade_history(limit=limit, user_id=user_id)


# ── $500 Strategy engine ────────────────────────────────────────────────────────

@mcp.tool()
def strategy_setup_account(reset: bool = False) -> dict:
    """Create the dedicated $500 strategy paper account (user_id='strategy-500').

    Idempotent: if it already exists, does nothing unless reset=True, which wipes all
    its stock/option positions, fills, and journal entries and restores the $500 balance.
    Run this once before using the other strategy_* tools.

    Args:
        reset: If True, wipe the account back to a fresh $500. Destroys all its history.
    """
    return _strategy.setup_account(reset=reset)


@mcp.tool()
def strategy_find_trades(max_candidates: int = 8, interval: str = "1D", include_options: bool = True, options_only: bool = False) -> dict:
    """Screen a broad universe (~70 liquid US names) for high-quality LONG momentum/breakout
    setups and size each to the $500 account's Balanced risk model (max ~5% risk, ~25% capital
    per trade, >=2:1 reward:risk enforced by ATR-based stops and 2R/3R targets).

    Every candidate is CLEARLY LABELED by instrument: it's a STOCK setup (📈, `label`,
    `how_to_trade`), and if an affordable contract fits, it ALSO carries an OPTION alternative
    (🎯) under `option_idea` with its own `how_to_trade`. Each card's `available_as`
    (["STOCK"] or ["STOCK","OPTION"]) and `trade_as` one-liner spell out exactly how you can
    take it; the top-level `candidate_breakdown` counts stock-only vs option-capable.

    Nothing is executed: per the strategy's discipline you review, then commit via paper_trade
    (stock) or paper_option_trade (option), both with user_id='strategy-500', then journal it
    with strategy_log_trade. An empty list is a valid, expected result on a weak day — cash is
    a position.

    Set options_only=True to return ONLY names that have an affordable, liquid call fitting
    the risk cap (the OPTION is the headline) — for when you want leverage/defined risk instead
    of tying capital up in shares. It walks strikes from near-the-money outward to find a
    contract that fits, scans deeper, and skips names where the only way in is the stock.

    Args:
        max_candidates: Max final trade cards to return (default 8, up to 20).
        interval: Analysis timeframe (default '1D' swing horizon; also 4h, 1h, 1W).
        include_options: Attach a call-option alternative to each stock card (default True).
        options_only: Only return names with an affordable, liquid option that fits the cap
            (default False). Use when you specifically want options, not shares.
    """
    interval = sanitize_timeframe(interval, "1D")
    max_candidates = max(1, min(max_candidates, 20))
    return _strategy.find_trades(max_candidates=max_candidates, interval=interval,
                                 include_options=include_options, options_only=options_only)


@mcp.tool()
def strategy_status() -> dict:
    """Full snapshot of the $500 strategy account: cash, stock + option positions with
    live mark-to-market P&L, combined equity, total return %, and trade-journal stats
    (open/closed count, win rate, realized P&L). This is the same data the dashboard shows.
    """
    return _strategy.account_status()


@mcp.tool()
def strategy_log_missed(
    symbol: str,
    instrument_type: str,
    reference_price: float,
    reason: str,
    option_type: Optional[str] = None,
    option_strike: Optional[float] = None,
    option_expiry: Optional[str] = None,
) -> dict:
    """Record a setup we PASSED on (the "shadow journal"), with the price/premium at the
    moment we decided. Later, strategy_missed_trades computes the live "what-if" return so
    we can learn whether our discipline dodged a loss or missed a gain.

    Args:
        symbol: Ticker (e.g. "F").
        instrument_type: "STOCK" or "OPTION".
        reference_price: Stock price (STOCK) or option premium (OPTION) at decision time.
        reason: Why we passed (e.g. "score 6 Avoid", "risk-off tape", "concentration").
        option_type/option_strike/option_expiry: for OPTION, to re-price the exact contract.
    """
    return _strategy.log_missed_trade(symbol, instrument_type, reference_price, reason,
                                      option_type=option_type, strike=option_strike, expiry=option_expiry)


@mcp.tool()
def strategy_missed_trades() -> dict:
    """The missed-trade scorecard: every setup we passed on, with its live 'what-if' return,
    and a discipline hit-rate (how often skipping dodged a loss vs missed a gain). Negative
    what-if = skipping SAVED money (good discipline); positive = money left on the table. Use
    it to judge whether the quality gate is calibrated right — or too strict.
    """
    return _strategy.missed_trades()


@mcp.tool()
def market_tracker() -> dict:
    """Whole-market health read → a clear trade posture, so you don't make bad trades
    into a bad tape. Pulls indices (S&P/NASDAQ/Dow), the VIX, and all 11 sector SPDRs
    from Yahoo (NOT the rate-limited TradingView scanner) and returns:
      - regime: RISK-ON / NEUTRAL / RISK-OFF + a 0-100 risk-appetite score
      - trade_posture + new_longs_ok flag (whether the tape supports new long trades)
      - sector breadth, leaders/laggards, and explicit warnings (VIX spike, weak breadth)
    Check this before adding risk; on RISK-OFF, the disciplined move is to stand down.
    """
    return _strategy.market_regime()


@mcp.tool()
def strategy_log_trade(
    symbol: str,
    instrument_type: str,
    setup_type: str,
    thesis: str,
    entry: float,
    stop: float,
    targets: list[float],
    quantity: float,
    notes: str = "",
    option_type: Optional[str] = None,
    option_strike: Optional[float] = None,
    option_expiry: Optional[str] = None,
) -> dict:
    """Journal a committed trade — the strategy doc requires a documented thesis, entry,
    stop, target(s), and R:R for every trade. Call this right after you paper_trade /
    paper_option_trade a candidate, so the dashboard and review stats capture the plan.
    For OPTION trades, also pass option_type/strike/expiry so strategy_manage_positions
    can re-price the exact contract and manage its exit.

    Args:
        symbol: Ticker (e.g. "AAPL").
        instrument_type: "STOCK" or "OPTION".
        setup_type: momentum | breakout | catalyst | high_rvol | pullback.
        thesis: One or two sentences on WHY this trade (the edge/catalyst).
        entry: Planned/actual entry price (for options: the premium paid, per share).
        stop: Predefined stop-loss (required — no trade without one; for options a
            premium level, e.g. -50% of entry).
        targets: One or more profit-target prices, e.g. [334.83, 347.93] (option premium
            levels for option trades, e.g. [1.6, 2.4]).
        quantity: Shares (stock) or contracts (option).
        notes: Optional extra context.
        option_type: "CALL" or "PUT" (OPTION trades only).
        option_strike: Strike price (OPTION trades only).
        option_expiry: Expiry date YYYY-MM-DD (OPTION trades only).
    """
    return _strategy.log_trade(symbol, instrument_type, setup_type, thesis, entry, stop, targets,
                               quantity, notes, option_type=option_type,
                               option_strike=option_strike, option_expiry=option_expiry)


@mcp.tool()
def strategy_close_trade(journal_id: int, outcome_pnl: float, notes: str = "") -> dict:
    """Close a journaled trade with its realized P&L and a review note (the doc's
    'document, review, analyze' loop). Feeds the win-rate and expectancy stats.

    Args:
        journal_id: The id returned by strategy_log_trade (also shown in strategy_status).
        outcome_pnl: Realized profit/loss in dollars (negative for a loss).
        notes: What you learned — what worked, what didn't, rule adherence.
    """
    return _strategy.close_trade(journal_id, outcome_pnl, notes)


@mcp.tool()
def strategy_manage_positions(execute_stops: bool = False) -> dict:
    """Review every OPEN journaled position against its predefined stop and targets and
    classify each: HOLD / SCALE_OUT_T1 (first target hit — trim + trail stop) /
    TAKE_PROFIT_FINAL (final target hit) / EXIT_STOP (stop hit) — with the live
    unrealized R-multiple. This is the exit discipline that turns the strategy from a
    one-time screen into an ongoing system. Run it whenever you check the account.

    Args:
        execute_stops: If True, AUTO-CLOSE any position whose stop has been hit (paper
            sell + journal close) for capital preservation. Profit-taking is never
            auto-executed — winners stay your discretionary call. Default False
            (recommendations only).
    """
    return _strategy.manage_positions(execute_stops=execute_stops)


@mcp.tool()
def strategy_daily_run(execute_stops: bool = False) -> dict:
    """The repeatable daily routine in one call — this is what makes the strategy 'not a
    one-time wonder': (1) records an equity snapshot for the performance curve, (2) manages
    every open position against its stops/targets, (3) surfaces fresh candidates for new
    capital. Run it once per trading day (or on a schedule — see GUIDE.md).

    Args:
        execute_stops: Passed through to management — auto-close stopped-out positions.
    """
    return _strategy.daily_run(execute_stops=execute_stops)


@mcp.tool()
def broker_status() -> dict:
    """Report whether real (live-money) order execution is available.

    No broker is configured by default (BROKER_PROVIDER=none), so orders route to the
    paper-trading engine. Axis Direct is implemented (BROKER_PROVIDER=axisdirect) but
    UNTESTED against the live API — see axisdirect_login_start below to set it up, and
    check this tool's `session` field to confirm login state before trusting mode='live'.
    """
    return get_broker_status()


@mcp.tool()
def axisdirect_login_start(redirect_url: str) -> dict:
    """Step 1/2 of Axis Direct SSO login — returns a URL to open and log into.

    Requires AXISDIRECT_CLIENT_ID / AXISDIRECT_AUTHORIZATION_KEY set in .env and
    BROKER_PROVIDER=axisdirect. After logging in at the returned login_url, you'll be
    redirected to `redirect_url` with a `ssoId` query parameter — copy that value and
    call axisdirect_login_complete(sso_id=...) to finish.

    Args:
        redirect_url: A URL you control that Axis will redirect back to after login
            (e.g. "https://localhost/callback" — it doesn't need to be a live server,
            you just need to read the `ssoId` param off the resulting browser URL).
    """
    try:
        return _axisdirect.login_start(redirect_url)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def axisdirect_login_complete(sso_id: str) -> dict:
    """Step 2/2 of Axis Direct SSO login — exchanges ssoId for a session.

    Caches sub_account_id/auth_token/refresh_token to
    ~/.tradingview_mcp_data/axisdirect_session.json. The auth_token auto-refreshes
    on subsequent calls using the refresh_token, so you shouldn't need to repeat this
    browser login often — only when the refresh_token itself expires or is revoked.

    Args:
        sso_id: The `ssoId` query-parameter value from the redirect URL after logging
            in at the URL returned by axisdirect_login_start.
    """
    try:
        return _axisdirect.login_complete(sso_id)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def robinhood_login(mfa_code: Optional[str] = None) -> dict:
    """Log into Robinhood (or reuse/refresh a cached session).

    READ BEFORE CALLING: Robinhood has NO paper-trading mode — once logged in,
    place_order(mode="live", ...) against this broker places a REAL order with REAL
    money immediately. This also uses an unofficial SDK (robin_stocks) that goes
    against Robinhood's Terms of Service; accounts have been restricted for this
    kind of use before. Only proceed if you've explicitly decided to accept that.

    Requires ROBINHOOD_USERNAME / ROBINHOOD_PASSWORD set in .env and
    BROKER_PROVIDER=robinhood — there's no OAuth/API-key flow, this needs your real
    account credentials. Runs in an isolated subprocess with a ~25s timeout so a
    device-verification challenge can never hang this server; if your account
    demands an SMS/email code or app push-approval on a new device, this will time
    out with a clear error rather than working — that flow isn't supported here.
    Session is cached by robin_stocks itself at ~/.tokens/robinhood.pickle and reused
    automatically afterwards.

    Args:
        mfa_code: Current 6-digit code from your authenticator app, if your account
            uses TOTP-based 2FA and no session is cached yet. Omit otherwise.
    """
    try:
        return _robinhood.login(mfa_code=mfa_code)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def broker_positions() -> dict:
    """Live open positions from the configured broker. Requires a broker to be
    configured and logged in (see broker_status) — returns a clear error otherwise.
    """
    return get_broker_positions()


@mcp.tool()
def broker_holdings() -> dict:
    """Live portfolio holdings from the configured broker. Requires a broker to be
    configured and logged in (see broker_status) — returns a clear error otherwise.
    """
    return get_broker_holdings()


@mcp.tool()
def place_order(
    symbol: str,
    quantity: float,
    side: str,
    mode: str = "paper",
    exchange: str = "NSE",
    price: Optional[float] = None,
    user_id: str = "default",
) -> dict:
    """Single order-entry point. mode='paper' (default) simulates the fill locally and is
    always available. mode='live' attempts a REAL order through the configured broker
    (BROKER_PROVIDER=axisdirect is implemented but untested against the live API; anything
    else returns a clear "not configured" error rather than silently doing nothing or
    pretending to execute). Never assume mode='live' succeeded without checking the
    response for an 'error' key — check broker_status first if unsure.

    Args:
        symbol: Stock symbol (e.g. "RELIANCE").
        quantity: Number of shares (> 0).
        side: "BUY" or "SELL".
        mode: "paper" (simulated, default) or "live" (real broker, requires setup).
        exchange: Exchange for price lookup in paper mode (default NSE).
        price: Optional fill/limit price override.
        user_id: Which paper account to trade under in mode='paper' (e.g. an email).
            Ignored in mode='live' (a real broker account has only one portfolio).
    """
    return _place_order(symbol, quantity, side, mode=mode, exchange=exchange, price=price, user_id=user_id)


# ── Multi-timeframe analysis ───────────────────────────────────────────────────

@mcp.tool()
def multi_timeframe_analysis(symbol: str, exchange: str = "KUCOIN") -> dict:
    """Multi-timeframe alignment analysis (Weekly → Daily → 4H → 1H → 15m).

    Args:
        symbol: Symbol — crypto: "BTCUSDT"; stocks: "COMI" (EGX), "THYAO" (BIST), "600519" (SSE), "300251" (SZSE), "2330" (TWSE), "3105" (TPEX), "GDX" (AMEX)
        exchange: Exchange — crypto: KUCOIN, BINANCE, MEXC; stocks: EGX, BIST, NASDAQ, NYSE, AMEX, NYSEARCA, PCX, SSE, SZSE, TWSE, TPEX
    """
    exchange = sanitize_exchange(exchange, "KUCOIN")
    full_symbol = normalize_tradingview_symbol(symbol, exchange)
    return run_multi_timeframe_analysis(full_symbol, exchange)


# ── Sentiment & news tools ─────────────────────────────────────────────────────

@mcp.tool()
def market_sentiment(symbol: str, category: str = "all", limit: int = 20) -> dict:
    """Real-time Reddit sentiment analysis for stocks and crypto.

    Args:
        symbol: Asset symbol ("AAPL", "BTC", "ETH", "TSLA")
        category: Subreddit group to search ("crypto", "stocks", "all")
        limit: Number of posts to analyse
    """
    return analyze_sentiment(symbol, category, limit)


@mcp.tool()
def financial_news(symbol: str = None, category: str = "stocks", limit: int = 10) -> dict:
    """Real-time financial news from RSS feeds (Reuters, CoinDesk, etc.)

    Args:
        symbol: Optional symbol filter ("AAPL", "BTC"). None = all news.
        category: Feed category ("crypto", "stocks", "all")
        limit: Max number of news items
    """
    return fetch_news_summary(symbol, category, limit)


@mcp.tool()
def combined_analysis(symbol: str, exchange: str = "NASDAQ", timeframe: str = "1D") -> dict:
    """POWER TOOL: TradingView technical analysis + Reddit sentiment + Financial news.

    Args:
        symbol: Asset symbol ("AAPL", "BTCUSDT", "THYAO", "GDX")
        exchange: Exchange (NASDAQ, NYSE, AMEX, NYSEARCA, PCX, BINANCE, KUCOIN, MEXC, BIST, EGX, TWSE, TPEX)
        timeframe: Analysis timeframe (5m, 15m, 1h, 4h, 1D, 1W)
    """
    tech = coin_analysis(symbol, exchange, timeframe)
    cat = "crypto" if exchange.upper() in ["BINANCE", "KUCOIN", "BYBIT", "MEXC"] else "stocks"
    sentiment = analyze_sentiment(symbol, category=cat)
    news = fetch_news_summary(symbol, category=cat, limit=5)

    tech_momentum = tech.get("market_sentiment", {}).get("momentum", "") if isinstance(tech, dict) else ""
    tech_bullish = tech_momentum == "Bullish"
    sent_bullish = sentiment.get("sentiment_score", 0) > 0.1
    signals_agree = tech_bullish == sent_bullish
    confidence = "HIGH" if signals_agree else "MIXED"
    tech_signal = tech.get("market_sentiment", {}).get("buy_sell_signal", "N/A") if isinstance(tech, dict) else "N/A"

    return {
        "symbol": symbol,
        "exchange": exchange,
        "timeframe": timeframe,
        "technical": tech,
        "sentiment": sentiment,
        "news": {"count": news.get("count", 0), "latest": news.get("items", [])[:3]},
        "confluence": {
            "signals_agree": signals_agree,
            "confidence": confidence,
            "recommendation": (
                f"Technical {tech_signal} "
                f"{'confirmed by' if signals_agree else 'conflicts with'} "
                f"{sentiment.get('sentiment_label', 'Neutral')} Reddit sentiment "
                f"({sentiment.get('posts_analyzed', 0)} posts analyzed)"
            ),
        },
    }


# ── Backtest tools ─────────────────────────────────────────────────────────────

@mcp.tool()
def backtest_strategy(
    symbol: str,
    strategy: str,
    period: str = "1y",
    initial_capital: float = 10000.0,
    commission_pct: float = 0.1,
    slippage_pct: float = 0.05,
    interval: str = "1d",
    include_trade_log: bool = False,
    include_equity_curve: bool = False,
) -> dict:
    """Backtest a trading strategy on historical data with institutional-grade metrics.

    Args:
        symbol: Yahoo Finance symbol (AAPL, BTC-USD, THYAO.IS, ^GSPC)
        strategy: rsi | bollinger | macd | ema_cross | supertrend | donchian
                  | rsi_pullback | keltner_breakout | triple_ema
                  (rsi_pullback and triple_ema need period >= '1y' for SMA200 warmup)
        period: '1mo', '3mo', '6mo', '1y', '2y'
        initial_capital: Starting capital in USD (default $10,000)
        commission_pct: Per-trade commission % (default 0.1%)
        slippage_pct: Per-trade slippage % (default 0.05%)
        interval: '1d' (daily) or '1h' (hourly)
        include_trade_log: Include full per-trade log (default False)
        include_equity_curve: Include equity curve data points (default False)
    """
    return run_backtest(
        symbol, strategy, period, initial_capital,
        commission_pct, slippage_pct, interval,
        include_trade_log, include_equity_curve,
    )


@mcp.tool()
def compare_strategies(
    symbol: str,
    period: str = "1y",
    initial_capital: float = 10000.0,
    interval: str = "1d",
) -> dict:
    """Run all 9 strategies (RSI, Bollinger, MACD, EMA Cross, Supertrend, Donchian, RSI Pullback, Keltner Breakout, Triple EMA) and return a ranked leaderboard.

    Args:
        symbol: Yahoo Finance symbol (AAPL, BTC-USD, SPY…)
        period: '1mo', '3mo', '6mo', '1y', '2y'
                (period >= '1y' recommended so rsi_pullback and triple_ema can
                 complete SMA200 warmup; otherwise they contribute zero trades)
        initial_capital: Starting capital in USD (default $10,000)
        interval: '1d' (daily) or '1h' (hourly)
    """
    return _compare_strategies(symbol, period, initial_capital, interval=interval)


@mcp.tool()
def walk_forward_backtest_strategy(
    symbol: str,
    strategy: str,
    period: str = "2y",
    initial_capital: float = 10000.0,
    commission_pct: float = 0.1,
    slippage_pct: float = 0.05,
    n_splits: int = 3,
    train_ratio: float = 0.7,
    interval: str = "1d",
) -> dict:
    """Walk-forward backtest to detect overfitting — validates strategy on unseen data.

    Args:
        symbol: Yahoo Finance symbol (AAPL, BTC-USD, SPY…)
        strategy: rsi | bollinger | macd | ema_cross | supertrend | donchian
                  | keltner_breakout
                  (rsi_pullback and triple_ema not supported here — SMA200 warmup
                   exceeds typical fold size; use run_backtest with period='2y')
        period: '1mo', '3mo', '6mo', '1y', '2y' (recommend '2y')
        initial_capital: Starting capital per fold in USD (default $10,000)
        commission_pct: Per-trade commission % (default 0.1%)
        slippage_pct: Per-trade slippage % (default 0.05%)
        n_splits: Number of walk-forward folds (default 3, max 10)
        train_ratio: Fraction of each fold used for training (default 0.7)
        interval: '1d' (daily) or '1h' (hourly)
    """
    return walk_forward_backtest(
        symbol, strategy, period, initial_capital,
        commission_pct, slippage_pct, n_splits, train_ratio, interval,
    )


# ── Yahoo Finance tools ────────────────────────────────────────────────────────

@mcp.tool()
def yahoo_price(symbol: str) -> dict:
    """Real-time price quote from Yahoo Finance for any stock, crypto, ETF or index.

    Args:
        symbol: Yahoo Finance symbol — e.g. AAPL, BTC-USD, SPY, ^GSPC, EURUSD=X, THYAO.IS
    """
    return get_price(normalize_yahoo_symbol(symbol))


@mcp.tool()
def market_snapshot() -> dict:
    """Global market overview: major indices, top crypto, FX rates, and key ETFs.
    Powered by Yahoo Finance.
    """
    return get_market_snapshot()


@mcp.tool()
def bitcoin_market_pulse() -> dict:
    """Single-call BTC macro context: price, dominance, total market cap + risk assessment.

    Use this WHENEVER analyzing any cryptocurrency (altcoin or BTC itself) to
    get the broader market frame in one shot. A SOL/ETH/whatever setup looks
    very different when BTC is dumping with rising dominance vs. when alts
    are leading. Calling this once gives Claude the macro context to provide
    Bitcoin-aware commentary alongside the per-coin analysis - without
    chaining 2-3 separate yahoo_price + manual reasoning calls.

    Returns:
      - bitcoin: price, 24h change %, volume, market cap
      - dominance: BTC and ETH market-cap share of total crypto
      - total_market: total crypto mcap + 24h change + active coin count
      - assessment: label (HIGH_RISK / ALT_RISK / ALT_FAVORABLE / OPPORTUNITY_WITH_CAUTION / NEUTRAL) + 1-paragraph reasoning
    """
    return get_bitcoin_market_pulse()


@mcp.tool()
def stock_extended_hours(symbol: str) -> dict:
    """Real-time pre-market and after-hours prices for a US stock symbol.

    Use this when the user asks about a stock outside the regular 9:30am-4pm
    ET session — earnings reactions, overnight news, "what is X doing in
    after-hours?", "how did Y open in pre-market?". Returns the most recent
    valid print from each session window (pre-market, regular, post-market)
    along with computed % changes vs. the previous close and the regular
    close, respectively.

    During the regular session, post_market will be null (no data yet).
    On weekends/holidays, returns whatever's most recent in each window.

    Args:
        symbol: US stock symbol — AAPL, NVDA, TSLA, SPY, ^GSPC, etc.

    Returns:
        - pre_market: {price, as_of_utc, change_vs_previous_close_pct} or null
        - regular: {price, as_of_utc, change_pct} (consolidated tape close)
        - post_market: {price, as_of_utc, change_vs_regular_close_pct} or null
        - previous_close, currency, exchange, market_state for context
    """
    return get_extended_hours_price(symbol)


@mcp.tool()
def stock_options_chain(symbol: str, expiry: Optional[str] = None) -> dict:
    """Full options chain (calls + puts) for a US stock symbol and one expiry.

    Use this when the user asks "what's the options chain for X?", "show me
    AAPL puts expiring next Friday", or wants to inspect bid/ask/IV/volume on
    a specific strike. If no expiry is provided, returns the nearest expiry
    so Claude can quote it back and ask "want a different one?".

    Args:
        symbol: US stock symbol — AAPL, NVDA, TSLA, SPY, etc.
        expiry: Optional ISO date (YYYY-MM-DD). Must match one of the
            `available_expiries` Yahoo returns; otherwise returns an error
            with the list of valid dates.

    Returns:
        - underlying_price, underlying_change_pct
        - requested_expiry, available_expiries (list of YYYY-MM-DD)
        - call_count, put_count
        - calls: list of {strike, last_price, bid, ask, volume,
          open_interest, implied_volatility, in_the_money, expiration}
        - puts: same shape as calls
    """
    return get_options_chain(symbol, expiry)


@mcp.tool()
def stock_options_unusual_activity(
    symbol: str,
    top_n: int = 10,
    min_volume: int = 100,
    expiries: int = 4,
) -> dict:
    """Top strikes by volume / open-interest ratio — institutional positioning signal.

    Use this when the user asks "any unusual options activity on X?", "where
    is the smart money positioned on NVDA before earnings?", or wants a
    V/OI screener for a ticker. A V/OI ratio > 1 means today's volume already
    exceeds standing open interest, which classically flags fresh institutional
    positioning on a specific strike in a specific direction (call vs put).

    Scans the soonest few expirations, filters out illiquid strikes (under
    `min_volume`), and returns the top-N sorted by V/OI descending. Also
    returns aggregate call vs put volume so Claude can comment on the
    overall directional bias.

    Args:
        symbol: US stock symbol — AAPL, NVDA, TSLA, SPY, META, etc.
        top_n: How many strikes to return. Default 10.
        min_volume: Filter floor for today's volume — prevents noise from
            illiquid strikes with high V/OI ratios. Default 100.
        expiries: Number of soonest expirations to scan. Default 4
            (typically covers ~1 month of weeklies + monthlies).

    Returns:
        - underlying_price
        - expiries_scanned (list of YYYY-MM-DD)
        - total_call_volume, total_put_volume, put_call_volume_ratio
        - unusual: list of top-N contracts sorted by V/OI desc, each with
          {strike, side (call|put), expiration, volume, open_interest,
          v_oi_ratio, last_price, implied_volatility, in_the_money,
          strike_vs_spot_pct (moneyness)}
    """
    return get_unusual_options_activity(symbol, top_n, min_volume, expiries)


# ── Futures tools ─────────────────────────────────────────────────────────────

@mcp.tool()
def futures_market_overview(
    category: str = "all",
    exchanges: str = "us",
    limit: int = 30,
    volume_min: int = 0,
) -> dict:
    """Top futures contracts sorted by trading volume.

    Args:
        category:   all | equity_index | energy | metals | agriculture | rates | forex | crypto_futures
        exchanges:  us (CME, COMEX, NYMEX, CBOT) | global (adds ICE, EUREX)
        limit:      max contracts to return (default 30)
        volume_min: minimum volume filter (0 = no filter)

    Returns:
        Dict with total_available count and list of contracts with OHLCV + % change.
    """
    try:
        return get_futures_overview(
            category=category,
            exchanges=exchanges,
            limit=limit,
            volume_min=volume_min,
        )
    except Exception as exc:
        return make_error(ErrorCode.SERVICE_ERROR, f"Futures overview failed: {exc}")


@mcp.tool()
def futures_top_movers(
    direction: str = "gainers",
    exchanges: str = "us",
    limit: int = 20,
    volume_min: int = 10,
) -> dict:
    """Futures contracts with the biggest percentage moves today.

    Args:
        direction:  gainers | losers
        exchanges:  us | global
        limit:      max results
        volume_min: minimum volume filter (default 10, filters illiquid contracts)

    Returns:
        List of futures ranked by % change with OHLCV data.
    """
    direction = direction.lower()
    if direction not in ("gainers", "losers"):
        direction = "gainers"
    try:
        return get_futures_movers(
            direction=direction,
            exchanges=exchanges,
            limit=limit,
            volume_min=volume_min,
        )
    except Exception as exc:
        return make_error(ErrorCode.SERVICE_ERROR, f"Futures movers failed: {exc}")


@mcp.tool()
def futures_category_snapshot(category: str = "energy") -> dict:
    """Quote all major front-month contracts in a specific futures category.

    Args:
        category: equity_index | energy | metals | agriculture | rates | forex | crypto_futures

    Returns:
        OHLCV quotes for the standard watchlist of contracts in that category.
        Example symbols: ES1! NQ1! (equity_index), CL1! NG1! (energy), GC1! SI1! (metals).
    """
    return get_futures_category_snapshot(category)


@mcp.tool()
def futures_watchlist() -> dict:
    """Return the full categorized list of well-known front-month futures symbols.

    Categories: equity_index, energy, metals, agriculture, rates, forex, crypto_futures.
    Use these symbols with futures_category_snapshot or coin_analysis for deeper analysis.
    """
    return get_futures_watchlist()


# ── Resource ───────────────────────────────────────────────────────────────────

@mcp.resource("exchanges://list")
def exchanges_list() -> str:
    """List available exchanges from the coinlist directory."""
    try:
        current_dir = os.path.dirname(__file__)
        coinlist_dir = os.path.join(current_dir, "coinlist")
        if os.path.exists(coinlist_dir):
            exchanges = [
                f[:-4].upper()
                for f in os.listdir(coinlist_dir)
                if f.endswith(".txt")
            ]
            if exchanges:
                return f"Available exchanges: {', '.join(sorted(exchanges))}"
    except Exception:
        pass
    return "Common exchanges: KUCOIN, BINANCE, BYBIT, MEXC, BITGET, OKX, COINBASE, GATEIO, HUOBI, BITFINEX, KRAKEN, BITSTAMP, BIST, EGX, NASDAQ, TWSE, TPEX"


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="TradingView Screener MCP server")
    parser.add_argument(
        "transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        nargs="?",
        help="Transport (default stdio)",
    )
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args()

    if os.environ.get("DEBUG_MCP"):
        import sys
        print(f"[DEBUG_MCP] pkg cwd={os.getcwd()} argv={sys.argv} file={__file__}", file=sys.stderr, flush=True)

    if args.transport == "stdio":
        mcp.run()
    else:
        try:
            mcp.settings.host = args.host
            mcp.settings.port = args.port
        except Exception:
            pass
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
