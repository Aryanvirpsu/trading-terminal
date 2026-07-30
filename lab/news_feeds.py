"""Per-ticker news via Google News RSS (free, no key) — a far stronger catalyst
signal than matching a general feed. Reuses catalyst_scan's keyword scorer.
"""
from __future__ import annotations
import os, sys, time, urllib.parse
from typing import Any, Dict

sys.path.insert(0, os.path.dirname(__file__))
try:
    import feedparser
    _OK = True
except Exception:
    _OK = False

_CACHE: Dict[str, tuple] = {}
_TTL = 900.0


def google_news(symbol: str, limit: int = 20) -> list:
    if not _OK:
        return []
    now = time.time()
    hit = _CACHE.get(symbol.upper())
    if hit and now - hit[0] < _TTL:
        return hit[1]
    q = urllib.parse.quote(f"{symbol} stock")
    url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
    try:
        f = feedparser.parse(url)
        items = [{"title": e.get("title", ""), "summary": e.get("summary", ""),
                  "url": e.get("link", "")} for e in f.entries[:limit]]
    except Exception:
        items = []
    _CACHE[symbol.upper()] = (now, items)
    return items


def catalyst_signal(symbol: str) -> Dict[str, Any]:
    """Per-ticker catalyst family input, scored with catalyst_scan's keyword engine."""
    try:
        from catalyst_scan import _COMPILED
    except Exception:
        return {"dir": 0.0, "conf": 0.0, "detail": "no scorer"}
    items = google_news(symbol)
    if not items:
        return {"dir": 0.0, "conf": 0.2, "detail": "no per-ticker news"}
    score, bull, bear, hits = 0, 0, 0, 0
    for it in items:
        text = f"{it['title']} {it['summary']}"
        for rx, w, d in _COMPILED:
            if rx.search(text):
                score += w; hits += 1
                if d == "bull": bull += w
                elif d == "bear": bear += w
    if hits == 0:
        return {"dir": 0.0, "conf": 0.3, "detail": f"{len(items)} headlines, no catalyst keywords"}
    net = (bull - bear)
    d = 0.4 if net > 0 else (-0.4 if net < 0 else 0.0)
    return {"dir": d, "conf": min(0.7, 0.35 + score * 0.03),
            "detail": f"{len(items)} headlines, catalyst score {score} ({bull}bull/{bear}bear)"}


if __name__ == "__main__":
    import json
    for s in (sys.argv[1:] or ["NVDA", "AAPL"]):
        print(s, json.dumps(catalyst_signal(s), default=str))
