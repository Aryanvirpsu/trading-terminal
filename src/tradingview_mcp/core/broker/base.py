"""Broker adapter interface — real order execution, unconfigured by default.

Per explicit user decision: data (quotes, options, screeners) uses free
public sources (TradingView scanner + Yahoo Finance + NSE India). Live order
execution is opt-in per broker: BROKER_PROVIDER defaults to "none", which
routes every live-mode call through NotConfiguredBroker (raises a clear,
actionable error instead of silently no-op'ing or crashing).

Two adapters are currently implemented:
  - axisdirect.py (Axis Direct via the community `rapidapi-axisdirect` SDK) —
    set BROKER_PROVIDER=axisdirect plus its credentials in .env; see that
    module's docstring for the login flow. NOT tested against Axis's live
    API (built by introspecting the SDK, not against real credentials).
  - robinhood.py (Robinhood via the community `robin_stocks` SDK) — set
    BROKER_PROVIDER=robinhood plus credentials in .env. READ ITS MODULE
    DOCSTRING FIRST: no paper-trading mode exists on Robinhood at all (every
    live order is real money immediately), and this SDK is unofficial /
    against Robinhood's Terms of Service. NOT tested against Robinhood's
    live API for the same reason as above.

Wiring another broker later means: implement a subclass of BrokerAdapter for
that broker's API in this same package (e.g. broker/upstox.py), then extend
get_broker() below to return it for that provider name. Nothing in
server.py needs to change.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class BrokerNotConfiguredError(RuntimeError):
    """Raised whenever real order execution is attempted without a broker
    wired up. Carries a setup hint so the error is actionable, not cryptic."""


class BrokerAdapter(ABC):
    """Interface a real broker integration (Zerodha/Upstox/Angel One/...)
    would implement. Every method is a live-money operation."""

    name: str = "unconfigured"

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        quantity: float,
        side: str,
        order_type: str = "MARKET",
        price: Optional[float] = None,
        product: str = "MIS",
        exchange: str = "NSE",
    ) -> Dict[str, Any]:
        """Place a live order. side: 'BUY'|'SELL'. order_type: 'MARKET'|'LIMIT'."""
        raise NotImplementedError

    @abstractmethod
    def get_positions(self) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_holdings(self) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_order_status(self, order_id: str) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        raise NotImplementedError


class NotConfiguredBroker(BrokerAdapter):
    """Default broker — every method raises BrokerNotConfiguredError with
    setup instructions instead of placing (or pretending to place) an order."""

    name = "unconfigured"

    def _refuse(self, action: str) -> None:
        provider = os.environ.get("BROKER_PROVIDER", "").strip().lower() or "none"
        raise BrokerNotConfiguredError(
            f"Cannot {action}: no live broker is configured (BROKER_PROVIDER={provider!r}). "
            "This server intentionally ships without real order execution. To enable it: "
            "1) get API credentials from a broker (Zerodha Kite Connect / Upstox / Angel One), "
            "2) set BROKER_PROVIDER and that broker's *_API_KEY / *_API_SECRET / *_ACCESS_TOKEN "
            "in your .env (see .env.example), 3) implement that broker's adapter under "
            "core/broker/<provider>.py. Until then, use the paper-trading tools "
            "(paper_trade, paper_portfolio, paper_trade_history) — same interface, simulated fills."
        )

    def place_order(self, symbol, quantity, side, order_type="MARKET", price=None, product="MIS", exchange="NSE"):
        self._refuse("place a live order")

    def get_positions(self):
        self._refuse("fetch live positions")

    def get_holdings(self):
        self._refuse("fetch live holdings")

    def get_order_status(self, order_id: str):
        self._refuse("fetch live order status")

    def cancel_order(self, order_id: str):
        self._refuse("cancel a live order")


def get_broker_provider() -> str:
    return os.environ.get("BROKER_PROVIDER", "").strip().lower() or "none"


def is_broker_configured() -> bool:
    provider = get_broker_provider()
    if provider == "axisdirect":
        try:
            from tradingview_mcp.core.broker.axisdirect import _creds, _load_session, _SDK_AVAILABLE
        except ImportError:
            return False
        client_id, auth_key = _creds()
        return bool(_SDK_AVAILABLE and client_id and auth_key and _load_session())
    if provider == "robinhood":
        try:
            from tradingview_mcp.core.broker.robinhood import _creds, _SDK_AVAILABLE, _has_valid_session
        except ImportError:
            return False
        username, password = _creds()
        return bool(_SDK_AVAILABLE and username and password and _has_valid_session())
    return False


def get_broker() -> BrokerAdapter:
    """Factory — returns the adapter for BROKER_PROVIDER, or the stub if
    unset/unrecognized. Add a new `elif provider == "..."` branch here (and
    a matching core/broker/<provider>.py) to wire up another broker."""
    provider = get_broker_provider()
    if provider == "axisdirect":
        from tradingview_mcp.core.broker.axisdirect import AxisDirectBroker
        return AxisDirectBroker()
    if provider == "robinhood":
        from tradingview_mcp.core.broker.robinhood import RobinhoodBroker
        return RobinhoodBroker()
    return NotConfiguredBroker()
