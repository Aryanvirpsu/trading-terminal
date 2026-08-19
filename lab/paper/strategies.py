"""The three launch strategies + the sector-narrowed candidate funnel.

Strategies are deliberately simple and orthogonal — they exist to generate *candidates*
cheaply. The expensive decision engine runs only on a handful of finalists.

  liquid_momentum          — strong trend, above 50-DMA, real volume
  sector_relative_strength — leaders inside the strongest sectors (and shorts in the
                             weakest, expressed as avoid/short-lean candidates)
  mean_reversion           — oversold pullback INSIDE an intact uptrend, not a falling knife

The funnel (mission §8) is what keeps this affordable:
    rank sectors -> take strongest + weakest -> rank stocks inside them
    -> <= 5 finalists -> deep decision-engine analysis only on those
"""
from __future__ import annotations

import os
import sys
from statistics import mean
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in ("..", os.path.join("..", "..", "dashboard"), os.path.join("..", "..", "src")):
    sys.path.insert(0, os.path.join(_HERE, _p))

from . import config as cfg


def _sector_map_module():
    import sector_map as sm
    return sm


def rank_sectors(limit_strong: int = 2, limit_weak: int = 1) -> Dict[str, Any]:
    """Rank sectors by a blend of relative strength, breadth and momentum, then take
    the strongest (for longs) and weakest (for mean-reversion / avoidance context)."""
    sm = _sector_map_module()
    # blocking=True: a scheduler has nobody to poll a `loading` placeholder for it.
    m = sm.sector_map("cap", blocking=True)
    if m.get("state") != "ok":
        return {"state": m.get("state", "unavailable"), "strong": [], "weak": [],
                "reason": m.get("reason", "sector map unavailable")}
    scored = []
    for t in m.get("sectors", []):
        if t.get("state") != "ok":
            continue
        br = t.get("breadth") or {}
        counted = br.get("counted") or 0
        breadth = ((br.get("advancers", 0) - br.get("decliners", 0)) / counted) if counted else 0.0
        rs = t.get("rs_vs_spy_1m") or 0.0
        mom = t.get("momentum_pct") or 0.0
        score = round(0.5 * rs + 0.3 * (breadth * 10) + 0.2 * mom, 3)
        scored.append({**t, "sector_score": score, "breadth_ratio": round(breadth, 3)})
    scored.sort(key=lambda x: x["sector_score"], reverse=True)
    return {"state": "ok", "ranked": scored,
            "strong": scored[:limit_strong], "weak": scored[-limit_weak:] if scored else [],
            "benchmark": m.get("benchmark"), "freshness": m.get("freshness")}


def _members(sector_key: str) -> List[str]:
    sm = _sector_map_module()
    meta = sm.SECTORS.get(sector_key) or {}
    return sorted({t for grp in meta.get("industries", {}).values() for t in grp})


def _bars(symbol: str) -> Optional[Dict[str, Any]]:
    """Daily history via the existing research layer (Yahoo primary, cached)."""
    import research as R
    h = R.price_history(symbol, "3M")
    if h.get("state") != "ok" or not h.get("points"):
        return None
    pts = h["points"]
    closes = [p["c"] for p in pts]
    vols = [p.get("v") or 0 for p in pts]
    highs = [p["h"] for p in pts]
    lows = [p["l"] for p in pts]
    if len(closes) < 55:
        return None
    return {"closes": closes, "vols": vols, "highs": highs, "lows": lows,
            "as_of": h.get("as_of")}


def _rsi(closes: List[float], n: int = 14) -> float:
    if len(closes) <= n:
        return 50.0
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    ag, al = mean(gains[-n:]), mean(losses[-n:])
    return 100.0 if al == 0 else round(100 - 100 / (1 + ag / al), 1)


def score_liquid_momentum(symbol: str, b: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    c, v = b["closes"], b["vols"]
    last, sma20, sma50 = c[-1], mean(c[-20:]), mean(c[-50:])
    avg_vol, dollar_vol = mean(v[-20:]), last * mean(v[-20:])
    if dollar_vol < 5e6:                      # liquidity floor — must be tradeable
        return None
    if not (last > sma20 > sma50):            # trend must be intact and stacked
        return None
    rsi = _rsi(c)
    if rsi > 78:                              # already extended
        return None
    ret_1m = (last / c[-21] - 1) * 100 if len(c) > 21 else 0.0
    rel_vol = (v[-1] / avg_vol) if avg_vol else 1.0
    score = round(0.5 * ret_1m + 0.3 * ((last / sma50 - 1) * 100) + 0.2 * (rel_vol * 5), 2)
    return {"symbol": symbol, "strategy": "liquid_momentum", "score": score,
            "rsi": rsi, "ret_1m": round(ret_1m, 2), "rel_vol": round(rel_vol, 2),
            "dollar_volume": round(dollar_vol), "direction": "LONG",
            "why": f"above 20/50-DMA, 1M {ret_1m:+.1f}%, RSI {rsi}, ${dollar_vol/1e6:.0f}M/day"}


def score_sector_rs(symbol: str, b: Dict[str, Any], sector_score: float,
                    sector_name: str, sector_is_strong: bool = True) -> Optional[Dict[str, Any]]:
    """Relative strength only means something in a sector that is actually leading.
    A name inside the WEAKEST sector is not a 'sector RS' setup — swimming against the
    sector is the opposite of this strategy — so we decline it here and let
    mean_reversion pick it up if it qualifies on its own merits."""
    if not sector_is_strong:
        return None
    c = b["closes"]
    last, sma50 = c[-1], mean(c[-50:])
    if last <= sma50:                          # a leader must at least hold its 50-DMA
        return None
    ret_1m = (last / c[-21] - 1) * 100 if len(c) > 21 else 0.0
    score = round(0.6 * ret_1m + 0.4 * sector_score, 2)
    return {"symbol": symbol, "strategy": "sector_relative_strength", "score": score,
            "ret_1m": round(ret_1m, 2), "sector_score": sector_score, "direction": "LONG",
            "why": f"leader in leading sector {sector_name} "
                   f"(sector score {sector_score:+.2f}), 1M {ret_1m:+.1f}%"}


def score_mean_reversion(symbol: str, b: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    c = b["closes"]
    last, sma20, sma50 = c[-1], mean(c[-20:]), mean(c[-50:])
    rsi = _rsi(c)
    # Pullback INSIDE an uptrend — never a falling knife: the 50-DMA must still hold.
    if last <= sma50:
        return None
    if not (rsi < 40 or last < sma20):
        return None
    stretch = (sma20 / last - 1) * 100
    score = round((40 - rsi) * 0.6 + stretch * 0.4, 2)
    return {"symbol": symbol, "strategy": "mean_reversion", "score": score,
            "rsi": rsi, "stretch_pct": round(stretch, 2), "direction": "LONG",
            "why": f"pullback in an uptrend: RSI {rsi}, {stretch:.1f}% below 20-DMA, above 50-DMA"}


def scan(max_finalists: Optional[int] = None,
         strategies: Optional[tuple] = None) -> Dict[str, Any]:
    """The full funnel. Returns ranked finalists WITHOUT running the decision engine —
    the caller decides how many deserve deep analysis."""
    strategies = strategies or cfg.enabled_strategies()
    max_finalists = max_finalists or cfg.max_finalists()

    sect = rank_sectors()
    if sect.get("state") != "ok":
        return {"state": "sector_map_unavailable", "reason": sect.get("reason"),
                "finalists": [], "candidates": []}

    # Strongest sectors supply leadership candidates; the weakest are scanned too, but
    # only for setups that stand on their own (momentum / mean reversion) — never as
    # "relative strength", which is meaningless in a lagging sector.
    focus = [(s["key"], s["name"], s["sector_score"], True) for s in sect["strong"]]
    strong_keys = {s["key"] for s in sect["strong"]}
    focus += [(s["key"], s["name"], s["sector_score"], False)
              for s in sect["weak"] if s["key"] not in strong_keys]

    candidates: List[Dict[str, Any]] = []
    seen: set = set()
    for key, name, sscore, is_strong in focus:
        for sym in _members(key):
            if sym in seen:
                continue
            seen.add(sym)
            b = _bars(sym)
            if not b:
                continue
            for st in strategies:
                c = None
                if st == "liquid_momentum":
                    c = score_liquid_momentum(sym, b)
                elif st == "sector_relative_strength":
                    c = score_sector_rs(sym, b, sscore, name, is_strong)
                elif st == "mean_reversion":
                    c = score_mean_reversion(sym, b)
                if c:
                    c.update(sector=key, sector_name=name, sector_is_strong=is_strong,
                             as_of=b.get("as_of"))
                    candidates.append(c)

    candidates.sort(key=lambda x: x["score"], reverse=True)
    # One finalist per symbol (best-scoring strategy wins) so we never double-enter.
    finalists: List[Dict[str, Any]] = []
    used: set = set()
    for c in candidates:
        if c["symbol"] in used:
            continue
        used.add(c["symbol"])
        finalists.append({**c, "scanner_rank": len(finalists) + 1})
        if len(finalists) >= max_finalists:
            break

    return {"state": "ok", "sectors_considered": [f[1] for f in focus],
            "candidates": candidates, "finalists": finalists,
            "candidate_count": len(candidates), "finalist_count": len(finalists),
            "sector_ranking": [{"name": s["name"], "score": s["sector_score"],
                                "rs": s.get("rs_vs_spy_1m")} for s in sect["ranked"]],
            "freshness": sect.get("freshness")}
