"""Robinhood dashboard view — turns the READ-ONLY MCP client into the terminal's
account panels, keeping the Cash/primary and Agentic accounts SEPARATE and never
combining balances.

Every value here is derived from tools DISCOVERED on the official Robinhood MCP
server (get_accounts / get_portfolio / get_positions / get_realized_pnl / …). All
account identifiers are masked; nothing raises (a clean state is returned on any
auth/outage failure); a short cache provides a stale fallback so a Robinhood blip
never blanks the panel or blocks the rest of the page.
"""
from __future__ import annotations

import os
import sys
import time
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(__file__))
import robinhood_mcp as rh

_CACHE: Dict[str, Any] = {}
_TTL = 120.0


def _f(x) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _first(d: Dict[str, Any], *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return default


# Record-list keys the Robinhood MCP uses, in priority order.
_LIST_KEYS = ("accounts", "positions", "orders", "results", "items", "holdings",
              "tax_lots", "equities", "data")


def _list_dicts(v: Any) -> List[Dict[str, Any]]:
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _extract_list(raw: Any) -> List[Dict[str, Any]]:
    """MCP `tools/call` results wrap records in several shapes — dig out the record
    list defensively (handles `structuredContent.data.accounts`, top-level lists,
    and the `content[].text` JSON fallback). Does NOT assume one sample response."""
    if isinstance(raw, list):
        return _list_dicts(raw)
    if not isinstance(raw, dict):
        return []
    # Search structuredContent (and its nested `data`) then the top level.
    containers = []
    sc = raw.get("structuredContent")
    if isinstance(sc, dict):
        containers.append(sc)
        if isinstance(sc.get("data"), dict):
            containers.append(sc["data"])
    containers.append(raw)
    if isinstance(raw.get("data"), dict):
        containers.append(raw["data"])
    for container in containers:
        for k in _LIST_KEYS:
            got = _list_dicts(container.get(k))
            if got:
                return got
    # Fallback: the human-readable content block often holds the same JSON.
    content = raw.get("content")
    if isinstance(content, list):
        import json
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                try:
                    return _extract_list(json.loads(c.get("text", "")))
                except Exception:
                    pass
    return []


def _extract_cursor(raw: Any) -> Optional[str]:
    """Pull a pagination cursor if the tool paginates (positions/orders do)."""
    for src in (raw, raw.get("structuredContent") if isinstance(raw, dict) else None,
                (raw.get("structuredContent") or {}).get("data") if isinstance(raw, dict) else None):
        if isinstance(src, dict):
            for k in ("next_cursor", "cursor", "next", "next_page_token"):
                v = src.get(k)
                if isinstance(v, str) and v:
                    return v
    return None


def _tool_ok(raw: Any) -> Optional[Dict[str, Any]]:
    """Return the tool result unless the MCP server flagged an error (isError)."""
    if isinstance(raw, dict) and raw.get("isError"):
        return None
    return raw if isinstance(raw, dict) else None


def _extract_obj(raw: Any) -> Dict[str, Any]:
    """Pull the object payload (portfolio) from `structuredContent.data` / content."""
    if not isinstance(raw, dict):
        return {}
    sc = raw.get("structuredContent")
    if isinstance(sc, dict):
        if isinstance(sc.get("data"), dict):
            return sc["data"]
        return {k: v for k, v in sc.items() if k != "data"} or sc
    content = raw.get("content")
    if isinstance(content, list):
        import json
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                try:
                    return _extract_obj(json.loads(c.get("text", "")))
                except Exception:
                    pass
    return {}


def _account_number(a: Dict[str, Any]) -> Optional[str]:
    return _first(a, "account_number", "id", "account_id", "rhs_account_number")


def _is_agentic(acct: Dict[str, Any]) -> bool:
    """Agentic = a managed-agentic account OR a self-directed account with agentic
    execution enabled (`agentic_allowed`). Robinhood exposes it via management_type /
    brokerage_account_type / the agentic_allowed flag — not a name string."""
    mt = str(acct.get("management_type", "")).lower()
    bt = str(acct.get("brokerage_account_type", "")).lower()
    if mt == "agentic" or bt == "agentic":
        return True
    if acct.get("agentic_allowed") is True:
        return True
    blob = " ".join(str(acct.get(k, "")) for k in ("type", "name", "nickname", "category")).lower()
    return "agent" in blob or "cortex" in blob


def _norm_ts(v: Any) -> Optional[str]:
    """Normalise a timestamp to ISO-8601 UTC; pass through strings, handle epochs."""
    if v in (None, ""):
        return None
    from datetime import datetime, timezone
    try:
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(float(v), timezone.utc).isoformat()
        return str(v)
    except Exception:
        return str(v)


def _norm_position(p: Dict[str, Any]) -> Dict[str, Any]:
    qty = _f(_first(p, "quantity", "units", "shares", "shares_held", "total_quantity"))
    avg = _f(_first(p, "average_cost", "average_buy_price", "average_purchase_price",
                    "cost_basis_per_share", "average_price"))
    price = _f(_first(p, "price", "last_price", "market_price", "current_price", "last_trade_price"))
    mv = _f(_first(p, "market_value", "equity", "value"))
    if mv is None and qty is not None and price is not None:
        mv = qty * price
    cb = _f(_first(p, "cost_basis", "total_cost"))
    if cb is None and avg is not None and qty is not None:
        cb = avg * qty
    upnl = _f(_first(p, "unrealized_pnl", "open_pnl", "total_return", "unrealized_gain_loss"))
    if upnl is None and mv is not None and cb is not None:
        upnl = mv - cb
    return {
        "symbol": _first(p, "symbol", "ticker", "instrument_symbol", "chain_symbol"),
        "instrument_id": _first(p, "instrument_id", "instrument", "id"),
        "quantity": qty, "average_cost": avg, "current_price": price,
        "market_value": round(mv, 2) if mv is not None else None,
        "cost_basis": round(cb, 2) if cb is not None else None,
        "unrealized_pnl": round(upnl, 2) if upnl is not None else None,
        "unrealized_pnl_pct": _f(_first(p, "unrealized_pnl_pct", "total_return_pct")),
        "updated_at": _norm_ts(_first(p, "updated_at", "last_updated", "timestamp")),
    }


def _dedup_positions(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Prevent duplicate positions (same symbol/instrument across pages)."""
    seen, out = set(), []
    for p in rows:
        key = (p.get("symbol") or "", p.get("instrument_id") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _paginated(tool: str, acct_no: str, max_pages: int = 15) -> List[Dict[str, Any]]:
    """Fetch every page of a paginated read tool (positions/orders) via cursor."""
    rows: List[Dict[str, Any]] = []
    cursor, pages = None, 0
    while pages < max_pages:
        args = {"account_number": acct_no}
        if cursor:
            args["cursor"] = cursor
        try:
            raw = rh.call_tool(tool, args)
        except rh.RobinhoodMCPError:
            break
        if _tool_ok(raw) is None:
            break
        rows.extend(_extract_list(raw))
        cursor = _extract_cursor(raw)
        pages += 1
        if not cursor:
            break
    return rows


def _normalize_account(acct: Dict[str, Any], execution_capable: bool) -> Dict[str, Any]:
    """Fetch this account's portfolio + positions + realized P&L via the REAL
    per-account tools and normalise defensively. Values arrive as STRINGS."""
    acct_no = _account_number(acct)
    port = _extract_obj(_tool_ok(rh.call_tool("get_portfolio", {"account_number": acct_no})) or {})
    bp_obj = port.get("buying_power") if isinstance(port.get("buying_power"), dict) else {}
    currency = _first(port, "currency") or _first(bp_obj, "display_currency") or "USD"

    positions = _dedup_positions([_norm_position(p) for p in _paginated("get_equity_positions", acct_no)])
    opt_positions = [_norm_position(p) for p in _paginated("get_option_positions", acct_no)]
    unreal = sum(p["unrealized_pnl"] for p in positions if p.get("unrealized_pnl") is not None) or None

    # Realized P&L (best-effort). Confirmed live: needs span ∈ {3month,all,day,
    # month,week,year} AND asset_classes as an ARRAY. Returns `total_returns`.
    realized, realized_rate, realized_state = None, None, "unavailable"
    try:
        rp = rh.call_tool("get_realized_pnl", {"account_number": acct_no, "span": "year",
                                               "asset_classes": ["equity"]})
        obj = _extract_obj(_tool_ok(rp) or {})
        if obj:
            realized = _f(_first(obj, "total_returns", "realized_pnl", "total_realized_pnl", "total"))
            realized_rate = _f(_first(obj, "total_rate_of_return"))
            realized_state = "ok"
        elif isinstance(rp, dict) and rp.get("isError"):
            realized_state = str((rp.get("_meta") or {}).get("rh_error_category") or "error")
    except rh.RobinhoodMCPError:
        realized_state = "error"

    return {
        "account_masked": rh.mask_account(acct_no),
        "account_type": _first(acct, "type", default="brokerage"),
        "brokerage_account_type": acct.get("brokerage_account_type"),
        "management_type": acct.get("management_type"),
        "agentic_allowed": bool(acct.get("agentic_allowed")),
        "is_default": bool(acct.get("is_default")),
        "currency": currency,
        "portfolio_value": _f(_first(port, "total_value", "portfolio_value", "market_value", "total_equity")),
        "equity_value": _f(_first(port, "equity_value")),
        "cash": _f(_first(port, "cash", "cash_balance", "uninvested_cash")),
        "buying_power": _f(_first(bp_obj, "buying_power") or _first(port, "buying_power")),
        "unrealized_pnl": round(unreal, 2) if unreal is not None else None,
        "realized_pnl": realized, "realized_rate_of_return": realized_rate,
        "realized_pnl_state": realized_state,
        "positions": positions, "position_count": len(positions),
        "option_positions": opt_positions,
        "orders": _paginated("get_equity_orders", acct_no) if _first(acct, "account_number") else [],
        "execution_capable": execution_capable,     # confirmed by discovered order tools
        "read_only": not (execution_capable and rh.trading_enabled()),
    }


def _empty_account(reason: str, status_obj: Dict[str, Any]) -> Dict[str, Any]:
    return {"connected": bool(status_obj.get("connected")), "status": status_obj.get("status"),
            "reason": reason, "portfolio_value": None, "cash": None, "positions": [],
            "position_count": 0, "read_only": True}


def accounts(force: bool = False) -> Dict[str, Any]:
    """Return {status, cash, agentic, other_accounts, tools_discovered} — Cash and
    Agentic kept SEPARATE. Never combines balances, never raises, masks account ids,
    serves the last good snapshot on failure (stale-while-error)."""
    now = time.time()
    if not force and _CACHE.get("v") and now - _CACHE["v"][0] < _TTL:
        return _CACHE["v"][1]

    st = rh.status()
    base = {"status": st, "endpoint": rh.MCP_URL, "read_only_phase": not rh.trading_enabled(),
            "last_refresh": now}
    if not st.get("connected"):
        out = {**base, "cash": _empty_account(st.get("reason", "not connected"), st),
               "agentic": _empty_account(st.get("reason", "not connected"), st),
               "other_accounts": [], "tools_discovered": []}
        _CACHE["v"] = (now, out)
        return out

    try:
        tools = rh.list_tools()
        tool_names = [t.get("name") for t in tools]
        # Execution capability = the MCP server actually exposes order tools.
        has_order_tool = any(n in ("place_equity_order", "place_option_order") for n in tool_names)
        accts = [a for a in _extract_list(rh.get_accounts()) if not a.get("deactivated")]

        agent_accts = [a for a in accts if _is_agentic(a)]
        cash_accts = [a for a in accts if not _is_agentic(a)]
        # Primary cash = the default account; else the first cash account.
        primary = next((a for a in cash_accts if a.get("is_default")), cash_accts[0] if cash_accts else None)

        cash = (_normalize_account(primary, execution_capable=False) if primary
                else _empty_account("no cash account returned", st))
        cash["connected"] = bool(primary); cash["label"] = "Robinhood · Cash / Primary"; cash["read_only"] = True

        if agent_accts:
            agentic = _normalize_account(agent_accts[0], execution_capable=has_order_tool)
            agentic["connected"] = True
        else:
            agentic = _empty_account("no separate Agentic account on this login", st)
        agentic["label"] = "Robinhood · Agentic"
        agentic["execution_confirmed_by_tools"] = has_order_tool

        # Any remaining accounts (e.g. IRAs) — listed SEPARATELY, masked, NOT summed.
        used = {id(primary)} | {id(a) for a in agent_accts[:1]}
        others = [{"account_masked": rh.mask_account(_account_number(a)),
                   "type": a.get("type"), "brokerage_account_type": a.get("brokerage_account_type"),
                   "agentic_allowed": bool(a.get("agentic_allowed"))}
                  for a in accts if id(a) not in used]

        out = {**base, "cash": cash, "agentic": agentic, "other_accounts": others,
               "account_count": len(accts), "tools_discovered": tool_names}
        _CACHE["v"] = (now, out)
        return out
    except rh.RobinhoodMCPError as e:
        stale = _CACHE.get("v")
        if stale is not None:
            data = dict(stale[1]); data["stale"] = True
            data["status"] = {"connected": False, "status": e.kind, "reason": e.detail}
            return data
        deg = {"connected": False, "status": e.kind, "reason": e.detail}
        return {**base, "status": deg, "cash": _empty_account(e.detail, deg),
                "agentic": _empty_account(e.detail, deg), "other_accounts": [], "tools_discovered": []}


def position_for(symbol: str) -> Dict[str, Any]:
    """The selected ticker's holding across the Robinhood accounts (for the stock
    page). Returns which account holds it + weight. Never raises."""
    sym = (symbol or "").upper()
    data = accounts()
    out = {"symbol": sym, "connected": bool(data.get("status", {}).get("connected")),
           "status": data.get("status", {}).get("status"), "holdings": []}
    for key in ("cash", "agentic"):
        acct = data.get(key) or {}
        pv = acct.get("portfolio_value") or 0
        for p in acct.get("positions", []):
            if (p.get("symbol") or "").upper() == sym:
                mv = p.get("market_value") or 0
                out["holdings"].append({
                    "account": acct.get("label", key), "account_masked": acct.get("account_masked"),
                    "shares": p.get("quantity"), "average_cost": p.get("average_cost"),
                    "market_value": p.get("market_value"), "cost_basis": p.get("cost_basis"),
                    "unrealized_pnl": p.get("unrealized_pnl"),
                    "portfolio_weight_pct": round(mv / pv * 100, 2) if pv else None,
                    "orders": [o for o in acct.get("orders", [])
                               if (str(o.get("symbol", "")).upper() == sym)],
                })
    out["held"] = bool(out["holdings"])
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(accounts(), indent=2, default=str))
