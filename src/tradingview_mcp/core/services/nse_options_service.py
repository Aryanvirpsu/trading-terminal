"""NSE India options chain service — F&O option chain, PCR, max pain, OI activity.

Data source: NSE India's public (undocumented) API:
  https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY   (index options)
  https://www.nseindia.com/api/option-chain-equities?symbol=RELIANCE  (stock options)

No official docs, no API key — same "consumer-facing website's own XHR
endpoint" pattern as options_service.py's Yahoo crumb session, but NSE's
bot protection (Akamai) is considerably more aggressive than Yahoo's:

  - It requires a browser-like cookie session (obtained by GETting nseindia.com
    first, same as here) AND
  - Akamai's `_abck` cookie stays in an "unvalidated" state without real JS
    execution, which a plain HTTP client can never satisfy. In practice this
    means calls made from datacenter/cloud IPs fail often (observed ~0-15%
    success rate from a cloud sandbox during development); calls made from a
    residential/office network in India succeed far more often.

Because of that, every failure path here returns an ERROR ENVELOPE (never a
bare exception) with an actionable hint, and successful responses are cached
for a short TTL so a single working fetch covers several quick follow-up
calls. If this is unreliable for you, the accurate fix is a licensed broker
API (Zerodha Kite Connect / Upstox / Angel One) — see BROKER_PROVIDER in
.env.example — not a better scraper.
"""
from __future__ import annotations

import http.cookiejar
import json
import random
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

_TIMEOUT = 12
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_BASE_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}
_INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}

_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 1.2

# Short-TTL cache for successful fetches only (we don't cache failures —
# transient blocks should not poison every subsequent call for a full minute).
_CACHE: Dict[str, tuple] = {}
_CACHE_TTL = 45.0


def _cache_get(key: str) -> Optional[dict]:
    entry = _CACHE.get(key)
    if not entry:
        return None
    ts, payload = entry
    if time.time() - ts > _CACHE_TTL:
        return None
    return payload


def _cache_set(key: str, payload: dict) -> None:
    _CACHE[key] = (time.time(), payload)


def _new_opener() -> urllib.request.OpenerDirector:
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    opener.addheaders = list(_BASE_HEADERS.items())
    return opener


def _fetch_chain_json(symbol: str) -> dict:
    """One attempt: fresh cookie session -> homepage -> option-chain API.

    Raises on any failure (network, HTTP status, bad JSON) — caller retries.
    """
    opener = _new_opener()
    # Cookie-drop step: NSE issues nsit/AKA_A2/_abck/bm_sz cookies here.
    opener.open("https://www.nseindia.com/option-chain", timeout=_TIMEOUT)
    time.sleep(0.4)  # let Akamai's edge settle the session before the XHR call

    endpoint = "option-chain-indices" if symbol in _INDEX_SYMBOLS else "option-chain-equities"
    api_headers = dict(_BASE_HEADERS)
    api_headers["Accept"] = "application/json, text/plain, */*"
    api_headers["Referer"] = "https://www.nseindia.com/option-chain"
    api_headers["X-Requested-With"] = "XMLHttpRequest"

    url = f"https://www.nseindia.com/api/{endpoint}?symbol={symbol}"
    req = urllib.request.Request(url, headers=api_headers)
    with opener.open(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_with_retry(symbol: str) -> dict:
    """Retry _fetch_chain_json with a FRESH session each attempt (reusing a
    session that just got blocked doesn't help — Akamai's block is scored
    per-session/per-fingerprint, not per-request)."""
    last_exc: Optional[BaseException] = None
    for i in range(_RETRY_ATTEMPTS):
        if i > 0:
            time.sleep(_RETRY_BASE_DELAY * (i + random.uniform(0, 0.5)))
        try:
            return _fetch_chain_json(symbol)
        except Exception as e:  # noqa: BLE001
            last_exc = e
            continue
    assert last_exc is not None
    raise RuntimeError(
        f"NSE blocked all {_RETRY_ATTEMPTS} attempts ({type(last_exc).__name__}: {last_exc}). "
        "This is common from cloud/datacenter networks (Akamai bot protection on "
        "nseindia.com) and less common from a residential/office connection in India. "
        "For reliable option-chain data regardless of network, connect a broker API "
        "(Zerodha Kite Connect / Upstox / Angel One) once you have credentials."
    ) from last_exc


def _safe_round(v, n: int = 2):
    if v is None:
        return None
    try:
        return round(float(v), n)
    except (TypeError, ValueError):
        return None


def _normalize_leg(leg: Optional[dict]) -> Optional[dict]:
    if not leg:
        return None
    return {
        "open_interest": leg.get("openInterest") or 0,
        "change_in_oi": leg.get("changeinOpenInterest") or 0,
        "pct_change_in_oi": _safe_round(leg.get("pchangeinOpenInterest"), 2),
        "volume": leg.get("totalTradedVolume") or 0,
        "implied_volatility": _safe_round(leg.get("impliedVolatility"), 2),
        "last_price": _safe_round(leg.get("lastPrice"), 2),
        "change": _safe_round(leg.get("change"), 2),
        "bid_qty": leg.get("bidQty") or 0,
        "bid_price": _safe_round(leg.get("bidprice"), 2),
        "ask_qty": leg.get("askQty") or 0,
        "ask_price": _safe_round(leg.get("askPrice"), 2),
    }


def _compute_pcr(rows: List[dict]) -> Optional[float]:
    total_ce_oi = sum((r.get("CE") or {}).get("open_interest") or 0 for r in rows)
    total_pe_oi = sum((r.get("PE") or {}).get("open_interest") or 0 for r in rows)
    if total_ce_oi <= 0:
        return None
    return round(total_pe_oi / total_ce_oi, 3)


def _compute_max_pain(rows: List[dict]) -> Optional[Dict[str, Any]]:
    """Strike at which option WRITERS (sellers) collectively lose the least
    money if the underlying settles there at expiry — the classic "max pain"
    heuristic for where price tends to gravitate into expiry.
    """
    strikes = [r["strike"] for r in rows if r.get("CE") or r.get("PE")]
    if not strikes:
        return None

    best_strike = None
    best_loss = None
    losses_by_strike: Dict[float, float] = {}

    for settle in strikes:
        total_loss = 0.0
        for r in rows:
            k = r["strike"]
            ce = r.get("CE")
            if ce and settle > k:
                total_loss += (settle - k) * (ce.get("open_interest") or 0)
            pe = r.get("PE")
            if pe and settle < k:
                total_loss += (k - settle) * (pe.get("open_interest") or 0)
        losses_by_strike[settle] = total_loss
        if best_loss is None or total_loss < best_loss:
            best_loss = total_loss
            best_strike = settle

    return {
        "max_pain_strike": best_strike,
        "total_writer_loss_at_max_pain": round(best_loss, 0) if best_loss is not None else None,
    }


def get_nse_option_chain(symbol: str, expiry: Optional[str] = None) -> dict:
    """Fetch NSE option chain (calls + puts) for an index or F&O-eligible stock.

    Args:
        symbol: Index (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50) or
            an F&O-eligible NSE stock symbol (e.g. RELIANCE).
        expiry: Optional expiry date in NSE's own format (e.g. '26-Jun-2026').
            If omitted, returns the nearest expiry. See `available_expiries`.

    Returns:
        Dict with underlying_value, requested_expiry, available_expiries,
        pcr (put/call OI ratio), max_pain, and strikes: list of
        {strike, CE: {...} | None, PE: {...} | None}.
        On failure: {"error": {"code": ..., "message": ...}}.
    """
    sym = symbol.strip().upper()
    cache_key = f"chain:{sym}:{expiry or 'nearest'}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        data = _fetch_with_retry(sym)
    except Exception as e:
        return {
            "symbol": sym,
            "error": {
                "code": "NSE_BLOCKED_OR_UNAVAILABLE",
                "message": str(e),
            },
        }

    records = data.get("records") or {}
    all_expiries: List[str] = records.get("expiryDates") or []
    underlying_value = records.get("underlyingValue")
    raw_rows = records.get("data") or []

    target_expiry = expiry or (all_expiries[0] if all_expiries else None)
    if expiry and expiry not in all_expiries:
        return {
            "symbol": sym,
            "error": {
                "code": "INVALID_EXPIRY",
                "message": f"expiry {expiry!r} not available",
            },
            "available_expiries": all_expiries,
        }

    rows: List[dict] = []
    for raw in raw_rows:
        if target_expiry and raw.get("expiryDate") != target_expiry:
            continue
        strike = raw.get("strikePrice")
        if strike is None:
            continue
        rows.append({
            "strike": strike,
            "CE": _normalize_leg(raw.get("CE")),
            "PE": _normalize_leg(raw.get("PE")),
        })
    rows.sort(key=lambda r: r["strike"])

    result = {
        "symbol": sym,
        "underlying_value": _safe_round(underlying_value, 2),
        "requested_expiry": target_expiry,
        "available_expiries": all_expiries,
        "strike_count": len(rows),
        "pcr_oi": _compute_pcr(rows),
        "max_pain": _compute_max_pain(rows),
        "strikes": rows,
        "source": "NSE India (unofficial public API)",
        "timestamp": records.get("timestamp"),
    }
    _cache_set(cache_key, result)
    return result


def get_nse_unusual_options_activity(
    symbol: str,
    top_n: int = 10,
    min_volume: int = 100,
) -> dict:
    """Rank strikes by today's volume / standing-OI ratio and by OI build-up —
    NSE analogue of the US "unusual options activity" scanner.

    Args:
        symbol: Index or F&O-eligible NSE stock symbol.
        top_n: How many strikes to return, ranked by V/OI descending.
        min_volume: Filter floor for today's volume (drops illiquid noise).

    Returns:
        Dict with underlying_value, pcr_oi, total_call_volume,
        total_put_volume, and `unusual`: top-N contracts by V/OI ratio, each
        annotated with OI build-up direction (Long Buildup / Short Buildup /
        Long Unwinding / Short Covering — classic NSE F&O desk shorthand).
        On failure: {"error": {...}}.
    """
    chain = get_nse_option_chain(symbol)
    if "error" in chain:
        return chain

    ranked: List[Dict[str, Any]] = []
    total_call_vol = 0
    total_put_vol = 0

    for row in chain["strikes"]:
        strike = row["strike"]
        for side, leg in (("call", row.get("CE")), ("put", row.get("PE"))):
            if not leg:
                continue
            vol = leg["volume"] or 0
            oi = leg["open_interest"] or 0
            if side == "call":
                total_call_vol += vol
            else:
                total_put_vol += vol
            if vol < min_volume:
                continue
            ratio = vol / max(oi, 1)
            chg_oi = leg["change_in_oi"] or 0
            price_up = (leg["change"] or 0) > 0
            # OI build-up classification: price direction x OI direction.
            if chg_oi > 0 and price_up:
                buildup = "Long Buildup"
            elif chg_oi > 0 and not price_up:
                buildup = "Short Buildup"
            elif chg_oi < 0 and price_up:
                buildup = "Short Covering"
            elif chg_oi < 0 and not price_up:
                buildup = "Long Unwinding"
            else:
                buildup = "Neutral"
            ranked.append({
                "strike": strike,
                "side": side,
                "volume": vol,
                "open_interest": oi,
                "change_in_oi": chg_oi,
                "v_oi_ratio": round(ratio, 2),
                "last_price": leg["last_price"],
                "implied_volatility": leg["implied_volatility"],
                "oi_buildup": buildup,
            })

    ranked.sort(key=lambda r: r["v_oi_ratio"], reverse=True)

    return {
        "symbol": chain["symbol"],
        "underlying_value": chain["underlying_value"],
        "requested_expiry": chain["requested_expiry"],
        "pcr_oi": chain["pcr_oi"],
        "max_pain": chain["max_pain"],
        "total_call_volume": total_call_vol,
        "total_put_volume": total_put_vol,
        "put_call_volume_ratio": round(total_put_vol / total_call_vol, 2) if total_call_vol else None,
        "filter": {"min_volume": min_volume, "top_n": top_n},
        "unusual": ranked[:max(1, top_n)],
        "source": "NSE India (unofficial public API)",
    }
