"""Sector Map (Prompt 5B) — a TradingView-free sector/industry heatmap.

Built on the 11 SPDR sector ETFs (whose OWN Yahoo performance is the authoritative
sector return — no need to aggregate thousands of names or touch TradingView) plus a
curated set of large constituents per sector for breadth, advancers/decliners and the
top/bottom movers. SPY is the relative-strength benchmark.

Everything degrades gracefully: a tile whose ETF quote is unavailable renders with a
typed state and a freshness stamp rather than a fabricated number. Nothing here calls
TradingView; the map works with `TRADINGVIEW_ENABLED=false`.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from statistics import mean, pstdev
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in ("..", os.path.join("..", "lab"), os.path.join("..", "src")):
    sys.path.insert(0, os.path.join(_HERE, _p))
sys.path.insert(0, _HERE)

import research as _R   # cache, gather, price_history, catalysts, provider guards


# ── Curated sector taxonomy (11 GICS sectors ⇄ SPDR ETFs) ────────────────────
# constituents are a handful of the largest, liquid names — enough for breadth and
# top/bottom movers without a mass scan. Industry groups drive the drill-down.
SECTORS: Dict[str, Dict[str, Any]] = {
    "technology": {"name": "Technology", "etf": "XLK",
        "industries": {"Semiconductors": ["NVDA", "AVGO", "AMD"],
                       "Software": ["MSFT", "ORCL", "CRM", "ADBE"],
                       "Hardware": ["AAPL", "CSCO", "DELL"]},
        "cap_weight": 0.32},
    "communication": {"name": "Communication Services", "etf": "XLC",
        "industries": {"Interactive Media": ["GOOGL", "META"],
                       "Entertainment": ["NFLX", "DIS"],
                       "Telecom": ["T", "VZ", "TMUS"]},
        "cap_weight": 0.09},
    "consumer_discretionary": {"name": "Consumer Discretionary", "etf": "XLY",
        "industries": {"Retail": ["AMZN", "HD", "LOW"],
                       "Autos": ["TSLA", "GM", "F"],
                       "Restaurants": ["MCD", "SBUX", "CMG"]},
        "cap_weight": 0.10},
    "consumer_staples": {"name": "Consumer Staples", "etf": "XLP",
        "industries": {"Food & Beverage": ["KO", "PEP", "MDLZ"],
                       "Household": ["PG", "CL", "KMB"],
                       "Staples Retail": ["WMT", "COST"]},
        "cap_weight": 0.06},
    "financials": {"name": "Financials", "etf": "XLF",
        "industries": {"Banks": ["JPM", "BAC", "WFC"],
                       "Payments": ["V", "MA", "AXP"],
                       "Insurance": ["BRK-B", "PGR", "CB"]},
        "cap_weight": 0.13},
    "health_care": {"name": "Health Care", "etf": "XLV",
        "industries": {"Pharma": ["LLY", "JNJ", "MRK", "ABBV"],
                       "Biotech": ["AMGN", "GILD", "VRTX"],
                       "Providers & Devices": ["UNH", "ABT", "TMO"]},
        "cap_weight": 0.12},
    "industrials": {"name": "Industrials", "etf": "XLI",
        "industries": {"Aerospace & Defense": ["BA", "RTX", "LMT"],
                       "Machinery": ["CAT", "DE", "HON"],
                       "Transports": ["UBER", "UNP", "UPS"]},
        "cap_weight": 0.09},
    "energy": {"name": "Energy", "etf": "XLE",
        "industries": {"Integrated Oil & Gas": ["XOM", "CVX"],
                       "E&P": ["COP", "EOG", "OXY"],
                       "Equipment & Services": ["SLB", "HAL"]},
        "cap_weight": 0.04},
    "materials": {"name": "Materials", "etf": "XLB",
        "industries": {"Chemicals": ["LIN", "SHW", "APD"],
                       "Metals & Mining": ["FCX", "NEM"],
                       "Construction Materials": ["VMC", "MLM"]},
        "cap_weight": 0.02},
    "utilities": {"name": "Utilities", "etf": "XLU",
        "industries": {"Electric": ["NEE", "SO", "DUK"],
                       "Multi & Independent": ["CEG", "AEP", "EXC"]},
        "cap_weight": 0.025},
    "real_estate": {"name": "Real Estate", "etf": "XLRE",
        "industries": {"Infrastructure REITs": ["AMT", "EQIX", "CCI"],
                       "Industrial & Retail REITs": ["PLD", "SPG", "O"],
                       "Health & Residential": ["WELL", "AVB"]},
        "cap_weight": 0.024},
}

_BENCHMARK = "SPY"


# ── Performance helpers (Yahoo history; NO TradingView) ──────────────────────

def _perf_from_history(sym: str) -> Dict[str, Any]:
    """1D / 5D / 1M performance, relative volume, momentum and volatility from a
    single cached Yahoo daily history call."""
    h = _R.price_history(sym, "3M")
    if h.get("state") != "ok" or not h.get("points"):
        return {"symbol": sym, "state": h.get("state", "empty"),
                "reason": h.get("reason", "no history")}
    pts = h["points"]
    closes = [p["c"] for p in pts]
    vols = [p.get("v") or 0 for p in pts]

    def _chg(n):
        if len(closes) <= n:
            return None
        a, b = closes[-1 - n], closes[-1]
        return round((b - a) / a * 100, 2) if a else None

    last = closes[-1]
    sma20 = mean(closes[-20:]) if len(closes) >= 20 else mean(closes)
    sma50 = mean(closes[-50:]) if len(closes) >= 50 else sma20
    rets = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes)) if closes[i - 1]]
    vol_annual = round(pstdev(rets[-30:]) * (252 ** 0.5) * 100, 1) if len(rets) >= 5 else None
    avg_vol = mean(vols[-20:]) if len(vols) >= 20 else (mean(vols) if vols else 0)
    rel_vol = round((vols[-1] / avg_vol), 2) if avg_vol else None
    momentum = round(((last / sma50) - 1) * 100, 2) if sma50 else None
    return {"symbol": sym, "state": "ok", "price": round(last, 2),
            "perf_1d": _chg(1), "perf_5d": _chg(5), "perf_1m": _chg(21),
            "rel_volume": rel_vol, "momentum_pct": momentum,
            "volatility_pct": vol_annual, "above_sma20": bool(last > sma20),
            "above_sma50": bool(last > sma50), "as_of": h.get("as_of")}


def _constituent_quotes(tickers: List[str]) -> Dict[str, Any]:
    """Concurrent 1D quotes for a sector's constituents (breadth + movers).

    Returns {quotes, requested, missing, coverage} — NOT a bare list. Breadth
    computed from a partial fetch is not the same fact as breadth computed from a
    full one, and the caller has to be able to tell them apart: dropping the
    failures on the floor is how a starved pool used to render as "0 advancers,
    0 decliners" on a sector that was actually up on the day.
    """
    res = _R.quotes(tickers, ttl=120, timeout=20.0)
    out, missing = [], []
    for s in tickers:
        r = res.get(s) or {}
        if r.get("state") == "ok" and r.get("change_pct") is not None:
            out.append({"symbol": s, "price": r["price"], "change_pct": r["change_pct"],
                        "source_timestamp": r.get("source_timestamp")})
        else:
            missing.append({"symbol": s,
                            "reason": str(r.get("reason") or r.get("state") or "no quote")[:60]})
    n = len(tickers)
    return {"quotes": out, "requested": n, "missing": missing,
            "coverage": round(len(out) / n, 3) if n else None}


def _freshness_for(as_of: Optional[str]):
    """Freshness of a DAILY bar. Aged from the bar's session CLOSE (see
    `freshness.bar_age_seconds`) — aging it from the midnight stamp Yahoo puts on a
    daily bar made yesterday's close, the freshest datum that can exist pre-market,
    read as 30h old and show an "ageing" badge on perfectly current data."""
    import freshness as _fr
    age = _fr.bar_age_seconds(as_of)
    if age is None:
        return _fr.classify(None)
    # daily-bar cadence: the previous close is fresh through the next session; a
    # long weekend/holiday is ageing; only a multi-day gap is a stalled feed.
    return _fr.classify(age, thresholds={"fresh": 26 * 3600, "ageing": 4 * 86400,
                                         "stale": 8 * 86400})


# ── Tiles + full map ─────────────────────────────────────────────────────────

def sector_tile(key: str, *, benchmark_1m: Optional[float] = None) -> Dict[str, Any]:
    meta = SECTORS.get(key)
    if not meta:
        return {"key": key, "state": "unsupported", "reason": "unknown sector"}
    etf_perf = _perf_from_history(meta["etf"])
    consts = list({t for grp in meta["industries"].values() for t in grp})
    cq = _constituent_quotes(consts)
    quotes = cq["quotes"]
    adv = sum(1 for q in quotes if (q["change_pct"] or 0) > 0)
    decl = sum(1 for q in quotes if (q["change_pct"] or 0) < 0)
    ranked = sorted(quotes, key=lambda q: (q["change_pct"] or 0), reverse=True)
    top = ranked[:3]
    bottom = ranked[-3:][::-1] if len(ranked) > 3 else []
    # opportunity_count: constituents with constructive momentum (up on the day AND
    # the ETF above its 50-DMA) — a cheap, honest proxy, not a full scan.
    opp = sum(1 for q in quotes if (q["change_pct"] or 0) > 0.5) if etf_perf.get("above_sma50") else 0
    rs = None
    if etf_perf.get("state") == "ok" and etf_perf.get("perf_1m") is not None and benchmark_1m is not None:
        rs = round(etf_perf["perf_1m"] - benchmark_1m, 2)   # relative strength vs SPY (1M)
    tile = {
        "key": key, "name": meta["name"], "etf": meta["etf"],
        "state": etf_perf.get("state", "empty"),
        "perf_1d": etf_perf.get("perf_1d"), "perf_5d": etf_perf.get("perf_5d"),
        "perf_1m": etf_perf.get("perf_1m"),
        "rel_volume": etf_perf.get("rel_volume"), "momentum_pct": etf_perf.get("momentum_pct"),
        "volatility_pct": etf_perf.get("volatility_pct"),
        "rs_vs_spy_1m": rs,
        "breadth": {"advancers": adv, "decliners": decl, "counted": len(quotes),
                    "requested": cq["requested"], "coverage": cq["coverage"],
                    "missing": cq["missing"],
                    # breadth from a partial fetch is a weaker fact — say so.
                    "complete": not cq["missing"]},
        "opportunity_count": opp,
        "opportunity_count_complete": not cq["missing"],
        "top": top, "bottom": bottom,
        "cap_weight": meta.get("cap_weight"),
        "industry_count": len(meta["industries"]),
        "freshness": _freshness_for(etf_perf.get("as_of")),
        "as_of": etf_perf.get("as_of"),
    }
    return tile


def sector_map(weighting: str = "cap", blocking: bool = False) -> Dict[str, Any]:
    """The whole map — all 11 tiles computed CONCURRENTLY + the SPY benchmark.

    `blocking=False` (default, used by the web UI): SWR-async, so a cold call returns
    a `loading` placeholder immediately and the frontend polls — the map appears
    progressively instead of hanging the request.

    `blocking=True` (used by batch callers like the paper scheduler): compute
    synchronously and return the real map. A scheduler has no one to poll for it, and
    silently receiving a `loading` placeholder would make the pre-market scan abort
    on every cold start.
    """
    def _compute():
        # Warm EVERY constituent quote in one pooled batch before fanning out to the
        # tiles: the tiles then hit a warm per-symbol cache instead of 11 separate
        # bursts contending for connections.
        try:
            _R.quotes(sorted({t for s in SECTORS.values()
                              for g in s["industries"].values() for t in g}),
                      ttl=120, timeout=25.0)
        except Exception:  # noqa: BLE001 — a failed prewarm just means tiles fetch their own
            pass
        bench = _perf_from_history(_BENCHMARK)
        bench_1m = bench.get("perf_1m") if bench.get("state") == "ok" else None
        tasks = {k: (lambda k=k: sector_tile(k, benchmark_1m=bench_1m)) for k in SECTORS}
        tiles_map = _R.gather(tasks, timeout=25.0)
        tiles = [tiles_map.get(k) if isinstance(tiles_map.get(k), dict) else
                 {"key": k, "state": "error"} for k in SECTORS]
        # tile size for the treemap: equal, or approximate market-cap weight.
        for t in tiles:
            if weighting == "equal":
                t["size"] = round(1.0 / len(tiles), 4)
            else:
                t["size"] = t.get("cap_weight") or round(1.0 / len(tiles), 4)
        import freshness as _fr
        worst = _fr.worst(*[(t.get("freshness") or {}).get("state", "unknown") for t in tiles])
        ok = [t for t in tiles if t.get("state") == "ok"]
        return {
            "state": "ok" if ok else "empty",
            "weighting": weighting,
            "sectors": sorted(tiles, key=lambda t: (t.get("perf_1d") is None, -(t.get("perf_1d") or -999))),
            "benchmark": {"symbol": _BENCHMARK, "perf_1d": bench.get("perf_1d"),
                          "perf_5d": bench.get("perf_5d"), "perf_1m": bench_1m,
                          "state": bench.get("state")},
            "freshness": {"state": worst, "label": _fr.label(worst)},
            "tradingview_used": False,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    if blocking:
        val, cst = _R.swr(f"sectormap:{weighting}", 300, _compute)
    else:
        val, cst = _R.swr_async(f"sectormap:{weighting}", 300, _compute,
                                loading={"state": "loading", "reason": "building sector map…",
                                         "weighting": weighting})
    if isinstance(val, dict):
        val = {**val, "cache_state": cst}
    return val


# ── Sector drill-down ─────────────────────────────────────────────────────────

def sector_detail(key: str) -> Dict[str, Any]:
    meta = SECTORS.get(key)
    if not meta:
        return {"key": key, "state": "unsupported", "reason": "unknown sector"}

    def _compute():
        etf = meta["etf"]
        etf_perf = _perf_from_history(etf)
        bench = _perf_from_history(_BENCHMARK)
        bench_1m = bench.get("perf_1m") if bench.get("state") == "ok" else None
        # industry groups with their constituent quotes + group performance
        # industry groups fetched CONCURRENTLY — this loop used to be sequential,
        # paying one full round-trip per group before starting the next.
        gtasks = {g: (lambda t=tk: _constituent_quotes(t)) for g, tk in meta["industries"].items()}
        gres = _R.gather(gtasks, timeout=20.0)
        groups = []
        all_quotes: List[Dict[str, Any]] = []
        for gname in meta["industries"]:
            cq = gres.get(gname)
            if not isinstance(cq, dict) or "quotes" not in cq:
                groups.append({"name": gname, "perf_1d": None, "members": [],
                               "state": "unavailable", "coverage": 0.0})
                continue
            q = cq["quotes"]
            all_quotes.extend(q)
            gperf = round(mean([x["change_pct"] for x in q]), 2) if q else None
            groups.append({"name": gname, "perf_1d": gperf,
                           "members": sorted(q, key=lambda x: (x["change_pct"] or 0), reverse=True),
                           "coverage": cq["coverage"], "missing": cq["missing"],
                           "state": "ok" if q else "unavailable"})
        ranked = sorted(all_quotes, key=lambda x: (x["change_pct"] or 0), reverse=True)
        rs = (round(etf_perf["perf_1m"] - bench_1m, 2)
              if (etf_perf.get("perf_1m") is not None and bench_1m is not None) else None)
        # sector trend from the ETF's own posture
        trend = "up" if etf_perf.get("above_sma50") and (etf_perf.get("momentum_pct") or 0) > 0 else (
            "down" if not etf_perf.get("above_sma50") else "sideways")
        # catalysts: the ETF's own news (sector-level), reused from research
        try:
            cat = _R.catalysts(etf)
            catalysts = {"lean": cat.get("lean"), "items": (cat.get("items") or [])[:5],
                         "state": cat.get("state")}
        except Exception:
            catalysts = {"state": "empty", "items": []}
        # options availability: large constituents almost always have liquid options;
        # report the fraction we can name rather than guessing per-name chains here.
        opt_names = [q["symbol"] for q in ranked[:8]]
        return {
            "key": key, "name": meta["name"], "state": etf_perf.get("state", "empty"),
            "etf_proxy": {"symbol": etf, **{k: etf_perf.get(k) for k in
                          ("price", "perf_1d", "perf_5d", "perf_1m", "momentum_pct",
                           "volatility_pct", "rel_volume", "above_sma50")}},
            "trend": trend, "rs_vs_spy_1m": rs,
            "benchmark": {"symbol": _BENCHMARK, "perf_1m": bench_1m},
            "industry_groups": groups,
            "ranked_stocks": ranked,
            "catalysts": catalysts,
            "options_availability": {"liquid_names": opt_names,
                                     "note": "large-cap sector constituents with listed options"},
            "freshness": _freshness_for(etf_perf.get("as_of")),
            "as_of": etf_perf.get("as_of"),
            "tradingview_used": False,
        }

    val, cst = _R.swr(f"sectordetail:{key}", 300, _compute)
    if isinstance(val, dict):
        val = {**val, "cache_state": cst}
    return val


def sector_keys() -> List[str]:
    return list(SECTORS)
