"""Symbol → GICS-style sector/industry, cached on disk.

The local security master (~30k names) carries identity but no sector, and Yahoo's
`quoteSummary` assetProfile endpoint now returns 401 without a crumb. Finnhub's
`/stock/profile2` does supply an industry and a market cap on the free tier, but at
60 requests/minute it cannot be called inside a scan.

So: a persistent JSON cache outside the repo, seeded from the CURATED sector map
(authoritative for the ~90 large caps that anchor each sector) and filled in lazily
and rate-limited from Finnhub in the background. A symbol whose sector is not known
yet returns `None` — the scanner scores that as "unknown", never as a guess.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in ("..", os.path.join("..", "lab"), os.path.join("..", "src")):
    sys.path.insert(0, os.path.join(_HERE, _p))
sys.path.insert(0, _HERE)

_DATA_DIR = os.path.expanduser(os.environ.get("TVMCP_DATA_DIR", "~/.tradingview_mcp_data"))
_PATH = os.path.join(_DATA_DIR, "sector_lookup.json")
_TTL_DAYS = float(os.environ.get("SECTOR_LOOKUP_TTL_DAYS", 30))
_RATE_PER_MIN = int(os.environ.get("FINNHUB_RATE_PER_MIN", 55))

_LOCK = threading.Lock()
_MEM: Dict[str, Any] = {}
_LOADED = {"v": False}
_CALLS: List[float] = []          # finnhub call timestamps, for rate limiting

# Finnhub industry strings → the 11 sector keys used by dashboard/sector_map.py
_INDUSTRY_TO_SECTOR = {
    "technology": "technology",
    "semiconductors": "technology",
    "electronic equipment": "technology",
    "communications": "communication",
    "media": "communication",
    "telecommunication": "communication",
    "retail": "consumer_discretionary",
    "automobiles": "consumer_discretionary",
    "auto": "consumer_discretionary",
    "textiles apparel & luxury goods": "consumer_discretionary",
    "hotels restaurants & leisure": "consumer_discretionary",
    "consumer products": "consumer_staples",
    "food products": "consumer_staples",
    "beverages": "consumer_staples",
    "tobacco": "consumer_staples",
    "banking": "financials",
    "financial services": "financials",
    "insurance": "financials",
    "diversified financial services": "financials",
    "health care": "health_care",
    "healthcare": "health_care",
    "pharmaceuticals": "health_care",
    "biotechnology": "health_care",
    "life sciences tools & services": "health_care",
    "industrial conglomerates": "industrials",
    "machinery": "industrials",
    "aerospace & defense": "industrials",
    "airlines": "industrials",
    "road & rail": "industrials",
    "transportation": "industrials",
    "building": "industrials",
    "trading companies & distributors": "industrials",
    "commercial services & supplies": "industrials",
    "professional services": "industrials",
    "energy": "energy",
    "oil & gas": "energy",
    "chemicals": "materials",
    "metals & mining": "materials",
    "basic materials": "materials",
    "paper & forest": "materials",
    "packaging": "materials",
    "utilities": "utilities",
    "real estate": "real_estate",
    "reit": "real_estate",
}


def _now() -> float:
    return time.time()


def _load() -> None:
    if _LOADED["v"]:
        return
    with _LOCK:
        if _LOADED["v"]:
            return
        try:
            with open(_PATH, encoding="utf-8") as f:
                _MEM.update(json.load(f))
        except Exception:  # noqa: BLE001 — a missing/corrupt cache just means a cold start
            pass
        _seed_from_curated()
        _LOADED["v"] = True


def _seed_from_curated() -> None:
    """The curated sector map is authoritative for its own members."""
    try:
        import sector_map as _SM
    except Exception:  # noqa: BLE001
        return
    for key, spec in _SM.SECTORS.items():
        for industry, members in spec["industries"].items():
            for sym in members:
                cur = _MEM.get(sym)
                if not cur or cur.get("source") != "curated":
                    _MEM[sym] = {"sector": key, "sector_name": spec["name"],
                                 "industry": industry, "source": "curated", "ts": _now()}


def _save() -> None:
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        tmp = _PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_MEM, f)
        os.replace(tmp, _PATH)
    except Exception:  # noqa: BLE001
        pass


def get(sym: str) -> Optional[Dict[str, Any]]:
    """Cached sector record for a symbol, or None if not known yet."""
    _load()
    r = _MEM.get(sym.upper())
    if not r:
        return None
    if r.get("source") != "curated" and _now() - r.get("ts", 0) > _TTL_DAYS * 86400:
        return None
    return r


def get_many(syms: List[str]) -> Dict[str, Optional[Dict[str, Any]]]:
    return {s: get(s) for s in syms}


def _rate_ok() -> bool:
    cutoff = _now() - 60
    while _CALLS and _CALLS[0] < cutoff:
        _CALLS.pop(0)
    return len(_CALLS) < _RATE_PER_MIN


def _fetch_one(sym: str) -> Optional[Dict[str, Any]]:
    key = os.environ.get("FINNHUB_API_KEY")
    if not key or not _rate_ok():
        return None
    try:
        import research as _R
        _CALLS.append(_now())
        r = _R._http().get("https://finnhub.io/api/v1/stock/profile2",
                           params={"symbol": sym, "token": key}, timeout=8)
        if r.status_code != 200:
            return None
        d = r.json() or {}
    except Exception:  # noqa: BLE001
        return None
    ind = (d.get("finnhubIndustry") or "").strip()
    if not ind:
        return None
    low = ind.lower()
    sector = None
    for frag, key_ in _INDUSTRY_TO_SECTOR.items():
        if frag in low:
            sector = key_
            break
    name = None
    try:
        import sector_map as _SM
        name = (_SM.SECTORS.get(sector) or {}).get("name") if sector else None
    except Exception:  # noqa: BLE001
        pass
    return {"sector": sector, "sector_name": name, "industry": ind,
            "market_cap_musd": d.get("marketCapitalization"),
            "source": "finnhub", "ts": _now()}


def backfill(syms: List[str], budget: int = 40) -> Dict[str, Any]:
    """Fill in up to `budget` unknown symbols, respecting the Finnhub rate limit.
    Safe to call from a background thread; returns what it managed to resolve."""
    _load()
    todo = [s for s in dict.fromkeys(x.upper() for x in syms) if not get(s)][:budget]
    got = 0
    for s in todo:
        rec = _fetch_one(s)
        if rec:
            _MEM[s] = rec
            got += 1
        elif not _rate_ok():
            break
    if got:
        _save()
    return {"requested": len(todo), "resolved": got, "cache_size": len(_MEM),
            "rate_limited": not _rate_ok()}


def backfill_async(syms: List[str], budget: int = 40) -> None:
    threading.Thread(target=lambda: backfill(syms, budget),
                     name="sector-backfill", daemon=True).start()


def stats() -> Dict[str, Any]:
    _load()
    by_src: Dict[str, int] = {}
    for r in _MEM.values():
        by_src[r.get("source", "?")] = by_src.get(r.get("source", "?"), 0) + 1
    return {"size": len(_MEM), "by_source": by_src, "path": _PATH,
            "finnhub_key": bool(os.environ.get("FINNHUB_API_KEY"))}
