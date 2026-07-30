"""Catalyst Scan — headless event/news scanner. NO LLM, NO Claude required.

Pulls free RSS market news (news_service) and scores each watchlist name for
tradeable catalysts using a keyword-weighted rules engine. Runs in the scheduled
task and writes catalysts.json for the dashboard / the trade engine to consume.

Deliberately rules-based so it runs unattended: earnings, upgrades/downgrades,
guidance, M&A, product launches, regulatory (FDA), and macro triggers each carry
a weight and a direction (bullish/bearish).

Headless: `python lab/catalyst_scan.py`  → prints + writes catalysts.json
"""
from __future__ import annotations
import json, os, re, sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from tradingview_mcp.core.services import news_service

DATA_DIR = os.path.expanduser("~/.tradingview_mcp_data")
OUT = os.path.join(DATA_DIR, "catalysts.json")

# symbol -> name keywords to match in headlines (free feeds rarely carry tickers)
WATCH = {
    "AAPL": ["apple"], "BAC": ["bank of america"], "AXP": ["american express", "amex"],
    "ABNB": ["airbnb"], "IMAX": ["imax"], "NVDA": ["nvidia"], "AMD": ["amd"],
    "TSLA": ["tesla"], "AMZN": ["amazon"], "META": ["meta", "facebook"],
    "GOOGL": ["google", "alphabet"], "MSFT": ["microsoft"], "JPM": ["jpmorgan", "jp morgan"],
    "F": ["ford"], "SOFI": ["sofi"], "INTC": ["intel"], "NFLX": ["netflix"],
    "SONY": ["sony"], "CMCSA": ["comcast", "universal"], "PLTR": ["palantir"],
}

# keyword -> (weight, direction). Higher weight = stronger catalyst.
SIGNALS = {
    r"\bearnings\b|\bbeats?\b|\bmisses?\b|\bguidance\b|\bq[1-4]\b": (3, "event"),
    r"\bupgrade[sd]?\b|\braises? (?:target|rating)\b|\boutperform\b|\bbuy rating\b": (3, "bull"),
    r"\bdowngrade[sd]?\b|\bcuts? (?:target|rating)\b|\bunderperform\b|\bsell rating\b": (3, "bear"),
    r"\bmerger\b|\bacquir\w+|\bbuyout\b|\btakeover\b|\bdeal\b": (4, "bull"),
    r"\blaunch\w*|\bunveil\w*|\breleases?\b|\bdebut\b|\bbox office\b": (2, "bull"),
    r"\bfda\b|\bapproval\b|\brecall\b|\blawsuit\b|\bprobe\b|\binvestigat\w+": (3, "event"),
    r"\brecord\b|\bsurge[sd]?\b|\bjumps?\b|\bsoars?\b|\ball-time high\b": (2, "bull"),
    r"\bplunge[sd]?\b|\btumbles?\b|\bslumps?\b|\bwarn\w*|\bcut jobs\b|\blayoffs?\b": (2, "bear"),
}
_COMPILED = [(re.compile(p, re.I), w, d) for p, (w, d) in SIGNALS.items()]


def scan() -> dict:
    items = news_service.fetch_news(symbol=None, category="all", limit=60)
    items = [i for i in items if "error" not in i]
    hits = defaultdict(lambda: {"score": 0, "bull": 0, "bear": 0, "headlines": []})

    for it in items:
        text = f"{it.get('title','')} {it.get('summary','')}"
        low = text.lower()
        # which watchlist names are mentioned?
        syms = [s for s, kws in WATCH.items() if any(k in low for k in kws)]
        if not syms:
            continue
        s_score = 0; s_bull = 0; s_bear = 0; tags = []
        for rx, w, d in _COMPILED:
            if rx.search(text):
                s_score += w; tags.append(d)
                if d == "bull": s_bull += w
                elif d == "bear": s_bear += w
        if s_score == 0:
            continue
        for sym in syms:
            h = hits[sym]
            h["score"] += s_score; h["bull"] += s_bull; h["bear"] += s_bear
            h["headlines"].append({"title": it.get("title"), "source": it.get("source"),
                                   "tags": tags, "url": it.get("url")})

    ranked = []
    for sym, h in hits.items():
        lean = "bullish" if h["bull"] > h["bear"] else ("bearish" if h["bear"] > h["bull"] else "mixed")
        ranked.append({"symbol": sym, "catalyst_score": h["score"], "lean": lean,
                       "headline_count": len(h["headlines"]), "headlines": h["headlines"][:3]})
    ranked.sort(key=lambda x: x["catalyst_score"], reverse=True)

    return {
        "generated_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(),
        "watchlist_size": len(WATCH),
        "news_scanned": len(items),
        "catalysts_found": len(ranked),
        "catalysts": ranked,
        "note": "Rules-based headline scoring — a screen, not a signal. Confirm the "
                "underlying passes the quality gate before trading a catalyst.",
    }


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    r = scan()
    os.makedirs(DATA_DIR, exist_ok=True)
    json.dump(r, open(OUT, "w", encoding="utf-8"), indent=2, default=str)
    print(f"=== CATALYST SCAN · {r['news_scanned']} headlines · {r['catalysts_found']} names ===")
    for c in r["catalysts"][:10]:
        print(f"  {c['symbol']:6} score {c['catalyst_score']:2} {c['lean']:8} ({c['headline_count']} hl)")
        for h in c["headlines"][:1]:
            print(f"         \"{(h['title'] or '')[:80]}\"")
    if not r["catalysts"]:
        print("  (no watchlist catalysts in current feed — normal on a quiet news cycle)")
    print(f"\n[written] {OUT}")
