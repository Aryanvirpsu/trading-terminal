"""Axis Direct (Axis Securities) broker adapter.

Wraps the community SDK `rapidapi-axisdirect` (PyPI, v0.0.2 — NOT an
official Axis Direct library) behind the BrokerAdapter interface.

STATUS: wired against the SDK's real method signatures, verified by
installing the package and introspecting `AxisAPIClient` directly (not by
trusting docs alone). UNTESTED against Axis's live API — no real
client_id/authorization_key were available while building this, so the
happy path (place_order, holdings, positions) has not been exercised
end-to-end. `resolve_script_id` in particular guesses at the
`search_scrip` response shape since that endpoint's schema isn't publicly
documented; if it guesses wrong, pass `script_id` explicitly to bypass it.

Going live once you have real credentials (~5 minutes, no code changes):
    1. .env:
         BROKER_PROVIDER=axisdirect
         AXISDIRECT_CLIENT_ID=...
         AXISDIRECT_AUTHORIZATION_KEY=...
    2. pip install rapidapi-axisdirect  (or: pip install -e ".[axisdirect]")
    3. Call the `axisdirect_login_start` MCP tool with a redirect_url you
       control, open the returned login_url, log in with your Axis Direct
       credentials.
    4. Copy the `ssoId` query param from the redirect, call
       `axisdirect_login_complete(sso_id=...)` — session is cached to disk
       and auto-refreshed afterwards using the refresh_token.
    5. `place_order(mode="live", ...)` and `broker_status()` now route here.

get_broker() in base.py picks this class up automatically once
BROKER_PROVIDER=axisdirect — nothing else in the codebase needs to change.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from tradingview_mcp.core.broker.base import BrokerAdapter, BrokerNotConfiguredError

_SESSION_PATH = Path(os.path.expanduser("~/.tradingview_mcp_data/axisdirect_session.json"))

try:
    from rapidapi_axisdirect import AxisAPIClient
    from rapidapi_axisdirect.exceptions import TokenException
    _SDK_AVAILABLE = True
except ImportError:
    AxisAPIClient = None  # type: ignore[assignment]
    TokenException = Exception  # type: ignore[assignment,misc]
    _SDK_AVAILABLE = False


def _creds() -> tuple:
    client_id = os.environ.get("AXISDIRECT_CLIENT_ID", "").strip()
    auth_key = os.environ.get("AXISDIRECT_AUTHORIZATION_KEY", "").strip()
    return client_id, auth_key


def _load_session() -> Optional[dict]:
    if not _SESSION_PATH.exists():
        return None
    try:
        return json.loads(_SESSION_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_session(sub_account_id: str, auth_token: str, refresh_token: Optional[str]) -> None:
    _SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SESSION_PATH.write_text(
        json.dumps({
            "sub_account_id": sub_account_id,
            "auth_token": auth_token,
            "refresh_token": refresh_token,
            "saved_at": time.time(),
        }),
        encoding="utf-8",
    )


def _call_sdk(fn, *args, **kwargs) -> Any:
    """Call an SDK method and normalize its failure modes into a clear
    RuntimeError. The SDK itself raises bare KeyError/TypeError (not one of
    its own AxisDAPIException subclasses) when the handshake or a response
    doesn't have the shape it expects — most commonly caused by a wrong/
    expired client_id or authorization_key. Without this, that surfaces as
    a cryptic `KeyError: 'data'` instead of something actionable.
    """
    try:
        return fn(*args, **kwargs)
    except (KeyError, TypeError, IndexError) as e:
        raise RuntimeError(
            f"Axis Direct API call failed with an unexpected response shape ({type(e).__name__}: {e}). "
            "This usually means AXISDIRECT_CLIENT_ID / AXISDIRECT_AUTHORIZATION_KEY are wrong, "
            "expired, or not yet activated for this API — double check them in Axis Direct's "
            "RAPID API portal. It can also mean Axis changed their response schema."
        ) from e


def _new_client():
    if not _SDK_AVAILABLE:
        raise BrokerNotConfiguredError(
            "rapidapi-axisdirect is not installed. Run: pip install rapidapi-axisdirect "
            '(or `pip install -e ".[axisdirect]"` from the repo root).'
        )
    client_id, auth_key = _creds()
    if not client_id or not auth_key:
        raise BrokerNotConfiguredError(
            "AXISDIRECT_CLIENT_ID / AXISDIRECT_AUTHORIZATION_KEY are not set. Get these from "
            "Axis Direct's RAPID API portal, add them to .env, then call axisdirect_login_start "
            "-> log in -> axisdirect_login_complete to establish a session."
        )
    return AxisAPIClient(client_id=client_id, authorization_key=auth_key)


def login_start(redirect_url: str) -> Dict[str, Any]:
    """Step 1 of the SSO flow — returns a URL to open in a browser and log into."""
    client = _new_client()
    resp = _call_sdk(client.initiate_sso, redirect_url)
    login_url = (resp.get("data") or {}).get("redirectURL")
    return {
        "login_url": login_url,
        "instructions": (
            "Open login_url in a browser and log in with your Axis Direct credentials. "
            "You'll be redirected to redirect_url with a `ssoId` query parameter — copy "
            "that value and call axisdirect_login_complete(sso_id=...)."
        ),
        "raw": resp,
    }


def login_complete(sso_id: str) -> Dict[str, Any]:
    """Step 2 of the SSO flow — exchanges sso_id for a session, caches it to disk."""
    client = _new_client()
    data = _call_sdk(client.authenticate_sso, sso_id)
    try:
        sub_account_id = data["metadata"]["accounts"][0]["subAccountId"]
        auth_token = data["authToken"]["token"]
        refresh_token = (data.get("refreshToken") or {}).get("token")
    except (KeyError, IndexError, TypeError) as e:
        return {
            "error": f"Unexpected authenticate_sso response shape ({e}). "
                     "Axis may have changed their schema — check the raw response.",
            "raw": data,
        }
    _save_session(sub_account_id, auth_token, refresh_token)
    return {
        "status": "success",
        "sub_account_id": sub_account_id,
        "has_refresh_token": bool(refresh_token),
        "session_cached_at": str(_SESSION_PATH),
    }


def session_status() -> Dict[str, Any]:
    session = _load_session()
    if not session:
        return {
            "logged_in": False,
            "hint": "Run axisdirect_login_start, log in, then axisdirect_login_complete(sso_id=...).",
        }
    age_s = time.time() - session.get("saved_at", 0)
    return {
        "logged_in": True,
        "sub_account_id": session.get("sub_account_id"),
        "session_age_minutes": round(age_s / 60, 1),
        "has_refresh_token": bool(session.get("refresh_token")),
    }


def _call_with_refresh(method_name: str, *args, **kwargs) -> Any:
    """Call an authenticated client method; on a TokenException, refresh the
    short-lived auth_token once (via the cached refresh_token) and retry —
    avoids forcing a full browser SSO re-login every time auth_token expires.
    """
    session = _load_session()
    if not session:
        raise BrokerNotConfiguredError(
            "No Axis Direct session cached. Run axisdirect_login_start, log in, then "
            "axisdirect_login_complete(sso_id=...) to establish one."
        )
    client = _new_client()
    client.set_session(session["sub_account_id"], session["auth_token"])
    try:
        return _call_sdk(getattr(client, method_name), *args, **kwargs)
    except TokenException:
        refresh_token = session.get("refresh_token")
        if not refresh_token:
            raise
        refreshed = _call_sdk(client.refresh_auth_token, refresh_token)
        new_token = (refreshed.get("data") or {}).get("authToken", {}).get("token")
        if not new_token:
            raise
        _save_session(session["sub_account_id"], new_token, refresh_token)
        client.set_session(session["sub_account_id"], new_token)
        return _call_sdk(getattr(client, method_name), *args, **kwargs)


def resolve_script_id(symbol: str, exchange: str = "NSE") -> Dict[str, Any]:
    """Best-effort symbol -> script_id lookup via search_scrip.

    This endpoint's response shape isn't documented publicly and couldn't be
    verified against live credentials while building this integration. If
    this parsing doesn't match reality, pass `script_id` explicitly to
    place_order instead of relying on this.
    """
    result = _call_with_refresh("search_scrip", symbol)
    candidates = result.get("data") if isinstance(result, dict) else result
    if isinstance(candidates, dict):
        candidates = candidates.get("scrips") or candidates.get("results") or [candidates]
    if not isinstance(candidates, list):
        return {"error": f"Unrecognized search_scrip response shape for {symbol!r}", "raw": result}

    exch_u = exchange.upper()
    for c in candidates:
        if not isinstance(c, dict):
            continue
        c_exch = str(c.get("exchange") or c.get("exch") or "").upper()
        if not c_exch or c_exch == exch_u:
            script_id = c.get("scriptId") or c.get("script_id") or c.get("id")
            if script_id:
                return {"script_id": str(script_id), "matched": c}
    return {"error": f"No script_id match for {symbol} on {exchange}", "candidates": candidates}


class AxisDirectBroker(BrokerAdapter):
    name = "axisdirect"

    def place_order(
        self,
        symbol: str,
        quantity: float,
        side: str,
        order_type: str = "MARKET",
        price: Optional[float] = None,
        product: str = "DELIVERY",
        exchange: str = "NSE",
        segment: str = "EQ",
        script_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if script_id is None:
            resolved = resolve_script_id(symbol, exchange)
            if "error" in resolved:
                return {
                    "error": resolved["error"],
                    "hint": "Pass script_id explicitly (from Axis's Security Master) if "
                            "auto-lookup via search_scrip fails.",
                }
            script_id = resolved["script_id"]

        order_ref_id = f"tvmcp-{int(time.time() * 1000)}"
        return _call_with_refresh(
            "place_order",
            order_ref_id=order_ref_id,
            script_id=script_id,
            exchange=exchange.upper(),
            transaction_type=side.strip().upper(),
            quantity=quantity,
            segment=segment,
            order_type=order_type.strip().upper(),
            order_price=price or 0,
            product_type=product,
            validity_type="DAY",
        )

    def get_positions(self) -> Dict[str, Any]:
        return _call_with_refresh("get_positions", segment="EQ")

    def get_holdings(self) -> Dict[str, Any]:
        return _call_with_refresh("holdings", segment="EQ")

    def get_order_status(self, order_id: str) -> Dict[str, Any]:
        return _call_with_refresh("get_order_history", order_id, segment="EQ")

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        raise NotImplementedError(
            "Axis Direct's cancel_order needs the full original order context "
            "(oms_order_serial_number, open_quantity, price, etc.), not just an order_id. "
            "Call get_order_status(order_id) to retrieve those fields, then use the SDK's "
            "AxisAPIClient.cancel_order directly — not yet wrapped at this interface level."
        )
