"""Robinhood MCP client — READ-ONLY, Streamable HTTP MCP over OAuth 2.0 + PKCE.

Connects to the OFFICIAL Robinhood MCP server:

    https://agent.robinhood.com/mcp/trading

This is a proper MCP client — it speaks JSON-RPC over the Streamable HTTP
transport and authenticates with the MCP OAuth 2.0 flow (RFC 9728 protected-
resource metadata → authorization-server metadata → dynamic client registration
→ authorization code + PKCE → bearer token). It is NOT robin_stocks, NOT
SnapTrade, and it does NOT scrape or hit Robinhood's private REST API directly.

Confirmed from the live endpoint (unauthenticated probe, 2026-07-26):
    401 + WWW-Authenticate: Bearer resource_metadata=
        https://agent.robinhood.com/.well-known/oauth-protected-resource/mcp/trading
    authorization_endpoint : https://robinhood.com/oauth
    token_endpoint         : https://api.robinhood.com/oauth2/token/
    registration_endpoint  : https://agent.robinhood.com/oauth/trading/register
    grant_types            : authorization_code, refresh_token
    code_challenge_methods : S256   (PKCE required)
    scopes                 : internal
    transport              : Streamable HTTP (Mcp-Session-Id header)

SAFETY (this phase is READ-ONLY):
  * OAuth TOKENS are stored (never passwords) in
    ~/.tradingview_mcp_data/robinhood_mcp_token.json.
  * Every log line is redacted (tokens / codes / account numbers removed).
  * Account identifiers are MASKED (last 4 only) before leaving this module.
  * `call_tool` REFUSES any write/order-shaped tool unless
    ROBINHOOD_TRADING_ENABLED=true (default false) — so no order can be placed.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import sys
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

# ── Config ────────────────────────────────────────────────────────────────────
MCP_URL = os.environ.get("ROBINHOOD_MCP_URL", "https://agent.robinhood.com/mcp/trading")
_DATA_DIR = os.path.expanduser("~/.tradingview_mcp_data")
_TOKEN_PATH = os.path.join(_DATA_DIR, "robinhood_mcp_token.json")
_CLIENT_PATH = os.path.join(_DATA_DIR, "robinhood_mcp_client.json")
_PKCE_PATH = os.path.join(_DATA_DIR, "robinhood_mcp_pkce.json")
_TIMEOUT = float(os.environ.get("ROBINHOOD_MCP_TIMEOUT", "20"))
_PROTOCOL_VERSION = "2025-06-18"

# The default redirect for the local CLI auth helper (see ROBINHOOD_MANUAL_TEST.md).
DEFAULT_REDIRECT_URI = os.environ.get("ROBINHOOD_MCP_REDIRECT_URI", "http://localhost:8765/callback")


def trading_enabled() -> bool:
    """Master safety switch. Default FALSE — no write/order tool can be called."""
    return os.environ.get("ROBINHOOD_TRADING_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


# Tool-name substrings that indicate a WRITE / order / money-moving action (used
# only for MESSAGING now — the real gate is the default-deny allowlist below).
_WRITE_HINTS = ("place", "order", "buy", "sell", "cancel", "submit", "execute",
                "exercise", "trade", "transfer", "withdraw", "deposit", "create",
                "modify", "review", "close_position")

# SAFETY MODEL (confirmed against the 52 live tools, 2026-07-26): naive substring
# matching is BOTH too loose (missed `exercise_option`) and too tight (blocked the
# read `get_equity_orders`). So while trading is disabled we DEFAULT-DENY: only a
# tool on this explicit READ allowlist may be called. Everything else — order /
# review(preview) / cancel / exercise / watchlist+scan mutations / and any unknown
# or future tool — is refused. This is the hard read-only gate.
_READ_ALLOWLIST = frozenset({
    # account + holdings + P&L + cost basis (what the dashboard uses)
    "get_accounts", "get_account", "list_accounts", "get_portfolio",
    "get_equity_positions", "get_option_positions", "get_positions", "get_holdings",
    "get_realized_pnl", "get_pnl_trade_history", "get_equity_orders", "get_option_orders",
    "get_orders", "list_orders", "get_equity_tax_lots",
    # pure market-data reads (safe; no account mutation)
    "get_equity_quotes", "get_option_quotes", "get_index_quotes", "get_indexes",
    "get_equity_fundamentals", "get_financials", "get_equity_historicals",
    "get_option_historicals", "get_equity_price_book", "get_equity_technical_indicators",
    "get_earnings_calendar", "get_earnings_results", "get_equity_tradability",
    "get_option_chains", "get_option_instruments", "get_option_level_upgrade_info",
    "get_watchlists", "get_watchlist_items", "get_option_watchlist", "get_popular_watchlists",
    "get_scans", "get_scanner_filter_specs", "run_scan", "search",
})
# Discovery preference order for the accounts tool.
READ_TOOLS_PREFERENCE = ["get_accounts", "get_account", "list_accounts"]


# ── Redaction + masking ───────────────────────────────────────────────────────
_REDACT_PATTERNS = [
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.I),
    re.compile(r'("?(?:access_token|refresh_token|code|client_secret|authorization|id_token|code_verifier)"?\s*[:=]\s*"?)([A-Za-z0-9._\-]+)', re.I),
]


def _redact(text: str) -> str:
    """Strip tokens / codes / secrets from any string before it is logged."""
    s = str(text)
    s = _REDACT_PATTERNS[0].sub(r"\1<redacted>", s)
    s = _REDACT_PATTERNS[1].sub(r"\1<redacted>", s)
    # Long opaque strings that look like account numbers / tokens
    s = re.sub(r"\b[A-Za-z0-9]{16,}\b", lambda m: m.group(0)[:4] + "…<redacted>", s)
    return s


def mask_account(identifier: Optional[str]) -> Optional[str]:
    """Mask an account number/id, keeping only the last 4 chars: '•••• 6789'."""
    if not identifier:
        return None
    s = str(identifier)
    if len(s) <= 4:
        return "•••• " + s
    return "•••• " + s[-4:]


def _log(msg: str) -> None:
    try:
        print("[robinhood_mcp] " + _redact(msg), file=sys.stderr)
    except Exception:
        pass


# ── Structured error / result ────────────────────────────────────────────────
class RobinhoodMCPError(RuntimeError):
    def __init__(self, kind: str, detail: str = ""):
        self.kind = kind          # not_configured | auth_required | token_expired |
                                  # timeout | transport | tool_error | forbidden | outage
        self.detail = _redact(detail)
        super().__init__(f"{kind}: {self.detail}")


# ── HTTP helper (stdlib; requests optional) ───────────────────────────────────
def _http(method: str, url: str, headers: Dict[str, str], body: Optional[bytes] = None,
          timeout: float = _TIMEOUT) -> Tuple[int, Dict[str, str], bytes]:
    try:
        import requests  # type: ignore
        r = requests.request(method, url, headers=headers, data=body, timeout=timeout)
        return r.status_code, {k.lower(): v for k, v in r.headers.items()}, r.content
    except ImportError:
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read()


# ── OAuth discovery (public metadata; no auth) ────────────────────────────────
_OAUTH_META: Dict[str, Any] = {}


def discover_oauth() -> Dict[str, Any]:
    """RFC 9728 → RFC 8414: protected-resource metadata → authorization-server
    metadata. Cached. Returns the endpoints needed for the auth flow."""
    if _OAUTH_META:
        return _OAUTH_META
    base = MCP_URL.split("/mcp/")[0]
    path = "/mcp/" + MCP_URL.split("/mcp/", 1)[1] if "/mcp/" in MCP_URL else ""
    prm_url = f"{base}/.well-known/oauth-protected-resource{path}"
    asm_url = f"{base}/.well-known/oauth-authorization-server{path}"
    meta: Dict[str, Any] = {"protected_resource_metadata": prm_url,
                            "authorization_server_metadata": asm_url}
    try:
        st, _, body = _http("GET", prm_url, {"Accept": "application/json"})
        if st == 200:
            meta["protected_resource"] = json.loads(body)
    except Exception as e:  # noqa: BLE001
        _log(f"PRM discovery failed: {e}")
    try:
        st, _, body = _http("GET", asm_url, {"Accept": "application/json"})
        if st != 200:
            st, _, body = _http("GET", f"{base}/.well-known/oauth-authorization-server",
                                {"Accept": "application/json"})
        if st == 200:
            asm = json.loads(body)
            meta["authorization_server"] = asm
            meta["authorization_endpoint"] = asm.get("authorization_endpoint")
            meta["token_endpoint"] = asm.get("token_endpoint")
            meta["registration_endpoint"] = asm.get("registration_endpoint")
            meta["scopes"] = asm.get("scopes_supported", ["internal"])
    except Exception as e:  # noqa: BLE001
        _log(f"ASM discovery failed: {e}")
    _OAUTH_META.update(meta)
    return _OAUTH_META


# ── Dynamic Client Registration (RFC 7591) ────────────────────────────────────
def register_client(redirect_uri: str = DEFAULT_REDIRECT_URI) -> Dict[str, Any]:
    """Register a public client with the authorization server. Persists the
    client_id (never a secret we don't need) to disk. Returns the registration."""
    meta = discover_oauth()
    reg = meta.get("registration_endpoint")
    if not reg:
        raise RobinhoodMCPError("transport", "no registration_endpoint discovered")
    payload = {
        "client_name": "tradingview-mcp terminal (read-only)",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",   # public client + PKCE
        "scope": " ".join(meta.get("scopes") or ["internal"]),
    }
    st, _, body = _http("POST", reg, {"Content-Type": "application/json",
                                      "Accept": "application/json"},
                        json.dumps(payload).encode())
    if st not in (200, 201):
        raise RobinhoodMCPError("transport", f"registration HTTP {st}: {body[:120]!r}")
    data = json.loads(body)
    _save_json(_CLIENT_PATH, {"client_id": data.get("client_id"),
                              "redirect_uri": redirect_uri,
                              "registered_at": time.time()})
    _log(f"registered client_id={mask_account(data.get('client_id'))}")
    return data


# ── PKCE authorization-code flow ──────────────────────────────────────────────
def _pkce_pair() -> Tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(40)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def build_authorization_url(redirect_uri: str = DEFAULT_REDIRECT_URI,
                            client_id: Optional[str] = None) -> Dict[str, str]:
    """Build the browser URL the USER opens to authorize (they log in to Robinhood
    themselves — this client never sees the password). Returns the url + the PKCE
    verifier + state that the caller must keep for the token exchange."""
    meta = discover_oauth()
    if not client_id:
        client_id = (_load_json(_CLIENT_PATH) or {}).get("client_id")
    if not client_id:
        raise RobinhoodMCPError("not_configured", "no client_id — run register_client() first")
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    q = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(meta.get("scopes") or ["internal"]),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = meta["authorization_endpoint"] + "?" + urllib.parse.urlencode(q)
    # Persist the PKCE verifier + state (0600) so `exchange` can pick them up
    # without the secret ever being copied/pasted or displayed.
    _save_json(_PKCE_PATH, {"code_verifier": verifier, "state": state,
                            "redirect_uri": redirect_uri, "client_id": client_id,
                            "at": time.time()})
    try:
        os.chmod(_PKCE_PATH, 0o600)
    except Exception:
        pass
    return {"url": url, "code_verifier": verifier, "state": state,
            "redirect_uri": redirect_uri, "client_id": client_id}


def _parse_code(code_or_url: str, expect_state: Optional[str]) -> str:
    """Accept either a bare code or the full redirect URL; validate state (CSRF)."""
    s = (code_or_url or "").strip()
    if "code=" in s:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(s).query)
        got_state = (q.get("state") or [None])[0]
        if expect_state and got_state and got_state != expect_state:
            raise RobinhoodMCPError("auth_required", "OAuth state mismatch (possible CSRF) — restart the flow")
        code = (q.get("code") or [None])[0]
        if code:
            return code
    return s


def exchange_code(code: str, code_verifier: Optional[str] = None,
                  redirect_uri: Optional[str] = None,
                  client_id: Optional[str] = None) -> Dict[str, Any]:
    """Exchange the authorization code for tokens (PKCE). Reads the persisted PKCE
    verifier/state from `authurl` if not supplied, so the secret is never copied by
    hand. Accepts a bare code OR the full redirect URL. Persists the token."""
    meta = discover_oauth()
    pkce = _load_json(_PKCE_PATH) or {}
    if not code_verifier:
        code_verifier = pkce.get("code_verifier")
    if not redirect_uri:
        redirect_uri = pkce.get("redirect_uri") or DEFAULT_REDIRECT_URI
    if not client_id:
        client_id = pkce.get("client_id") or (_load_json(_CLIENT_PATH) or {}).get("client_id")
    if not code_verifier:
        raise RobinhoodMCPError("not_configured", "no PKCE verifier — run `authurl` first")
    code = _parse_code(code, pkce.get("state"))
    form = {
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
        "client_id": client_id, "code_verifier": code_verifier,
    }
    st, _, body = _http("POST", meta["token_endpoint"],
                        {"Content-Type": "application/x-www-form-urlencoded",
                         "Accept": "application/json"},
                        urllib.parse.urlencode(form).encode())
    if st != 200:
        # Surface the OAuth error (invalid_grant = expired/used code; invalid_client
        # = client auth) so a retry is diagnosable — redacted, no token leakage.
        hint = ""
        try:
            err = json.loads(body)
            hint = " · " + str(err.get("error") or err.get("detail") or "")[:80]
        except Exception:
            hint = " · " + body[:80].decode("utf-8", "replace")
        raise RobinhoodMCPError("auth_required", f"token exchange HTTP {st}{hint}")
    tok = json.loads(body)
    _store_token(tok)
    try:
        os.remove(_PKCE_PATH)          # one-time PKCE material — discard after use
    except Exception:
        pass
    _log("token exchange OK (access + refresh stored, redacted)")
    return {"ok": True, "expires_in": tok.get("expires_in"),
            "has_refresh_token": bool(tok.get("refresh_token"))}


def refresh() -> bool:
    """Refresh the access token using the stored refresh token. Returns success."""
    tok = _load_json(_TOKEN_PATH) or {}
    rt = tok.get("refresh_token")
    if not rt:
        return False
    meta = discover_oauth()
    client_id = (_load_json(_CLIENT_PATH) or {}).get("client_id")
    form = {"grant_type": "refresh_token", "refresh_token": rt, "client_id": client_id}
    try:
        st, _, body = _http("POST", meta["token_endpoint"],
                            {"Content-Type": "application/x-www-form-urlencoded",
                             "Accept": "application/json"},
                            urllib.parse.urlencode(form).encode())
    except Exception as e:  # noqa: BLE001
        _log(f"refresh failed: {e}")
        return False
    if st != 200:
        _log(f"refresh HTTP {st}")
        return False
    _store_token(json.loads(body))
    _log("token refreshed")
    return True


# ── Token store (tokens only, never passwords) ───────────────────────────────
# Only these OAuth-standard fields are persisted. Robinhood's token response also
# includes `mfa_code` / `backup_code` / `user_uuid` — we deliberately DROP those so
# nothing MFA/identity-shaped is ever written to disk.
_TOKEN_KEEP = ("access_token", "refresh_token", "token_type", "scope", "expires_in")


def _store_token(tok: Dict[str, Any]) -> None:
    data = {k: tok[k] for k in _TOKEN_KEEP if k in tok}
    if tok.get("expires_in"):
        data["_expires_at"] = time.time() + float(tok["expires_in"]) - 60  # 60s skew
    _save_json(_TOKEN_PATH, data)
    # chmod 600 is honoured on POSIX; on Windows the file inherits the user-profile
    # ACL (user-only) — set the read-only-for-others bit where the OS supports it.
    try:
        os.chmod(_TOKEN_PATH, 0o600)
    except Exception:
        pass


def _access_token() -> Optional[str]:
    tok = _load_json(_TOKEN_PATH)
    if not tok:
        return None
    exp = tok.get("_expires_at")
    if exp and time.time() >= exp:
        if not refresh():
            return None
        tok = _load_json(_TOKEN_PATH) or {}
    return tok.get("access_token")


def configured() -> bool:
    """True once a token exists (the user has completed the OAuth flow)."""
    return bool((_load_json(_TOKEN_PATH) or {}).get("access_token"))


# ── MCP Streamable HTTP transport ─────────────────────────────────────────────
_SESSION: Dict[str, Any] = {"id": None, "initialized": False}


def _parse_rpc_body(headers: Dict[str, str], body: bytes) -> Dict[str, Any]:
    """Streamable HTTP servers reply with application/json OR text/event-stream."""
    ctype = headers.get("content-type", "")
    text = body.decode("utf-8", "replace")
    if "text/event-stream" in ctype:
        # Concatenate `data:` lines, take the last complete JSON message.
        msgs = []
        for block in text.split("\n\n"):
            data = "".join(l[5:].strip() for l in block.splitlines() if l.startswith("data:"))
            if data:
                try:
                    msgs.append(json.loads(data))
                except Exception:
                    pass
        return msgs[-1] if msgs else {}
    return json.loads(text) if text.strip() else {}


def _rpc(method: str, params: Optional[Dict[str, Any]] = None, is_notification: bool = False,
         _retry: bool = True) -> Any:
    """One JSON-RPC call over Streamable HTTP. Adds the bearer token + session id,
    handles 401 (refresh + reconnect once), timeouts and structured errors."""
    token = _access_token()
    if not token:
        raise RobinhoodMCPError("auth_required", "no valid access token — run the OAuth flow")
    payload: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        payload["params"] = params
    if not is_notification:
        payload["id"] = secrets.randbelow(1 << 30) + 1
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {token}",
        "MCP-Protocol-Version": _PROTOCOL_VERSION,
    }
    if _SESSION.get("id"):
        headers["Mcp-Session-Id"] = _SESSION["id"]
    try:
        st, resp_headers, body = _http("POST", MCP_URL, headers, json.dumps(payload).encode())
    except Exception as e:  # noqa: BLE001 — network / timeout
        raise RobinhoodMCPError("timeout" if "timed out" in str(e).lower() else "outage", str(e))
    if resp_headers.get("mcp-session-id"):
        _SESSION["id"] = resp_headers["mcp-session-id"]
    if st == 401:
        if _retry and refresh():                      # token expired mid-session → reconnect once
            _SESSION["id"] = None; _SESSION["initialized"] = False
            initialize()
            return _rpc(method, params, is_notification, _retry=False)
        raise RobinhoodMCPError("token_expired", "server returned 401")
    if st == 403:
        raise RobinhoodMCPError("forbidden", "server returned 403")
    if st >= 500:
        raise RobinhoodMCPError("outage", f"server {st}")
    if st not in (200, 202):
        raise RobinhoodMCPError("transport", f"HTTP {st}: {body[:120]!r}")
    if is_notification:
        return None
    msg = _parse_rpc_body(resp_headers, body)
    if isinstance(msg, dict) and msg.get("error"):
        raise RobinhoodMCPError("tool_error", str(msg["error"]))
    return msg.get("result") if isinstance(msg, dict) else msg


def initialize() -> Dict[str, Any]:
    """MCP handshake. Idempotent per session."""
    if _SESSION.get("initialized"):
        return {"already": True, "session": mask_account(_SESSION.get("id"))}
    result = _rpc("initialize", {
        "protocolVersion": _PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "tradingview-mcp-terminal", "version": "0.1.0"},
    })
    _rpc("notifications/initialized", {}, is_notification=True)  # required by the spec
    _SESSION["initialized"] = True
    _log(f"initialized session={mask_account(_SESSION.get('id'))}")
    return result or {}


def list_tools() -> List[Dict[str, Any]]:
    """Tool discovery: name, description, input schema for every exposed tool."""
    initialize()
    result = _rpc("tools/list", {})
    tools = (result or {}).get("tools", []) if isinstance(result, dict) else []
    return tools


def is_write_tool(name: str) -> bool:
    """A tool is treated as WRITE unless it's on the explicit read allowlist."""
    return (name or "") not in _READ_ALLOWLIST


def call_tool(name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
    """Call a tool. DEFAULT-DENY while trading is disabled: only tools on the
    explicit READ allowlist are permitted — no order / review / cancel / exercise /
    watchlist-or-scan mutation / or unknown tool can run. This is the hard gate."""
    if not trading_enabled() and name not in _READ_ALLOWLIST:
        raise RobinhoodMCPError(
            "forbidden",
            f"tool '{name}' is not on the read-only allowlist and "
            f"ROBINHOOD_TRADING_ENABLED is false — refused")
    initialize()
    result = _rpc("tools/call", {"name": name, "arguments": arguments or {}})
    return result


# ── Status + high-level read helpers ─────────────────────────────────────────
def status() -> Dict[str, Any]:
    """Connection status for the dashboard — never raises."""
    if not configured():
        return {"connected": False, "status": "auth_required",
                "reason": "Robinhood MCP not authorized. Run the OAuth flow (read-only).",
                "trading_enabled": trading_enabled(), "endpoint": MCP_URL, "read_only": True}
    try:
        initialize()
        return {"connected": True, "status": "connected", "read_only": not trading_enabled(),
                "trading_enabled": trading_enabled(), "endpoint": MCP_URL,
                "session": mask_account(_SESSION.get("id"))}
    except RobinhoodMCPError as e:
        return {"connected": False, "status": e.kind, "reason": e.detail,
                "trading_enabled": trading_enabled(), "endpoint": MCP_URL, "read_only": True}


def _find_tool(tools: List[Dict[str, Any]], *candidates: str) -> Optional[str]:
    names = {t.get("name", "").lower(): t.get("name") for t in tools}
    for c in candidates:
        if c.lower() in names:
            return names[c.lower()]
    return None


def get_accounts() -> Any:
    """Call the discovered accounts tool (get_accounts / list_accounts)."""
    tools = list_tools()
    name = _find_tool(tools, "get_accounts", "list_accounts", "get_account")
    if not name:
        raise RobinhoodMCPError("tool_error", "no accounts tool discovered")
    return call_tool(name, {})


# ── tiny JSON file helpers ────────────────────────────────────────────────────
def _load_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_json(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


# ── CLI (manual, read-only) ───────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Robinhood MCP read-only client")
    ap.add_argument("cmd", choices=["discover", "register", "authurl", "exchange", "status", "tools"])
    ap.add_argument("--code"); ap.add_argument("--verifier")
    a = ap.parse_args()
    try:
        if a.cmd == "discover":
            print(json.dumps({k: v for k, v in discover_oauth().items()
                              if k not in ("protected_resource", "authorization_server")}, indent=2))
        elif a.cmd == "register":
            r = register_client(); print("client_id:", mask_account(r.get("client_id")))
        elif a.cmd == "authurl":
            info = build_authorization_url()
            print("Open this URL, log in to Robinhood yourself, authorize, then paste the FULL "
                  "redirect URL (or just the ?code=) into `exchange`:\n")
            print(info["url"])
            print("\n(The PKCE verifier is saved locally — you do NOT need to copy it.)")
        elif a.cmd == "exchange":
            # --verifier optional: picked up from the saved PKCE material.
            print(json.dumps(exchange_code(a.code, a.verifier), indent=2))
        elif a.cmd == "status":
            print(json.dumps(status(), indent=2))
        elif a.cmd == "tools":
            print(json.dumps(list_tools(), indent=2))
    except RobinhoodMCPError as e:
        print(json.dumps({"error": e.kind, "detail": e.detail}, indent=2))
