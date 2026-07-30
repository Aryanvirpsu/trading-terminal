"""Paper trading + order-routing service.

Wraps the (previously dormant, never wired into server.py) SQLite paper
portfolio in core/portfolio.py with live-price lookups, and provides a single
`place_order` entrypoint that routes to paper trading by default and to a
real broker only if one is configured (core/broker/base.py). BROKER_PROVIDER
defaults to "none" (paper-only); "axisdirect" is implemented but untested
against the live API — see core/broker/axisdirect.py.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from tradingview_mcp.core import portfolio
from tradingview_mcp.core.broker.base import get_broker, BrokerNotConfiguredError, get_broker_provider, is_broker_configured
from tradingview_mcp.core.services.yahoo_finance_service import get_price
from tradingview_mcp.core.services.options_service import get_options_chain
from tradingview_mcp.core.utils.validators import normalize_yahoo_symbol

DEFAULT_USER = "default"


def _resolve_price(symbol: str, exchange: str) -> Dict[str, Any]:
    """Look up a live price for `symbol` to fill a paper order at market."""
    exch = (exchange or "").strip().lower()
    sym = symbol.strip().upper()
    if exch in ("nse", "bse") and "." not in sym and not sym.startswith("^"):
        yahoo_sym = f"{sym}.{'NS' if exch == 'nse' else 'BO'}"
    else:
        yahoo_sym = normalize_yahoo_symbol(sym)
    quote = get_price(yahoo_sym)
    if "error" in quote or quote.get("price") is None:
        return {"error": f"Could not fetch a live price for {sym} ({yahoo_sym}): {quote.get('error', 'no price')}"}
    return {"price": quote["price"], "yahoo_symbol": yahoo_sym, "currency": quote.get("currency")}


def paper_trade(
    symbol: str,
    quantity: float,
    side: str,
    exchange: str = "NSE",
    price: Optional[float] = None,
    user_id: str = DEFAULT_USER,
) -> Dict[str, Any]:
    """Execute a simulated BUY/SELL against the local paper portfolio.

    If `price` is omitted, fills at the current live market price.
    """
    if quantity <= 0:
        return {"error": "quantity must be greater than 0"}
    side_u = side.strip().upper()
    if side_u not in ("BUY", "SELL"):
        return {"error": "side must be 'BUY' or 'SELL'"}

    fill_price = price
    price_source = "user-specified"
    if fill_price is None:
        resolved = _resolve_price(symbol, exchange)
        if "error" in resolved:
            return resolved
        fill_price = resolved["price"]
        price_source = f"live market ({resolved['yahoo_symbol']})"

    result = portfolio.execute_trade(user_id, symbol, quantity, fill_price, side_u)
    if "error" not in result:
        result["price_source"] = price_source
        result["mode"] = "PAPER"
    return result


def _resolve_option_contract(symbol: str, option_type: str, strike: float, expiry: str) -> Dict[str, Any]:
    """Find the matching live contract in the chain and return its usable price.
    Prefers last_price; falls back to bid/ask midpoint when last_price is 0/stale
    (common for illiquid strikes)."""
    chain = get_options_chain(symbol, expiry)
    if "error" in chain:
        return {"error": f"Could not fetch option chain for {symbol}: {chain['error']}"}

    contracts = chain.get("calls" if option_type == "CALL" else "puts", [])
    match = next((c for c in contracts if c.get("strike") == strike), None)
    if not match:
        available = sorted({c["strike"] for c in contracts if c.get("strike") is not None})
        return {
            "error": f"No {option_type} contract at strike {strike} for {symbol} expiring {expiry}",
            "available_strikes": available,
            "available_expiries": chain.get("available_expiries"),
        }

    premium = match.get("last_price")
    if not premium:
        bid, ask = match.get("bid"), match.get("ask")
        if bid and ask:
            premium = round((bid + ask) / 2, 2)
    if not premium:
        return {"error": f"No usable price (last/bid/ask all empty) for {symbol} {expiry} {strike} {option_type} — likely no recent trades on this strike"}

    return {"premium": premium, "contract": match, "underlying_price": chain.get("underlying_price")}


def paper_option_trade(
    symbol: str,
    option_type: str,
    strike: float,
    expiry: str,
    quantity: float,
    side: str,
    premium: Optional[float] = None,
    user_id: str = DEFAULT_USER,
) -> Dict[str, Any]:
    """Execute a simulated option BUY (open/add long) or SELL (close long).

    Fills at the live option chain premium unless you pass one explicitly.
    US equity options only (100-share contract multiplier). No naked/short
    writing — SELL requires an existing long position, same as SELL for stocks.
    """
    if quantity <= 0:
        return {"error": "quantity must be greater than 0"}
    side_u = side.strip().upper()
    if side_u not in ("BUY", "SELL"):
        return {"error": "side must be 'BUY' or 'SELL'"}
    option_type_u = option_type.strip().upper()
    if option_type_u not in ("CALL", "PUT"):
        return {"error": "option_type must be 'CALL' or 'PUT'"}

    fill_premium = premium
    price_source = "user-specified"
    if fill_premium is None:
        resolved = _resolve_option_contract(symbol, option_type_u, strike, expiry)
        if "error" in resolved:
            return resolved
        fill_premium = resolved["premium"]
        price_source = f"live market ({resolved['contract'].get('contract_symbol')})"

    result = portfolio.execute_option_trade(
        user_id, symbol, option_type_u, strike, expiry, quantity, fill_premium, side_u
    )
    if "error" not in result:
        result["price_source"] = price_source
        result["mode"] = "PAPER"
    return result


def get_paper_option_portfolio(user_id: str = DEFAULT_USER) -> Dict[str, Any]:
    """Return option positions with live mark-to-market P&L per contract."""
    pf = portfolio.get_option_portfolio(user_id)
    total_value = pf["balance"]
    total_unrealized_pnl = 0.0
    chain_cache: Dict[tuple, dict] = {}

    for pos in pf["positions"]:
        cache_key = (pos["underlying_symbol"], pos["expiry"])
        if cache_key not in chain_cache:
            chain_cache[cache_key] = get_options_chain(pos["underlying_symbol"], pos["expiry"])
        chain = chain_cache[cache_key]

        current = None
        if "error" not in chain:
            contracts = chain.get("calls" if pos["option_type"] == "CALL" else "puts", [])
            match = next((c for c in contracts if c.get("strike") == pos["strike"]), None)
            if match:
                current = match.get("last_price")
                if not current and match.get("bid") and match.get("ask"):
                    current = round((match["bid"] + match["ask"]) / 2, 2)

        if current is None:
            pos["current_premium"] = None
            pos["unrealized_pnl"] = None
            pos["market_value"] = None
            continue

        market_value = current * pos["quantity"] * portfolio.OPTION_CONTRACT_MULTIPLIER
        unrealized = (current - pos["average_premium"]) * pos["quantity"] * portfolio.OPTION_CONTRACT_MULTIPLIER
        pos["current_premium"] = current
        pos["market_value"] = round(market_value, 2)
        pos["unrealized_pnl"] = round(unrealized, 2)
        pos["unrealized_pnl_pct"] = (
            round((current - pos["average_premium"]) / pos["average_premium"] * 100, 2)
            if pos["average_premium"] else None
        )
        total_value += market_value
        total_unrealized_pnl += unrealized

    pf["total_portfolio_value"] = round(total_value, 2)
    pf["total_unrealized_pnl"] = round(total_unrealized_pnl, 2)
    pf["mode"] = "PAPER"
    return pf


def get_paper_option_trade_history(limit: int = 50, user_id: str = DEFAULT_USER) -> Dict[str, Any]:
    return portfolio.get_option_trade_history(user_id, limit)


def get_paper_portfolio(exchange: str = "NSE", user_id: str = DEFAULT_USER) -> Dict[str, Any]:
    """Return the paper portfolio with live mark-to-market P&L per position."""
    pf = portfolio.get_portfolio(user_id)
    total_value = pf["balance"]
    total_unrealized_pnl = 0.0

    for pos in pf["positions"]:
        resolved = _resolve_price(pos["symbol"], exchange)
        if "error" in resolved:
            pos["current_price"] = None
            pos["unrealized_pnl"] = None
            pos["market_value"] = None
            continue
        current = resolved["price"]
        market_value = current * pos["quantity"]
        unrealized = (current - pos["average_price"]) * pos["quantity"]
        pos["current_price"] = current
        pos["market_value"] = round(market_value, 2)
        pos["unrealized_pnl"] = round(unrealized, 2)
        pos["unrealized_pnl_pct"] = (
            round((current - pos["average_price"]) / pos["average_price"] * 100, 2)
            if pos["average_price"] else None
        )
        total_value += market_value
        total_unrealized_pnl += unrealized

    pf["total_portfolio_value"] = round(total_value, 2)
    pf["total_unrealized_pnl"] = round(total_unrealized_pnl, 2)
    pf["mode"] = "PAPER"
    return pf


def get_paper_trade_history(limit: int = 50, user_id: str = DEFAULT_USER) -> Dict[str, Any]:
    return portfolio.get_trade_history(user_id, limit)


def get_broker_status() -> Dict[str, Any]:
    """Report whether real order execution is available."""
    provider = get_broker_provider()
    configured = is_broker_configured()
    status: Dict[str, Any] = {
        "broker_provider": provider,
        "live_trading_enabled": configured,
    }

    if provider == "axisdirect":
        from tradingview_mcp.core.broker.axisdirect import session_status, _creds, _SDK_AVAILABLE
        client_id, auth_key = _creds()
        status["sdk_installed"] = _SDK_AVAILABLE
        status["credentials_set"] = bool(client_id and auth_key)
        status["session"] = session_status()
        status["note"] = (
            "AxisDirectBroker is implemented but UNTESTED against Axis's live API "
            "(built by introspecting the rapidapi-axisdirect SDK, not against real "
            "credentials). Verify small orders carefully before relying on it."
        )

    if provider == "robinhood":
        from tradingview_mcp.core.broker.robinhood import session_status, _creds, _SDK_AVAILABLE
        username, password = _creds()
        status["sdk_installed"] = _SDK_AVAILABLE
        status["credentials_set"] = bool(username and password)
        status["session"] = session_status()
        status["note"] = (
            "RobinhoodBroker is implemented but UNTESTED against Robinhood's live API. "
            "IMPORTANT: Robinhood has NO paper-trading mode — every mode='live' order here "
            "is real money immediately, with no way to simulate first. This SDK is also "
            "unofficial and against Robinhood's Terms of Service (risk of account "
            "restriction). Confirm you intend this before placing anything."
        )

    if not configured:
        status["available_now"] = [
            "paper_trade (simulated BUY/SELL, local SQLite ledger)",
            "paper_portfolio (balance + positions + live unrealized P&L)",
            "paper_trade_history (past simulated fills)",
        ]
        if provider == "none":
            status["reason"] = (
                "No broker configured (BROKER_PROVIDER=none). Set BROKER_PROVIDER=axisdirect "
                "or BROKER_PROVIDER=robinhood plus that broker's credentials in .env to enable "
                "live trading (see .env.example)."
            )
        elif provider == "axisdirect":
            status["reason"] = (
                "BROKER_PROVIDER=axisdirect but not fully set up yet — check sdk_installed, "
                "credentials_set, and session.logged_in above for what's missing."
            )
        elif provider == "robinhood":
            status["reason"] = (
                "BROKER_PROVIDER=robinhood but not fully set up yet — check sdk_installed, "
                "credentials_set, and session.session_cached above for what's missing. Run "
                "the robinhood_login tool to establish a session."
            )
        else:
            status["reason"] = f"BROKER_PROVIDER={provider!r} has no adapter implemented yet."

    return status


def get_broker_positions() -> Dict[str, Any]:
    """Live positions from the configured broker (error if none configured)."""
    try:
        return get_broker().get_positions()
    except BrokerNotConfiguredError as e:
        return {"error": str(e)}


def get_broker_holdings() -> Dict[str, Any]:
    """Live holdings from the configured broker (error if none configured)."""
    try:
        return get_broker().get_holdings()
    except BrokerNotConfiguredError as e:
        return {"error": str(e)}


def place_order(
    symbol: str,
    quantity: float,
    side: str,
    mode: str = "paper",
    exchange: str = "NSE",
    price: Optional[float] = None,
    order_type: str = "MARKET",
    user_id: str = DEFAULT_USER,
) -> Dict[str, Any]:
    """Single order-entry point. mode='paper' (default) simulates the fill
    locally; mode='live' attempts real execution through the configured
    broker, which is unconfigured by default and will return a clear
    "not configured" error rather than silently doing nothing.
    """
    mode_l = mode.strip().lower()
    if mode_l == "live":
        broker = get_broker()
        try:
            result = broker.place_order(symbol, quantity, side, order_type=order_type, price=price, exchange=exchange)
            if isinstance(result, dict) and "error" not in result:
                result["mode"] = "LIVE"
                result["broker"] = get_broker_provider()
            return result
        except BrokerNotConfiguredError as e:
            return {"error": str(e), "mode": "LIVE_REJECTED"}
        except NotImplementedError as e:
            return {"error": str(e), "mode": "LIVE_REJECTED"}
    return paper_trade(symbol, quantity, side, exchange=exchange, price=price, user_id=user_id)
