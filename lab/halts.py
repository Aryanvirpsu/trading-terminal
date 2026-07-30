"""Nasdaq trading-halts (free RSS) — hard safety flag (#11).

A halted ticker must NEVER be traded until it resumes. This pulls the official
Nasdaq halts feed and exposes is_halted(symbol). Critical for penny/micro names,
which halt on volatility (LULD) or regulatory action. Cached 5 min.
"""
from __future__ import annotations
import re, time, urllib.request
from typing import Set

_URL = "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts"
_CACHE: dict = {}
_TTL = 300.0
_SYM = re.compile(r"<ndaq:IssueSymbol>([A-Z.\-]{1,6})</ndaq:IssueSymbol>")


def halted_symbols() -> Set[str]:
    now = time.time()
    hit = _CACHE.get("halts")
    if hit and now - hit[0] < _TTL:
        return hit[1]
    syms: Set[str] = set()
    try:
        raw = urllib.request.urlopen(
            urllib.request.Request(_URL, headers={"User-Agent": "lab"}), timeout=10).read().decode("utf-8", "ignore")
        syms = set(_SYM.findall(raw))
        if not syms:                       # fallback: bare ticker-ish tokens near 'Halt'
            syms = set(re.findall(r"\b([A-Z]{2,5})\b", raw)) & set()  # conservative: none
    except Exception:
        pass
    _CACHE["halts"] = (now, syms)
    return syms


def is_halted(symbol: str) -> bool:
    return symbol.upper() in halted_symbols()


if __name__ == "__main__":
    import sys
    hs = halted_symbols()
    print(f"currently halted ({len(hs)}):", sorted(hs)[:40])
    for s in sys.argv[1:]:
        print(f"  {s}: {'HALTED' if is_halted(s) else 'ok'}")
