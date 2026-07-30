"""Free fundamental / short-interest / insider data via yfinance (Yahoo).

Yahoo has been our reliable free source all session (the TradingView scanner is
what throttles, not Yahoo). yfinance exposes float, short %, insider/institutional
holdings, cash, debt, EV, shares outstanding — enough to power the squeeze,
insider, dilution-risk and liquidity families the decision engine needs, with NO
paid feed. Cached 30 min in-process (info calls are slow).
"""
from __future__ import annotations
import time
from typing import Any, Dict

try:
    import yfinance as yf
    _OK = True
except Exception:
    _OK = False

_CACHE: Dict[str, tuple] = {}
_TTL = 1800.0


def fundamentals(symbol: str) -> Dict[str, Any]:
    if not _OK:
        return {"symbol": symbol, "available": False, "error": "yfinance not installed"}
    now = time.time()
    hit = _CACHE.get(symbol.upper())
    if hit and now - hit[0] < _TTL:
        return hit[1]
    try:
        info = yf.Ticker(symbol).info or {}
    except Exception as e:
        return {"symbol": symbol, "available": False, "error": str(e)[:100]}

    price = info.get("currentPrice") or info.get("regularMarketPrice")
    flt = info.get("floatShares")
    short_pf = info.get("shortPercentOfFloat")
    cash, debt = info.get("totalCash"), info.get("totalDebt")
    out = {
        "symbol": symbol.upper(), "available": True,
        "price": price, "market_cap": info.get("marketCap"),
        "enterprise_value": info.get("enterpriseValue"),
        "float_shares": flt, "shares_outstanding": info.get("sharesOutstanding"),
        "short_pct_float": round(short_pf * 100, 2) if short_pf else None,
        "short_ratio": info.get("shortRatio"),
        "held_insiders_pct": round((info.get("heldPercentInsiders") or 0) * 100, 2),
        "held_institutions_pct": round((info.get("heldPercentInstitutions") or 0) * 100, 2),
        "total_cash": cash, "total_debt": debt,
        "avg_volume": info.get("averageVolume"),
        # derived flags
        "low_float": bool(flt and flt < 50_000_000),
        "high_short": bool(short_pf and short_pf > 0.15),
        "squeeze_setup": bool(flt and flt < 75_000_000 and short_pf and short_pf > 0.15),
        "net_cash_positive": bool(cash is not None and debt is not None and cash > debt),
        "thin_liquidity": bool((info.get("averageVolume") or 0) < 300_000),
    }
    _CACHE[symbol.upper()] = (now, out)
    return out


def short_squeeze_signal(symbol: str) -> Dict[str, Any]:
    """Directional family input: high short % of a low float = squeeze fuel (bullish
    IF price is already turning up — caller combines with trend)."""
    f = fundamentals(symbol)
    if not f.get("available"):
        return {"dir": 0.0, "conf": 0.0, "detail": "no data"}
    sp = f.get("short_pct_float") or 0
    d = 0.0
    if f.get("squeeze_setup"): d = 0.4
    elif sp > 20: d = 0.3
    elif sp > 10: d = 0.15
    return {"dir": d, "conf": 0.5 if sp else 0.2,
            "detail": f"short {sp}% float, float {f.get('float_shares')}, "
                      f"{'SQUEEZE setup' if f.get('squeeze_setup') else 'no squeeze'}"}


if __name__ == "__main__":
    import sys, json
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    for s in (sys.argv[1:] or ["AAPL", "SOFI"]):
        print(json.dumps(fundamentals(s), indent=2, default=str))
