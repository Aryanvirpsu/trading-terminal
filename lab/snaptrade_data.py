"""SnapTrade — READ-ONLY Robinhood link (official aggregator, no password to us).

Uses PERSONAL keys (PERS-*): the user is auto-provisioned at SnapTrade signup and
the brokerage is connected in SnapTrade's own portal, so there's no registerUser
and no password here. We only READ balances/positions — SnapTrade cannot trade or
move money. If keys are missing or nothing is connected, returns a clean state.
"""
from __future__ import annotations
import os, sys, time

sys.path.insert(0, os.path.dirname(__file__))
from _config import SNAPTRADE_CLIENT_ID, SNAPTRADE_CONSUMER_KEY

_CACHE: dict = {}
_TTL = 120.0


def configured() -> bool:
    return bool(SNAPTRADE_CLIENT_ID and SNAPTRADE_CONSUMER_KEY)


def _client():
    from snaptrade_client import SnapTrade
    return SnapTrade(client_id=SNAPTRADE_CLIENT_ID, consumer_key=SNAPTRADE_CONSUMER_KEY)


def _creds():
    # Personal-key context: userSecret = consumer key; userId = registered email.
    uid = SNAPTRADE_CLIENT_ID
    try:
        users = _client().authentication.list_snap_trade_users().body or []
        if users:
            uid = users[0]
    except Exception:
        pass
    return {"userId": uid, "userSecret": SNAPTRADE_CONSUMER_KEY}


def _ticker(pos: dict):
    s = pos.get("symbol") or {}
    for path in (("symbol", "symbol"), ("symbol",), ("raw_symbol",)):
        cur = s
        ok = True
        for k in path:
            cur = cur.get(k) if isinstance(cur, dict) else None
            if cur is None:
                ok = False
                break
        if ok and isinstance(cur, str):
            return cur
    return (s.get("symbol") if isinstance(s.get("symbol"), str) else None) or "?"


def cash_account() -> dict:
    if not configured():
        return {"connected": False, "label": "Cash (Robinhood)", "status": "configuration_required",
                "reason": "SnapTrade keys not set in .env."}
    now = time.time()
    if _CACHE.get("acct") and now - _CACHE["acct"][0] < _TTL:
        return _CACHE["acct"][1]
    try:
        c = _client()
        cr = _creds()
        accts = c.account_information.list_user_accounts(query_params=cr).body or []
    except Exception as e:
        out = {"connected": False, "label": "Cash (Robinhood)", "status": "error", "reason": str(e)[:140]}
        _CACHE["acct"] = (now, out)
        return out
    if not accts:
        return {"connected": False, "label": "Cash (Robinhood)", "status": "connect_required",
                "reason": "No brokerage connected in SnapTrade yet."}

    qp = _creds()
    tot_val = tot_cash = tot_bp = 0.0
    breakdown, stock_positions, option_positions = [], [], []
    for a in accts:
        val = float((((a.get("balance") or {}).get("total") or {}).get("amount")) or 0)
        tot_val += val
        aid = a.get("id")
        cash = bp = None
        try:
            bals = c.account_information.get_user_account_balance(query_params=qp, account_id=aid).body or []
            usd = next((b for b in bals if (b.get("currency") or {}).get("code") == "USD"), (bals[0] if bals else {}))
            cash, bp = usd.get("cash"), usd.get("buying_power")
            tot_cash += float(cash or 0); tot_bp += float(bp or 0)
        except Exception:
            pass
        breakdown.append({"name": a.get("name"), "type": (a.get("meta") or {}).get("type"),
                          "value": round(val, 2), "cash": cash})
        try:
            pos = c.account_information.get_user_account_positions(query_params=qp, account_id=aid).body or []
            for p in pos:
                units, price = p.get("units") or 0, p.get("price") or 0
                stock_positions.append({"account": a.get("name"), "symbol": _ticker(p), "quantity": units,
                    "average_price": p.get("average_purchase_price"), "current_price": price,
                    "market_value": round((units or 0) * (price or 0), 2), "unrealized_pnl": p.get("open_pnl")})
        except Exception:
            pass
        try:
            opts = c.options.list_option_holdings(query_params=qp, account_id=aid).body or []
            for o in opts:
                osym = (o.get("symbol") or {}).get("option_symbol") or {}
                tk = (osym.get("ticker") or "").replace(" ", "")
                units = o.get("units") or 0
                price = o.get("price") or 0
                option_positions.append({
                    "account": a.get("name"),
                    "underlying": (osym.get("underlying_symbol") or {}).get("symbol"),
                    "strike": osym.get("strike_price"), "expiry": osym.get("expiration_date"),
                    "option_type": "CALL" if ("C0" in tk or "C1" in tk) else "PUT",
                    "quantity": units, "average_premium": o.get("average_purchase_price"),
                    "current_premium": price,
                    "market_value": o.get("market_value") or round(units * price * 100, 2),
                    "unrealized_pnl": o.get("open_pnl")})
        except Exception:
            pass

    out = {
        "connected": True, "label": "Cash (Robinhood)", "status": "active", "read_only": True,
        "broker": "robinhood · read-only via SnapTrade",
        "portfolio_value": round(tot_val, 2), "cash": round(tot_cash, 2), "buying_power": round(tot_bp, 2),
        "day_pnl": None, "accounts": len(accts), "account_breakdown": breakdown,
        "stock_positions": stock_positions, "option_positions": option_positions, "pending_orders": [],
        "note": "Read-only via SnapTrade — cannot place orders or move money.",
        "last_sync": (accts[0].get("sync_status") or {}).get("holdings", {}).get("last_successful_sync"),
    }
    _CACHE["acct"] = (now, out)
    return out


_CREDS = None
def _creds_for(_a):
    global _CREDS
    if _CREDS is None:
        _CREDS = _creds()
    return _CREDS


def connect_url() -> str:
    """Portal URL to (re)connect a brokerage read-only, if ever needed."""
    cr = _creds()
    r = _client().authentication.login_snap_trade_user(query_params=cr)
    b = r.body
    return (b.get("redirectURI") if isinstance(b, dict) else None) or str(b)


if __name__ == "__main__":
    import json
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(json.dumps(cash_account(), indent=2, default=str))
