"""Research backend for the terminal dashboard.

Universal per-symbol analysis, options ranking, catalysts, news + sentiment,
fundamentals and provider health — reusing EVERY provider already wired in
`lab/` and `src/` (TradingView screener, Yahoo options/quotes, Finnhub,
StockTwits, Google News, FRED, EDGAR, the decision engine).

Design rules pulled straight from the brief:
  * Works on ANY valid US stock/ETF — no hardcoded ticker universe, no hidden caps.
  * Cross-provider symbol normalization (Yahoo `BRK-B` vs Finnhub `BRK.B`).
  * Rate-limit-aware FALLBACK CHAINS; every provider call is time-boxed and cached.
  * Failures are isolated and RETURN A STATE ("throttled"/"missing_key"/
    "unsupported"/"stale"/"empty"/"error") — never a fake-neutral value.
  * Nothing here can block the protective path; it's a separate import surface.
"""
from __future__ import annotations

import collections
import concurrent.futures as _cf
import os
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in ("src", "lab", "automation"):
    sys.path.insert(0, os.path.join(_HERE, "..", _p))
sys.path.insert(0, _HERE)  # so `security_master` imports whether run as dashboard/ cwd or not

import security_master as _SM  # local security-master service (universal search)
try:
    import model_registry as _MR  # provider-independent sentiment/model registry
except Exception:  # never let a model layer problem break research imports
    _MR = None

try:
    import finbert_service as _FB  # FinBERT sentiment (lazy, optional)
except Exception:
    _FB = None

_FB_WARMED = False

# ── Symbol normalization ─────────────────────────────────────────────────────

def canonical(sym: str) -> str:
    """One canonical form: upper, no $. A space or slash between a root and a
    one-char share class becomes a dot (BRK B / BRK/B -> BRK.B); other spaces are
    dropped."""
    return _SM.canonical_query(sym)


def to_yahoo(sym: str) -> str:
    """Map to the Yahoo symbol. Delegates to the security master so a FOREIGN
    exchange suffix is preserved (NOKIA.HE stays NOKIA.HE) while a US share class
    is converted (BRK.B -> BRK-B). The old code broke every foreign listing by
    blindly turning '.' into '-'."""
    return _SM.to_yahoo(sym)


def to_finnhub(sym: str) -> str:
    """Finnhub uses a dot for US share classes: BRK-B -> BRK.B (foreign suffixes
    kept as-is)."""
    s = canonical(sym)
    if "." in s:
        root, _, suf = s.rpartition(".")
        if suf in _SM._FOREIGN_SUFFIXES:
            return s
    return s.replace("-", ".")


def valid_symbol_shape(sym: str) -> bool:
    s = canonical(sym)
    return bool(s) and len(s) <= 8 and all(c.isalnum() or c in ".-" for c in s)


# ── Stale-while-revalidate cache + coalescing + timing + pool ────────────────

# Per-data-type TTLs (seconds). Beyond the TTL a value is served STALE while a
# background refresh runs — the endpoint never blocks on a live provider once warm.
TTL = {"quote": 4, "portfolio": 2, "manage": 3, "regime": 30, "technicals": 20,
       "options": 20, "news": 180, "sentiment": 180, "catalysts": 1800,
       "fundamentals": 21600, "chart": 300, "resolve": 86400, "meta": 86400,
       "earnings": 1800}

_CACHE: Dict[str, Dict[str, Any]] = {}          # key -> {ts, val, refreshing}
_LOCKS: Dict[str, threading.Lock] = {}
_CACHE_LOCK = threading.Lock()
_PROVIDERS: Dict[str, Dict[str, Any]] = {}
_STATS = collections.Counter()                  # hit/stale/miss/refresh/refresh_err
_DIAG = collections.deque(maxlen=300)           # recent {stage, ms, at, extra}
_POOL = _cf.ThreadPoolExecutor(max_workers=16, thread_name_prefix="research")


def _now() -> float:
    return time.time()


def _lock_for(key: str) -> threading.Lock:
    with _CACHE_LOCK:
        lk = _LOCKS.get(key)
        if lk is None:
            lk = _LOCKS[key] = threading.Lock()
        return lk


def swr(key: str, ttl: float, fn: Callable[[], Any], stale_ok: bool = True):
    """Stale-while-revalidate with in-flight coalescing. Returns (value, cache_state)
    where cache_state ∈ live|refreshing|miss. A stale value is returned INSTANTLY and
    refreshed in the background; a cold miss computes once (concurrent callers share)."""
    ent = _CACHE.get(key)
    now = _now()
    if ent and now - ent["ts"] < ttl:
        _STATS["hit"] += 1
        return ent["val"], "live"
    if ent and stale_ok:
        _STATS["stale"] += 1
        if not ent.get("refreshing"):
            ent["refreshing"] = True

            def _bg():
                try:
                    v = fn()
                    _CACHE[key] = {"ts": _now(), "val": v, "refreshing": False}
                    _STATS["refresh"] += 1
                except Exception:  # keep the old value on a failed refresh
                    ent["refreshing"] = False
                    _STATS["refresh_err"] += 1
            _POOL.submit(_bg)
        return ent["val"], "refreshing"
    # Cold miss — compute synchronously under a per-key lock so N concurrent
    # identical requests collapse into ONE provider call.
    with _lock_for(key):
        ent = _CACHE.get(key)
        if ent and _now() - ent["ts"] < ttl:
            _STATS["hit"] += 1
            return ent["val"], "live"
        _STATS["miss"] += 1
        val = fn()
        _CACHE[key] = {"ts": _now(), "val": val, "refreshing": False}
        return val, "miss"


def swr_async(key: str, ttl: float, fn: Callable[[], Any], loading: Any = None):
    """Like swr, but a COLD MISS NEVER BLOCKS: kick the (expensive) compute on the
    background pool and return a 'loading' placeholder immediately. The real value
    lands on a subsequent poll/re-fetch. This is what keeps slow research endpoints
    (decision engine, throttled screener) from ever holding up a request."""
    ent = _CACHE.get(key)
    now = _now()
    if ent and now - ent["ts"] < ttl:
        _STATS["hit"] += 1
        return ent["val"], "live"
    if ent:
        _STATS["stale"] += 1
        if not ent.get("refreshing"):
            ent["refreshing"] = True
            _POOL.submit(lambda: _bg_refresh(key, fn, ent))
        return ent["val"], "refreshing"
    _STATS["miss"] += 1
    pend = key + "::pending"
    if not _CACHE.get(pend):
        _CACHE[pend] = {"ts": now, "val": True, "refreshing": False}

        def _bg():
            try:
                v = fn()
                _CACHE[key] = {"ts": _now(), "val": v, "refreshing": False}
                _STATS["refresh"] += 1
            except Exception:
                _STATS["refresh_err"] += 1
            finally:
                _CACHE.pop(pend, None)
        _POOL.submit(_bg)
    return (loading or {"state": "loading", "reason": "fetching…"}), "loading"


def _bg_refresh(key, fn, ent):
    try:
        v = fn()
        _CACHE[key] = {"ts": _now(), "val": v, "refreshing": False}
        _STATS["refresh"] += 1
    except Exception:
        ent["refreshing"] = False
        _STATS["refresh_err"] += 1


def cached(key: str, ttl: float, fn: Callable[[], Any]) -> Any:
    """Back-compat wrapper — value only."""
    return swr(key, ttl, fn)[0]


def cache_age(key: str) -> Optional[float]:
    ent = _CACHE.get(key)
    return round(_now() - ent["ts"], 1) if ent else None


def cache_state(key: str) -> str:
    ent = _CACHE.get(key)
    if not ent:
        return "miss"
    return "refreshing" if ent.get("refreshing") else "live"


def invalidate(prefix: str = "") -> int:
    """Drop cache entries whose key starts with prefix (targeted, not global)."""
    with _CACHE_LOCK:
        ks = [k for k in _CACHE if k.startswith(prefix)]
        for k in ks:
            _CACHE.pop(k, None)
    return len(ks)


class timed:
    """Context manager: record a stage duration into the diagnostics ring buffer."""
    def __init__(self, stage: str, extra: Any = None):
        self.stage, self.extra = stage, extra

    def __enter__(self):
        self.t = time.perf_counter()
        return self

    def __exit__(self, *a):
        ms = round((time.perf_counter() - self.t) * 1000, 1)
        _DIAG.append({"stage": self.stage, "ms": ms, "extra": self.extra,
                      "at": datetime.now(timezone.utc).isoformat()})


def gather(tasks: Dict[str, Callable[[], Any]], timeout: float = 12.0) -> Dict[str, Any]:
    """Run independent callables CONCURRENTLY on the shared pool; per-task timeout so
    one slow provider can't hold up the rest. Returns name -> result (or {'__err':...})."""
    futs = {n: _POOL.submit(fn) for n, fn in tasks.items()}
    out: Dict[str, Any] = {}
    deadline = time.time() + timeout
    for n, f in futs.items():
        try:
            out[n] = f.result(timeout=max(0.1, deadline - time.time()))
        except Exception as e:  # noqa: BLE001 (timeout or worker error)
            out[n] = {"__err": str(e)[:80] or type(e).__name__}
    return out


def diag_snapshot() -> Dict[str, Any]:
    recent = list(_DIAG)[-60:]
    by_stage: Dict[str, List[float]] = collections.defaultdict(list)
    for d in recent:
        by_stage[d["stage"]].append(d["ms"])
    slowest = sorted(({"stage": s, "avg_ms": round(sum(v) / len(v), 1),
                       "max_ms": max(v), "n": len(v)} for s, v in by_stage.items()),
                     key=lambda x: x["max_ms"], reverse=True)[:12]
    return {"cache": dict(_STATS), "cache_entries": len(_CACHE),
            "slowest": slowest, "recent": recent[-20:]}


_BREAKER: Dict[str, float] = {}   # provider -> cooldown-until timestamp


def _tripped(name: str) -> bool:
    """True if a provider is in a throttle cooldown — skip it and use a fallback
    rather than paying its full timeout again."""
    return _BREAKER.get(name, 0.0) > time.time()


def _trip(name: str, cooldown: float = 45.0) -> None:
    _BREAKER[name] = time.time() + cooldown


def _mark(name: str, status: str, detail: str = "") -> None:
    _PROVIDERS[name] = {"status": status, "detail": detail,
                        "at": datetime.now(timezone.utc).isoformat()}
    if status == "throttled":
        _trip(name)
    elif status == "ok":
        _BREAKER.pop(name, None)


def _guard(name: str, fn: Callable[[], Any], throttle_words=("429", "rate", "throttl", "too many")):
    """Run a provider call, classify the outcome into provider health, return
    (value, status). Never raises."""
    try:
        v = fn()
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        status = "throttled" if any(w in msg for w in throttle_words) else "error"
        _mark(name, status, str(e)[:120])
        return None, status
    if isinstance(v, dict) and v.get("error"):
        msg = str(v["error"]).lower()
        if "key" in msg or "token" in msg or "apikey" in msg:
            _mark(name, "missing_key", str(v["error"])[:120]); return v, "missing_key"
        status = "throttled" if any(w in msg for w in throttle_words) else "error"
        _mark(name, status, str(v["error"])[:120]); return v, status
    _mark(name, "ok", "")
    return v, "ok"


def _box(fn: Callable[[], Any], timeout: float, default=None):
    """Time-box a blocking call so one slow provider can't freeze an endpoint. Runs on
    the SHARED pool and does NOT wait on the abandoned worker — using a `with`
    ThreadPoolExecutor here was the bug that defeated the timeout, because its
    __exit__ shutdown(wait=True) blocked until the slow call finished anyway."""
    fut = _POOL.submit(fn)
    try:
        return fut.result(timeout=timeout)
    except Exception:  # noqa: BLE001 (timeout or worker error) — leave it running, move on
        return default


def _stamp(source: str, key: Optional[str] = None, state: str = "ok") -> Dict[str, Any]:
    return {"source": source, "state": state,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "cache_age_s": cache_age(key) if key else None,
            "cache_state": cache_state(key) if key else None}


# ── Universal search (local security master; instant, no provider call) ──────

def search(query: str, limit: int = 10) -> Dict[str, Any]:
    """Ranked local search over the security master — symbol, company name,
    alias, foreign listing, typo. Sub-150 ms warm, no external provider call."""
    with timed("research.search"):
        return _SM.search(query, limit)


def popular_symbols(limit: int = 12) -> List[Dict[str, Any]]:
    return _SM.popular(limit)


def master_stats() -> Dict[str, Any]:
    return _SM.stats()


def master_refresh() -> Dict[str, Any]:
    """Blocking incremental rebuild of the security master (scheduler/CLI use)."""
    return _SM.refresh_sync()


# ── Scan universe (built from the security master + local filters) ───────────
# Replaces the fixed 73-name list: we filter the ~30k master LOCALLY (active,
# supported exchange, common-stock/ETF/ADR type, USD) down to a bounded, liquid,
# preset-specific pool that the scanner then pre-ranks (cheap) and deep-analyses
# for only the top slice. We do NOT deep-scan 30k names.

_SCAN_EXCH_MAP = {  # security-master exchange label -> TradingView screener exchange
    "NASDAQ": "NASDAQ", "NYSE": "NYSE", "NYSE American": "AMEX", "NYSE Arca": "AMEX",
    "Cboe BZX": "AMEX", "Cboe BYX": "AMEX", "AMEX": "AMEX", "BATS": "AMEX",
}
_SCAN_ELIGIBLE_TYPES = {"common stock", "adr", "etp", "etf", "reit"}
# A few liquid speculative small/mid names for the labelled speculative preset.
_SPEC_SEED = ["SOFI", "PLTR", "MARA", "RIOT", "AI", "IONQ", "RIVN", "LCID", "CHPT",
              "AFRM", "DKNG", "RBLX", "HOOD", "SNAP", "PINS", "GME", "AMC"]


def _to_tv_ticker(rec: Optional[Dict[str, Any]]) -> Optional[str]:
    """Map a master record to a 'EXCH:SYM' TradingView ticker, normalising the
    raw MIC (e.g. 'XNAS') to a friendly exchange first. Returns None for venues
    the US screener doesn't cover (OTC, foreign)."""
    if not rec:
        return None
    raw = (rec.get("exchange") or "").upper()
    friendly = _SM._MIC_NAME.get(raw, rec.get("exchange") or "")
    exch = _SCAN_EXCH_MAP.get(friendly)
    if not exch:
        return None
    sym = (rec.get("symbol") or "")
    if not sym or sym.count(".") > 1:  # skip odd multi-part symbols
        return None
    return f"{exch}:{sym}"


def build_scan_universe(preset: str = "liquid", limit: int = 120) -> Dict[str, Any]:
    """Return a bounded, preset-specific TradingView-ticker pool drawn from the
    local security master. Popularity (the master's curated liquidity prior) is
    used as the local ranking key since per-symbol volume isn't stored locally —
    the scanner's batched pre-rank then does the real momentum ranking."""
    _SM.ensure_loaded()
    preset = (preset or "liquid").lower()
    speculative = preset in ("small_cap_speculative", "speculative")

    # Always seed with the strategy's proven curated liquid/optionable set.
    try:
        from tradingview_mcp.core.services.strategy_service import UNIVERSE as _BASE
        base = list(_BASE)
    except Exception:
        base = []

    # Local filters: eligible exchange + type + USD + popularity-ranked.
    eligible = []
    for r in _SM._INDEX:
        if (r.get("currency") or "USD") != "USD":
            continue
        if (r.get("type") or "").lower() not in _SCAN_ELIGIBLE_TYPES:
            continue
        tv = _to_tv_ticker(r)
        if not tv:
            continue
        eligible.append((r.get("popularity", 0), tv, r))
    eligible.sort(key=lambda x: x[0], reverse=True)

    def _pool_from(records):
        seen, out = set(), []
        for pop, tv, r in records:
            sym = r["symbol"]
            if sym in seen:
                continue
            seen.add(sym)
            out.append(tv)
        return out

    if preset == "etf":
        etfs = [(p, tv, r) for (p, tv, r) in eligible if (r.get("type") or "").lower() in ("etp", "etf")]
        pool = _pool_from(etfs)
        base = []  # ETF preset is ETFs only — don't inherit the base stocks
    elif speculative:
        # Curated speculative seed (clearly labelled), mapped through the master.
        pool = []
        for s in _SPEC_SEED:
            rec = _SM._BY_SYMBOL.get(s)
            tv = _to_tv_ticker(rec) if rec else None
            if tv:
                pool.append(tv)
        base = []  # speculative preset is its own labelled pool
    else:  # liquid / momentum / mean_reversion / options_eligible / default
        pool = _pool_from(eligible)

    # Merge the curated base first (dedup by bare symbol), cap to limit.
    merged, seen = [], set()
    for tv in (base + pool):
        sym = tv.partition(":")[2]
        if sym in seen:
            continue
        seen.add(sym)
        merged.append(tv)
        if len(merged) >= limit:
            break

    return {"preset": preset, "tickers": merged, "size": len(merged),
            "speculative": speculative, "limit": limit,
            "source": "security master (local filter) + curated liquid base",
            "note": ("Speculative small/mid-caps — higher risk, wider spreads."
                     if speculative else "Liquid, optionable US names.")}


# ── Symbol resolution (fallback chain) ───────────────────────────────────────

def resolve_symbol(query: str) -> Dict[str, Any]:
    """Cached wrapper: valid symbols live 24h, invalid ones 120s (so a typo doesn't
    re-pay the provider chain on every keystroke/prefetch, but a real ticker that was
    briefly throttled can be retried soon)."""
    sym = canonical(query)
    key = f"resolve:{sym}"
    ent = _CACHE.get(key)
    if ent:
        ttl = TTL["resolve"] if ent["val"].get("valid") else 120
        if time.time() - ent["ts"] < ttl:
            _STATS["hit"] += 1
            return ent["val"]
    val = _resolve_compute(query)
    _CACHE[key] = {"ts": time.time(), "val": val, "refreshing": False}
    return val


def _resolve_compute(query: str) -> Dict[str, Any]:
    """Resolve ANY query — symbol, company name, alias, foreign listing — to a
    canonical record with a live price. The security master supplies identity
    (symbol/name/exchange/type/currency) INSTANTLY; a provider chain
    (Finnhub -> Yahoo -> TradingView) then attaches a live price. Distinguishes
    unsupported from throttled."""
    sym = canonical(query)

    # 1) Identity via the local security master (handles "Apple", "Google",
    #    "BRK B", "NOKIA.HE" — none of which are a valid ticker *shape*).
    master = None
    try:
        hit = _SM.search(query, 1)
        if hit.get("results"):
            top = hit["results"][0]
            # Accept a strong match (exact/alias/prefix/name) as the identity.
            if top.get("score", 0) >= 300:
                master = top
                sym = top["symbol"]
    except Exception:
        master = None

    if master is None and not valid_symbol_shape(sym):
        return {"query": query, "valid": False, "state": "unsupported",
                "reason": "no matching security (try a symbol or company name)"}

    tried: List[str] = []
    name = (master or {}).get("name")
    exch = (master or {}).get("exchange")
    price = change = None
    src = None
    yahoo_sym = (master or {}).get("yahoo") or to_yahoo(sym)

    def _finnhub():
        import finnhub_data
        q = finnhub_data.quote(to_finnhub(sym))
        if isinstance(q, dict) and q.get("c"):
            return q
        raise ValueError("no quote")

    # Foreign listings (e.g. NOKIA.HE) have no Finnhub US quote — skip straight to
    # Yahoo, which does support the exchange-suffixed symbol.
    is_foreign = "." in sym and sym.rpartition(".")[2] in _SM._FOREIGN_SUFFIXES
    q, st = (None, "skip") if is_foreign else _guard("finnhub", lambda: cached(f"res_q:{sym}", 60, _finnhub))
    tried.append(f"finnhub:{st}")
    if st == "ok" and q:
        price = q.get("c"); change = q.get("dp"); src = "finnhub"
        if not name:  # only fill identity the master didn't already provide
            prof, _ = _guard("finnhub", lambda: __import__("finnhub_data").profile(to_finnhub(sym)))
            if isinstance(prof, dict):
                name = name or prof.get("name"); exch = exch or prof.get("exchange")

    if price is None:
        def _yahoo():
            from tradingview_mcp.core.services.yahoo_finance_service import get_price
            p = get_price(yahoo_sym)
            if isinstance(p, dict) and p.get("price"):
                return p
            raise ValueError("no price")
        y, st = _guard("yahoo", lambda: cached(f"res_y:{sym}", 60, _yahoo))
        tried.append(f"yahoo:{st}")
        if st == "ok" and y:
            price = y.get("price"); change = y.get("change_pct"); src = "yahoo"
            exch = exch or y.get("exchange")

    if price is None and not _tripped("tradingview"):
        def _tv():
            from tradingview_mcp.core.services import strategy_service as ss
            a = ss._analyze_cached(sym, "NASDAQ", "1D")
            pd = (a or {}).get("price_data") or {}
            if pd.get("current_price"):
                return pd
            raise ValueError("no tv price")
        t, st = _guard("tradingview", lambda: _box(_tv, 6))
        tried.append(f"tradingview:{st}")
        if st == "ok" and t:
            price = t.get("current_price"); src = "tradingview"

    if price is None:
        # Could be a bad symbol OR every provider throttled — say which. If the
        # master knew the identity, surface it even without a live price (so the
        # UI can still name the security and show a "price unavailable" state).
        throttled = any(s.endswith("throttled") for s in tried)
        base = {"query": query, "symbol": sym,
                "state": "throttled" if throttled else ("no_price" if master else "unsupported"),
                "sources_tried": tried}
        if master:
            base.update(valid=True, name=name or sym, price=None, change_pct=None,
                        exchange=exch, type=master.get("type"), currency=master.get("currency"),
                        country=master.get("country"), source="security_master",
                        reason="identity from security master; live price temporarily unavailable")
        else:
            base.update(valid=False,
                        reason=("all price providers throttled — try again shortly" if throttled
                                else "no price from any provider (likely not a US-listed symbol)"))
        return base

    return {"query": query, "symbol": sym, "valid": True, "name": name or sym,
            "price": price, "change_pct": change, "exchange": exch, "source": src,
            "type": (master or {}).get("type"), "currency": (master or {}).get("currency"),
            "country": (master or {}).get("country"), "cik": (master or {}).get("cik"),
            "sources_tried": tried, "state": "ok"}


# ── Overview / decision-engine analysis ──────────────────────────────────────

def overview(sym: str, direction: str = "LONG", balance: float = 500.0) -> Dict[str, Any]:
    sym = canonical(sym)
    # Fast-fail invalid tickers instead of grinding the full engine.
    res = resolve_symbol(sym)
    if not res.get("valid"):
        return {"symbol": sym, "state": res.get("state", "unsupported"),
                "reason": res.get("reason", "symbol could not be resolved")}
    val, _cs = swr_async(f"ov:{sym}:{direction}", 300, lambda: _overview_compute(sym, direction, balance),
                         loading={"symbol": sym, "state": "loading", "reason": "running the decision engine…"})
    return val


def summary(sym: str, direction: str = "LONG", balance: float = 500.0) -> Dict[str, Any]:
    """FAST decision summary for instant stock-page paint — trend + regime +
    scenarios only, returned with a completion status. The full multi-family
    analysis is `overview()`; both share the cached base TA (no duplicate calc)."""
    sym = canonical(sym)
    # Cheap local validity check — the summary's base-TA load resolves price/validity
    # itself, so we skip the extra resolve() network round-trip for speed.
    if not _SM.lookup(sym) and not valid_symbol_shape(sym):
        return {"symbol": sym, "state": "unsupported",
                "reason": "no matching security (try a symbol or company name)"}
    val, _cs = swr(f"sum:{sym}:{direction}", 60, lambda: _summary_compute(sym, direction, balance))
    return val


def _summary_compute(sym: str, direction: str, balance: float) -> Dict[str, Any]:
    import decision_engine
    exch = (_SM.lookup(sym) or {}).get("exchange") or "NASDAQ"
    r = decision_engine.evaluate_summary(sym, exch, direction, balance)
    if not r.get("price"):
        return {"symbol": sym, "state": r.get("data_state", "error"),
                "reason": r.get("reason", "no price"), "analysis_status": "summary"}
    r["state"] = "ok"
    r["scenarios"] = _scenarios(r)
    r["provenance"] = _stamp("decision_engine summary (TradingView/yfinance + regime)",
                             f"sum:{sym}:{direction}")
    return r


def _overview_compute(sym: str, direction: str, balance: float) -> Dict[str, Any]:
    import decision_engine
    # Pass the RIGHT exchange from the security master so the engine hits the
    # correct TradingView venue once, instead of a blind NASDAQ->NYSE double-hit.
    exch = (_SM.lookup(sym) or {}).get("exchange") or "NASDAQ"
    r = decision_engine.evaluate(sym, exch, direction, balance, evaluate_option=True)
    if r.get("decision") == "REJECT" and "reason" in r and "price" not in r:
        return {"symbol": sym, "state": "error", "reason": r.get("reason")}
    r["state"] = "ok"
    r["scenarios"] = _scenarios(r)
    r["what_changed"] = _what_changed(sym, direction, r)
    r["provenance"] = _stamp("decision_engine (Yahoo/TV/Finnhub/EDGAR/FRED/StockTwits)",
                             f"ov:{sym}:{direction}")
    return r


def _scenarios(r: Dict[str, Any]) -> Dict[str, Any]:
    """Compact bull / base / bear price scenarios anchored on the engine's own
    entry, stop and target. Probabilities come from the engine's authoritative,
    mutually-exclusive `scenario_probabilities` (a double-barrier ATR model that
    SUMS TO 100% and is NOT derived from P(dir)) — a structured payoff view, not a
    forecast of a single 'guaranteed' price."""
    price = r.get("price")
    stop = r.get("stop")
    target = r.get("target")
    if not price:
        return {"state": "insufficient"}

    def _pct(to):
        return round((to / price - 1) * 100, 1) if (to and price) else None

    # Bull extends beyond the first target by the same reward leg; bear = stop.
    bull_to = round(target + (target - price), 2) if (target and target > price) else (
        round(price * 1.08, 2))
    base_to = target or round(price * 1.02, 2)
    bear_to = stop or round(price * 0.95, 2)

    # Authoritative, mutually-exclusive probabilities from the engine (percent).
    sp = r.get("scenario_probabilities") or {}
    bull_p = sp.get("bull"); base_p = sp.get("base"); bear_p = sp.get("bear")
    if bull_p is None or base_p is None or bear_p is None:
        # Fallback ONLY if the engine didn't provide them: still mutually exclusive
        # and normalised to exactly 100 (never overlapping P(dir)-derived numbers).
        p_dir = r.get("p_direction") or 0.5
        raw = {"bull": max(0.01, p_dir * 0.45), "base": max(0.01, p_dir * 0.55),
               "bear": max(0.01, 1 - p_dir)}
        tot = sum(raw.values()) or 1e-9
        bull_p = round(raw["bull"] / tot * 100, 1)
        base_p = round(raw["base"] / tot * 100, 1)
        bear_p = round(100.0 - bull_p - base_p, 1)
        method = "fallback normalisation (engine probabilities unavailable)"
        sp_conf = None
    else:
        method = sp.get("method"); sp_conf = sp.get("confidence")

    return {
        "state": "ok",
        "method": method, "confidence": sp_conf,
        "total_pct": round(bull_p + base_p + bear_p, 1),   # must be 100.0
        "bull": {"target": bull_to, "pct": _pct(bull_to),
                 "note": "momentum continues; first target taken out",
                 "prob_pct": bull_p, "prob": round(bull_p / 100, 3)},
        "base": {"target": base_to, "pct": _pct(base_to),
                 "note": "reaches the engine's primary target",
                 "prob_pct": base_p, "prob": round(base_p / 100, 3)},
        "bear": {"target": bear_to, "pct": _pct(bear_to),
                 "note": "thesis invalidated; protective stop hit",
                 "prob_pct": bear_p, "prob": round(bear_p / 100, 3)},
        "invalidation": r.get("avoid_if") or (["price closes below the stop"] if stop else []),
    }


_SNAP_PATH = os.path.expanduser("~/.tradingview_mcp_data/analysis_snapshots.json")
_SNAP_LOCK = threading.Lock()


def _what_changed(sym: str, direction: str, r: Dict[str, Any]) -> Dict[str, Any]:
    """Diff this analysis against the previously stored one for the symbol, so the
    UI can show WHAT CHANGED (decision flips, quality / P(dir) / price moves)."""
    import json
    cur = {"decision": r.get("decision"), "quality": r.get("confidence_quality"),
           "p_direction": r.get("p_direction"), "price": r.get("price"),
           "at": datetime.now(timezone.utc).isoformat()}
    prev = None
    key = f"{sym}:{direction}"
    try:
        with _SNAP_LOCK:
            snaps = {}
            if os.path.exists(_SNAP_PATH):
                try:
                    snaps = json.load(open(_SNAP_PATH, encoding="utf-8"))
                except Exception:
                    snaps = {}
            prev = snaps.get(key)
            snaps[key] = cur
            try:
                json.dump(snaps, open(_SNAP_PATH, "w", encoding="utf-8"))
            except Exception:
                pass
    except Exception:
        prev = None
    if not prev:
        return {"state": "first_run", "note": "first analysis on record for this symbol"}
    changes = []
    if prev.get("decision") != cur["decision"]:
        changes.append(f"decision {prev.get('decision')} → {cur['decision']}")
    for fld, lab, dp in (("quality", "quality", 0), ("p_direction", "P(dir)", 2), ("price", "price", 2)):
        a, b = prev.get(fld), cur.get(fld)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and a:
            d = b - a
            if abs(d) > (0.01 if fld != "price" else max(0.01, abs(a) * 0.003)):
                changes.append(f"{lab} {round(a, dp)} → {round(b, dp)} ({'+' if d >= 0 else ''}{round(d, dp)})")
    return {"state": "ok" if changes else "unchanged",
            "since": prev.get("at"), "changes": changes,
            "note": "no material change since last analysis" if not changes else None}


def registry_status() -> Dict[str, Any]:
    return _MR.status() if _MR is not None else {"state": "unavailable"}


# ── Technicals (raw screener analysis) ───────────────────────────────────────

def technicals(sym: str) -> Dict[str, Any]:
    sym = canonical(sym)
    val, _cs = swr_async(f"tech:{sym}", TTL["technicals"], lambda: _technicals_compute(sym),
                         loading={"symbol": sym, "state": "loading", "reason": "loading indicators…"})
    return val


def _technicals_compute(sym: str) -> Dict[str, Any]:
    sym = canonical(sym)

    def _tv():
        from tradingview_mcp.core.services import strategy_service as ss
        for exch in ("NASDAQ", "NYSE", "AMEX"):
            a = ss._analyze_cached(sym, exch, "1D")
            if isinstance(a, dict) and "error" not in a:
                return a
        raise ValueError("screener miss")

    # Circuit breaker: if TradingView is in a throttle cooldown, skip it entirely
    # (paying an 8s timeout on every ticker is what made cold technicals ~30s).
    if _tripped("tradingview"):
        a, st = None, "throttled"
    else:
        a, st = _guard("tradingview", lambda: _box(lambda: cached(f"ta:{sym}", 300, _tv), 8))
    degraded, src = None, "TradingView screener"
    if st != "ok" or not a:
        # free fallback — fallback_ta returns the SAME schema as the screener
        def _fb():
            import fallback_ta
            return fallback_ta.analysis(to_yahoo(sym))
        a, st2 = _guard("yahoo", lambda: _box(lambda: cached(f"ta:{sym}", 300, _fb), 8))
        if st2 != "ok" or not isinstance(a, dict) or "error" in a:
            return {"symbol": sym, "state": st if st != "ok" else "throttled",
                    "reason": "no technicals (TradingView + yfinance both unavailable)"}
        degraded = "TradingView unavailable — reduced indicator set (yfinance)"
        src = "yfinance fallback"
    pd = a.get("price_data") or {}
    out = {
        "symbol": sym, "state": "ok",
        "price": pd.get("current_price"),
        "change_pct": pd.get("change_percent") if pd.get("change_percent") is not None else pd.get("change_pct"),
        "trend_state": a.get("trend_state"),
        "rsi": a.get("rsi"), "atr": a.get("atr"), "macd": a.get("macd"),
        "moving_averages": a.get("moving_averages") or a.get("mas"),
        "support_resistance": a.get("support_resistance") or a.get("pivots"),
        "market_sentiment": a.get("market_sentiment"),
        "adx": a.get("adx"), "volume": a.get("volume") or a.get("volume_data"),
        "provenance": _stamp(src, f"ta:{sym}"),
    }
    if degraded:
        out["degraded"] = degraded
    return out


# ── Options finder + ranking ─────────────────────────────────────────────────

def _rank_contract(c: dict, underlying: float, side: str, dte: int) -> Dict[str, Any]:
    """Score a contract 0-100 on liquidity, spread, moneyness, IV; list pass/fail
    reasons. Higher = more tradeable, not a directional call."""
    strike = c.get("strike") or 0
    bid, ask = c.get("bid") or 0, c.get("ask") or 0
    last = c.get("last_price") or 0
    mid = round((bid + ask) / 2, 2) if (bid and ask) else last
    oi = c.get("open_interest") or 0
    vol = c.get("volume") or 0
    iv = c.get("implied_volatility")
    spread_pct = round((ask - bid) / mid * 100, 1) if (bid and ask and mid) else None
    otm_pct = round((strike - underlying) / underlying * 100, 1) if underlying else None
    if side == "PUT" and otm_pct is not None:
        otm_pct = -otm_pct  # express as OTM magnitude for puts too
    breakeven = round(strike + mid, 2) if side == "CALL" else round(strike - mid, 2)

    passes, fails = [], []
    score = 0.0
    # Liquidity: OI
    if oi >= 1000: score += 30; passes.append(f"deep OI {oi:,}")
    elif oi >= 250: score += 18; passes.append(f"OK OI {oi:,}")
    else: fails.append(f"thin OI {oi:,}")
    # Volume today
    if vol >= 200: score += 15; passes.append(f"active vol {vol:,}")
    elif vol >= 25: score += 8
    else: fails.append(f"low vol {vol}")
    # Spread
    if spread_pct is None: fails.append("no two-sided quote")
    elif spread_pct <= 8: score += 30; passes.append(f"tight spread {spread_pct}%")
    elif spread_pct <= 20: score += 15; passes.append(f"fair spread {spread_pct}%")
    else: fails.append(f"wide spread {spread_pct}%")
    # Moneyness (near-money preferred for delta)
    if otm_pct is not None:
        if abs(otm_pct) <= 3: score += 20; passes.append("near the money")
        elif abs(otm_pct) <= 7: score += 12; passes.append(f"{abs(otm_pct)}% OTM")
        else: fails.append(f"far {abs(otm_pct)}% OTM (lottery)")
    # IV sanity (very high IV = expensive/pinned)
    if iv is not None:
        ivp = round(iv * 100, 1)
        if ivp > 120: fails.append(f"very high IV {ivp}%")
        else: score += 5
    grade = ("A" if score >= 80 else "B" if score >= 60 else
             "C" if score >= 40 else "D")
    return {
        **c, "mid": mid, "spread_pct": spread_pct, "otm_pct": otm_pct,
        "breakeven": breakeven, "dte": dte, "liquidity_score": round(score),
        "grade": grade, "passes": passes, "fails": fails,
        "tradeable": score >= 60 and not any("wide spread" in f or "no two-sided" in f for f in fails),
    }


def options(sym: str, expiry: Optional[str] = None, side: str = "CALL",
            max_contracts: int = 12) -> Dict[str, Any]:
    sym = canonical(sym)
    side = (side or "CALL").upper()

    def _chain():
        from tradingview_mcp.core.services.options_service import get_options_chain
        return get_options_chain(to_yahoo(sym), expiry)

    ch, st = _guard("yahoo", lambda: cached(f"opt:{sym}:{expiry}", 120, _chain))
    if st != "ok" or not ch:
        return {"symbol": sym, "state": st if st != "ok" else "error",
                "reason": "options chain unavailable"}
    if ch.get("error"):
        low = str(ch["error"]).lower()
        state = "unsupported" if "no options" in low or "not available" in low else "error"
        return {"symbol": sym, "state": state, "reason": ch["error"],
                "available_expiries": ch.get("available_expiries", [])}
    underlying = ch.get("underlying_price")
    exp = ch.get("requested_expiry")
    try:
        dte = max(0, (datetime.strptime(exp, "%Y-%m-%d").date() - datetime.now(timezone.utc).date()).days) if exp else None
    except Exception:
        dte = None
    contracts = ch.get("calls" if side == "CALL" else "puts", []) or []
    if not contracts:
        return {"symbol": sym, "state": "empty", "underlying_price": underlying,
                "requested_expiry": exp, "available_expiries": ch.get("available_expiries", []),
                "reason": f"no {side.lower()} contracts listed for {exp or 'nearest expiry'}"}
    from tradingview_mcp.core.services.options_grading import grade_contract
    ranked = [grade_contract(c, underlying or 0, side, dte or 7) for c in contracts]
    ranked.sort(key=lambda x: x["liquidity_score"], reverse=True)
    return {
        "symbol": sym, "state": "ok", "side": side, "underlying_price": underlying,
        "underlying_change_pct": ch.get("underlying_change_pct"),
        "requested_expiry": exp, "dte": dte,
        "available_expiries": ch.get("available_expiries", []),
        "contracts": ranked[:max_contracts],
        "tradeable_count": sum(1 for r in ranked if r["tradeable"]),
        "provenance": _stamp("Yahoo Finance options", f"opt:{sym}:{expiry}"),
        "note": "Ranking is LIQUIDITY/structure quality, not a buy signal. Execution stays manual.",
    }


# ── Catalysts (per-ticker) ───────────────────────────────────────────────────

def _score_headline(text: str):
    try:
        from catalyst_scan import _COMPILED
    except Exception:
        return 0, "neutral", []
    score, bull, bear, tags = 0, 0, 0, []
    for rx, w, d in _COMPILED:
        if rx.search(text):
            score += w; tags.append(d)
            if d == "bull": bull += w
            elif d == "bear": bear += w
    lean = "bullish" if bull > bear else ("bearish" if bear > bull else ("event" if tags else "neutral"))
    return score, lean, tags


def catalysts(sym: str) -> Dict[str, Any]:
    sym = canonical(sym)
    fh = to_finnhub(sym)
    items: List[Dict[str, Any]] = []
    sources_used, states = [], []

    # Three independent providers, concurrent (was sequential).
    res = gather({
        "gn": lambda: cached(f"gn:{sym}", TTL["catalysts"], lambda: __import__("news_feeds").google_news(sym, 25)),
        "fn": lambda: cached(f"fnnews:{sym}", TTL["catalysts"], lambda: __import__("finnhub_data").company_news(fh, 7)),
        "earn": lambda: cached(f"earn:{sym}", TTL["earnings"], lambda: __import__("finnhub_data").earnings_calendar(fh)),
    }, timeout=10.0)

    gn = res.get("gn") if isinstance(res.get("gn"), list) else []
    states.append("google_news:" + ("ok" if gn else "empty"))
    if gn:
        sources_used.append("Google News")
        for it in gn:
            title = it.get("title", "")
            sc, lean, tags = _score_headline(f"{title} {it.get('summary','')}")
            items.append({"title": title, "url": it.get("url"), "provider": "Google News",
                          "source": _extract_src(title), "ts": None,
                          "catalyst_score": sc, "sentiment": lean, "tags": tags})

    fn = res.get("fn") if isinstance(res.get("fn"), list) else []
    states.append("finnhub_news:" + ("ok" if fn else "empty"))
    if fn:
        sources_used.append("Finnhub")
        for it in fn[:25]:
            title = it.get("headline", "")
            sc, lean, tags = _score_headline(f"{title} {it.get('summary','')}")
            ts = it.get("datetime")
            items.append({"title": title, "url": it.get("url"), "provider": "Finnhub",
                          "source": it.get("source"),
                          "ts": datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None,
                          "catalyst_score": sc, "sentiment": lean, "tags": tags})

    earn = None
    e = res.get("earn") if isinstance(res.get("earn"), list) else []
    states.append("earnings:" + ("ok" if e else "empty"))
    if e:
        fut = [x for x in e if x.get("date", "") >= datetime.now(timezone.utc).date().isoformat()]
        earn = (sorted(fut, key=lambda x: x["date"])[0] if fut else sorted(e, key=lambda x: x.get("date", ""))[-1])

    items = _dedup(items)
    items.sort(key=lambda x: (x["catalyst_score"], x["ts"] or ""), reverse=True)
    if not items:
        thr = any(s.endswith("throttled") for s in states)
        return {"symbol": sym, "state": "throttled" if thr else "empty",
                "reason": ("news providers throttled" if thr else "no recent news found for this ticker"),
                "sources_tried": states}

    # Upgrade per-headline sentiment with the model registry (FinBERT if enabled,
    # else the calibrated lexical scorer) and compute ONE relevance/recency/
    # source-weighted aggregate — not a blind average of every mention.
    aliases = _aliases_for(sym)
    agg = None
    backend = "keyword"
    if _MR is not None:
        try:
            # If FinBERT is enabled + available, use hybrid scoring (70% FinBERT +
            # 30% lexical) for better accuracy on regulatory/legal/ambiguous headlines.
            # Otherwise fall back to model_registry.sentiment() (lexical-only).
            use_finbert = (_FB is not None and _FB.enabled() and _FB.available())
            titles = [it["title"] for it in items]
            if use_finbert:
                labels = _FB.score_hybrid(titles)
            else:
                labels = _MR.sentiment(titles)
            for it, se in zip(items, labels):
                it["sentiment"] = {"positive": "bullish", "negative": "bearish"}.get(se["label"], "neutral")
                it["polarity"] = se.get("score", 0.0)
                it["probs"] = se.get("probs")
                it["relevance"] = round(_MR._relevance(it["title"], sym, aliases), 2)
                it["sentiment_backend"] = se.get("backend", "lexical")
                # Hybrid results carry per-headline disagreement metadata.
                if "disagreement" in se:
                    it["finbert_label"] = se.get("finbert_label")
                    it["lexical_label"] = se.get("lexical_label")
                    it["disagreement"] = se["disagreement"]
            backend = labels[0]["backend"] if labels else "lexical"
            agg = _MR.aggregate_news_sentiment(items, sym, aliases)

            # Background-warm FinBERT so subsequent calls are fast (never blocks).
            global _FB_WARMED
            if _FB is not None and not _FB_WARMED:
                _FB.warm_async()
                _FB_WARMED = True
        except Exception:
            agg = None

    bull = sum(1 for i in items if i["sentiment"] == "bullish")
    bear = sum(1 for i in items if i["sentiment"] == "bearish")
    lean = (agg["label"] if agg and agg.get("state") == "ok"
            else ("bullish" if bull > bear else ("bearish" if bear > bull else "mixed")))
    return {"symbol": sym, "state": "ok", "items": items[:30],
            "catalyst_count": sum(1 for i in items if i["catalyst_score"] > 0),
            "earnings": earn, "lean": lean, "news_sentiment": agg, "model_backend": backend,
            "sources_used": sources_used, "sources_tried": states,
            "provenance": _stamp(" + ".join(sources_used) or "news", f"gn:{sym}")}


def _aliases_for(sym: str) -> List[str]:
    """Company-name tokens the ticker is known by — used for news relevance scoring."""
    rec = _SM.lookup(sym) or {}
    out = []
    name = rec.get("name") or ""
    tok = [t for t in name.replace(",", " ").replace(".", " ").split()
           if len(t) > 2 and t.lower() not in ("inc", "corp", "the", "company", "group",
                                               "holdings", "ltd", "plc", "class", "corporation")]
    if tok:
        out.append(tok[0].lower())  # e.g. "apple", "nvidia"
    return out


def _extract_src(title: str) -> Optional[str]:
    # Google News titles end with " - Source"
    return title.rsplit(" - ", 1)[-1] if " - " in title else None


def _dedup(items: List[dict]) -> List[dict]:
    seen, out = set(), []
    for it in items:
        key = (it.get("title") or "").lower().strip()[:80]
        if key and key not in seen:
            seen.add(key); out.append(it)
    return out


# ── News + sentiment (stock / sector / market, never fake-neutral) ───────────

_SECTOR_ETF = {
    "technology": "XLK", "semiconductors": "SMH", "software": "IGV", "internet": "XLC",
    "financial": "XLF", "bank": "XLF", "insurance": "XLF", "energy": "XLE", "oil": "XLE",
    "health": "XLV", "pharma": "XLV", "biotech": "XBI", "consumer": "XLY",
    "retail": "XLY", "industrial": "XLI", "materials": "XLB", "utilities": "XLU",
    "real estate": "XLRE", "communication": "XLC", "auto": "XLY", "airline": "XLI",
}


def _sector_etf_for(industry: Optional[str]) -> Optional[str]:
    if not industry:
        return None
    low = industry.lower()
    for k, etf in _SECTOR_ETF.items():
        if k in low:
            return etf
    return None


def _market_regime():
    return __import__("tradingview_mcp.core.services.strategy_service",
                      fromlist=["market_regime"]).market_regime()


def news_sentiment(sym: str) -> Dict[str, Any]:
    # Cache the WHOLE aggregated result (not just sub-calls) so the endpoint returns
    # instantly when warm — otherwise the sector-ETF technicals (throttle-prone
    # TradingView) is re-attempted and time-boxed on every request.
    sym = canonical(sym)
    val, _cs = swr_async(f"sentiment:{sym}", TTL["sentiment"], lambda: _sentiment_compute(sym),
                         loading={"symbol": sym, "state": "loading", "reason": "aggregating sentiment…"})
    return val


def _sentiment_compute(sym: str) -> Dict[str, Any]:
    sym = canonical(sym)
    fh = to_finnhub(sym)
    out: Dict[str, Any] = {"symbol": sym}

    # Fan out every INDEPENDENT provider concurrently (was sequential → 48s cold).
    # Each is wrapped in SWR so a warm cache returns instantly and a stale value is
    # served while it refreshes in the background.
    with timed("sentiment.gather"):
        res = gather({
            "news": lambda: swr(f"catsig:{sym}", TTL["sentiment"], lambda: __import__("news_feeds").catalyst_signal(sym)),
            "social": lambda: swr(f"stw:{sym}", TTL["sentiment"], lambda: __import__("social_sentiment").stocktwits(sym)),
            "analyst": lambda: swr(f"an:{sym}", TTL["sentiment"], lambda: __import__("finnhub_data").analyst_signal(fh)),
            "profile": lambda: swr(f"prof:{sym}", TTL["fundamentals"], lambda: __import__("finnhub_data").profile(fh)),
            "regime": lambda: swr("regime", TTL["regime"], _market_regime),
            "macro": lambda: swr("macro", TTL["fundamentals"], lambda: __import__("fred").macro_signal("LONG")),
        }, timeout=6.0)  # strict: partial sentiment beats a 16s hang (a slow provider is dropped)

    def _val(name):
        r = res.get(name)
        if isinstance(r, tuple):  # (value, cache_state)
            return r[0]
        return None if (isinstance(r, dict) and "__err" in r) else r

    # STOCK-SPECIFIC
    stock: Dict[str, Any] = {}
    nf = _val("news")
    stock["news"] = {"state": "ok", **nf} if isinstance(nf, dict) and nf.get("dir") is not None else {"state": "error" if res.get("news", {}).get("__err") else "empty"}
    stw = _val("social")
    if isinstance(stw, dict) and stw.get("available"):
        tot = stw.get("labeled") or 0
        net = round((stw["bullish"] - stw["bearish"]) / tot, 2) if tot else None
        stock["social"] = {"state": "ok" if tot >= 3 else "thin", "bullish": stw["bullish"],
                           "bearish": stw["bearish"], "messages": stw["messages"], "net": net, "source": "StockTwits"}
    else:
        stock["social"] = {"state": "empty", "source": "StockTwits"}
    an = _val("analyst")
    stock["analyst"] = {"state": "ok", **an} if isinstance(an, dict) and an.get("dir") is not None else {"state": "empty"}

    parts = []
    if stock["news"].get("state") == "ok":
        parts.append(("news", stock["news"]["dir"], stock["news"].get("conf", 0.3)))
    if stock["social"].get("state") == "ok" and stock["social"].get("net") is not None:
        parts.append(("social", stock["social"]["net"], 0.3))
    if stock["analyst"].get("state") == "ok":
        parts.append(("analyst", stock["analyst"]["dir"], stock["analyst"].get("conf", 0.5)))
    if parts:
        wsum = sum(w for _, _, w in parts) or 1e-9
        score = round(sum(d * w for _, d, w in parts) / wsum, 3)
        stock.update(score=score, label=("bullish" if score > 0.12 else "bearish" if score < -0.12 else "neutral"),
                     contributors=[p[0] for p in parts], state="ok")
    else:
        stock.update(score=None, label="no_data", state="missing")  # explicitly NOT neutral
    out["stock"] = stock

    # SECTOR: sector-ETF momentum (technicals() is itself SWR-cached; time-boxed).
    prof = _val("profile")
    industry = prof.get("finnhubIndustry") if isinstance(prof, dict) else None
    etf = _sector_etf_for(industry)
    if etf:
        t = gather({"t": lambda: technicals(etf)}, timeout=3.0).get("t", {})  # best-effort; skip if slow
        if isinstance(t, dict) and t.get("state") == "ok":
            ms = t.get("market_sentiment") or {}
            out["sector"] = {"state": "ok", "etf": etf, "industry": industry, "change_pct": t.get("change_pct"),
                             "trend": t.get("trend_state"), "momentum": ms.get("momentum"),
                             "signal": ms.get("buy_sell_signal"), "source": f"{etf} (sector proxy)"}
        else:
            out["sector"] = {"state": (t or {}).get("state", "throttled"), "etf": etf, "industry": industry}
    else:
        out["sector"] = {"state": "unknown", "industry": industry, "reason": "no sector mapping"}

    # MARKET regime + macro
    reg = _val("regime")
    if isinstance(reg, dict) and "error" not in reg:
        out["market"] = {"state": "ok", "regime": reg.get("regime"), "score": reg.get("risk_appetite_score"),
                         "posture": reg.get("posture") or reg.get("guidance"), "source": "Market regime engine"}
    else:
        out["market"] = {"state": "error", "source": "Market regime engine"}
    macro = _val("macro")
    if isinstance(macro, dict) and "error" not in macro:
        out["market"]["macro"] = macro.get("detail"); out["market"]["macro_state"] = "ok"
    else:
        out["market"]["macro_state"] = "error"

    out["provenance"] = _stamp("Google News + StockTwits + Finnhub + FRED + regime", f"catsig:{sym}")
    return out


# ── Fundamentals ─────────────────────────────────────────────────────────────

def fundamentals(sym: str) -> Dict[str, Any]:
    sym = canonical(sym)
    fh = to_finnhub(sym)
    res = gather({
        "prof": lambda: cached(f"prof:{sym}", TTL["fundamentals"], lambda: __import__("finnhub_data").profile(fh)),
        "fin": lambda: cached(f"fin:{sym}", TTL["fundamentals"], lambda: __import__("finnhub_data").basic_financials(fh)),
    }, timeout=8.0)
    prof = res.get("prof") if not (isinstance(res.get("prof"), dict) and "__err" in res["prof"]) else None
    fin = res.get("fin") if not (isinstance(res.get("fin"), dict) and "__err" in res["fin"]) else None
    ok1 = isinstance(prof, dict) and not prof.get("error")
    ok2 = isinstance(fin, dict) and not fin.get("error")
    if not ok1 and not ok2:
        return {"symbol": sym, "state": "error", "reason": "fundamentals unavailable (Finnhub)"}
    m = (fin or {}).get("metric", {}) if isinstance(fin, dict) else {}
    prof = prof if isinstance(prof, dict) else {}
    return {
        "symbol": sym, "state": "ok",
        "name": prof.get("name"), "exchange": prof.get("exchange"),
        "industry": prof.get("finnhubIndustry"), "country": prof.get("country"),
        "market_cap": prof.get("marketCapitalization"), "currency": prof.get("currency"),
        "ipo": prof.get("ipo"), "logo": prof.get("logo"), "weburl": prof.get("weburl"),
        "metrics": {
            "pe": m.get("peBasicExclExtraTTM") or m.get("peTTM"),
            "ps": m.get("psTTM"), "pb": m.get("pbQuarterly"),
            "eps_ttm": m.get("epsBasicExclExtraItemsTTM") or m.get("epsTTM"),
            "roe": m.get("roeTTM"), "net_margin": m.get("netProfitMarginTTM"),
            "gross_margin": m.get("grossMarginTTM"), "beta": m.get("beta"),
            "div_yield": m.get("dividendYieldIndicatedAnnual"),
            "52w_high": m.get("52WeekHigh"), "52w_low": m.get("52WeekLow"),
            "rev_growth_ttm": m.get("revenueGrowthTTMYoy"),
        },
        "provenance": _stamp("Finnhub fundamentals", f"fin:{sym}"),
    }


# ── Price history (for charts) ───────────────────────────────────────────────

_RANGE = {"1D": ("5d", "5m"), "5D": ("5d", "30m"), "1M": ("1mo", "1d"),
          "3M": ("3mo", "1d"), "6M": ("6mo", "1d"), "YTD": ("ytd", "1d"),
          "1Y": ("1y", "1d")}


def price_history(sym: str, rng: str = "3M") -> Dict[str, Any]:
    sym = canonical(sym)
    period, interval = _RANGE.get(rng.upper(), ("3mo", "1d"))

    def _hist():
        import yfinance as yf
        df = yf.Ticker(to_yahoo(sym)).history(period=period, interval=interval)
        pts = [{"t": i.isoformat(), "c": round(float(r["Close"]), 2),
                "o": round(float(r["Open"]), 2), "h": round(float(r["High"]), 2),
                "l": round(float(r["Low"]), 2), "v": int(r["Volume"])}
               for i, r in df.iterrows() if r["Close"] == r["Close"]]  # drop NaN
        return pts

    pts, st = _guard("yahoo", lambda: _box(lambda: cached(f"hist:{sym}:{rng}", 300, _hist), 15))
    if st != "ok" or not pts:
        return {"symbol": sym, "range": rng, "state": st if st != "ok" else "empty",
                "reason": "no price history from Yahoo"}
    first, last = pts[0]["c"], pts[-1]["c"]
    return {"symbol": sym, "range": rng, "state": "ok", "points": pts,
            "change_pct": round((last - first) / first * 100, 2) if first else None,
            "as_of": pts[-1]["t"], "interval": interval,
            "provenance": _stamp("Yahoo daily history", f"hist:{sym}:{rng}")}


# ── Comparison workspace (2-8 securities, one normalized schema) ─────────────

_COMPARE_BENCHMARK = "SPY"


def _daily_returns(points: List[dict]) -> List[float]:
    """Simple daily returns from a close series (drops the first point)."""
    out = []
    for i in range(1, len(points)):
        p0, p1 = points[i - 1]["c"], points[i]["c"]
        if p0:
            out.append(p1 / p0 - 1.0)
    return out


def _max_drawdown_pct(points: List[dict]) -> Optional[float]:
    peak = None
    mdd = 0.0
    for p in points:
        c = p["c"]
        if peak is None or c > peak:
            peak = c
        if peak:
            dd = (c - peak) / peak
            if dd < mdd:
                mdd = dd
    return round(mdd * 100, 2) if points else None


def _annual_vol_pct(rets: List[float]) -> Optional[float]:
    if len(rets) < 2:
        return None
    import numpy as np
    return round(float(np.std(rets, ddof=1)) * (252 ** 0.5) * 100, 2)


def _beta(asset_rets_by_date: Dict[str, float], bench_rets_by_date: Dict[str, float]) -> Optional[float]:
    import numpy as np
    common = sorted(set(asset_rets_by_date) & set(bench_rets_by_date))
    if len(common) < 20:
        return None
    a = np.array([asset_rets_by_date[d] for d in common])
    b = np.array([bench_rets_by_date[d] for d in common])
    var = float(np.var(b, ddof=1))
    if var == 0:
        return None
    cov = float(np.cov(a, b, ddof=1)[0, 1])
    return round(cov / var, 2)


def _rets_by_date(points: List[dict]) -> Dict[str, float]:
    out = {}
    for i in range(1, len(points)):
        p0, p1 = points[i - 1]["c"], points[i]["c"]
        if p0:
            out[points[i]["t"][:10]] = p1 / p0 - 1.0
    return out


def compare(symbols: List[str], rng: str = "6M") -> Dict[str, Any]:
    """Compare 2-8 securities on ONE normalized schema: aligned %-performance,
    volatility, beta, max drawdown, correlation, fundamentals, technicals and
    sentiment. Every panel degrades independently — a symbol that fails to load
    gets an explicit state, never a fabricated value."""
    syms = []
    for s in symbols:
        c = canonical(s)
        if c and c not in syms:
            syms.append(c)
        if len(syms) >= 8:
            break
    if len(syms) < 2:
        return {"state": "error", "reason": "need at least 2 valid symbols to compare",
                "symbols": syms}
    key = "cmp:" + ",".join(syms) + ":" + rng.upper()
    val, _cs = swr(key, TTL["chart"], lambda: _compare_compute(syms, rng))
    return val


def _compare_compute(syms: List[str], rng: str) -> Dict[str, Any]:
    bench = _COMPARE_BENCHMARK
    want_hist = list(dict.fromkeys(syms + [bench]))

    # Fan out every per-symbol fetch concurrently (history + fundamentals +
    # technicals), each already SWR-cached and time-boxed inside its own fn.
    tasks: Dict[str, Callable[[], Any]] = {}
    for s in want_hist:
        tasks[f"hist:{s}"] = (lambda s=s: price_history(s, rng))
    for s in syms:
        tasks[f"fund:{s}"] = (lambda s=s: fundamentals(s))
        tasks[f"tech:{s}"] = (lambda s=s: technicals(s))
    with timed("compare.gather"):
        res = gather(tasks, timeout=20.0)

    def _get(name):
        v = res.get(name)
        return None if (isinstance(v, dict) and "__err" in v) else v

    # Benchmark returns keyed by date (for beta + relative strength).
    bench_hist = _get(f"hist:{bench}") or {}
    bench_pts = bench_hist.get("points") or [] if isinstance(bench_hist, dict) else []
    bench_rbd = _rets_by_date(bench_pts)
    bench_perf = round((bench_pts[-1]["c"] / bench_pts[0]["c"] - 1) * 100, 2) if len(bench_pts) >= 2 else None

    series: Dict[str, Any] = {}
    metrics: List[Dict[str, Any]] = []
    rets_map: Dict[str, Dict[str, float]] = {}

    for s in syms:
        hist = _get(f"hist:{s}")
        fund = _get(f"fund:{s}")
        tech = _get(f"tech:{s}")
        pts = hist.get("points") if isinstance(hist, dict) else None

        m: Dict[str, Any] = {"symbol": s}
        # Identity from the master (name/type/currency) — always available.
        ident = _SM.lookup(s) or {}
        m["name"] = ident.get("name") or s
        m["type"] = ident.get("type")
        m["currency"] = ident.get("currency")
        m["exchange"] = ident.get("exchange")

        if pts and len(pts) >= 2:
            base = pts[0]["c"]
            perf = [{"t": p["t"][:10], "pct": round((p["c"] / base - 1) * 100, 3)} for p in pts if base]
            series[s] = {"state": "ok", "points": perf,
                         "last_price": pts[-1]["c"], "as_of": pts[-1]["t"]}
            rbd = _rets_by_date(pts)
            rets_map[s] = rbd
            perf_pct = round((pts[-1]["c"] / base - 1) * 100, 2)
            m.update(
                state="ok", price=pts[-1]["c"],
                perf_range_pct=perf_pct,
                rel_strength_vs_bench_pct=(round(perf_pct - bench_perf, 2) if bench_perf is not None else None),
                volatility_annual_pct=_annual_vol_pct(_daily_returns(pts)),
                max_drawdown_pct=_max_drawdown_pct(pts),
                beta=_beta(rbd, bench_rbd),
            )
        else:
            series[s] = {"state": (hist or {}).get("state", "empty") if isinstance(hist, dict) else "error",
                         "points": []}
            m.update(state="no_price", price=None, perf_range_pct=None,
                     volatility_annual_pct=None, max_drawdown_pct=None, beta=None)

        # Fundamentals (independent — may be missing without blanking the row).
        if isinstance(fund, dict) and fund.get("state") == "ok":
            fm = fund.get("metrics") or {}
            m.update(market_cap=fund.get("market_cap"), industry=fund.get("industry"),
                     pe=fm.get("pe"), ps=fm.get("ps"), net_margin=fm.get("net_margin"),
                     gross_margin=fm.get("gross_margin"), roe=fm.get("roe"),
                     rev_growth=fm.get("rev_growth_ttm"), div_yield=fm.get("div_yield"),
                     beta_fund=fm.get("beta"), fundamentals_state="ok")
            if not m.get("name") or m["name"] == s:
                m["name"] = fund.get("name") or m["name"]
        else:
            m["fundamentals_state"] = (fund or {}).get("state", "empty") if isinstance(fund, dict) else "error"

        # Technicals (RSI / trend / change).
        if isinstance(tech, dict) and tech.get("state") == "ok":
            m.update(rsi=(tech.get("rsi") or {}).get("value") if isinstance(tech.get("rsi"), dict) else tech.get("rsi"),
                     trend=tech.get("trend_state"), change_pct=tech.get("change_pct"),
                     technicals_state="ok")
        else:
            m["technicals_state"] = (tech or {}).get("state", "empty") if isinstance(tech, dict) else "error"

        metrics.append(m)

    # Correlation matrix over the intersection of return dates.
    correlation = _correlation_matrix(syms, rets_map)

    ok_syms = [s for s in syms if series.get(s, {}).get("state") == "ok"]
    return {
        "state": "ok" if ok_syms else "error",
        "symbols": syms, "range": rng.upper(), "benchmark": bench,
        "benchmark_perf_pct": bench_perf,
        "series": series, "metrics": metrics, "correlation": correlation,
        "loaded": len(ok_syms), "requested": len(syms),
        "provenance": _stamp("Yahoo history + Finnhub fundamentals + TradingView technicals"),
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


def _correlation_matrix(syms: List[str], rets_map: Dict[str, Dict[str, float]]) -> Dict[str, Any]:
    import numpy as np
    present = [s for s in syms if rets_map.get(s)]
    n = len(present)
    if n < 2:
        return {"symbols": present, "matrix": [], "state": "insufficient"}
    matrix = [[None] * n for _ in range(n)]
    for i in range(n):
        matrix[i][i] = 1.0
        for j in range(i + 1, n):
            a, b = rets_map[present[i]], rets_map[present[j]]
            common = sorted(set(a) & set(b))
            if len(common) >= 20:
                va = np.array([a[d] for d in common])
                vb = np.array([b[d] for d in common])
                if np.std(va) > 0 and np.std(vb) > 0:
                    c = round(float(np.corrcoef(va, vb)[0, 1]), 2)
                else:
                    c = None
            else:
                c = None
            matrix[i][j] = matrix[j][i] = c
    return {"symbols": present, "matrix": matrix, "state": "ok"}


# ── System / provider health ─────────────────────────────────────────────────

def provider_health() -> Dict[str, Any]:
    from _config import FRED_KEY, FINNHUB_KEY, AV_KEY, SNAPTRADE_CLIENT_ID
    configured = {
        "finnhub": bool(FINNHUB_KEY), "fred": bool(FRED_KEY),
        "alphavantage": bool(AV_KEY), "snaptrade": bool(SNAPTRADE_CLIENT_ID),
        "gemini": bool(os.environ.get("GEMINI_API_KEY")),
        "yahoo": True, "tradingview": True, "stocktwits": True, "google_news": True,
    }
    out = {}
    for name, has_key in configured.items():
        last = _PROVIDERS.get(name)
        if not has_key:
            out[name] = {"status": "disabled", "detail": "no API key", "at": None}
        elif last:
            out[name] = last
        else:
            out[name] = {"status": "configured", "detail": "no calls yet", "at": None}
    return {"providers": out, "cache_entries": len(_CACHE),
            "at": datetime.now(timezone.utc).isoformat()}
