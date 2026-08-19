"""HISTORICAL STRATEGY VALIDATION — what happened after this setup, historically.

The question is not "what followed an RSI of 70 on PLTR". It is: **when this exact
scanner strategy fired under comparable conditions, what happened next?** So the engine
re-runs the SAME code the live scanner runs — `scanner.indicators`, `scanner.detect_setups`,
`scanner._levels` — at each historical bar, and measures the forward outcome.

═══ ANTI-LOOK-AHEAD ═══════════════════════════════════════════════════════════
This is the part that makes the numbers worth anything, so it is enforced
structurally rather than by convention:

  1. Features at bar *i* are computed from a STRICT PREFIX `bars[i-WINDOW+1 : i+1]`.
     The slice is taken before any indicator code runs, so no function can reach
     forward even by accident — there is nothing forward to reach.
  2. The market regime at bar *i* comes from SPY's OWN bars up to *i*, and the sector
     rank from the 11 sector ETFs' bars up to *i*. Today's regime and today's sector
     ranking are never applied to a 2023 signal.
  3. Entry / stop / targets come from `scanner._levels` on those prefix indicators —
     the same levels a trader would have been given that day.
  4. The OUTCOME uses only `bars[i+1:]`. Measuring the future is the whole point; the
     rule is that nothing about the future may enter the SETUP.
  5. Instances whose full outcome horizon is not yet available are DROPPED, not
     truncated — otherwise recent signals would be silently biased toward "no exit".
  6. No historical news, sentiment or analyst rating is used. None is stored, and
     back-filling today's labels onto old headlines would be the purest form of
     look-ahead. The engine says so explicitly instead.

═══ HONESTY RULES ═════════════════════════════════════════════════════════════
  * Same-bar ambiguity: if one bar's low reaches the stop AND its high reaches the
    target, daily bars cannot say which came first. It is counted as a STOP. The
    pessimistic reading is the only defensible one.
  * Overlapping signals: a setup that stays true for eight sessions is ONE trade, not
    eight. A minimum gap is enforced so correlated days cannot inflate the sample.
  * Small samples do not get a verdict. Below `MIN_SAMPLE` the result is
    INSUFFICIENT_SAMPLE — never "75% win rate (N=4)".
  * Options are NOT backtested. No historical chain, IV or Greek series exists in this
    repo, and synthesising one would be fabrication. The engine validates the
    UNDERLYING strategy and says the option leg is unvalidated.
"""
from __future__ import annotations

import math
import os
import statistics
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in ("..", os.path.join("..", "lab"), os.path.join("..", "src")):
    sys.path.insert(0, os.path.join(_HERE, _p))
sys.path.insert(0, _HERE)

import research as _R
import scanner as _SC


# ── Configuration ────────────────────────────────────────────────────────────

def _i(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Enough prefix for every indicator the scanner computes (SMA200 is the deepest).
WINDOW = 260
# Bars of forward data required before an instance counts.
HORIZON = _i("HIST_HORIZON_DAYS", 20)
# A setup true on consecutive days is one trade. Minimum spacing between instances.
MIN_GAP = _i("HIST_MIN_GAP_BARS", 5)
# Below this, report INSUFFICIENT_SAMPLE rather than a statistic.
MIN_SAMPLE = _i("HIST_MIN_SAMPLE", 12)
# Similarity below this is not a comparable setup.
SIM_FLOOR = _f("HIST_SIM_FLOOR", 0.55)
LOOKBACK = os.environ.get("HIST_LOOKBACK_RANGE", "10y")

SECTOR_ETFS = ("XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLU", "XLRE", "XLC")

CLASSES = ("STRONG HISTORY", "MIXED HISTORY", "WEAK HISTORY", "INSUFFICIENT SAMPLE",
           "NO HISTORY")


# ── Prefix feature computation (the anti-look-ahead core) ────────────────────

def _prefix(bars: List[dict], i: int) -> List[dict]:
    """bars[..i] only. Everything downstream sees a series that ENDS at bar i, so no
    indicator can consult the future — there is no future in the object it receives."""
    lo = max(0, i - WINDOW + 1)
    return bars[lo:i + 1]


def _indicators_at(symbol: str, bars: List[dict], i: int) -> Dict[str, Any]:
    """The live scanner's own indicator function, on a strict prefix."""
    return _SC.indicators(symbol, {"bars": _prefix(bars, i),
                                   "source_timestamp": bars[i].get("t")},
                          session_fraction=None)


def _pct_change(bars: List[dict], i: int, n: int) -> Optional[float]:
    if i - n < 0:
        return None
    a, b = bars[i - n].get("c"), bars[i].get("c")
    return ((b - a) / a * 100) if (a and b) else None


def _realized_vol(bars: List[dict], i: int, n: int = 20) -> Optional[float]:
    if i - n < 0:
        return None
    cs = [b["c"] for b in bars[i - n:i + 1] if b.get("c")]
    if len(cs) < 5:
        return None
    rets = [(cs[k] - cs[k - 1]) / cs[k - 1] for k in range(1, len(cs)) if cs[k - 1]]
    if len(rets) < 3:
        return None
    m = sum(rets) / len(rets)
    sd = (sum((x - m) ** 2 for x in rets) / len(rets)) ** 0.5
    return sd * math.sqrt(252) * 100


def _index_by_date(bars: List[dict]) -> Dict[str, int]:
    return {str(b.get("t", ""))[:10]: k for k, b in enumerate(bars)}


def _regime_at(spy: List[dict], idx: Dict[str, int], date: str) -> Dict[str, Any]:
    """Market regime from SPY's OWN history up to `date`. Never today's regime."""
    j = idx.get(date)
    if j is None or j < 60:
        return {"state": "unknown", "spy_20d": None, "spy_vol": None,
                "trend": None, "note": "no SPY history at this date"}
    spy20 = _pct_change(spy, j, 20)
    spy60 = _pct_change(spy, j, 60)
    vol = _realized_vol(spy, j, 20)
    closes = [b["c"] for b in spy[max(0, j - 199):j + 1] if b.get("c")]
    sma200 = (sum(closes) / len(closes)) if len(closes) >= 150 else None
    above = (spy[j]["c"] > sma200) if sma200 else None
    if spy20 is None:
        trend = None
    elif spy20 > 2 and above:
        trend = "bull"
    elif spy20 < -2 or above is False:
        trend = "bear"
    else:
        trend = "sideways"
    vol_regime = None if vol is None else ("high" if vol > 22 else "low" if vol < 12 else "normal")
    return {"state": "ok", "spy_20d": round(spy20, 2) if spy20 is not None else None,
            "spy_60d": round(spy60, 2) if spy60 is not None else None,
            "spy_vol": round(vol, 1) if vol is not None else None,
            "trend": trend, "vol_regime": vol_regime,
            "above_sma200": above}


def _sector_ranks_at(etfs: Dict[str, Tuple[List[dict], Dict[str, int]]],
                     date: str) -> Dict[str, int]:
    """Rank the 11 sector ETFs by their own 20-day return AS OF `date`."""
    perf = []
    for sym, (bars, idx) in etfs.items():
        j = idx.get(date)
        if j is None or j < 21:
            continue
        c = _pct_change(bars, j, 20)
        if c is not None:
            perf.append((sym, c))
    perf.sort(key=lambda x: -x[1])
    return {s: r + 1 for r, (s, _) in enumerate(perf)}


# ── Outcome measurement (the ONLY place the future is allowed) ───────────────

def _outcome(bars: List[dict], i: int, lv: Dict[str, Any],
             horizon: int = HORIZON) -> Optional[Dict[str, Any]]:
    """What happened after bar i. Uses bars[i+1:] exclusively."""
    entry = lv.get("reference_price")
    stop = lv.get("invalidation")
    t1, t2 = lv.get("target_1"), lv.get("target_2")
    if not entry or not stop or not t1:
        return None
    fwd = bars[i + 1:i + 1 + horizon]
    if len(fwd) < horizon:
        return None                      # incomplete horizon — dropped, never truncated

    hit_t1 = hit_t2 = hit_stop = None
    mfe = mae = 0.0
    for k, b in enumerate(fwd):
        h, l = b.get("h"), b.get("l")
        if h is None or l is None:
            continue
        mfe = max(mfe, (h - entry) / entry * 100)
        mae = min(mae, (l - entry) / entry * 100)
        # Same-bar ambiguity resolves to the STOP: daily bars cannot order the two,
        # and assuming the favourable one would flatter every result in the table.
        if hit_stop is None and l <= stop:
            hit_stop = k + 1
        if hit_t1 is None and h >= t1:
            hit_t1 = k + 1
        if hit_t2 is None and t2 and h >= t2:
            hit_t2 = k + 1
        if hit_stop is not None or hit_t2 is not None:
            break

    risk = entry - stop
    if hit_stop is not None and (hit_t1 is None or hit_t1 >= hit_stop):
        exit_px, exit_reason, held = stop, "stop", hit_stop
    elif hit_t2 is not None:
        exit_px, exit_reason, held = t2, "target_2", hit_t2
    elif hit_t1 is not None:
        exit_px, exit_reason, held = t1, "target_1", hit_t1
    else:
        exit_px, exit_reason, held = fwd[-1].get("c"), "horizon", len(fwd)
    if exit_px is None:
        return None

    ret = (exit_px - entry) / entry * 100
    r_multiple = (exit_px - entry) / risk if risk else None
    return {
        "exit_reason": exit_reason, "held_days": held,
        "return_pct": round(ret, 2),
        "r_multiple": round(r_multiple, 2) if r_multiple is not None else None,
        "hit_t1": hit_t1 is not None and (hit_stop is None or hit_t1 < hit_stop),
        "hit_t2": hit_t2 is not None and (hit_stop is None or hit_t2 < hit_stop),
        "hit_stop": exit_reason == "stop",
        "mfe_pct": round(mfe, 2), "mae_pct": round(mae, 2),
        "ret_5d": _fwd_return(bars, i, 5, entry),
        "ret_10d": _fwd_return(bars, i, 10, entry),
        "ret_20d": _fwd_return(bars, i, 20, entry),
    }


def _fwd_return(bars: List[dict], i: int, n: int, entry: float) -> Optional[float]:
    if i + n >= len(bars):
        return None
    c = bars[i + n].get("c")
    return round((c - entry) / entry * 100, 2) if c else None


# ── Similarity ───────────────────────────────────────────────────────────────
# Only variables knowable at decision time. Each is normalised by a sensible scale so
# one wide-ranging feature cannot dominate the distance.

_SIM_FEATURES: Tuple[Tuple[str, float, float], ...] = (
    # (name, scale, weight)
    ("atr_pct", 4.0, 1.0),
    ("rsi14", 25.0, 1.0),
    ("rel_volume", 1.5, 1.0),
    ("pos_in_20d_range", 40.0, 0.8),
    ("rel_strength_20d", 15.0, 1.4),
    ("chg_20d", 20.0, 0.7),
)
_SIM_FLAGS: Tuple[Tuple[str, float], ...] = (
    ("above_sma20", 0.6), ("above_sma50", 0.8), ("above_sma200", 0.8),
)


def _feature_vector(ind: Dict[str, Any], regime: Dict[str, Any],
                    sector_rank: Optional[int]) -> Dict[str, Any]:
    spy20 = regime.get("spy_20d")
    chg20 = ind.get("chg_20d")
    return {
        "atr_pct": ind.get("atr_pct"), "rsi14": ind.get("rsi14"),
        "rel_volume": ind.get("rel_volume"),
        "pos_in_20d_range": ind.get("pos_in_20d_range"),
        "chg_20d": chg20,
        "rel_strength_20d": (chg20 - spy20) if (chg20 is not None and spy20 is not None) else None,
        "above_sma20": ind.get("above_sma20"), "above_sma50": ind.get("above_sma50"),
        "above_sma200": ind.get("above_sma200"),
        "regime_trend": regime.get("trend"), "vol_regime": regime.get("vol_regime"),
        "sector_rank": sector_rank,
    }


def similarity(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    """0..1 over the variables that define the setup. Missing on either side is
    neither credited nor penalised — it simply does not vote."""
    num = den = 0.0
    for name, scale, w in _SIM_FEATURES:
        x, y = a.get(name), b.get(name)
        if x is None or y is None:
            continue
        num += w * max(0.0, 1.0 - abs(float(x) - float(y)) / scale)
        den += w
    for name, w in _SIM_FLAGS:
        x, y = a.get(name), b.get(name)
        if x is None or y is None:
            continue
        num += w * (1.0 if bool(x) == bool(y) else 0.0)
        den += w
    # Regime agreement is part of the setup, not decoration: a momentum signal in a
    # bull tape is not the same event as the identical signal in a selloff.
    for name, w in (("regime_trend", 1.2), ("vol_regime", 0.6)):
        x, y = a.get(name), b.get(name)
        if x is None or y is None:
            continue
        num += w * (1.0 if x == y else 0.0)
        den += w
    ar, br = a.get("sector_rank"), b.get("sector_rank")
    if ar and br:
        num += 0.5 * max(0.0, 1.0 - abs(ar - br) / 10.0)
        den += 0.5
    return round(num / den, 3) if den else 0.0


# ── Instance discovery ───────────────────────────────────────────────────────

def _load_context(lookback: str = LOOKBACK) -> Dict[str, Any]:
    """SPY + the 11 sector ETFs, once, cached. All of it is OHLCV — no live state."""
    def _build():
        want = ["SPY"] + list(SECTOR_ETFS)
        got = _R.bars_batch(want, rng=lookback, interval="1d", ttl=6 * 3600, timeout=90.0)
        spy = (got.get("SPY") or {}).get("bars") or []
        etfs = {}
        for s in SECTOR_ETFS:
            b = (got.get(s) or {}).get("bars") or []
            if b:
                etfs[s] = (b, _index_by_date(b))
        return {"spy": spy, "spy_idx": _index_by_date(spy), "etfs": etfs}
    return _R.cached(f"hist:ctx:{lookback}", 6 * 3600, _build)


# Sector key -> the ETF used as its historical proxy.
_SECTOR_ETF_FOR = {
    "technology": "XLK", "financials": "XLF", "financial": "XLF", "energy": "XLE",
    "healthcare": "XLV", "health_care": "XLV", "consumer_discretionary": "XLY",
    "consumer_staples": "XLP", "industrials": "XLI", "materials": "XLB",
    "utilities": "XLU", "real_estate": "XLRE", "communication_services": "XLC",
    "communication": "XLC",
}


def scan_history(symbol: str, *, strategy: Optional[str] = None,
                 lookback: str = LOOKBACK, horizon: int = HORIZON,
                 sector: Optional[str] = None) -> Dict[str, Any]:
    """Every historical instance of `strategy` on `symbol`, with its outcome."""
    sym = (symbol or "").upper().strip()
    b = _R.bars(sym, rng=lookback, interval="1d", ttl=6 * 3600)
    bars = [x for x in (b.get("bars") or []) if x.get("c") is not None]
    if len(bars) < WINDOW + horizon + 10:
        return {"state": "insufficient_history", "symbol": sym,
                "bars": len(bars),
                "reason": f"{len(bars)} daily bars — need at least "
                          f"{WINDOW + horizon + 10} to compute features and a "
                          f"complete {horizon}-day outcome"}

    ctx = _load_context(lookback)
    spy, spy_idx, etfs = ctx["spy"], ctx["spy_idx"], ctx["etfs"]
    etf = _SECTOR_ETF_FOR.get((sector or "").lower()) if sector else None
    cfg = _SC.config()

    instances: List[Dict[str, Any]] = []
    considered = 0
    last_i = -10 ** 9
    # Stop early enough that every instance has a COMPLETE forward horizon.
    end = len(bars) - horizon - 1
    for i in range(WINDOW - 1, end):
        date = str(bars[i].get("t", ""))[:10]
        ind = _indicators_at(sym, bars, i)
        if ind.get("state") != "ok":
            continue
        considered += 1
        regime = _regime_at(spy, spy_idx, date)
        ranks = _sector_ranks_at(etfs, date) if etfs else {}
        srank = ranks.get(etf) if etf else None
        setups = _SC.detect_setups(ind, srank, len(ranks) or 11, regime.get("spy_20d"))
        if not setups:
            continue
        kinds = {s["type"] for s in setups}
        if strategy and strategy not in kinds:
            continue
        if i - last_i < MIN_GAP:
            continue                     # correlated repeat of the same trade
        lv = _SC._levels(ind, cfg)
        if lv.get("state") != "ok":
            continue
        out = _outcome(bars, i, lv, horizon)
        if out is None:
            continue
        last_i = i
        instances.append({
            "date": date, "index": i,
            "features": _feature_vector(ind, regime, srank),
            "regime": {k: regime.get(k) for k in ("trend", "vol_regime", "spy_20d")},
            "setups": sorted(kinds),
            "levels": {k: lv.get(k) for k in ("reference_price", "invalidation",
                                              "target_1", "target_2", "rr_target_1")},
            "outcome": out,
        })

    return {"state": "ok", "symbol": sym, "strategy": strategy,
            "lookback": lookback, "horizon": horizon,
            "bars_scanned": considered, "instances": instances,
            "count": len(instances),
            "first": instances[0]["date"] if instances else None,
            "last": instances[-1]["date"] if instances else None,
            "sector_proxy": etf}


def scan_history_cached(symbol: str, strategy: Optional[str] = None,
                        sector: Optional[str] = None) -> Dict[str, Any]:
    key = f"hist:scan:{(symbol or '').upper()}:{strategy or '*'}"
    return _R.cached(key, 12 * 3600,
                     lambda: scan_history(symbol, strategy=strategy, sector=sector))


# ── Aggregation ──────────────────────────────────────────────────────────────

def _stats(outcomes: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(outcomes)
    if not n:
        return {}
    rets = [o["return_pct"] for o in outcomes]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    streak = worst = 0
    for o in outcomes:
        if o["return_pct"] <= 0:
            streak += 1
            worst = max(worst, streak)
        else:
            streak = 0

    def _avg(xs):
        return round(sum(xs) / len(xs), 2) if xs else None

    def _hzn(key):
        xs = [o[key] for o in outcomes if o.get(key) is not None]
        return {"n": len(xs), "avg": _avg(xs),
                "median": round(statistics.median(xs), 2) if xs else None,
                "positive_pct": round(100 * sum(1 for x in xs if x > 0) / len(xs), 1) if xs else None}

    return {
        "sample_size": n,
        "win_rate": round(100 * len(wins) / n, 1),
        "t1_hit_rate": round(100 * sum(1 for o in outcomes if o["hit_t1"]) / n, 1),
        "t2_hit_rate": round(100 * sum(1 for o in outcomes if o["hit_t2"]) / n, 1),
        "stop_hit_rate": round(100 * sum(1 for o in outcomes if o["hit_stop"]) / n, 1),
        "avg_return_pct": _avg(rets),
        "median_return_pct": round(statistics.median(rets), 2),
        "avg_mfe_pct": _avg([o["mfe_pct"] for o in outcomes]),
        "avg_mae_pct": _avg([o["mae_pct"] for o in outcomes]),
        "avg_hold_days": _avg([o["held_days"] for o in outcomes]),
        "profit_factor": (round(gross_win / gross_loss, 2) if gross_loss > 0
                          else (None if gross_win == 0 else float("inf"))),
        "expectancy_pct": _avg(rets),
        "expectancy_r": _avg([o["r_multiple"] for o in outcomes if o.get("r_multiple") is not None]),
        "max_loss_pct": round(min(rets), 2),
        "max_gain_pct": round(max(rets), 2),
        "max_losing_streak": worst,
        "ret_5d": _hzn("ret_5d"), "ret_10d": _hzn("ret_10d"), "ret_20d": _hzn("ret_20d"),
        "exit_mix": {r: sum(1 for o in outcomes if o["exit_reason"] == r)
                     for r in ("target_1", "target_2", "stop", "horizon")},
    }


def _classify(stats: Dict[str, Any], n: int) -> Tuple[str, str]:
    if n == 0:
        return "NO HISTORY", "this strategy has never fired on this symbol in the window"
    if n < MIN_SAMPLE:
        return ("INSUFFICIENT SAMPLE",
                f"{n} comparable setup(s) — below the {MIN_SAMPLE} needed before a "
                f"hit rate means anything. No verdict is drawn from this.")
    pf = stats.get("profit_factor")
    exp = stats.get("expectancy_pct") or 0.0
    t1 = stats.get("t1_hit_rate") or 0.0
    stop = stats.get("stop_hit_rate") or 0.0
    pf_v = 0.0 if pf is None else (3.0 if pf == float("inf") else pf)
    if exp > 0.8 and pf_v >= 1.6 and t1 >= 50 and stop <= 40:
        return "STRONG HISTORY", f"expectancy {exp:+.1f}%, profit factor {pf_v:.2f}, T1 hit {t1:.0f}%"
    if exp <= 0 or pf_v < 1.0:
        return "WEAK HISTORY", f"expectancy {exp:+.1f}% with profit factor {pf_v:.2f} — this setup has not paid"
    return "MIXED HISTORY", f"expectancy {exp:+.1f}%, profit factor {pf_v:.2f}, T1 hit {t1:.0f}%"


def evaluate(symbol: str, *, strategy: Optional[str] = None,
             current_features: Optional[Dict[str, Any]] = None,
             sector: Optional[str] = None,
             sim_floor: float = SIM_FLOOR) -> Dict[str, Any]:
    """HISTORICAL FIT for the setup that is live right now."""
    scan = scan_history_cached(symbol, strategy, sector)
    if scan.get("state") != "ok":
        return {"state": scan.get("state", "error"), "symbol": symbol,
                "strategy": strategy, "classification": "NO HISTORY",
                "reason": scan.get("reason"), "sample_size": 0,
                "options_note": OPTIONS_NOTE, "news_note": NEWS_NOTE}

    insts = scan["instances"]
    if current_features:
        for it in insts:
            it["similarity"] = similarity(current_features, it["features"])
        comparable = [it for it in insts if it["similarity"] >= sim_floor]
        comparable.sort(key=lambda it: -it["similarity"])
        fit = (round(100 * sum(it["similarity"] for it in comparable) / len(comparable))
               if comparable else 0)
    else:
        comparable = insts
        for it in comparable:
            it["similarity"] = None
        fit = None

    outcomes = [it["outcome"] for it in comparable]
    stats = _stats(outcomes)
    cls, why = _classify(stats, len(comparable))
    return {
        "state": "ok", "symbol": (symbol or "").upper(), "strategy": strategy,
        "classification": cls, "classification_reason": why,
        "fit_pct": fit,
        "sample_size": len(comparable),
        "total_instances": len(insts),
        "similarity_floor": sim_floor,
        "window": {"lookback": scan["lookback"], "first": scan.get("first"),
                   "last": scan.get("last"), "bars_scanned": scan.get("bars_scanned")},
        "horizon_days": scan["horizon"],
        "stats": stats,
        "examples": [{"date": it["date"], "similarity": it["similarity"],
                      "regime": it["regime"],
                      "return_pct": it["outcome"]["return_pct"],
                      "exit": it["outcome"]["exit_reason"],
                      "held_days": it["outcome"]["held_days"],
                      "mfe": it["outcome"]["mfe_pct"], "mae": it["outcome"]["mae_pct"]}
                     for it in comparable[:12]],
        "methodology": METHODOLOGY,
        "options_note": OPTIONS_NOTE,
        "news_note": NEWS_NOTE,
        "sector_proxy": scan.get("sector_proxy"),
    }


METHODOLOGY = (
    "Features are recomputed by the live scanner's own functions on a strict prefix of "
    "daily bars ending at each historical date; the market regime comes from SPY's bars "
    "and the sector rank from the 11 sector ETFs' bars, all as of that same date. Entry, "
    "invalidation and targets come from the same `_levels` a trader would have been "
    "given. Outcomes read only later bars. Signals within "
    f"{MIN_GAP} bars of each other count once. Instances without a complete "
    f"{HORIZON}-bar forward window are dropped. When a single bar touches both the stop "
    "and the target, it is recorded as a stop."
)
OPTIONS_NOTE = (
    "Underlying strategy historically validated. Historical option execution NOT "
    "validated — this repo stores no historical option chains, IV or Greeks, and "
    "synthesising them would not be a backtest."
)
NEWS_NOTE = (
    "Historical news unavailable — no timestamped headline archive is stored, so "
    "sentiment is deliberately excluded from historical matching rather than "
    "back-filled from today's labels. Technical and regime validation only."
)


def current_features(candidate: Dict[str, Any],
                     regime: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build today's feature vector in the SAME shape the history engine produces, so
    the two are compared like with like."""
    ind = candidate.get("indicators") or {}
    reg = regime or {}
    trend = reg.get("trend")
    if trend is None:
        risk = reg.get("risk_appetite")
        trend = {"risk-on": "bull", "risk-off": "bear", "mixed": "sideways"}.get(risk)
    spy20 = reg.get("spy_20d")
    chg20 = ind.get("chg_20d")
    return {
        "atr_pct": ind.get("atr_pct"), "rsi14": ind.get("rsi14"),
        "rel_volume": ind.get("rel_volume"),
        "pos_in_20d_range": ind.get("pos_in_20d_range"),
        "chg_20d": chg20,
        "rel_strength_20d": (chg20 - spy20) if (chg20 is not None and spy20 is not None) else None,
        "above_sma20": ind.get("above_sma20"), "above_sma50": ind.get("above_sma50"),
        "above_sma200": ind.get("above_sma200"),
        "regime_trend": trend, "vol_regime": reg.get("vol_regime"),
        "sector_rank": candidate.get("sector_rank"),
    }
