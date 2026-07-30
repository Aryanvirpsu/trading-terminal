"""Free social sentiment — StockTwits (labeled bullish/bearish), no auth needed.

WHERE TO GET SOCIAL SENTIMENT FOR FREE:
  1. StockTwits  — api.stocktwits.com/api/2/streams/symbol/{TICKER}.json
       Best free source: messages carry an explicit Bullish/Bearish label.
       No auth for this endpoint; ~200 req/hr. USED HERE.
  2. Reddit      — reddit.com/r/{sub}/search.json needs a registered (free) OAuth
       app; the anonymous endpoint 403s from server IPs. Not used (blocked).
  3. Google Trends (pytrends, free) — search-interest spikes = abnormal attention.
  4. Message VOLUME itself = attention; a spike on a penny name is a PUMP red flag
       (#11), not a bullish signal — treated with caution for speculative tickers.

Social is NOISY and manipulable, so this family carries LOW confidence by design.
Cached 30 min.
"""
from __future__ import annotations
import json, time, urllib.request
from typing import Any, Dict

_UA = {"User-Agent": "Mozilla/5.0 trading-lab research"}
_CACHE: Dict[str, tuple] = {}
_TTL = 1800.0

try:
    import requests
    _SESSION = requests.Session()
    _SESSION.headers.update(_UA)
    _SESSION.mount("https://", requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=8, max_retries=0))
except Exception:
    _SESSION = None


def stocktwits(symbol: str) -> Dict[str, Any]:
    now = time.time()
    hit = _CACHE.get(symbol.upper())
    if hit and now - hit[0] < _TTL:
        return hit[1]
    try:
        if _SESSION is not None:
            resp = _SESSION.get(f"https://api.stocktwits.com/api/2/streams/symbol/{symbol.upper()}.json", timeout=8)
            resp.raise_for_status()
            d = resp.json()
        else:
            req = urllib.request.Request(
                f"https://api.stocktwits.com/api/2/streams/symbol/{symbol.upper()}.json", headers=_UA)
            with urllib.request.urlopen(req, timeout=12) as r:
                d = json.load(r)
    except Exception as e:
        out = {"available": False, "error": str(e)[:80]}
        _CACHE[symbol.upper()] = (now, out)
        return out
    msgs = d.get("messages", [])
    labeled = [(m.get("entities", {}) or {}).get("sentiment") for m in msgs]
    bull = sum(1 for s in labeled if s and s.get("basic") == "Bullish")
    bear = sum(1 for s in labeled if s and s.get("basic") == "Bearish")
    out = {"available": True, "messages": len(msgs), "bullish": bull, "bearish": bear,
           "labeled": bull + bear}
    _CACHE[symbol.upper()] = (now, out)
    return out


def social_signal(symbol: str, speculative: bool = False) -> Dict[str, Any]:
    s = stocktwits(symbol)
    if not s.get("available") or s.get("labeled", 0) < 3:
        return {"dir": 0.0, "conf": 0.15 if s.get("available") else 0.0,
                "detail": "thin/no social data"}
    net = (s["bullish"] - s["bearish"]) / max(s["labeled"], 1)
    d = round(max(-0.35, min(0.35, net * 0.5)), 2)     # capped — social is low-trust
    # Penny + heavy chatter = pump-and-dump caution (#11): don't reward the hype.
    if speculative and s["messages"] >= 25:
        d = min(d, 0.0)
        note = " PUMP-RISK (spec + heavy chatter)"
    else:
        note = ""
    return {"dir": d, "conf": 0.35 if s["labeled"] >= 10 else 0.2,
            "detail": f"StockTwits {s['bullish']}B/{s['bearish']}Be of {s['messages']} msgs{note}"}


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    for s in (sys.argv[1:] or ["AAPL", "GME"]):
        print(s, json.dumps(social_signal(s), default=str))
