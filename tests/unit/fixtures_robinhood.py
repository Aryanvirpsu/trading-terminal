"""Synthetic Robinhood MCP responses matching the REAL tool response SHAPES
observed live on 2026-07-26 (get_accounts / get_portfolio / get_equity_positions /
get_realized_pnl / get_option_positions / get_equity_orders).

Every value here is FAKE and safe to commit — no real balances, account numbers,
tokens, or positions. Shapes only: `structuredContent.data.<list|obj>`, string
money values, `buying_power` nested object with `display_currency`, position field
names, pagination `next_cursor`, and the `isError` error envelope.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def wrap(data: Dict[str, Any]) -> Dict[str, Any]:
    """MCP tools/call envelope: human text + structuredContent.data."""
    return {"content": [{"type": "text", "text": "(synthetic)"}],
            "structuredContent": {"data": data}, "guide": "..."}


# 3 accounts: default cash (no agentic), agentic-enabled cash, roth IRA. Fake numbers.
ACCOUNTS = wrap({"accounts": [
    {"account_number": "FAKE1000001111", "rhs_account_number": "FAKE1000001111",
     "type": "cash", "brokerage_account_type": "individual", "management_type": "self_directed",
     "agentic_allowed": False, "is_default": True, "deactivated": False},
    {"account_number": "FAKE2000002222", "rhs_account_number": "FAKE2000002222",
     "type": "cash", "brokerage_account_type": "individual", "management_type": "self_directed",
     "agentic_allowed": True, "is_default": False, "deactivated": False},
    {"account_number": "FAKE3000003333", "rhs_account_number": "FAKE3000003333",
     "type": "cash", "brokerage_account_type": "ira_roth", "management_type": "self_directed",
     "agentic_allowed": False, "is_default": False, "deactivated": False},
]})

PORTFOLIO_CASH = wrap({"total_value": "5000.00", "equity_value": "2000.00", "cash": "1200.00",
                       "options_value": "0.00", "currency": "USD",
                       "buying_power": {"buying_power": "1200.00", "unleveraged_buying_power": "1200.00",
                                        "display_currency": "USD"}})
PORTFOLIO_AGENTIC = wrap({"total_value": "800.00", "equity_value": "240.00", "cash": "50.00",
                          "currency": "USD",
                          "buying_power": {"buying_power": "50.00", "display_currency": "USD"}})

# Two-page positions for the cash account, with a DUPLICATE across pages (AAPL).
POSITIONS_CASH_P1 = wrap({"positions": [
    {"symbol": "AAPL", "instrument_id": "i-aapl", "quantity": "10",
     "average_buy_price": "180.00", "price": "200.00", "market_value": "2000.00",
     "updated_at": "2026-07-25T15:30:00Z"},
], "next_cursor": "CURSOR2"})
POSITIONS_CASH_P2 = wrap({"positions": [
    {"symbol": "MSFT", "instrument_id": "i-msft", "quantity": "5",
     "average_buy_price": "300.00", "price": "320.00", "market_value": "1600.00"},
    {"symbol": "AAPL", "instrument_id": "i-aapl", "quantity": "10",   # duplicate across pages
     "average_buy_price": "180.00", "price": "200.00", "market_value": "2000.00"},
]})
POSITIONS_EMPTY = wrap({"positions": []})

REALIZED_OK = wrap({"account_number": "x", "window": "year", "display_currency": "USD",
                    "total_returns": "150.00", "total_rate_of_return": "0.03", "data_points": []})
REALIZED_ERR = {"isError": True, "_meta": {"rh_error_category": "invalid_request"},
                "content": [{"type": "text", "text": "un-specified asset class"}]}
ORDERS_EMPTY = wrap({"orders": []})

TOOLS = [{"name": n} for n in (
    "get_accounts", "get_portfolio", "get_equity_positions", "get_option_positions",
    "get_realized_pnl", "get_equity_orders", "place_equity_order", "place_option_order")]


def make_call_tool(empty: bool = False, realized_ok: bool = True,
                   calls: Optional[List[str]] = None):
    """Build a fake `rh.call_tool(name, args)` that dispatches to the fixtures and
    records the tool names it was asked for (so a test can assert NO write tool)."""
    def call_tool(name: str, args: Optional[Dict[str, Any]] = None):
        if calls is not None:
            calls.append(name)
        args = args or {}
        an = args.get("account_number")
        cursor = args.get("cursor")
        if name == "get_accounts":
            return ACCOUNTS
        if name == "get_portfolio":
            return PORTFOLIO_AGENTIC if an == "FAKE2000002222" else PORTFOLIO_CASH
        if name == "get_equity_positions":
            if empty:
                return POSITIONS_EMPTY
            if an == "FAKE1000001111":
                return POSITIONS_CASH_P2 if cursor == "CURSOR2" else POSITIONS_CASH_P1
            return POSITIONS_EMPTY
        if name == "get_option_positions":
            return POSITIONS_EMPTY
        if name == "get_realized_pnl":
            return REALIZED_OK if realized_ok else REALIZED_ERR
        if name == "get_equity_orders":
            return ORDERS_EMPTY
        return wrap({})
    return call_tool
