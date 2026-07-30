"""Robinhood MCP read-only integration tests (Prompt 4A).

All automated tests use MOCKS — no real Robinhood auth, no network, and NEVER an
order. Covers: auth success/failure, tool discovery, multiple accounts, cash read
access, agentic detection, empty portfolio, MCP timeout, session expiry + reconnect,
cached fallback, account masking, log redaction, and outage-not-blocking.

Run:  pytest tests/unit/test_robinhood_mcp.py -q
"""
from __future__ import annotations

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in ("lab",):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import robinhood_mcp as rh  # noqa: E402
import robinhood_view as rv  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    # Never touch the real token file, never enable trading, reset session + caches.
    monkeypatch.setattr(rh, "_SESSION", {"id": None, "initialized": False})
    monkeypatch.delenv("ROBINHOOD_TRADING_ENABLED", raising=False)
    rv._CACHE.clear()
    yield
    rv._CACHE.clear()


# ── Safety: read-only guard + trading switch ─────────────────────────────────

@pytest.mark.parametrize("tool", ["place_order", "submit_buy_order", "cancel_order",
                                  "execute_trade", "withdraw_funds"])
def test_order_tools_blocked_before_network(tool, monkeypatch):
    # If the guard ever let one through it would call _http — make that explode.
    monkeypatch.setattr(rh, "_http", lambda *a, **k: pytest.fail("network hit for a blocked tool"))
    with pytest.raises(rh.RobinhoodMCPError) as ei:
        rh.call_tool(tool, {})
    assert ei.value.kind == "forbidden"


def test_trading_disabled_by_default():
    assert rh.trading_enabled() is False


def test_read_tool_not_blocked_by_guard(monkeypatch):
    # A read tool passes the guard (then fails later only for lack of a token).
    monkeypatch.setattr(rh, "_access_token", lambda: None)
    with pytest.raises(rh.RobinhoodMCPError) as ei:
        rh.call_tool("get_accounts", {})
    assert ei.value.kind == "auth_required"     # NOT 'forbidden'


# ── Masking + redaction ──────────────────────────────────────────────────────

def test_account_masking():
    assert rh.mask_account("123456789") == "•••• 6789"
    assert rh.mask_account("42").endswith("42")
    assert rh.mask_account(None) is None


def test_log_redaction_strips_secrets():
    s = rh._redact('Authorization: Bearer abc123def456ghi789 code=SECRETCODE99887766 ok')
    assert "abc123def456ghi789" not in s
    assert "SECRETCODE99887766" not in s
    assert "<redacted>" in s


def test_redaction_masks_long_account_like_tokens():
    s = rh._redact("account 8XA92JZ7QW11KK22DDEE stays hidden")
    assert "8XA92JZ7QW11KK22DDEE" not in s


# ── Auth / status ────────────────────────────────────────────────────────────

def test_status_auth_required_without_token(monkeypatch):
    monkeypatch.setattr(rh, "_load_json", lambda p: None)
    st = rh.status()
    assert st["connected"] is False and st["status"] == "auth_required"
    assert st["read_only"] is True


def test_configured_true_with_token(monkeypatch):
    monkeypatch.setattr(rh, "_load_json", lambda p: {"access_token": "tok", "_expires_at": 9e12})
    assert rh.configured() is True


def test_pkce_challenge_is_s256():
    import base64, hashlib
    v, c = rh._pkce_pair()
    expect = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    assert c == expect


# ── Transport: timeout, outage, 401 session-expiry + reconnect ───────────────

def _token(monkeypatch):
    monkeypatch.setattr(rh, "_load_json", lambda p: {"access_token": "tok", "_expires_at": 9e12}
                        if p == rh._TOKEN_PATH else {"client_id": "cid"})


def test_rpc_timeout(monkeypatch):
    _token(monkeypatch)
    def boom(*a, **k):
        raise TimeoutError("read timed out")
    monkeypatch.setattr(rh, "_http", boom)
    with pytest.raises(rh.RobinhoodMCPError) as ei:
        rh._rpc("tools/list", {})
    assert ei.value.kind == "timeout"


def test_rpc_server_outage(monkeypatch):
    _token(monkeypatch)
    monkeypatch.setattr(rh, "_http", lambda *a, **k: (503, {}, b"down"))
    with pytest.raises(rh.RobinhoodMCPError) as ei:
        rh._rpc("tools/list", {})
    assert ei.value.kind == "outage"


def test_rpc_401_refreshes_and_reconnects(monkeypatch):
    _token(monkeypatch)
    calls = {"n": 0}

    def http(method, url, headers, body=None, timeout=20.0):
        calls["n"] += 1
        if calls["n"] == 1:
            return 401, {}, b"expired"                 # first call: token expired
        # after refresh + re-initialize, succeed
        return 200, {"content-type": "application/json"}, b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}'
    monkeypatch.setattr(rh, "_http", http)
    monkeypatch.setattr(rh, "refresh", lambda: True)
    monkeypatch.setattr(rh, "initialize", lambda: {"reconnected": True})
    out = rh._rpc("tools/list", {})
    assert out == {"ok": True}
    assert calls["n"] >= 2                              # it retried after refresh


def test_list_tools_discovery(monkeypatch):
    _token(monkeypatch)
    monkeypatch.setattr(rh, "initialize", lambda: {})
    monkeypatch.setattr(rh, "_rpc", lambda m, p=None, **k: {
        "tools": [{"name": "get_accounts", "description": "List accounts",
                   "inputSchema": {"type": "object"}},
                  {"name": "get_portfolio", "description": "Portfolio"}]})
    tools = rh.list_tools()
    assert {t["name"] for t in tools} == {"get_accounts", "get_portfolio"}


# ── View: multiple accounts, separation, agentic detection, empty, outage ────

import fixtures_robinhood as fx  # noqa: E402  (real-shape synthetic responses)


def _connected_view(monkeypatch, empty=False, realized_ok=True, tools=None, calls=None):
    """Wire the view to the REAL-SHAPE fixtures via a single call_tool dispatcher —
    get_accounts + per-account get_portfolio/get_equity_positions/get_realized_pnl."""
    monkeypatch.setattr(rh, "status", lambda: {"connected": True, "status": "connected", "read_only": True})
    monkeypatch.setattr(rh, "list_tools", lambda: tools if tools is not None else fx.TOOLS)
    ct = fx.make_call_tool(empty=empty, realized_ok=realized_ok, calls=calls)
    monkeypatch.setattr(rh, "call_tool", ct)
    monkeypatch.setattr(rh, "get_accounts", lambda: ct("get_accounts", {}))


def test_view_multiple_accounts_separated(monkeypatch):
    _connected_view(monkeypatch)
    v = rv.accounts(force=True)
    assert v["cash"]["connected"] and v["agentic"]["connected"]
    # balances kept SEPARATE, never summed (5000 cash vs 800 agentic)
    assert v["cash"]["portfolio_value"] == 5000.0
    assert v["agentic"]["portfolio_value"] == 800.0
    assert v["cash"]["portfolio_value"] != v["agentic"]["portfolio_value"]
    assert v["cash"]["read_only"] is True
    assert v["cash"]["currency"] == "USD"                # currency handled


def test_view_masks_account_numbers(monkeypatch):
    _connected_view(monkeypatch)
    v = rv.accounts(force=True)
    assert v["cash"]["account_masked"] == "•••• 1111"
    # NO raw account number ever leaks into the serialized output
    import json
    blob = json.dumps(v, default=str, ensure_ascii=False)
    for raw in ("FAKE1000001111", "FAKE2000002222", "FAKE3000003333"):
        assert raw not in blob


def test_view_agentic_detection(monkeypatch):
    _connected_view(monkeypatch, tools=[{"name": "get_accounts"}])   # no order tool discovered
    v = rv.accounts(force=True)
    # agentic account chosen by agentic_allowed flag; execution flag off (no order tool)
    assert v["agentic"]["agentic_allowed"] is True
    assert v["agentic"]["execution_confirmed_by_tools"] is False


def test_view_agentic_execution_flag_from_tools(monkeypatch):
    _connected_view(monkeypatch, tools=[{"name": "get_accounts"}, {"name": "place_equity_order"}])
    v = rv.accounts(force=True)
    assert v["agentic"]["execution_confirmed_by_tools"] is True
    assert v["agentic"]["read_only"] is True            # still read-only (trading disabled)


def test_view_realized_pnl_mapped(monkeypatch):
    _connected_view(monkeypatch)
    v = rv.accounts(force=True)
    assert v["cash"]["realized_pnl_state"] == "ok"
    assert v["cash"]["realized_pnl"] == 150.0           # from total_returns string


def test_view_realized_pnl_error_state(monkeypatch):
    _connected_view(monkeypatch, realized_ok=False)
    v = rv.accounts(force=True)
    assert v["cash"]["realized_pnl_state"] == "invalid_request"   # clear error state, not a fake 0
    assert v["cash"]["realized_pnl"] is None


def test_view_positions_pagination_and_dedup(monkeypatch):
    _connected_view(monkeypatch)
    v = rv.accounts(force=True)
    syms = [p["symbol"] for p in v["cash"]["positions"]]
    # 2 pages returned AAPL twice — dedup keeps ONE; MSFT from page 2 present
    assert syms.count("AAPL") == 1 and "MSFT" in syms
    aapl = next(p for p in v["cash"]["positions"] if p["symbol"] == "AAPL")
    assert aapl["average_cost"] == 180.0 and aapl["market_value"] == 2000.0
    assert aapl["cost_basis"] == 1800.0                 # avg*qty fallback


def test_view_empty_portfolio(monkeypatch):
    _connected_view(monkeypatch, empty=True)
    v = rv.accounts(force=True)
    assert v["cash"]["position_count"] == 0 and v["cash"]["positions"] == []
    assert v["cash"]["portfolio_value"] == 5000.0       # cash-only account still shows value


def test_view_never_calls_a_write_tool(monkeypatch):
    calls = []
    _connected_view(monkeypatch, calls=calls)
    rv.accounts(force=True)
    rv.position_for("AAPL")
    writes = [c for c in calls if rh.is_write_tool(c)]
    assert writes == [], f"view invoked write tools: {writes}"


def test_view_auth_required_is_clean(monkeypatch):
    monkeypatch.setattr(rh, "status", lambda: {"connected": False, "status": "auth_required",
                                               "reason": "not authorized", "read_only": True})
    v = rv.accounts(force=True)
    assert v["cash"]["connected"] is False and v["agentic"]["connected"] is False
    assert v["cash"]["read_only"] is True


def test_view_outage_serves_stale(monkeypatch):
    _connected_view(monkeypatch)
    good = rv.accounts(force=True)
    assert good["cash"]["portfolio_value"] == 5000.0
    monkeypatch.setattr(rh, "get_accounts", lambda: (_ for _ in ()).throw(rh.RobinhoodMCPError("outage", "down")))
    degraded = rv.accounts(force=True)
    assert degraded.get("stale") is True
    assert degraded["cash"]["portfolio_value"] == 5000.0   # last good snapshot preserved


def test_position_for_ticker(monkeypatch):
    _connected_view(monkeypatch)
    rv.accounts(force=True)
    p = rv.position_for("AAPL")
    assert p["held"] is True
    h = p["holdings"][0]
    assert h["shares"] == 10.0 and h["average_cost"] == 180.0
    assert h["portfolio_weight_pct"] == pytest.approx(40.0, abs=0.1)   # 2000/5000


def test_position_for_not_held(monkeypatch):
    _connected_view(monkeypatch)
    rv.accounts(force=True)
    p = rv.position_for("ZZZZ")
    assert p["held"] is False and p["holdings"] == []


def test_view_never_raises_on_bad_tool(monkeypatch):
    monkeypatch.setattr(rh, "status", lambda: {"connected": True, "status": "connected"})
    monkeypatch.setattr(rh, "list_tools", lambda: (_ for _ in ()).throw(rh.RobinhoodMCPError("tool_error", "boom")))
    v = rv.accounts(force=True)
    assert v["cash"]["connected"] is False           # degraded, but present — no exception
