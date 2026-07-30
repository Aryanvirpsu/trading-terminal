"""Alpha Vantage NEWS_SENTIMENT (free key, ~25 req/DAY) — per-ticker news
sentiment SCORES. Quota is tiny, so this is a TARGETED deep-dive tool, NOT wired
into the auto-sweep (that would blow the daily limit in one run). Cached 6h.
"""
from __future__ import annotations
import json, os, sys, time, urllib.parse, urllib.request
from typing import Any, Dict

sys.path.insert(0, os.path.dirname(__file__))
from _config import AV_KEY

_CACHE: Dict[str, tuple] = {}
_TTL = 21600.0


def news_sentiment(symbol: str, limit: int = 30) -> Dict[str, Any]:
    if not AV_KEY:
        return {"available": False, "error": "no key"}
    now = time.time()
    hit = _CACHE.get(symbol.upper())
    if hit and now - hit[0] < _TTL:
        return hit[1]
    url = (f"https://www.alphavantage.co/query?function=NEWS_SENTIMENT&tickers={symbol.upper()}"
           f"&apikey={AV_KEY}&limit={limit}")
    try:
        d = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "lab"}), timeout=15))
    except Exception as e:
        return {"available": False, "error": str(e)[:80]}
    if "feed" not in d:
        out = {"available": False, "error": str(d.get("Information") or d.get("Note") or "quota/limit")[:80]}
        _CACHE[symbol.upper()] = (now, out)
        return out
    scores, labels = [], []
    for art in d["feed"]:
        for ts in art.get("ticker_sentiment", []):
            if ts.get("ticker") == symbol.upper():
                try:
                    rel = float(ts.get("relevance_score", 0)); sc = float(ts.get("ticker_sentiment_score", 0))
                    if rel > 0.1:
                        scores.append(sc); labels.append(ts.get("ticker_sentiment_label"))
                except Exception:
                    pass
    avg = round(sum(scores) / len(scores), 3) if scores else 0.0
    out = {"available": True, "articles": len(scores), "avg_sentiment": avg,
           "label": ("Bullish" if avg > 0.15 else "Bearish" if avg < -0.15 else "Neutral"),
           "note": "AV free = ~25 req/day; targeted use only."}
    _CACHE[symbol.upper()] = (now, out)
    return out


if __name__ == "__main__":
    for s in (sys.argv[1:] or ["AAPL"]):
        print(s, json.dumps(news_sentiment(s), default=str))
