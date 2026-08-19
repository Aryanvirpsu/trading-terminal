"""Phase 2 — today's session state and market regime, from evidence.

Two independent things live here:

  * `session_state()` — a pure-calendar answer to "what is the US market doing
    right now": pre-market / open / after-hours / closed / holiday / early close,
    the New York and India clocks, and when the next regular session starts. No
    network, no provider, fully deterministic and therefore testable.

  * `regime()` — a classification of the tape (risk-on / risk-off / mixed,
    trending / range-bound, high- / low-volatility, event-driven) computed from
    the broad indices, volatility, rates, the dollar, crude, gold and bitcoin.

The classification is NEVER asserted bare: every label carries the `evidence`
list of the actual observations that produced it, each with its own value and
source timestamp, so a reader can disagree with the label and still trust the
numbers. Instruments that fail to fetch are reported as missing and REDUCE the
stated confidence rather than being silently treated as neutral.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in ("..", os.path.join("..", "lab"), os.path.join("..", "src")):
    sys.path.insert(0, os.path.join(_HERE, _p))
sys.path.insert(0, _HERE)

import research as _R

ET = ZoneInfo("America/New_York")
IST = ZoneInfo("Asia/Kolkata")

# Session boundaries (ET). Configurable so a change in exchange hours is a setting.
PREMARKET_OPEN = dtime(int(os.environ.get("MKT_PREMARKET_OPEN_H", 4)), 0)
REGULAR_OPEN = dtime(9, 30)
REGULAR_CLOSE = dtime(16, 0)
EARLY_CLOSE = dtime(13, 0)
AFTERHOURS_CLOSE = dtime(20, 0)


# ── NYSE calendar ────────────────────────────────────────────────────────────

def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm — Good Friday is Easter minus two days."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th `weekday` (Mon=0) of a month; n=-1 means the last one."""
    if n > 0:
        d = date(year, month, 1)
        offset = (weekday - d.weekday()) % 7
        return d + timedelta(days=offset + 7 * (n - 1))
    d = date(year, month, 1) + timedelta(days=32)
    d = date(d.year, d.month, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    """A holiday on Saturday is observed Friday; on Sunday, the following Monday."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def market_holidays(year: int) -> Dict[date, str]:
    """NYSE full-day closures for a year, computed from the rules (not a table that
    silently expires)."""
    h: Dict[date, str] = {}
    h[_observed(date(year, 1, 1))] = "New Year's Day"
    h[_nth_weekday(year, 1, 0, 3)] = "Martin Luther King Jr. Day"
    h[_nth_weekday(year, 2, 0, 3)] = "Washington's Birthday"
    h[_easter(year) - timedelta(days=2)] = "Good Friday"
    h[_nth_weekday(year, 5, 0, -1)] = "Memorial Day"
    if year >= 2022:
        h[_observed(date(year, 6, 19))] = "Juneteenth"
    h[_observed(date(year, 7, 4))] = "Independence Day"
    h[_nth_weekday(year, 9, 0, 1)] = "Labor Day"
    h[_nth_weekday(year, 11, 3, 4)] = "Thanksgiving Day"
    h[_observed(date(year, 12, 25))] = "Christmas Day"
    return h


def early_closes(year: int) -> Dict[date, str]:
    """Scheduled 13:00 ET closes."""
    e: Dict[date, str] = {}
    e[_nth_weekday(year, 11, 3, 4) + timedelta(days=1)] = "day after Thanksgiving"
    jul3 = date(year, 7, 3)
    if jul3.weekday() < 5 and _observed(date(year, 7, 4)) == date(year, 7, 4):
        e[jul3] = "July 3rd"
    dec24 = date(year, 12, 24)
    if dec24.weekday() < 5:
        e[dec24] = "Christmas Eve"
    return {d: r for d, r in e.items() if d not in market_holidays(year)}


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in market_holidays(d.year)


def next_trading_day(d: date) -> date:
    n = d + timedelta(days=1)
    while not is_trading_day(n):
        n += timedelta(days=1)
    return n


def close_time_for(d: date) -> dtime:
    return EARLY_CLOSE if d in early_closes(d.year) else REGULAR_CLOSE


def session_state(now: Optional[datetime] = None) -> Dict[str, Any]:
    """What the US market is doing right now — pure calendar, no network."""
    now = (now or datetime.now(timezone.utc)).astimezone(ET)
    today = now.date()
    hols = market_holidays(today.year)
    early = early_closes(today.year)
    t = now.time()
    close_t = close_time_for(today)

    holiday = hols.get(today)
    if today.weekday() >= 5:
        state, reason = "closed", f"weekend ({now.strftime('%A')})"
    elif holiday:
        state, reason = "holiday", f"NYSE closed — {holiday}"
    elif t < PREMARKET_OPEN:
        state, reason = "closed", "before the pre-market session"
    elif t < REGULAR_OPEN:
        state, reason = "premarket", "pre-market (extended hours)"
    elif t < close_t:
        state, reason = "open", ("regular session — EARLY CLOSE at 13:00 ET"
                                 if today in early else "regular session")
    elif t < AFTERHOURS_CLOSE:
        state, reason = "afterhours", "after-hours (extended hours)"
    else:
        state, reason = "closed", "after the extended session"

    # "Next regular session" = the next one that has NOT yet opened. Once today's
    # open has passed (in-session, after-hours, or closed for the day) the answer is
    # the next trading day — reporting today's finished session as "next" made the
    # after-hours countdown read as though the open were still ahead.
    if is_trading_day(today) and t < REGULAR_OPEN:
        nxt_day = today
    else:
        nxt_day = next_trading_day(today)
    nxt_open = datetime.combine(nxt_day, REGULAR_OPEN, tzinfo=ET)
    secs = max(0, round((nxt_open - now).total_seconds()))
    secs_to_close = None
    if state == "open":
        secs_to_close = max(0, round((datetime.combine(today, close_t, tzinfo=ET) - now).total_seconds()))

    return {
        "state": state,
        "reason": reason,
        "is_trading_day": is_trading_day(today),
        "holiday": holiday,
        "early_close": early.get(today),
        "date_et": today.isoformat(),
        "now_et": now.isoformat(timespec="seconds"),
        "now_ist": now.astimezone(IST).isoformat(timespec="seconds"),
        "now_utc": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "weekday_et": now.strftime("%A"),
        "regular_open_et": REGULAR_OPEN.strftime("%H:%M"),
        "regular_close_et": close_t.strftime("%H:%M"),
        "seconds_until_close": secs_to_close,
        "next_session": {
            "date": nxt_day.isoformat(),
            "opens_et": nxt_open.isoformat(timespec="seconds"),
            "opens_ist": nxt_open.astimezone(IST).isoformat(timespec="seconds"),
            "seconds_until_open": secs,
            "hours_until_open": round(secs / 3600, 2),
        },
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


# ── The tape ─────────────────────────────────────────────────────────────────
# Yahoo symbols. `kind` decides how an instrument contributes to risk appetite:
#   risk_on  — rises when appetite rises (equities, bitcoin)
#   risk_off — rises when appetite falls (volatility, gold)
#   context  — informative but not scored directionally (rates, dollar, crude)

TAPE: Dict[str, Dict[str, Any]] = {
    "SPY":       {"label": "S&P 500 (SPY)",       "kind": "risk_on",  "weight": 1.0},
    "QQQ":       {"label": "Nasdaq 100 (QQQ)",    "kind": "risk_on",  "weight": 0.9},
    "IWM":       {"label": "Russell 2000 (IWM)",  "kind": "risk_on",  "weight": 0.8},
    "DIA":       {"label": "Dow 30 (DIA)",        "kind": "risk_on",  "weight": 0.6},
    "^VIX":      {"label": "VIX",                 "kind": "risk_off", "weight": 1.0},
    "^TNX":      {"label": "US 10y yield",        "kind": "context",  "weight": 0.0},
    "DX-Y.NYB":  {"label": "US dollar index",     "kind": "context",  "weight": 0.0},
    "CL=F":      {"label": "Crude oil",           "kind": "context",  "weight": 0.0},
    "GC=F":      {"label": "Gold",                "kind": "risk_off", "weight": 0.4},
    "BTC-USD":   {"label": "Bitcoin",             "kind": "risk_on",  "weight": 0.4},
}

_BREADTH_PROXY = "SPY"


def _series_stats(sym: str) -> Dict[str, Any]:
    """Trend/range/vol statistics for one instrument from its daily history."""
    # Pooled chart API, not yfinance: yfinance intermittently reported IWM and ^TNX
    # as "possibly delisted; no price data found", which would have silently reduced
    # the tape rather than reporting a provider problem.
    h = _R.bars(sym, rng="6mo", interval="1d")
    if h.get("state") != "ok" or len(h.get("bars") or []) < 30:
        return {"state": h.get("state", "empty"), "reason": h.get("reason")}
    pts = [b for b in h["bars"] if b.get("c") is not None
           and b.get("h") is not None and b.get("l") is not None]
    closes = [p["c"] for p in pts]
    highs = [p["h"] for p in pts]
    lows = [p["l"] for p in pts]
    last = closes[-1]
    sma20 = sum(closes[-20:]) / 20
    sma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else sma20
    rets = [(closes[i] - closes[i - 1]) / closes[i - 1]
            for i in range(1, len(closes)) if closes[i - 1]]

    def _stdev(xs):
        if len(xs) < 2:
            return None
        m = sum(xs) / len(xs)
        return (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5

    sd20 = _stdev(rets[-20:])
    sd60 = _stdev(rets[-60:]) if len(rets) >= 60 else sd20
    rv20 = round(sd20 * (252 ** 0.5) * 100, 1) if sd20 else None
    rv60 = round(sd60 * (252 ** 0.5) * 100, 1) if sd60 else None

    # True-range based ADX-lite: how much of the 20-day range the net move covered.
    # 1.0 = every day pushed the same way (trend); ~0 = chop inside a band.
    win = closes[-21:]
    net = abs(win[-1] - win[0])
    path = sum(abs(win[i] - win[i - 1]) for i in range(1, len(win)))
    efficiency = round(net / path, 3) if path else None

    hi20, lo20 = max(highs[-20:]), min(lows[-20:])
    pos_in_range = round((last - lo20) / (hi20 - lo20) * 100, 1) if hi20 > lo20 else None
    range_pct = round((hi20 - lo20) / lo20 * 100, 2) if lo20 else None

    def _chg(n):
        return round((closes[-1] - closes[-1 - n]) / closes[-1 - n] * 100, 2) if len(closes) > n else None

    return {
        "state": "ok", "price": round(last, 4),
        "chg_1d": _chg(1), "chg_5d": _chg(5), "chg_20d": _chg(20), "chg_60d": _chg(60),
        "sma20": round(sma20, 4), "sma50": round(sma50, 4),
        "above_sma20": last > sma20, "above_sma50": last > sma50,
        "realized_vol_20d": rv20, "realized_vol_60d": rv60,
        "vol_ratio_20_60": round(rv20 / rv60, 2) if (rv20 and rv60) else None,
        "trend_efficiency": efficiency,
        "pos_in_20d_range_pct": pos_in_range, "range_20d_pct": range_pct,
        "as_of": h.get("source_timestamp"),
    }


def _fmt(v, suffix="%"):
    return "n/a" if v is None else f"{v:+.2f}{suffix}"


def tape(refresh: bool = False) -> Dict[str, Any]:
    """Quotes + statistics for every tape instrument, concurrently."""
    if refresh:
        for s in TAPE:
            _R.invalidate(f"q1:{s}")
    q = _R.quotes(list(TAPE), ttl=45, timeout=25.0)
    stats = _R.gather({s: (lambda s=s: _series_stats(s)) for s in TAPE}, timeout=30.0)
    out: Dict[str, Any] = {}
    for sym, meta in TAPE.items():
        qq = q.get(sym) or {}
        st = stats.get(sym)
        st = st if isinstance(st, dict) and "state" in st else {"state": "error"}
        ok = qq.get("state") == "ok"
        out[sym] = {
            "symbol": sym, "label": meta["label"], "kind": meta["kind"],
            "state": "ok" if ok else (qq.get("state") or "error"),
            "price": qq.get("price"),
            "change_pct": qq.get("change_pct"),
            "source_timestamp": qq.get("source_timestamp"),
            "fetched_at": qq.get("fetched_at"),
            "reason": qq.get("reason"),
            "stats": st,
        }
    return out


def _risk_appetite(t: Dict[str, Any]) -> Dict[str, Any]:
    """Weighted risk-on/risk-off score in [-100, +100] from the day's moves."""
    num = den = 0.0
    contribs: List[Dict[str, Any]] = []
    for sym, meta in TAPE.items():
        if meta["kind"] == "context" or not meta["weight"]:
            continue
        row = t.get(sym) or {}
        chg = row.get("change_pct")
        if row.get("state") != "ok" or chg is None:
            continue
        # VIX moves are an order of magnitude larger than index moves — scale it so
        # a 5% VIX pop is comparable to a 0.5% SPY move, not ten times louder.
        scale = 0.1 if sym == "^VIX" else 1.0
        signed = chg * scale * (1 if meta["kind"] == "risk_on" else -1)
        num += signed * meta["weight"]
        den += meta["weight"]
        contribs.append({"symbol": sym, "label": meta["label"], "change_pct": chg,
                         "direction": meta["kind"], "contribution": round(signed * meta["weight"], 3)})
    if not den:
        return {"score": None, "contributors": contribs, "coverage": 0.0}
    raw = num / den
    return {"score": round(max(-100.0, min(100.0, raw * 40)), 1),
            "contributors": sorted(contribs, key=lambda c: -abs(c["contribution"])),
            "coverage": round(den / sum(m["weight"] for m in TAPE.values() if m["weight"]), 2)}


def classify(t: Dict[str, Any]) -> Dict[str, Any]:
    """Regime labels + the exact observations behind each one."""
    ev: List[Dict[str, Any]] = []
    labels: List[str] = []

    def obs(k, text, value=None, symbol=None):
        ev.append({"factor": k, "observation": text, "value": value, "symbol": symbol,
                   "source_timestamp": ((t.get(symbol) or {}).get("source_timestamp") if symbol else None)})

    spy = t.get("SPY") or {}
    spy_s = spy.get("stats") or {}
    vix = t.get("^VIX") or {}
    vix_s = vix.get("stats") or {}

    # 1) risk appetite -------------------------------------------------------
    ra = _risk_appetite(t)
    score = ra["score"]
    # Neutral band: wide enough that a tape with the indices pointing in different
    # directions reads "mixed" rather than being forced onto one side by a single
    # loud contributor. Configurable.
    band = float(os.environ.get("REGIME_RISK_BAND", 20))
    if score is None:
        risk = "unknown"
        obs("risk_appetite", "no index or volatility data available — risk appetite not computed")
    elif score >= band:
        risk = "risk-on"
    elif score <= -band:
        risk = "risk-off"
    else:
        risk = "mixed"
    if score is not None:
        parts = ", ".join(f"{c['label']} {_fmt(c['change_pct'])}" for c in ra["contributors"][:4])
        obs("risk_appetite", f"weighted risk score {score:+.1f} from {parts}", score)
    labels.append(risk)

    # 2) trend vs range ------------------------------------------------------
    eff = spy_s.get("trend_efficiency")
    posr = spy_s.get("pos_in_20d_range_pct")
    if spy_s.get("state") != "ok" or eff is None:
        structure = "unknown"
        obs("structure", "SPY history unavailable — trend/range not determined", symbol="SPY")
    elif eff >= 0.35:
        structure = "trending"
        obs("structure",
            f"SPY 20-day path efficiency {eff:.2f} (net move is {eff:.0%} of total travel); "
            f"price {'above' if spy_s.get('above_sma20') else 'below'} its 20-DMA and "
            f"{'above' if spy_s.get('above_sma50') else 'below'} its 50-DMA", eff, "SPY")
    else:
        structure = "range-bound"
        obs("structure",
            f"SPY 20-day path efficiency {eff:.2f} — travel without progress; price sits at "
            f"{posr}% of the 20-day range ({spy_s.get('range_20d_pct')}% wide)", eff, "SPY")
    labels.append(structure)

    # 3) volatility regime ---------------------------------------------------
    vix_level = vix.get("price")
    rv20, rv60 = spy_s.get("realized_vol_20d"), spy_s.get("realized_vol_60d")
    if vix_level is None and rv20 is None:
        vol_state = "unknown"
        obs("volatility", "neither VIX nor SPY realized volatility available")
    else:
        hi = (vix_level is not None and vix_level >= 22) or (rv20 is not None and rv20 >= 22)
        lo = (vix_level is not None and vix_level < 15) and (rv20 is None or rv20 < 15)
        vol_state = "high-volatility" if hi else ("low-volatility" if lo else "normal-volatility")
        obs("volatility",
            f"VIX {vix_level if vix_level is not None else 'n/a'} "
            f"({_fmt(vix.get('change_pct'))} today); SPY realized vol 20d {rv20}% vs 60d {rv60}%",
            vix_level, "^VIX")
    labels.append(vol_state)

    # 4) event-driven --------------------------------------------------------
    vix_chg = vix.get("change_pct")
    vr = spy_s.get("vol_ratio_20_60")
    event = False
    if vix_chg is not None and abs(vix_chg) >= 12:
        event = True
        obs("event_risk", f"VIX moved {_fmt(vix_chg)} today — a volatility shock, not a drift", vix_chg, "^VIX")
    if vr is not None and vr >= 1.5:
        event = True
        obs("event_risk", f"SPY 20-day realized vol is {vr}x its 60-day — volatility is expanding", vr, "SPY")
    if spy.get("change_pct") is not None and abs(spy["change_pct"]) >= 1.5:
        event = True
        obs("event_risk", f"SPY {_fmt(spy['change_pct'])} on the day — an outsized index move", spy["change_pct"], "SPY")
    if event:
        labels.append("event-driven")
    else:
        obs("event_risk", "no volatility shock, vol expansion or outsized index move detected")

    # 5) context instruments (never scored, always shown) ---------------------
    for sym in ("^TNX", "DX-Y.NYB", "CL=F"):
        row = t.get(sym) or {}
        if row.get("state") == "ok":
            obs("context", f"{row['label']} {row.get('price')} ({_fmt(row.get('change_pct'))})",
                row.get("price"), sym)
        else:
            obs("context", f"{TAPE[sym]['label']} unavailable ({row.get('state')})", symbol=sym)

    missing = [s for s, r in t.items() if r.get("state") != "ok"]
    coverage = round(1 - len(missing) / len(TAPE), 2)
    confidence = round(max(0.0, min(1.0, coverage * (0.6 + 0.4 * (ra.get("coverage") or 0)))), 2)

    return {
        "labels": labels,
        "headline": " / ".join(labels),
        "risk_appetite": risk,
        "risk_score": score,
        "structure": structure,
        "volatility_regime": vol_state,
        "event_driven": event,
        "evidence": ev,
        "risk_contributors": ra["contributors"],
        "missing_instruments": missing,
        "data_coverage": coverage,
        "confidence": confidence,
        "confidence_note": ("Confidence is data COVERAGE, not predictive accuracy — "
                            "it says how much of the tape was actually observed."),
    }


def overview(refresh: bool = False, blocking: bool = False) -> Dict[str, Any]:
    """Session + tape + regime, the whole Market Overview payload.

    `blocking=False` (the web UI): a cold miss returns the SESSION immediately with
    a loading regime and computes the tape in the background — the panel paints its
    clock and session state instantly instead of waiting ~12s on ten providers.
    `blocking=True` (schedulers, scans): compute synchronously.
    """
    def _compute():
        t = tape(refresh=refresh)
        c = classify(t)
        return {"state": "ok", "session": session_state(), "tape": t, "regime": c,
                "generated_at": datetime.now(timezone.utc).isoformat()}

    if refresh:
        _R.invalidate("regime:overview")
    if blocking:
        val, cs = _R.swr("regime:overview", 60, _compute)
    else:
        val, cs = _R.swr_async("regime:overview", 60, _compute, loading={
            "state": "loading", "session": session_state(), "tape": {},
            "regime": {"headline": "computing…", "labels": [], "evidence": []},
            "reason": "reading the tape"})
    if isinstance(val, dict):
        # the session clock is pure calendar — never serve a cached one
        return {**val, "session": session_state(), "cache_state": cs}
    return val
