"""Finnhub free tier (60 req/min) — quote, analyst recommendations, company news.

Two big wins:
  1. QUOTE is a reliable price source independent of the throttle-prone TradingView
     scanner (bottleneck relief).
  2. Analyst RECOMMENDATIONS are an INDEPENDENT signal family (not price-derived),
     which is exactly what raises real cross-family agreement.
"""
from __future__ import annotations
import json, os, sys, time, urllib.request
from typing import Any, Dict

sys.path.insert(0, os.path.dirname(__file__))
from _config import FINNHUB_KEY

_BASE = "https://finnhub.io/api/v1"
_CACHE: Dict[str, tuple] = {}
_TTL = 600.0

# Persistent keep-alive session with a small connection pool — avoids a fresh TLS
# handshake on every call (the dominant cost when the dashboard fans out many
# per-ticker Finnhub requests). Falls back to urllib if requests is unavailable.
try:
    import requests
    _SESSION = requests.Session()
    _SESSION.headers.update({"User-Agent": "lab"})
    _ADAPTER = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=16, max_retries=0)
    _SESSION.mount("https://", _ADAPTER)
except Exception:
    _SESSION = None


def _get(path: str, timeout: float = 8.0) -> Any:
    url = f"{_BASE}/{path}{'&' if '?' in path else '?'}token={FINNHUB_KEY}"
    if _SESSION is not None:
        r = _SESSION.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json()
    req = urllib.request.Request(url, headers={"User-Agent": "lab"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _cached(key: str, fn):
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    try:
        v = fn()
    except Exception as e:
        v = {"error": str(e)[:80]}
    _CACHE[key] = (now, v)
    return v


def quote(symbol: str) -> Dict[str, Any]:
    if not FINNHUB_KEY:
        return {"error": "no key"}
    return _cached(f"q:{symbol}", lambda: _get(f"quote?symbol={symbol.upper()}"))


def analyst_signal(symbol: str) -> Dict[str, Any]:
    """Independent family: net analyst tilt (strongBuy+buy vs sell+strongSell)."""
    if not FINNHUB_KEY:
        return {"dir": 0.0, "conf": 0.0, "detail": "no key"}
    rec = _cached(f"rec:{symbol}", lambda: _get(f"stock/recommendation?symbol={symbol.upper()}"))
    if not isinstance(rec, list) or not rec:
        return {"dir": 0.0, "conf": 0.0, "detail": "no analyst data"}
    r = rec[0]
    bull = (r.get("strongBuy", 0) + r.get("buy", 0))
    bear = (r.get("sell", 0) + r.get("strongSell", 0))
    tot = bull + bear + r.get("hold", 0)
    if tot == 0:
        return {"dir": 0.0, "conf": 0.2, "detail": "no ratings"}
    net = (bull - bear) / tot
    return {"dir": round(max(-0.5, min(0.5, net)), 2), "conf": 0.55,
            "detail": f"analysts {bull}buy/{r.get('hold',0)}hold/{bear}sell ({r.get('period')})"}


def company_news_signal(symbol: str) -> Dict[str, Any]:
    """Per-ticker news volume as a mild catalyst/attention corroborator."""
    if not FINNHUB_KEY:
        return {"count": 0}
    import datetime as dt
    to = dt.date.today().isoformat()
    frm = (dt.date.today() - dt.timedelta(days=5)).isoformat()
    n = _cached(f"news:{symbol}", lambda: _get(f"company-news?symbol={symbol.upper()}&from={frm}&to={to}"))
    return {"count": len(n) if isinstance(n, list) else 0}


def company_news(symbol: str, days: int = 7) -> list:
    """Raw per-ticker company news (headline, source, url, ts, sentiment word)."""
    if not FINNHUB_KEY:
        return []
    import datetime as dt
    to = dt.date.today().isoformat()
    frm = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    n = _cached(f"cnews:{symbol}:{days}", lambda: _get(f"company-news?symbol={symbol.upper()}&from={frm}&to={to}"))
    return n if isinstance(n, list) else []


def profile(symbol: str) -> Dict[str, Any]:
    """Company profile: name, exchange, industry, market cap, currency, logo."""
    if not FINNHUB_KEY:
        return {"error": "no key"}
    return _cached(f"prof:{symbol}", lambda: _get(f"stock/profile2?symbol={symbol.upper()}"))


def basic_financials(symbol: str) -> Dict[str, Any]:
    """Key metrics: P/E, margins, 52w range, beta, growth (metric=all)."""
    if not FINNHUB_KEY:
        return {"error": "no key"}
    return _cached(f"fin:{symbol}", lambda: _get(f"stock/metric?symbol={symbol.upper()}&metric=all"))


def candles(symbol: str, *, days: int = 130, resolution: str = "D") -> Dict[str, Any]:
    """Daily OHLCV history — the documented `candles` category secondary
    (lab/providers.py: CATEGORIES["candles"], primary=yahoo,
    secondary=finnhub), never wired up until now.

    NOTE (documented honestly, not discovered by trial and error): Finnhub's
    `/stock/candle` endpoint has been restricted to paid plans since 2023 for
    most free API keys, commonly returning `{"s": "no_data"}` or a 403 even
    with a valid key. This function still implements the real, documented
    contract exactly as Finnhub specifies it, so it activates automatically
    the moment a plan/key that supports it is configured — no further code
    change needed. It is never the only path: research.price_history()
    treats a "no data"/error result here exactly like "provider
    unavailable", the same as a missing key.
    """
    if not FINNHUB_KEY:
        return {"error": "no key"}
    import time as _t
    to_ts = int(_t.time())
    from_ts = to_ts - days * 86400
    r = _cached(f"cand:{symbol}:{resolution}:{days}",
               lambda: _get(f"stock/candle?symbol={symbol.upper()}&resolution={resolution}"
                            f"&from={from_ts}&to={to_ts}"))
    if not isinstance(r, dict) or r.get("s") != "ok":
        detail = r.get("error") if isinstance(r, dict) else None
        return {"error": detail or (r.get("s") if isinstance(r, dict) else "no data") or "no data"}
    closes, opens, highs, lows, vols, times = (r.get(k) for k in ("c", "o", "h", "l", "v", "t"))
    if not (closes and opens and highs and lows and times):
        return {"error": "incomplete candle payload"}
    return {"c": closes, "o": opens, "h": highs, "l": lows,
            "v": vols or [0] * len(closes), "t": times}


def earnings_calendar(symbol: str) -> list:
    """Upcoming/recent earnings dates for a symbol (next ~90d + last 90d)."""
    if not FINNHUB_KEY:
        return []
    import datetime as dt
    frm = (dt.date.today() - dt.timedelta(days=90)).isoformat()
    to = (dt.date.today() + dt.timedelta(days=90)).isoformat()
    r = _cached(f"earn:{symbol}", lambda: _get(f"calendar/earnings?from={frm}&to={to}&symbol={symbol.upper()}"))
    return (r or {}).get("earningsCalendar", []) if isinstance(r, dict) else []


if __name__ == "__main__":
    for s in (sys.argv[1:] or ["AAPL", "SOFI"]):
        print(s, "quote:", quote(s), "| analyst:", analyst_signal(s), "| news:", company_news_signal(s))
