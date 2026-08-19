"""Phases 3-5 — sector-first broad scan, staged filtering, transparent scoring.

The funnel, in order, with every stage countable and every drop explained:

    universe        the broad eligible pool from the security master
    eligible        survives the CHEAP filters (price, liquidity, quote sanity)
    sector_aligned  belongs to a sector that is actually ranking
    deep            got a full OHLCV pull, indicators and setup detection
    top25           ranked by score for deeper reading
    top10           the actionable watchlist
    finalists       the 3-5 that would be worth an option/share decision
    rejected        everything dropped, each with the stage and the reason

Two rules the code is built around:

  * A stock is never forced into a setup. `detect_setups` returns an empty list when
    nothing matches, and a name with no setup cannot become a finalist.
  * A score is never a black box. `score()` returns the component breakdown that
    sums to the total, including the penalties, so the number can be argued with.

Nothing here is a prediction and nothing here places an order.
"""
from __future__ import annotations

import math
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in ("..", os.path.join("..", "lab"), os.path.join("..", "src")):
    sys.path.insert(0, os.path.join(_HERE, _p))
sys.path.insert(0, _HERE)

import research as _R
import sector_map as _SM
import sector_lookup as _SL
import market_regime as _MR


# ── Configuration (all env-overridable; nothing hard-coded into a decision) ──

def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def _i(name: str, default: int) -> int:
    return int(_f(name, default))


def config() -> Dict[str, Any]:
    return {
        "universe_limit": _i("SCAN_UNIVERSE_LIMIT", 800),
        "deep_n": _i("SCAN_DEEP_N", 70),
        "top25_n": _i("SCAN_TOP25_N", 25),
        "top10_n": _i("SCAN_TOP10_N", 10),
        "finalists_n": _i("SCAN_FINALISTS_N", 5),
        "min_price": _f("SCAN_MIN_PRICE", 5.0),
        "max_price": _f("SCAN_MAX_PRICE", 100000.0),
        "min_dollar_volume": _f("SCAN_MIN_DOLLAR_VOL", 20_000_000.0),
        "min_share_volume": _f("SCAN_MIN_SHARE_VOL", 400_000.0),
        "max_quote_age_hours": _f("SCAN_MAX_QUOTE_AGE_H", 30.0),
        "min_atr_pct": _f("SCAN_MIN_ATR_PCT", 1.0),
        "max_atr_pct": _f("SCAN_MAX_ATR_PCT", 12.0),
        "min_rr": _f("SCAN_MIN_RR", 1.8),
        "top_sectors": _i("SCAN_TOP_SECTORS", 5),
        "leveraged_allowed": os.environ.get("SCAN_ALLOW_LEVERAGED", "false").lower() == "true",
    }


# Leveraged/inverse products: excluded unless explicitly enabled AND labelled.
_LEVERAGED_HINTS = ("2X", "3X", "ULTRA", "INVERSE", "BEAR", "BULL 3", "LEVERAGED", "-1X")
_LEVERAGED_SYMBOLS = {"TQQQ", "SQQQ", "SPXL", "SPXS", "UPRO", "SPXU", "SOXL", "SOXS",
                      "TNA", "TZA", "LABU", "LABD", "FAS", "FAZ", "NUGT", "DUST",
                      "UVXY", "SVXY", "VXX", "YINN", "YANG", "TMF", "TMV"}


# ── Indicators (computed from the pooled OHLCV, no extra providers) ──────────

def _sma(xs: List[float], n: int) -> Optional[float]:
    return sum(xs[-n:]) / n if len(xs) >= n else None


def _atr(bars: List[dict], n: int = 14) -> Optional[float]:
    if len(bars) < n + 1:
        return None
    trs = []
    for i in range(len(bars) - n, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        if None in (h, l, pc):
            continue
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs) if trs else None


def _rsi(closes: List[float], n: int = 14) -> Optional[float]:
    if len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(len(closes) - n, len(closes)):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    if gains == 0 and losses == 0:
        return None          # a series that never moved has no RSI — 100 would be a lie
    if losses == 0:
        return 100.0
    rs = (gains / n) / (losses / n)
    return round(100 - 100 / (1 + rs), 1)


def session_fraction_elapsed(now: Optional[datetime] = None) -> Optional[float]:
    """Fraction of the regular session that has elapsed, or None when it is not open.

    Used to pro-rate relative volume. Today's daily bar holds only the volume traded
    SO FAR, so dividing it by a 20-day average of COMPLETE days makes every name look
    like it is trading at a third of normal at midday — a systematic under-score of
    the volume-confirmation component for the whole session.
    """
    s = _MR.session_state(now)
    if s["state"] != "open":
        return None
    total = 6.5 * 3600
    if s.get("early_close"):
        total = 3.5 * 3600
    remaining = s.get("seconds_until_close")
    if remaining is None:
        return None
    return max(0.02, min(1.0, (total - remaining) / total))


def indicators(sym: str, b: Dict[str, Any],
               session_fraction: Optional[float] = None) -> Dict[str, Any]:
    """Everything the scanner needs from one OHLCV series. Missing inputs produce
    None fields and a `missing` list — never a substituted value."""
    bars = [x for x in (b.get("bars") or []) if x.get("c") is not None]
    out: Dict[str, Any] = {"symbol": sym, "bars_count": len(bars),
                           "source_timestamp": b.get("source_timestamp"), "missing": []}
    if len(bars) < 30:
        out["state"] = "insufficient_history"
        out["missing"].append("history")
        return out
    closes = [x["c"] for x in bars]
    highs = [x["h"] for x in bars if x["h"] is not None]
    lows = [x["l"] for x in bars if x["l"] is not None]
    vols = [x["v"] or 0 for x in bars]
    last, prev = closes[-1], closes[-2]
    today = bars[-1]

    sma20, sma50, sma200 = _sma(closes, 20), _sma(closes, 50), _sma(closes, 200)
    atr = _atr(bars, 14)
    rets = [(closes[i] - closes[i - 1]) / closes[i - 1]
            for i in range(1, len(closes)) if closes[i - 1]]

    def _rvol(n):
        w = rets[-n:]
        if len(w) < 5:
            return None
        m = sum(w) / len(w)
        sd = (sum((x - m) ** 2 for x in w) / len(w)) ** 0.5
        return round(sd * (252 ** 0.5) * 100, 1)

    avg_vol20 = _sma([float(v) for v in vols], 20)
    dollar_vol = (avg_vol20 * last) if (avg_vol20 and last) else None

    def _chg(n):
        return round((closes[-1] - closes[-1 - n]) / closes[-1 - n] * 100, 2) if len(closes) > n else None

    hi20, lo20 = (max(highs[-20:]), min(lows[-20:])) if len(highs) >= 20 else (None, None)
    hi55 = max(highs[-55:]) if len(highs) >= 55 else None
    # typical-price VWAP over the session's own bar (daily proxy — labelled as such)
    vwap_proxy = round((today["h"] + today["l"] + today["c"]) / 3, 4) if None not in (
        today["h"], today["l"], today["c"]) else None
    gap_pct = round((today["o"] - prev) / prev * 100, 2) if (today.get("o") and prev) else None

    # Relative volume, pro-rated when the session is still running (see
    # `session_fraction_elapsed`). The un-adjusted figure is kept as `rel_volume_raw`
    # so the adjustment is visible rather than baked in silently.
    rel_vol, rel_vol_basis = None, None
    if avg_vol20 and today.get("v"):
        raw = today["v"] / avg_vol20
        if session_fraction and 0 < session_fraction < 0.995:
            rel_vol = round(raw / session_fraction, 2)
            rel_vol_basis = (f"pro-rated: {session_fraction:.0%} of the session elapsed "
                             f"(raw {raw:.2f}x of a full-day average)")
        else:
            rel_vol = round(raw, 2)
            rel_vol_basis = "full session vs 20-day average"

    out.update({
        "state": "ok",
        "price": round(last, 4), "prev_close": round(prev, 4),
        "change_pct": round((last - prev) / prev * 100, 2) if prev else None,
        "open": today.get("o"), "high": today.get("h"), "low": today.get("l"),
        "volume": today.get("v"), "avg_volume_20d": round(avg_vol20) if avg_vol20 else None,
        "rel_volume": rel_vol, "rel_volume_basis": rel_vol_basis,
        "rel_volume_raw": (round(today["v"] / avg_vol20, 2)
                           if (avg_vol20 and today.get("v")) else None),
        "dollar_volume_20d": round(dollar_vol) if dollar_vol else None,
        "sma20": round(sma20, 4) if sma20 else None,
        "sma50": round(sma50, 4) if sma50 else None,
        "sma200": round(sma200, 4) if sma200 else None,
        "above_sma20": (last > sma20) if sma20 else None,
        "above_sma50": (last > sma50) if sma50 else None,
        "above_sma200": (last > sma200) if sma200 else None,
        "atr14": round(atr, 4) if atr else None,
        "atr_pct": round(atr / last * 100, 2) if (atr and last) else None,
        # close-to-close realized vol, annualised — the honest yardstick for IV
        "realized_vol_20d": _rvol(20), "realized_vol_60d": _rvol(60),
        "rsi14": _rsi(closes, 14),
        "chg_1d": _chg(1), "chg_5d": _chg(5), "chg_20d": _chg(20), "chg_60d": _chg(60),
        "high_20d": round(hi20, 4) if hi20 else None,
        "low_20d": round(lo20, 4) if lo20 else None,
        "high_55d": round(hi55, 4) if hi55 else None,
        "pct_from_20d_high": round((last - hi20) / hi20 * 100, 2) if hi20 else None,
        "pos_in_20d_range": round((last - lo20) / (hi20 - lo20) * 100, 1) if (hi20 and lo20 and hi20 > lo20) else None,
        "vwap_proxy": vwap_proxy, "gap_pct": gap_pct,
        "support": round(lo20, 4) if lo20 else None,
        "resistance": round(hi20, 4) if hi20 else None,
    })
    for k in ("sma200", "atr14", "rel_volume"):
        if out.get(k) is None:
            out["missing"].append(k)
    return out


# ── Stage 1: cheap eligibility ───────────────────────────────────────────────

def eligibility(sym: str, q: Dict[str, Any], cfg: Dict[str, Any],
                name: str = "") -> Tuple[bool, List[str], Dict[str, Any]]:
    """The cheap gate: one quote per name, no history. Returns (ok, reasons, facts)."""
    reasons: List[str] = []
    price = q.get("price")
    vol = q.get("volume")
    facts = {"price": price, "volume": vol, "state": q.get("state"),
             "source_timestamp": q.get("source_timestamp")}

    if q.get("state") != "ok" or price is None:
        reasons.append(f"no usable quote ({q.get('state')}{': ' + str(q.get('reason'))[:40] if q.get('reason') else ''})")
        return False, reasons, facts
    if price < cfg["min_price"]:
        reasons.append(f"price ${price:.2f} below ${cfg['min_price']:.2f} minimum (microcap noise filter)")
    if price > cfg["max_price"]:
        reasons.append(f"price ${price:.2f} above ${cfg['max_price']:.0f} maximum")
    if vol is None:
        reasons.append("no volume reported")
    elif vol < cfg["min_share_volume"]:
        reasons.append(f"share volume {vol:,.0f} below {cfg['min_share_volume']:,.0f}")
    if price is not None and vol:
        dv = price * vol
        facts["dollar_volume"] = round(dv)
        if dv < cfg["min_dollar_volume"]:
            reasons.append(f"dollar volume ${dv/1e6:.1f}M below ${cfg['min_dollar_volume']/1e6:.0f}M")

    import freshness as _fr
    age = _fr.bar_age_seconds(q.get("source_timestamp"))
    facts["quote_age_hours"] = round(age / 3600, 2) if age is not None else None
    if age is None:
        reasons.append("quote carries no source timestamp — cannot verify freshness")
    elif age > cfg["max_quote_age_hours"] * 3600:
        reasons.append(f"quote {age/3600:.1f}h old (limit {cfg['max_quote_age_hours']:.0f}h) — stale or halted")

    up = (sym or "").upper()
    nm = (name or "").upper()
    if not cfg["leveraged_allowed"]:
        if up in _LEVERAGED_SYMBOLS or any(h in nm for h in _LEVERAGED_HINTS):
            reasons.append("leveraged/inverse product — excluded (enable SCAN_ALLOW_LEVERAGED to include, labelled)")
    return (not reasons), reasons, facts


# ── Stage 3: setup detection ─────────────────────────────────────────────────
# Each detector returns None when the evidence is not there. Nothing is forced.

def detect_setups(ind: Dict[str, Any], sector_rank: Optional[int],
                  sector_count: int, spy_20d: Optional[float]) -> List[Dict[str, Any]]:
    if ind.get("state") != "ok":
        return []
    s: List[Dict[str, Any]] = []
    px = ind["price"]
    rv = ind.get("rel_volume")
    atrp = ind.get("atr_pct")
    rsi = ind.get("rsi14")
    hi20, lo20 = ind.get("high_20d"), ind.get("low_20d")
    gap = ind.get("gap_pct")

    def add(t, strength, why, horizon):
        s.append({"type": t, "strength": round(strength, 1), "evidence": why, "horizon": horizon})

    # relative-strength momentum
    if (spy_20d is not None and ind.get("chg_20d") is not None
            and ind["chg_20d"] - spy_20d >= 5 and ind.get("above_sma50")):
        add("relative_strength_momentum", min(100, (ind["chg_20d"] - spy_20d) * 4),
            f"20-day return {ind['chg_20d']:+.1f}% vs SPY {spy_20d:+.1f}% "
            f"({ind['chg_20d'] - spy_20d:+.1f}pp), holding above the 50-DMA", "multi-day swing")

    # breakout with volume confirmation
    if hi20 and px >= hi20 * 0.999 and rv and rv >= 1.5:
        add("breakout_volume", min(100, 50 + (rv - 1.5) * 25),
            f"closing at/through the 20-day high {hi20:.2f} on {rv:.1f}x average volume", "days")
    elif hi20 and px >= hi20 * 0.999 and rv and rv < 1.2:
        # a breakout WITHOUT volume is explicitly not the same setup
        add("breakout_unconfirmed", 25,
            f"at the 20-day high {hi20:.2f} but only {rv:.1f}x volume — unconfirmed", "days")

    # pullback inside an established uptrend
    if (ind.get("above_sma50") and ind.get("above_sma200") and ind.get("sma20")
            and rsi is not None and 38 <= rsi <= 55 and px <= ind["sma20"]):
        add("pullback_in_trend", 60 + (55 - rsi),
            f"above the 50- and 200-DMA but pulled back under the 20-DMA "
            f"({ind['sma20']:.2f}) with RSI {rsi}", "days to weeks")

    # gap continuation / reversal
    if gap is not None and abs(gap) >= 2 and rv and rv >= 1.5:
        if ind.get("change_pct") is not None:
            same_way = (gap > 0) == (ind["change_pct"] > 0)
            if same_way and abs(ind["change_pct"]) >= abs(gap) * 0.8:
                add("gap_continuation", min(100, 40 + abs(gap) * 6),
                    f"gapped {gap:+.1f}% and held the move (close {ind['change_pct']:+.1f}%) on {rv:.1f}x volume", "days")
            elif not same_way:
                add("gap_reversal", min(100, 35 + abs(gap) * 5),
                    f"gapped {gap:+.1f}% then closed {ind['change_pct']:+.1f}% — the gap was faded on {rv:.1f}x volume", "days")

    # mean reversion (stretched, but only in a name that still has structure)
    if rsi is not None and rsi <= 30 and ind.get("above_sma200"):
        add("mean_reversion", min(100, (30 - rsi) * 6 + 40),
            f"RSI {rsi} (oversold) while still above the 200-DMA", "days")
    elif rsi is not None and rsi >= 78:
        add("mean_reversion_short_side", min(100, (rsi - 78) * 6 + 30),
            f"RSI {rsi} — extended; noted as risk, not a long entry", "days")

    # sector rotation: a top-ranked sector plus the name itself turning up
    if (sector_rank is not None and sector_count and sector_rank <= max(2, sector_count // 4)
            and ind.get("above_sma20") and ind.get("chg_5d") is not None and ind["chg_5d"] > 0):
        add("sector_rotation", 55 + (sector_count - sector_rank) * 2,
            f"member of the #{sector_rank} ranked sector, above its 20-DMA, {ind['chg_5d']:+.1f}% over 5 days",
            "days to weeks")

    # high-liquidity intraday candidate
    if (ind.get("dollar_volume_20d") and ind["dollar_volume_20d"] >= 2e8
            and atrp and atrp >= 2.0 and rv and rv >= 1.2):
        add("high_liquidity_intraday", min(100, 40 + atrp * 5),
            f"${ind['dollar_volume_20d']/1e6:.0f}M average daily turnover with {atrp:.1f}% ATR "
            f"and {rv:.1f}x volume — enough range and depth to trade intraday", "intraday")

    # multi-day swing structure
    if (ind.get("above_sma20") and ind.get("above_sma50") and atrp
            and 1.5 <= atrp <= 8 and ind.get("pos_in_20d_range") is not None
            and ind["pos_in_20d_range"] >= 60):
        add("multiday_swing", 45 + ind["pos_in_20d_range"] / 4,
            f"stacked above the 20- and 50-DMA at {ind['pos_in_20d_range']:.0f}% of the 20-day range, "
            f"ATR {atrp:.1f}%", "days to weeks")

    return sorted(s, key=lambda x: -x["strength"])


# ── Stage 4: transparent scoring ─────────────────────────────────────────────
# Weights sum to 100 before penalties. Every component reports its own max so the
# UI can render a bar per component rather than one opaque number.

WEIGHTS = {
    "market_regime_alignment": 8,
    "sector_strength": 10,
    "industry_strength": 4,
    "relative_strength": 12,
    "technical_structure": 14,
    "volume_confirmation": 10,
    "liquidity": 10,
    "volatility_suitability": 6,
    "catalyst_quality": 8,
    "news_sentiment": 4,
    "risk_reward": 10,
    "data_completeness": 4,
}


def _levels(ind: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Entry / invalidation / targets from ATR and structure. Returns Nones (never
    invented numbers) when ATR or the range is missing."""
    px, atr = ind.get("price"), ind.get("atr14")
    if not px or not atr:
        return {"state": "unavailable", "reason": "no ATR — cannot place a stop objectively"}
    sup = ind.get("support")
    stop_atr = px - 1.5 * atr
    # invalidation is the tighter-but-sane of a 1.5-ATR stop and the 20-day low
    stop = max(stop_atr, sup * 0.995) if (sup and sup < px) else stop_atr
    risk = px - stop
    if risk <= 0:
        return {"state": "unavailable", "reason": "stop is not below price — structure does not support an entry"}

    # Targets come from STRUCTURE, and the reward:risk ratio is then MEASURED from
    # them. Defining targets as 2R and 3R would make every setup score exactly 2:1
    # and 3:1 — a tautology that tells you nothing about whether this particular
    # chart has room to travel.
    hi20, lo20 = ind.get("high_20d"), ind.get("low_20d")
    rng = (hi20 - lo20) if (hi20 and lo20 and hi20 > lo20) else None
    # Targets are MEASURED MOVES off the recent range, never multiples of the stop.
    # Two earlier attempts were tautologies: targets at 2R/3R made every setup score
    # exactly 2:1, and a symmetric 1.5-ATR projection against a 1.5-ATR stop made
    # every breakout score exactly 1.0. Range height and ATR are independent, so this
    # ratio actually varies with the chart: a tight base under a stop that is small
    # relative to the range scores well; a wide, choppy name does not.
    if rng is None:
        t1, t1_basis = px + 1.5 * atr, "1.5 x ATR (no usable 20-day range)"
        t2, t2_basis = px + 3.0 * atr, "3 x ATR (no usable 20-day range)"
    elif (hi20 - px) >= 0.75 * atr:
        # room to the high: the high itself is target 1
        t1, t1_basis = hi20, "20-day high"
        t2, t2_basis = hi20 + 0.5 * rng, "20-day high plus half the range (measured move)"
    else:
        # at/through the high: the high is the breakout level, so project from it
        t1, t1_basis = hi20 + 0.5 * rng, "half the 20-day range projected off the breakout"
        t2, t2_basis = hi20 + rng, "full 20-day range projected off the breakout (measured move)"
    rr1 = round((t1 - px) / risk, 2)
    rr2 = round((t2 - px) / risk, 2)
    res = ind.get("resistance")
    return {
        "state": "ok",
        "entry_zone": [round(px - 0.25 * atr, 2), round(px + 0.35 * atr, 2)],
        "reference_price": round(px, 2),
        "invalidation": round(stop, 2),
        "invalidation_basis": ("20-day low" if (sup and stop > stop_atr) else "1.5 x ATR(14)"),
        "risk_per_share": round(risk, 2),
        "risk_pct": round(risk / px * 100, 2),
        "target_1": round(t1, 2), "target_1_basis": t1_basis,
        "target_2": round(t2, 2), "target_2_basis": t2_basis,
        "rr_target_1": rr1, "rr_target_2": rr2,
        "resistance_above": res,
        "do_not_chase_above": round(px + 0.75 * atr, 2),
        "note": ("Price is already at the 20-day high — target 1 is a projection, "
                 "not a level the chart has traded to" if t1_basis.startswith("1.5") else None),
    }


def lo20_ok(ind: Dict[str, Any]) -> bool:
    lo = ind.get("low_20d")
    return bool(lo and lo > 0)


def score(ind: Dict[str, Any], *, sector: Optional[Dict[str, Any]], sector_rank: Optional[int],
          sector_count: int, regime: Dict[str, Any], setups: List[Dict[str, Any]],
          levels: Dict[str, Any], spy_20d: Optional[float],
          catalyst: Optional[Dict[str, Any]] = None,
          sentiment: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """0-100 with a per-component breakdown that sums to the total."""
    comp: List[Dict[str, Any]] = []

    def put(name, pts, note):
        mx = WEIGHTS[name]
        comp.append({"component": name, "score": round(max(0.0, min(float(mx), pts)), 2),
                     "max": mx, "note": note})

    # 1) regime alignment — a long setup in a risk-off tape is worth less
    risk = (regime or {}).get("risk_appetite")
    struct = (regime or {}).get("structure")
    if risk == "risk-on":
        put("market_regime_alignment", 8, "risk-on tape supports long setups")
    elif risk == "mixed":
        put("market_regime_alignment", 5, "mixed tape — neither tailwind nor headwind")
    elif risk == "risk-off":
        put("market_regime_alignment", 2, "risk-off tape is a headwind for long setups")
    else:
        put("market_regime_alignment", 3, "regime unknown — no credit assumed")
    if struct == "trending":
        comp[-1]["score"] = min(WEIGHTS["market_regime_alignment"], comp[-1]["score"] + 1)
        comp[-1]["note"] += "; index is trending, not chopping"

    # 2/3) sector + industry
    if sector and sector_rank:
        frac = 1 - (sector_rank - 1) / max(1, sector_count - 1)
        put("sector_strength", 10 * frac,
            f"{sector.get('name')} ranked #{sector_rank} of {sector_count} "
            f"(RS vs SPY {sector.get('rs_vs_spy_1m')}, 5d {sector.get('perf_5d')}%)")
        br = sector.get("breadth") or {}
        counted = br.get("counted") or 0
        if counted:
            bfrac = (br.get("advancers") or 0) / counted
            note = f"{br.get('advancers')}/{counted} sector members advancing"
            if not br.get("complete"):
                note += f" (partial data, coverage {br.get('coverage')})"
            put("industry_strength", 4 * bfrac, note)
        else:
            put("industry_strength", 0, "no sector breadth available")
    else:
        put("sector_strength", 0, "sector unknown — no credit assumed")
        put("industry_strength", 0, "sector unknown")

    # 4) relative strength vs SPY
    if ind.get("chg_20d") is not None and spy_20d is not None:
        rel = ind["chg_20d"] - spy_20d
        put("relative_strength", 6 + rel * 0.6,
            f"20-day {ind['chg_20d']:+.1f}% vs SPY {spy_20d:+.1f}% ({rel:+.1f}pp)")
    else:
        put("relative_strength", 0, "no 20-day return or benchmark")

    # 5) technical structure
    pts, bits = 0.0, []
    for key, w, txt in (("above_sma20", 3, "above 20-DMA"), ("above_sma50", 4, "above 50-DMA"),
                        ("above_sma200", 4, "above 200-DMA")):
        if ind.get(key):
            pts += w
            bits.append(txt)
        elif ind.get(key) is None:
            bits.append(f"{key} unknown")
    pir = ind.get("pos_in_20d_range")
    if pir is not None:
        pts += 3 * (pir / 100)
        bits.append(f"{pir:.0f}% of 20-day range")
    put("technical_structure", pts, "; ".join(bits) or "no structure data")

    # 6) volume confirmation
    rv = ind.get("rel_volume")
    if rv is None:
        put("volume_confirmation", 0, "relative volume unavailable")
    elif rv >= 2.0:
        put("volume_confirmation", 10, f"{rv:.1f}x average volume")
    elif rv >= 1.3:
        put("volume_confirmation", 5 + (rv - 1.3) * 7, f"{rv:.1f}x average volume")
    elif rv >= 0.8:
        put("volume_confirmation", 3, f"{rv:.1f}x average volume — unremarkable")
    else:
        put("volume_confirmation", 1, f"{rv:.1f}x average volume — participation is drying up")

    # 7) liquidity
    dv = ind.get("dollar_volume_20d")
    if not dv:
        put("liquidity", 0, "no turnover figure")
    else:
        put("liquidity", min(10, math.log10(max(dv, 1) / 1e6) * 4),
            f"${dv/1e6:.0f}M average daily turnover")

    # 8) volatility suitability — enough range to pay for the risk, not so much it's noise
    atrp = ind.get("atr_pct")
    if atrp is None:
        put("volatility_suitability", 0, "ATR unavailable")
    elif 1.5 <= atrp <= 5:
        put("volatility_suitability", 6, f"ATR {atrp:.1f}% — workable daily range")
    elif atrp < 1.5:
        put("volatility_suitability", 2, f"ATR {atrp:.1f}% — may not travel far enough to pay for the risk")
    elif atrp <= 8:
        put("volatility_suitability", 4, f"ATR {atrp:.1f}% — wide; size down")
    else:
        put("volatility_suitability", 1, f"ATR {atrp:.1f}% — erratic")

    # 9/10) catalyst + sentiment (only scored when actually fetched)
    if catalyst is None:
        put("catalyst_quality", 0, "not evaluated at this stage")
    elif catalyst.get("state") != "ok":
        put("catalyst_quality", 0, f"catalyst data {catalyst.get('state')}")
    else:
        n = len(catalyst.get("items") or [])
        lean = catalyst.get("lean")
        base = {"bullish": 8, "bearish": 1, "neutral": 4}.get(lean, 3)
        put("catalyst_quality", base if n else 2,
            f"{n} recent item(s), lean {lean}" if n else "no recent catalyst found")
    if sentiment is None:
        put("news_sentiment", 0, "not evaluated at this stage")
    elif sentiment.get("state") != "ok":
        put("news_sentiment", 0, f"sentiment {sentiment.get('state')}")
    else:
        sc = sentiment.get("score")
        put("news_sentiment", 2 + (sc or 0) * 2 if sc is not None else 2,
            f"aggregate news sentiment {sc}" if sc is not None else "sentiment computed, no score")

    # 11) risk/reward
    if levels.get("state") != "ok":
        put("risk_reward", 0, levels.get("reason", "levels unavailable"))
    else:
        rr = levels["rr_target_1"]
        # measured from structural targets, so this scales rather than being a constant
        put("risk_reward", min(10.0, max(0.0, (rr - 0.5) * 4)),
            f"{rr:.2f}:1 to target 1 ({levels['target_1_basis']}) with a "
            f"{levels['risk_pct']:.1f}% stop at the {levels['invalidation_basis']}; "
            f"{levels['rr_target_2']:.2f}:1 to target 2")

    # 12) data completeness
    miss = list(ind.get("missing") or [])
    put("data_completeness", 4 - len(miss), ("complete" if not miss else "missing: " + ", ".join(miss)))

    subtotal = sum(c["score"] for c in comp)

    # penalties -------------------------------------------------------------
    penalties: List[Dict[str, Any]] = []
    kinds = {s["type"] for s in setups}
    if {"gap_continuation", "gap_reversal"} <= kinds:
        penalties.append({"penalty": "conflict", "points": -6,
                          "note": "gap continuation and gap reversal both fired — the signals disagree"})
    if "mean_reversion_short_side" in kinds and (
            "relative_strength_momentum" in kinds or "breakout_volume" in kinds):
        penalties.append({"penalty": "conflict", "points": -4,
                          "note": "overbought mean-reversion flag against an active momentum signal"})
    if "breakout_unconfirmed" in kinds and "breakout_volume" not in kinds:
        penalties.append({"penalty": "conflict", "points": -3,
                          "note": "at highs without volume confirmation"})
    if (regime or {}).get("event_driven"):
        penalties.append({"penalty": "event_risk", "points": -5,
                          "note": "event-driven tape — single-name setups are more likely to be overridden by the index"})
    if atrp is not None and atrp > 8:
        penalties.append({"penalty": "event_risk", "points": -4,
                          "note": f"ATR {atrp:.1f}% suggests an unresolved event in the name"})
    pen_total = sum(p["points"] for p in penalties)
    total = round(max(0.0, min(100.0, subtotal + pen_total)), 1)

    return {"total": total, "subtotal": round(subtotal, 2), "components": comp,
            "penalties": penalties, "penalty_total": pen_total, "max": 100}


# ── Sector ranking ───────────────────────────────────────────────────────────

def rank_sectors(smap: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Rank the 11 sectors strongest→weakest on a composite of relative strength,
    5-day return, momentum, breadth and participation. Tiles without an ETF quote
    are ranked last and flagged, never dropped silently."""
    tiles = [t for t in (smap.get("sectors") or []) if isinstance(t, dict)]
    scored = []
    for t in tiles:
        if t.get("state") != "ok":
            scored.append({**t, "rank_score": None, "rank_state": t.get("state", "unavailable")})
            continue
        br = t.get("breadth") or {}
        counted = br.get("counted") or 0
        breadth_frac = ((br.get("advancers") or 0) / counted) if counted else 0.5
        parts = {
            "rs_vs_spy_1m": (t.get("rs_vs_spy_1m") or 0) * 3.0,
            "perf_5d": (t.get("perf_5d") or 0) * 1.5,
            "perf_1d": (t.get("perf_1d") or 0) * 1.0,
            "momentum_pct": (t.get("momentum_pct") or 0) * 1.0,
            "breadth": (breadth_frac - 0.5) * 12.0,
            "rel_volume": ((t.get("rel_volume") or 1.0) - 1.0) * 3.0,
        }
        scored.append({**t, "rank_score": round(sum(parts.values()), 2),
                       "rank_components": {k: round(v, 2) for k, v in parts.items()},
                       "rank_state": "ok"})
    ok = sorted([s for s in scored if s["rank_score"] is not None],
                key=lambda s: -s["rank_score"])
    bad = [s for s in scored if s["rank_score"] is None]
    for i, s in enumerate(ok, 1):
        s["rank"] = i
    for s in bad:
        s["rank"] = None
    return ok + bad


# ── The scan ─────────────────────────────────────────────────────────────────

def scan(preset: str = "liquid", *, cfg: Optional[Dict[str, Any]] = None,
         regime_data: Optional[Dict[str, Any]] = None,
         smap: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run the whole funnel. Returns every stage plus the rejection ledger."""
    t0 = datetime.now(timezone.utc)
    cfg = {**config(), **(cfg or {})}
    stages: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []

    def drop(sym, stage, reasons, extra=None):
        rejected.append({"symbol": sym, "stage": stage,
                         "reasons": reasons if isinstance(reasons, list) else [reasons],
                         **(extra or {})})

    # 0) regime + sector ranking ------------------------------------------------
    reg = regime_data or _MR.overview(blocking=True)
    regime = reg.get("regime") or {}
    sm = smap or _SM.sector_map(blocking=True)
    ranks = rank_sectors(sm)
    rank_by_key = {r["key"]: r for r in ranks}
    spy_stats = ((reg.get("tape") or {}).get("SPY") or {}).get("stats") or {}
    spy_20d = spy_stats.get("chg_20d")

    # 1) universe ---------------------------------------------------------------
    uni = _R.build_scan_universe(preset, limit=cfg["universe_limit"])
    syms = [t.partition(":")[2] for t in uni.get("tickers", [])]
    stages.append({"stage": "universe", "count": len(syms),
                   "note": f"{uni.get('source')} — preset '{preset}'"})

    # 2) cheap eligibility ------------------------------------------------------
    names = {}
    try:
        import security_master as _SMaster
        _SMaster.ensure_loaded()
        names = {s: (_SMaster._BY_SYMBOL.get(s) or {}).get("name", "") for s in syms}
    except Exception:  # noqa: BLE001
        pass
    # Budget scales with the universe: `gather`'s timeout is a whole-batch deadline,
    # so a fixed 60s silently truncated the eligibility pass once the universe grew.
    q = _R.quotes(syms, ttl=120, timeout=max(60.0, len(syms) * 0.25))
    eligible = []
    for s in syms:
        ok, why, facts = eligibility(s, q.get(s) or {}, cfg, names.get(s, ""))
        if ok:
            eligible.append(s)
        else:
            drop(s, "eligibility", why, {"facts": facts})
    stages.append({"stage": "eligible", "count": len(eligible),
                   "note": (f"price ≥ ${cfg['min_price']:.0f}, ≥ ${cfg['min_dollar_volume']/1e6:.0f}M "
                            f"turnover, ≥ {cfg['min_share_volume']:,.0f} shares, quote < "
                            f"{cfg['max_quote_age_hours']:.0f}h old")})

    # 3) sector alignment -------------------------------------------------------
    _SL.backfill_async(eligible, budget=60)   # fills the cache for NEXT run; never blocks
    secs = _SL.get_many(eligible)
    top_keys = {r["key"] for r in ranks[:cfg["top_sectors"]] if r.get("rank")}
    aligned, unknown_sector = [], []
    for s in eligible:
        rec = secs.get(s)
        key = (rec or {}).get("sector")
        if key is None:
            unknown_sector.append(s)
            aligned.append(s)      # unknown is not a reason to drop — it scores 0 credit
        elif key in top_keys:
            aligned.append(s)
        else:
            r = rank_by_key.get(key)
            drop(s, "sector_alignment",
                 f"{(r or {}).get('name', key)} ranked #{(r or {}).get('rank', '?')} of "
                 f"{len(ranks)} — outside the top {cfg['top_sectors']}",
                 {"sector": key})
    stages.append({"stage": "sector_aligned", "count": len(aligned),
                   "note": (f"top {cfg['top_sectors']} sectors: "
                            + ", ".join(r["name"] for r in ranks[:cfg["top_sectors"]] if r.get("rank"))
                            + (f"; {len(unknown_sector)} carried with unknown sector" if unknown_sector else ""))})

    # 4) deep stage -------------------------------------------------------------
    order = sorted(aligned, key=lambda s: -((q.get(s) or {}).get("price") or 0) * 0 - (
        (q.get(s) or {}).get("volume") or 0) * ((q.get(s) or {}).get("price") or 0))
    deep_syms = order[:cfg["deep_n"]]
    for s in aligned[len(deep_syms):]:
        if s not in deep_syms:
            drop(s, "deep_cut", f"outside the top {cfg['deep_n']} by turnover for deep analysis")
    # 1y, not 6mo: 6 months of daily bars is ~124 rows, so SMA200 could never be
    # computed and every candidate reported `above_sma200: unknown`.
    bb = _R.bars_batch(deep_syms, rng="1y", interval="1d", timeout=90.0)
    sess_frac = session_fraction_elapsed()

    cands: List[Dict[str, Any]] = []
    for s in deep_syms:
        ind = indicators(s, bb.get(s) or {}, session_fraction=sess_frac)
        if ind.get("state") != "ok":
            drop(s, "deep_analysis", f"insufficient history ({ind.get('bars_count', 0)} bars)")
            continue
        if ind.get("atr_pct") is not None and not (cfg["min_atr_pct"] <= ind["atr_pct"] <= cfg["max_atr_pct"]):
            drop(s, "volatility", f"ATR {ind['atr_pct']:.1f}% outside the "
                                  f"{cfg['min_atr_pct']:.0f}-{cfg['max_atr_pct']:.0f}% workable band")
            continue
        rec = secs.get(s) or {}
        skey = rec.get("sector")
        srank = (rank_by_key.get(skey) or {}).get("rank") if skey else None
        setups = detect_setups(ind, srank, len(ranks), spy_20d)
        if not setups:
            drop(s, "no_setup", "no setup category matched — the evidence for every pattern was absent")
            continue
        lv = _levels(ind, cfg)
        sc = score(ind, sector=rank_by_key.get(skey), sector_rank=srank, sector_count=len(ranks),
                   regime=regime, setups=setups, levels=lv, spy_20d=spy_20d)
        cands.append({
            "symbol": s, "name": names.get(s, ""),
            "sector": skey, "sector_name": rec.get("sector_name"),
            "industry": rec.get("industry"), "sector_rank": srank,
            "sector_source": rec.get("source"),
            "indicators": ind, "setups": setups, "levels": lv, "score": sc,
            "primary_setup": setups[0]["type"],
            "quote": {k: (q.get(s) or {}).get(k) for k in
                      ("price", "change_pct", "source_timestamp", "fetched_at", "state")},
            "data_quality": {"missing": ind.get("missing"),
                             "bars": ind.get("bars_count"),
                             "source_timestamp": ind.get("source_timestamp")},
        })
    stages.append({"stage": "deep", "count": len(cands),
                   "note": f"{len(deep_syms)} names pulled 6-month OHLCV; {len(cands)} produced a setup"})

    cands.sort(key=lambda c: -c["score"]["total"])
    top25 = cands[:cfg["top25_n"]]
    top10 = cands[:cfg["top10_n"]]
    for c in cands[cfg["top25_n"]:]:
        drop(c["symbol"], "ranking", f"score {c['score']['total']} outside the top {cfg['top25_n']}")

    # 5) finalists: must additionally clear R:R and have levels ------------------
    finalists = []
    for c in top10:
        lv = c["levels"]
        if lv.get("state") != "ok":
            drop(c["symbol"], "finalist", lv.get("reason", "no tradeable levels"))
            continue
        if lv["rr_target_1"] < cfg["min_rr"]:
            drop(c["symbol"], "finalist", f"R:R {lv['rr_target_1']:.1f} below the {cfg['min_rr']} minimum")
            continue
        finalists.append(c)
        if len(finalists) >= cfg["finalists_n"]:
            break
    # 6) enrich the finalists with catalyst + sentiment and RE-SCORE. These two
    # components are deliberately not fetched for the whole universe (a news call per
    # name would dominate the scan), so up to this point they score 0 for everyone —
    # which is fair between candidates but understates the finalists. Re-scoring here
    # means the published finalist score is the one with the news actually read.
    if finalists:
        cat_tasks = {c["symbol"]: (lambda s=c["symbol"]: _R.catalysts(s)) for c in finalists}
        sen_tasks = {c["symbol"]: (lambda s=c["symbol"]: _R.news_sentiment(s, blocking=True))
                     for c in finalists}
        cats = _R.gather(cat_tasks, timeout=25.0)
        sens = _R.gather(sen_tasks, timeout=25.0)
        for c in finalists:
            cat = cats.get(c["symbol"]) if isinstance(cats.get(c["symbol"]), dict) else None
            sen = sens.get(c["symbol"]) if isinstance(sens.get(c["symbol"]), dict) else None
            c["catalyst"] = cat
            c["sentiment"] = sen
            c["score_structural"] = c["score"]
            c["score"] = score(c["indicators"], sector=rank_by_key.get(c["sector"]),
                               sector_rank=c["sector_rank"], sector_count=len(ranks),
                               regime=regime, setups=c["setups"], levels=c["levels"],
                               spy_20d=spy_20d, catalyst=cat, sentiment=sen)
            c["score"]["enriched"] = True
        # Finalists are the SAME objects that sit in cands/top25/top10, so re-scoring
        # them here changes scores those lists were already sorted by. Re-sort every
        # view, or the scanner table renders out of order against its own numbers.
        finalists.sort(key=lambda c: -c["score"]["total"])
        for lst in (cands, top25, top10):
            lst.sort(key=lambda c: -c["score"]["total"])

    stages.append({"stage": "top25", "count": len(top25), "note": "ranked for deeper reading"})
    stages.append({"stage": "top10", "count": len(top10), "note": "actionable watchlist"})
    stages.append({"stage": "finalists", "count": len(finalists),
                   "note": f"cleared levels and R:R ≥ {cfg['min_rr']}"})

    took = (datetime.now(timezone.utc) - t0).total_seconds()
    result = {
        "state": "ok", "preset": preset, "config": cfg,
        "session": reg.get("session"), "regime": regime,
        "sectors": ranks,
        "stages": stages,
        "candidates": cands, "top25": top25, "top10": top10, "finalists": finalists,
        "rejected": rejected,
        "rejected_count": len(rejected),
        "sector_lookup": _SL.stats(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "took_seconds": round(took, 2),
        "note": "Research output. Nothing here is an order and nothing here is executed.",
    }

    # 7) capture this scan's own Top-5 cohort for later monitoring. A NEW scan_run +
    # NEW observations every time — never merged into a prior day's rows, never
    # gated on whether an entry ever triggers (see scan_cohort.py). Best-effort: a
    # failure here must never fail the scan itself.
    try:
        import scan_cohort as _SCO
        _SCO.capture_scan(result)
    except Exception:  # noqa: BLE001
        pass

    return result


def scan_cached(preset: str = "liquid", ttl: float = 300.0, blocking: bool = False) -> Dict[str, Any]:
    key = f"scan:{preset}"
    if blocking:
        val, cs = _R.swr(key, ttl, lambda: scan(preset))
    else:
        val, cs = _R.swr_async(key, ttl, lambda: scan(preset), loading={
            "state": "loading", "preset": preset, "reason": "scanning the universe",
            "stages": [], "candidates": [], "finalists": [], "rejected": []})
    return {**val, "cache_state": cs} if isinstance(val, dict) else val


# ── One symbol, on demand — WITHOUT the universe ─────────────────────────────
# The scan funnel is a UNIVERSE operation (800 names, ~45-76s). Selecting a ticker in
# the terminal must never trigger it. `analyse_symbol` runs the same per-name pipeline
# (indicators → setups → levels → score) for exactly one symbol, reading the regime and
# sector map from cache only, so a symbol refresh costs two provider calls instead of a
# full rescan. The candidate it returns is the SAME shape the funnel produces, so
# options_desk.evaluate_candidate consumes it unchanged.

def _cached_context() -> Dict[str, Any]:
    """Regime + sector ranking from CACHE. Never blocks and never starts a scan —
    a missing regime scores zero credit, which is honest, rather than waiting."""
    try:
        reg = _MR.overview(blocking=False) or {}
    except Exception:  # noqa: BLE001
        reg = {}
    try:
        sm = _SM.sector_map(blocking=False) or {}
    except Exception:  # noqa: BLE001
        sm = {}
    try:
        ranks = rank_sectors(sm) if sm.get("state") != "loading" else []
    except Exception:  # noqa: BLE001
        ranks = []
    spy = ((reg.get("tape") or {}).get("SPY") or {}).get("stats") or {}
    return {"regime": reg.get("regime") or {}, "ranks": ranks,
            "rank_by_key": {r["key"]: r for r in ranks},
            "spy_20d": spy.get("chg_20d"),
            "context_state": ("complete" if (ranks and reg.get("regime")) else "partial"),
            "context_note": ("regime and sector ranking read from cache"
                             if (ranks and reg.get("regime")) else
                             "regime/sector context still warming — components that "
                             "depend on it score zero rather than being assumed")}


def analyse_symbol(sym: str, *, cfg: Optional[Dict[str, Any]] = None,
                   enrich: bool = True) -> Dict[str, Any]:
    """Full per-name analysis for ONE arbitrary symbol, no universe pass.

    Unlike the funnel this does not DROP a name — a symbol the user explicitly
    selected must still render. Every gate the funnel would have applied is evaluated
    and reported in `funnel`, so a name that would never have survived the scan says
    so instead of quietly looking like a finalist.
    """
    cfg = {**config(), **(cfg or {})}
    sym = (sym or "").upper().strip()
    if not sym:
        return {"state": "error", "reason": "symbol is required"}

    ctx = _cached_context()
    name = ""
    try:
        import security_master as _SMaster
        _SMaster.ensure_loaded()
        name = (_SMaster._BY_SYMBOL.get(sym) or {}).get("name", "") or ""
    except Exception:  # noqa: BLE001
        pass

    # One quote + one history pull, in parallel.
    got = _R.gather({"quote": lambda: _R.quote(sym, ttl=30.0),
                     "bars": lambda: _R.bars(sym, rng="1y", interval="1d")}, timeout=30.0)
    q = got.get("quote") if isinstance(got.get("quote"), dict) else {}
    b = got.get("bars") if isinstance(got.get("bars"), dict) else {}

    ind = indicators(sym, b, session_fraction=session_fraction_elapsed())
    if ind.get("state") != "ok":
        return {"state": "insufficient_history", "symbol": sym, "name": name,
                "reason": f"only {ind.get('bars_count', 0)} usable daily bars — "
                          f"indicators need at least 30",
                "indicators": ind, "quote": q,
                "generated_at": datetime.now(timezone.utc).isoformat()}

    rec = (_SL.get_many([sym]) or {}).get(sym) or {}
    skey = rec.get("sector")
    srank = (ctx["rank_by_key"].get(skey) or {}).get("rank") if skey else None
    setups = detect_setups(ind, srank, len(ctx["ranks"]), ctx["spy_20d"])
    lv = _levels(ind, cfg)

    cat = sen = None
    if enrich:
        tasks = {"catalyst": lambda: _R.catalysts(sym),
                 "sentiment": lambda: _R.news_sentiment(sym, blocking=False)}
        e = _R.gather(tasks, timeout=20.0)
        cat = e.get("catalyst") if isinstance(e.get("catalyst"), dict) else None
        sen = e.get("sentiment") if isinstance(e.get("sentiment"), dict) else None

    sc = score(ind, sector=ctx["rank_by_key"].get(skey), sector_rank=srank,
               sector_count=len(ctx["ranks"]), regime=ctx["regime"], setups=setups,
               levels=lv, spy_20d=ctx["spy_20d"], catalyst=cat, sentiment=sen)

    # Every funnel gate, evaluated and REPORTED rather than applied as a drop.
    elig_ok, elig_why, elig_facts = eligibility(sym, q, cfg, name)
    checks: List[Dict[str, Any]] = [
        {"gate": "eligibility", "pass": elig_ok,
         "detail": "; ".join(elig_why) if elig_why else
                   f"${(elig_facts.get('dollar_volume') or 0)/1e6:.0f}M turnover, "
                   f"quote {elig_facts.get('quote_age_hours')}h old"},
        {"gate": "volatility_band", "pass": (ind.get("atr_pct") is not None
                                             and cfg["min_atr_pct"] <= ind["atr_pct"] <= cfg["max_atr_pct"]),
         "detail": (f"ATR {ind['atr_pct']:.1f}% vs the {cfg['min_atr_pct']:.0f}-"
                    f"{cfg['max_atr_pct']:.0f}% workable band"
                    if ind.get("atr_pct") is not None else "ATR unavailable")},
        {"gate": "setup_present", "pass": bool(setups),
         "detail": (", ".join(s["type"] for s in setups) if setups
                    else "no setup category matched — the evidence for every pattern was absent")},
        {"gate": "sector_alignment",
         "pass": bool(srank and srank <= cfg["top_sectors"]),
         "detail": (f"{rec.get('sector_name') or skey} ranked #{srank} of {len(ctx['ranks'])}"
                    if srank else "sector unknown or ranking unavailable — no credit assumed")},
        {"gate": "levels", "pass": lv.get("state") == "ok",
         "detail": lv.get("reason") or (f"stop {lv.get('invalidation')} "
                                        f"({lv.get('invalidation_basis')}), "
                                        f"R:R {lv.get('rr_target_1')}")},
        {"gate": "reward_risk", "pass": bool(lv.get("state") == "ok"
                                             and lv.get("rr_target_1", 0) >= cfg["min_rr"]),
         "detail": (f"{lv.get('rr_target_1')}:1 vs the {cfg['min_rr']} minimum"
                    if lv.get("state") == "ok" else "no levels")},
    ]
    failed = [c["gate"] for c in checks if not c["pass"]]

    return {
        "state": "ok", "symbol": sym, "name": name,
        "origin": "on_demand",
        "sector": skey, "sector_name": rec.get("sector_name"),
        "industry": rec.get("industry"), "sector_rank": srank,
        "sector_source": rec.get("source"),
        "indicators": ind, "setups": setups, "levels": lv, "score": sc,
        "primary_setup": setups[0]["type"] if setups else None,
        "catalyst": cat, "sentiment": sen,
        "quote": {k: q.get(k) for k in ("price", "change_pct", "source_timestamp",
                                        "fetched_at", "state")},
        "data_quality": {"missing": ind.get("missing"), "bars": ind.get("bars_count"),
                         "source_timestamp": ind.get("source_timestamp")},
        "funnel": {"would_pass": not failed, "failed_gates": failed, "checks": checks,
                   "note": "This name was analysed because it was selected, not because "
                           "it survived the scan. Failed gates are why the funnel would "
                           "not have surfaced it."},
        "context": {"state": ctx["context_state"], "note": ctx["context_note"],
                    "regime": ctx["regime"].get("risk_appetite"),
                    "sectors_ranked": len(ctx["ranks"])},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def analyse_symbol_cached(sym: str, ttl: float = 120.0, *,
                          force: bool = False) -> Dict[str, Any]:
    """SWR-cached single-symbol analysis. `force` bypasses the cache for an explicit
    user refresh; everything else coalesces so a widget storm is one provider call."""
    key = f"sym:{(sym or '').upper().strip()}"
    if force:
        _R.invalidate(key)
    val, cs = _R.swr(key, ttl, lambda: analyse_symbol(sym))
    return {**val, "cache_state": cs} if isinstance(val, dict) else val
