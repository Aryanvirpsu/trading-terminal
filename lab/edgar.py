"""SEC EDGAR — free filings data for dilution / insider / penny-stock red flags.

data.sec.gov is a 100%-free official API (just needs a descriptive User-Agent).
Recent filing FORMS reveal exactly the risks the spec wants for micro-caps (#11):
  dilution  -> S-1, S-3, 424B*, S-8, ATM shelf
  insider   -> Form 4 (transactions), 144 (proposed insider sale)
  structural-> 8-K (events, reverse split, going concern), 25/25-NSE (delisting)

Rules-based flags only — no LLM. Caches the ticker->CIK map to disk.
"""
from __future__ import annotations
import json, os, time, urllib.request
from typing import Any, Dict

DATA_DIR = os.path.expanduser("~/.tradingview_mcp_data")
_CIK_FILE = os.path.join(DATA_DIR, "sec_cik_map.json")
_UA = {"User-Agent": "trading-lab research tool (contact: user@example.com)"}
_CACHE: Dict[str, tuple] = {}
_TTL = 3600.0

DILUTION = {"S-1", "S-3", "S-8", "424B1", "424B2", "424B3", "424B4", "424B5", "F-1", "F-3"}
INSIDER = {"4", "144", "3", "5"}
STRUCTURAL = {"8-K", "25", "25-NSE", "15-12B", "NT 10-K", "NT 10-Q"}


def _get(url: str):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def _cik_map() -> Dict[str, str]:
    try:
        if os.path.exists(_CIK_FILE) and time.time() - os.path.getmtime(_CIK_FILE) < 7 * 86400:
            return json.load(open(_CIK_FILE, encoding="utf-8"))
    except Exception:
        pass
    try:
        raw = _get("https://www.sec.gov/files/company_tickers.json")
        m = {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in raw.values()}
        os.makedirs(DATA_DIR, exist_ok=True)
        json.dump(m, open(_CIK_FILE, "w", encoding="utf-8"))
        return m
    except Exception:
        return {}


def filing_flags(symbol: str, lookback: int = 40) -> Dict[str, Any]:
    now = time.time()
    hit = _CACHE.get(symbol.upper())
    if hit and now - hit[0] < _TTL:
        return hit[1]
    cik = _cik_map().get(symbol.upper())
    if not cik:
        out = {"symbol": symbol, "available": False, "reason": "ticker not in EDGAR (foreign/OTC?)"}
        _CACHE[symbol.upper()] = (now, out)
        return out
    try:
        d = _get(f"https://data.sec.gov/submissions/CIK{cik}.json")
        recent = d.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])[:lookback]
        dates = recent.get("filingDate", [])[:lookback]
    except Exception as e:
        return {"symbol": symbol, "available": False, "reason": str(e)[:80]}

    dil = [f for f in forms if f in DILUTION]
    ins = [f for f in forms if f in INSIDER]
    str_ = [f for f in forms if f in STRUCTURAL]
    out = {
        "symbol": symbol.upper(), "available": True, "company": d.get("name"),
        "filings_scanned": len(forms), "latest_filing": dates[0] if dates else None,
        "dilution_filings": len(dil), "insider_filings": len(ins),
        "structural_filings": len(str_),
        # red flags
        "dilution_risk": len(dil) > 0,
        "heavy_insider_activity": len(ins) >= 5,
        "delisting_or_late": any(f in ("25", "25-NSE", "NT 10-K", "NT 10-Q", "15-12B") for f in forms),
        "recent_forms": forms[:10],
    }
    _CACHE[symbol.upper()] = (now, out)
    return out


def dilution_penalty(symbol: str) -> Dict[str, Any]:
    """Directional family input (bearish penalty for active dilution / red flags)."""
    f = filing_flags(symbol)
    if not f.get("available"):
        return {"dir": 0.0, "conf": 0.0, "detail": f.get("reason", "no EDGAR data")}
    d = 0.0
    if f["dilution_risk"]: d -= 0.35
    if f["delisting_or_late"]: d -= 0.4
    if f["heavy_insider_activity"]: d -= 0.15
    return {"dir": round(d, 2), "conf": 0.6,
            "detail": f"dilution={f['dilution_filings']} insider={f['insider_filings']} "
                      f"delist/late={f['delisting_or_late']}"}


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    for s in (sys.argv[1:] or ["AAPL", "SOFI"]):
        print(json.dumps(filing_flags(s), indent=2, default=str))
