"""Isolated worker process for robin_stocks calls.

Why this file exists as a standalone script instead of being called
in-process from robinhood.py: robin_stocks.login() can, deep inside its
device-verification ("Sherriff ID") flow, either call Python's blocking
input() directly (for SMS/email codes) or poll in a loop for up to two
minutes waiting for an app push approval. This MCP server's own transport
is stdio — running that in-process risks the input() call stealing bytes
from (or deadlocking against) the JSON-RPC stream the server is using to
talk to its client, which could hang or corrupt the live connection.

Running every robin_stocks call in a fresh subprocess with stdin closed
(DEVNULL) makes input() fail fast with EOFError instead of blocking, and
the parent process enforces a hard wall-clock timeout on top of that as a
second layer of defense. Worst case, a call here fails clearly; it can
never hang the server that spawned it.

Talks to its parent via environment variables in, one JSON line on stdout.
Never reads stdin. Never touches credentials via argv (visible to other
processes on the same machine); env vars are a materially smaller
exposure, though still readable by anything with access to this process's
environment while it's alive.
"""
from __future__ import annotations

import json
import os
import sys


def main() -> None:
    request = json.loads(os.environ.get("RH_REQUEST_JSON", "{}"))
    action = request.get("action")
    args = request.get("args", {})

    username = os.environ.get("RH_USERNAME")
    password = os.environ.get("RH_PASSWORD")
    mfa_code = os.environ.get("RH_MFA") or None

    try:
        import robin_stocks.robinhood as rh
    except ImportError:
        print(json.dumps({"ok": False, "error": "robin_stocks is not installed"}))
        return

    try:
        rh.login(username=username, password=password, mfa_code=mfa_code, store_session=True)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"login failed: {type(e).__name__}: {e}"}))
        return

    try:
        if action == "login":
            result = {"logged_in": True}

        elif action == "place_order":
            side = str(args["side"]).lower()
            order_type = str(args.get("order_type", "market")).lower()
            symbol = args["symbol"]
            quantity = args["quantity"]
            price = args.get("price")

            if order_type == "limit" and price:
                fn = rh.order_buy_limit if side == "buy" else rh.order_sell_limit
                result = fn(symbol, quantity, price)
            else:
                fn = rh.order_buy_market if side == "buy" else rh.order_sell_market
                result = fn(symbol, quantity)

        elif action == "positions":
            result = rh.get_open_stock_positions()

        elif action == "holdings":
            result = rh.build_holdings()

        elif action == "order_status":
            result = rh.get_stock_order_info(args["order_id"])

        elif action == "cancel_order":
            result = rh.cancel_stock_order(args["order_id"])

        else:
            print(json.dumps({"ok": False, "error": f"unknown action: {action!r}"}))
            return

        print(json.dumps({"ok": True, "data": result}, default=str))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}))


if __name__ == "__main__":
    sys.exit(main())
