"""Decision Engine v1 — evidence + expected-value + execution-aware scoring.

Design principles (from the spec):
  * Signals are grouped into INDEPENDENT families so correlated indicators (all
    price-derived) can't stack into fake confidence (#4).
  * Confidence is SEPARATED four ways (#6): P(direction), P(trade profitable),
    data confidence, execution confidence -> overall quality.
  * Every trade gets an EXPECTED VALUE net of spread/slippage/theta (#5) and is
    REJECTED if EV<=0 or liquidity/quality gates fail (#17). No-trade is success.
  * Fully EXPLAINABLE (#18): each family's weighted contribution is returned.
  * Speculative/penny names allowed but held to a STRICTER bar (#11/#12).

v1 uses free data only. Paid signal families (options flow, short interest,
insider, filings, social) are declared but return conf=0 until a data source is
wired — they never invent evidence.
"""
from __future__ import annotations
import os, sys, math
from statistics import fmean

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))
from tradingview_mcp.core.services import strategy_service as ss
from tradingview_mcp.core.services.options_service import get_options_chain

# ── Signal families (each: direction in [-1,1], conf in [0,1]) ────────────────

def _fam_trend(a: dict) -> dict:
    trend = (a.get("trend_state") or "").lower()
    ms = a.get("market_sentiment") or {}
    mom, sig = ms.get("momentum"), (ms.get("buy_sell_signal") or "").upper()
    rsi = (a.get("rsi") or {}).get("value") or 50
    d = 0.0
    d += {"strong uptrend": .6, "uptrend": .35, "weak uptrend": .2,
          "downtrend": -.4, "strong downtrend": -.6}.get(trend, 0.0)
    d += .25 if mom == "Bullish" else (-.25 if mom == "Bearish" else 0)
    d += .2 if sig in ("BUY", "STRONG BUY") else (-.2 if sig in ("SELL", "STRONG SELL") else 0)
    if rsi > 78: d -= .15          # overbought exhaustion
    elif rsi < 25: d += .1         # oversold snap-back
    conf = .8 if sig in ("BUY", "STRONG BUY", "SELL", "STRONG SELL") else .55
    return {"family": "trend/momentum", "dir": max(-1, min(1, round(d, 2))),
            "conf": conf, "detail": f"{trend or '?'}, {mom}, {sig}, RSI {round(rsi,1)}"}


def _fam_catalyst(sym: str) -> dict:
    """Per-ticker Google News (free) scored with the catalyst keyword engine."""
    try:
        import news_feeds
        return {"family": "catalyst", **news_feeds.catalyst_signal(sym)}
    except Exception:
        return {"family": "catalyst", "dir": 0.0, "conf": 0.0, "detail": "no news"}


def _fam_regime(regime: dict, direction: str) -> dict:
    if not regime:
        return {"family": "regime", "dir": 0.0, "conf": 0.0, "detail": "no regime"}
    score = regime.get("risk_appetite_score", 50)
    tilt = (score - 50) / 50.0                      # +1 risk-on ... -1 risk-off
    d = tilt if direction == "LONG" else -tilt
    return {"family": "regime", "dir": round(max(-1, min(1, d)), 2), "conf": .6,
            "detail": f"{regime.get('regime')} score {score}"}


# Free-data families (Yahoo/yfinance + SEC EDGAR — wired 2026-07-10).
def _fam_short(sym: str) -> dict:
    try:
        import market_data
        return {"family": "short-interest", **market_data.short_squeeze_signal(sym)}
    except Exception:
        return _fam_stub("short-interest")


def _fam_filings(sym: str) -> dict:
    try:
        import edgar
        return {"family": "filings/insider", **edgar.dilution_penalty(sym)}
    except Exception:
        return _fam_stub("filings/insider")


def _fam_options_flow(sym: str) -> dict:
    """Unusual options activity (V/OI) + put/call volume ratio = positioning signal."""
    try:
        from tradingview_mcp.core.services.options_service import get_unusual_options_activity
        oa = get_unusual_options_activity(sym)
        if "error" in oa:
            return _fam_stub("options-flow")
        pc = oa.get("put_call_volume_ratio")
        u = oa.get("unusual", [])
        calls = sum(1 for x in u if x.get("side") == "call")
        puts = sum(1 for x in u if x.get("side") == "put")
        d = 0.0
        if pc is not None:
            d += 0.3 if pc < 0.7 else (-0.3 if pc > 1.3 else 0)
        d += 0.15 if calls > puts else (-0.15 if puts > calls else 0)
        return {"family": "options-flow", "dir": max(-1, min(1, round(d, 2))),
                "conf": 0.5 if u else 0.2, "detail": f"P/C vol {pc}, unusual {calls}C/{puts}P"}
    except Exception:
        return _fam_stub("options-flow")


def _fam_social(sym: str, price: float) -> dict:
    """StockTwits labeled sentiment (low trust; penny+chatter = pump-risk)."""
    try:
        import social_sentiment
        return {"family": "social-sentiment",
                **social_sentiment.social_signal(sym, speculative=(price or 0) < 5.0)}
    except Exception:
        return _fam_stub("social-sentiment")


def _fam_analyst(sym: str) -> dict:
    """Finnhub analyst recommendations — independent (not price-derived) signal."""
    try:
        import finnhub_data
        return {"family": "analyst-ratings", **finnhub_data.analyst_signal(sym)}
    except Exception:
        return _fam_stub("analyst-ratings")


def _fam_macro(direction: str) -> dict:
    """FRED macro/rates regime — yield curve + VIX backdrop."""
    try:
        import fred
        return {"family": "macro-rates", **fred.macro_signal(direction)}
    except Exception:
        return _fam_stub("macro-rates")


# Still-unwired families — declared, never faked (conf=0 until a source exists).
def _fam_stub(name: str) -> dict:
    return {"family": name, "dir": 0.0, "conf": 0.0, "detail": "no data source yet"}


# ── Gates (non-directional; scale confidence, don't add evidence) ─────────────

def _gate_volatility(a: dict) -> dict:
    atrp = (a.get("atr") or {}).get("percent_of_price") or 0
    if 1.5 <= atrp <= 6: q = 1.0
    elif atrp < 1.2: q = 0.35            # too quiet — nothing to capture
    elif atrp > 9: q = 0.35             # too wild — stops get run
    else: q = 0.7
    return {"quality": q, "atr_pct": round(atrp, 2)}


def _gate_liquidity(price: float, opt: dict | None) -> dict:
    spec = price < 5.0                    # penny/micro tier
    if opt:
        bid, ask = opt.get("bid") or 0, opt.get("ask") or 0
        mid = (bid + ask) / 2 if (bid and ask) else (opt.get("premium") or 0)
        spr = (ask - bid) / mid if (bid and ask and mid) else 0.5
        oi = opt.get("open_interest") or 0
        g = 1.0 if (spr <= 0.10 and oi >= 500) else (0.6 if spr <= 0.25 else 0.25)
        return {"exec": g, "spread_pct": round(spr * 100, 1), "oi": oi, "speculative": spec}
    # stock: penny names penalised for spread/manipulation risk
    return {"exec": 0.5 if spec else 1.0, "spread_pct": None, "speculative": spec}


# ── Expected value ────────────────────────────────────────────────────────────

def _expected_value(p_win, profit, loss, cost):
    return round(p_win * profit - (1 - p_win) * loss - cost, 3)


# ── Auditable-decision helpers (gates, freshness, disagreement, scenarios) ────

def _env_true(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _min_data_quality() -> float:
    try:
        return float(os.environ.get("DECISION_MIN_DATA_QUALITY", "0.55"))
    except Exception:
        return 0.55


def _conviction_min() -> float:
    try:
        return float(os.environ.get("DECISION_CONVICTION_MIN", "60"))
    except Exception:
        return 60.0


def _engine_freshness(data_state: str, analysis: Optional[dict] = None) -> dict:
    """Freshness of the base analysis, via the SHARED classifier — so the engine,
    scanner, panels and header can never disagree.

    Freshness is the age of the DATA, not the identity of the provider that served it.
    This used to return `classify(0)` — a hardcoded zero — whenever the primary
    answered, so a Friday close read on Sunday reported "fresh, 0s old, tradeable".
    A working provider serving old data is exactly the case a freshness check exists
    to catch. When the analysis carries `as_of`, that bar's real age decides; the
    provider only decides the FLOOR (a degraded source still blocks).

      fallback-provider  ⇒ genuinely degraded source → `fallback` (blocks)
      secondary-provider ⇒ Yahoo down, on the optional TV secondary → `ageing` floor
      unavailable        ⇒ no data at all → `unknown`, never `fresh`
    """
    import freshness as _fr
    if data_state == "fallback-provider":
        return _fr.classify(None, is_fallback=True)
    if data_state in ("unavailable", None, ""):
        # No provider produced data — that is UNKNOWN freshness, never "fresh".
        return _fr.classify(None)

    market_state = None
    try:
        import market_regime as _mr
        market_state = _mr.session_state()["state"]
    except Exception:
        pass

    age = _fr.bar_age_seconds((analysis or {}).get("as_of"))
    if age is None:
        # No timestamp on the payload — that is UNKNOWN, not fresh. Saying "we cannot
        # tell how old this is" is the honest answer and it does not block on its own.
        rec = _fr.classify_typed("quote", None, market_state=market_state)
        rec["label"] = "age unknown — provider supplied no timestamp"
        return rec

    rec = _fr.classify_typed("quote", age, market_state=market_state)
    if data_state == "secondary-provider":
        # A real live read from the optional secondary is never rated better than
        # `ageing` — a mild, non-blocking penalty. Downgrade the STATE directly rather
        # than inflating the age: a fabricated age would then be reported as if measured.
        if rec["state"] == "fresh":
            rec["state"] = "ageing"
            rec["penalty"] = _fr.PENALTY["ageing"]
            rec["blocks_tradeable"] = _fr.blocks_tradeable("ageing")
        rec["label"] = f"secondary provider — {rec['label']}"
    return rec


def _disagreement(active: list, sign: int) -> dict:
    """Quantify cross-family conflict RELATIVE to the trade direction. Bull/bear/
    neutral are contributions (dir×conf); a family opposing the trade is a conflict.
    Severity is the minority share of directional weight."""
    bull = sum(f["dir"] * f["conf"] * sign for f in active if f["dir"] * sign > 0.02)
    bear = sum(-f["dir"] * f["conf"] * sign for f in active if f["dir"] * sign < -0.02)
    neutral = sum(f["conf"] for f in active if abs(f["dir"]) <= 0.02)
    bull, bear = abs(bull), abs(bear)
    total = bull + bear or 1e-9
    minority = min(bull, bear) / total
    conflicting = [f for f in active if f["dir"] * sign < -0.05]
    strongest = max(conflicting, key=lambda f: abs(f["dir"] * f["conf"])) if conflicting else None
    severity = "high" if minority >= 0.40 else ("medium" if minority >= 0.20 else "low")
    penalty = {"low": 0.0, "medium": 0.08, "high": 0.18}[severity]
    return {"severity": severity, "bullish_contribution": round(bull, 3),
            "bearish_contribution": round(bear, 3), "neutral_contribution": round(neutral, 3),
            "conflicting_family_count": len(conflicting),
            "strongest_conflict": (f"{strongest['family']}: {strongest['detail']}" if strongest else None),
            "confidence_penalty": penalty, "minority_share": round(minority, 3)}


def _scenario_probabilities(trend_dir: float, risk_ps: float, reward_ps: float,
                            atr: float, data_conf: float, agreement: float) -> dict:
    """Mutually-exclusive bull / base / bear distribution that SUMS TO 100%.

    Derived from the trade GEOMETRY (barrier distances in ATR units) plus a small
    drift from the TREND family — a drifted gambler's-ruin double-barrier model.
    Deliberately NOT a function of P(dir) (no shared/overlapping probability)."""
    atr = atr or 1e-9
    u = max(1e-6, min(10.0, reward_ps / atr))     # distance to target (ATR units)
    d = max(1e-6, min(10.0, risk_ps / atr))       # distance to stop (ATR units)
    mu = max(-0.5, min(0.5, trend_dir or 0.0)) * 0.15   # per-step drift from trend
    p_up = 0.5 + mu
    if abs(p_up - 0.5) < 1e-9:
        p_target = d / (u + d)                    # symmetric gambler's ruin
    else:
        ratio = (1 - p_up) / p_up
        try:
            p_target = (1 - ratio ** d) / (1 - ratio ** (u + d))
        except Exception:
            p_target = d / (u + d)
    p_target = max(0.05, min(0.90, p_target))
    p_bear = 1.0 - p_target
    ext = 0.25 + 0.35 * max(0.0, min(1.0, abs(trend_dir or 0.0)))   # target→extension share
    bull, base = p_target * ext, p_target * (1 - ext)
    tot = bull + base + p_bear or 1e-9
    pcts = {"bull": round(bull / tot * 100, 1), "base": round(base / tot * 100, 1),
            "bear": round(p_bear / tot * 100, 1)}
    # Force EXACT 100.0 by putting the rounding residue on the largest bucket.
    resid = round(100.0 - (pcts["bull"] + pcts["base"] + pcts["bear"]), 1)
    kmax = max(pcts, key=pcts.get)
    pcts[kmax] = round(pcts[kmax] + resid, 1)
    conf = round(min(0.90, 0.30 + 0.40 * data_conf + 0.20 * agreement), 2)
    return {**pcts,
            "method": "double-barrier ATR model (drifted gambler's ruin; drift from the trend family, not P(dir))",
            "confidence": conf}


def _grade_option_chain(opt, liq_opt, option_block, bar, stock_q) -> dict:
    """Grade the OPTION independently of the stock (Prompt 5B). A weak/absent option
    must NEVER reject the stock. Only produce a numeric option grade when the live
    microstructure (bid, ask, spread, OI, DTE) is actually present; otherwise report
    the option as ungradeable and keep the stock decision on its own merits.

    Returns {chain_quality, best_option_quality, preference, gradeable, missing}.
    preference ∈ prefer-stock | prefer-option | avoid-both | stock-only."""
    if not opt:
        return {"chain_quality": None, "best_option_quality": None,
                "preference": "stock-only", "gradeable": False,
                "missing": ["no option idea"], "stock_ok": stock_q >= bar}
    bid, ask = opt.get("bid"), opt.get("ask")
    oi = opt.get("open_interest")
    dte = opt.get("days_to_expiry")
    spr = (liq_opt or {}).get("spread_pct")
    vol = opt.get("volume")
    missing = [k for k, v in (("bid", bid), ("ask", ask), ("open_interest", oi),
                              ("days_to_expiry", dte), ("spread_pct", spr)) if v in (None, 0)]
    stock_ok = stock_q >= bar
    if missing:
        # Can't responsibly grade the option — say so, keep the stock decision intact.
        return {"chain_quality": None, "best_option_quality": None,
                "preference": ("prefer-stock" if stock_ok else "avoid-both"),
                "gradeable": False, "missing": missing, "stock_ok": stock_ok}
    liq_score = max(0.0, min(1.0, 1.0 - (spr / 100.0) / 0.10))     # spread ≤10% → best
    depth = min(1.0, (oi or 0) / 500.0) * 0.6 + min(1.0, (vol or 0) / 100.0) * 0.4
    dte_ok = 1.0 if (3 <= dte <= 60) else (0.5 if dte else 0.2)
    chain_q = round(100 * (0.5 * liq_score + 0.3 * depth + 0.2 * dte_ok), 1)
    ev_ok = bool(option_block) and (option_block.get("ev_per_contract") or 0) > 0
    best_q = chain_q if ev_ok else round(chain_q * 0.5, 1)
    if not stock_ok and not ev_ok:
        pref = "avoid-both"
    elif ev_ok and best_q >= max(50.0, stock_q):
        pref = "prefer-option"
    else:
        pref = "prefer-stock"
    return {"chain_quality": chain_q, "best_option_quality": best_q,
            "preference": pref, "gradeable": True, "missing": [], "stock_ok": stock_ok}


def _engine_workers() -> int:
    try:
        return max(2, int(os.environ.get("DECISION_ENGINE_WORKERS", "6")))
    except Exception:
        return 6


def _tv_exchange_hint(exchange: str) -> str:
    """Map a human / security-master exchange label to a TradingView screener
    exchange, so we hit the RIGHT venue once instead of blindly trying NASDAQ
    then NYSE (which paid the TradingView throttle twice — see BASELINE.md)."""
    e = (exchange or "").upper()
    # Check ARCA/AMEX BEFORE NYSE — "NYSE Arca" contains "NYSE" but TradingView
    # lists those ETFs under AMEX.
    if "ARCA" in e or "AMEX" in e or "BATS" in e or "CBOE" in e or e in ("PCX", "ASE"):
        return "AMEX"
    if "NYSE" in e or e in ("NYQ", "XNYS", "NYS"):
        return "NYSE"
    return "NASDAQ"


def _load_analysis(symbol: str, exchange: str):
    """Base technicals for ONE symbol. PRIMARY is now Yahoo/yfinance (free, reliable,
    not throttle-prone); TradingView is an OPTIONAL secondary, only tried if Yahoo is
    down AND `TRADINGVIEW_ENABLED` is on. This kills the TradingView single-point-of-
    failure — the stock page and scans stay useful with TV throttled or disabled.

    Returns (analysis, source, data_state) where data_state ∈
        fresh              — served by a live primary (Yahoo) or the TV secondary
        secondary-provider — Yahoo down, served by the optional TradingView confirm
        unavailable        — no provider produced usable technicals.
    """
    # PRIMARY — Yahoo/yfinance technicals (trend/RSI/ATR from real OHLCV).
    try:
        import fallback_ta
        fb = fallback_ta.analysis(symbol)
        if isinstance(fb, dict) and "error" not in fb:
            return fb, "yahoo", "fresh"
    except Exception:
        pass
    # SECONDARY (optional) — TradingView, only if enabled. Never required.
    try:
        import providers as _prov
        if _prov.tv_enabled():
            exch = _tv_exchange_hint(exchange)
            cand = ss._analyze_cached(symbol, exch, "1D")
            if isinstance(cand, dict) and "error" not in cand:
                return cand, f"tradingview:{exch}", "secondary-provider"
    except Exception:
        pass  # throttled / disabled — nothing else to try
    return None, "unavailable", "unavailable"


def _safe_regime():
    try:
        return ss.market_regime()
    except Exception:
        return None


def _early_reject(symbol: str, gate: str, source: str, reason: str, requirement: str,
                  data_state: str = "unavailable", price=None) -> dict:
    """An early exit (no data / no price / halted) still has to honour the gate
    contract: the action is derived ONLY from `decision_gates`. So even these bail-outs
    carry the single HARD gate that failed, rather than a bare {"decision": "REJECT"}
    the UI can't explain."""
    g = {"name": gate, "passed": False, "value": source, "requirement": requirement,
         "reason": reason, "blocking": True}
    out = {"symbol": symbol, "decision": "REJECT", "data_state": data_state,
           "data_source": source, "reason": reason,
           "decision_gates": [g], "failed_gates": [gate],
           "reject_reasons": [reason], "upgrade_conditions": [reason],
           "freshness": _engine_freshness(data_state),
           "data_quality_pct": 0.0,
           "data_quality": {"profile": None, "overall": 0.0, "sufficient": False,
                            "blocking_gaps": [gate], "categories": {},
                            "missing_fields": {}, "explain": reason},
           "analysis_status": "complete", "pending_families": []}
    if price is not None:
        out["price"] = price
    return out


def evaluate(symbol: str, exchange: str = "NASDAQ", direction: str = "LONG",
             balance: float = 368.0, evaluate_option: bool = True,
             profile: str = "momentum", portfolio_check: bool = True) -> dict:
    import concurrent.futures as _cf
    import time as _t
    _t0 = _t.perf_counter()

    a, a_source, data_state = _load_analysis(symbol, exchange)
    if a is None:
        return _early_reject(symbol, "symbol_resolution", "unavailable",
                             "no analysis data (every provider failed)",
                             "resolved technicals from a provider", data_state="unavailable")
    price = (a.get("price_data") or {}).get("current_price")
    if not price:
        return _early_reject(symbol, "symbol_resolution", a_source, "no price",
                             "a live price from a provider", data_state=data_state)
    # Hard safety gate: never trade a halted ticker (#11).
    try:
        import halts
        if halts.is_halted(symbol):
            return _early_reject(symbol, "not_halted", "nasdaq-halts",
                                 "TRADING HALTED (Nasdaq) — do not trade until it resumes",
                                 "ticker not halted", data_state=data_state,
                                 price=round(price, 2))
    except Exception:
        pass

    # Fan out the option pick + the 7 I/O-bound families CONCURRENTLY — these were
    # evaluated sequentially before and dominated warm decision-engine latency
    # (BASELINE.md: 36.8s warm). Each family is independent and network-bound.
    with _cf.ThreadPoolExecutor(max_workers=_engine_workers(), thread_name_prefix="engine") as _ex:
        f_opt = _ex.submit(ss._pick_option_idea, symbol, round(price, 2), balance, direction) if evaluate_option else None
        f_reg = _ex.submit(_safe_regime)
        f_cat = _ex.submit(_fam_catalyst, symbol)
        f_short = _ex.submit(_fam_short, symbol)
        f_fil = _ex.submit(_fam_filings, symbol)
        f_of = _ex.submit(_fam_options_flow, symbol)
        f_soc = _ex.submit(_fam_social, symbol, price)
        f_an = _ex.submit(_fam_analyst, symbol)
        f_mac = _ex.submit(_fam_macro, direction)
        regime = f_reg.result()
        opt = f_opt.result() if f_opt else None
        fams = [_fam_trend(a), f_cat.result(), _fam_regime(regime, direction),
                f_short.result(), f_fil.result(), f_of.result(),
                f_soc.result(), f_an.result(), f_mac.result()]
    _engine_ms = round((_t.perf_counter() - _t0) * 1000, 1)
    # EDGAR dilution/insider flags are PENNY/micro-cap checks (#11). On large caps,
    # routine 424B debt shelves and RSU Form-4s are false positives — dampen them.
    if price >= 5:
        for f in fams:
            if f["family"] == "filings/insider" and f["dir"] < 0:
                f["dir"] = round(f["dir"] * 0.2, 3)
                f["detail"] += " (dampened: large-cap routine filings)"

    active = [f for f in fams if f["conf"] > 0]
    vol = _gate_volatility(a)
    liq = _gate_liquidity(price, None)          # STOCK liquidity drives the stock score
    liq_opt = _gate_liquidity(price, opt) if opt else None  # option spread drives option EV only

    # Directional score: weight each family by its confidence.
    wsum = sum(f["conf"] for f in active) or 1e-9
    raw_dir = sum(f["dir"] * f["conf"] for f in active) / wsum
    # Cross-family AGREEMENT (guards against fake confidence): alignment vs cancellation.
    denom = sum(abs(f["dir"]) * f["conf"] for f in active) or 1e-9
    agreement = abs(sum(f["dir"] * f["conf"] for f in active)) / denom
    data_conf = round(fmean([f["conf"] for f in active]), 2) if active else 0.0

    sign = 1 if direction == "LONG" else -1
    aligned = raw_dir * sign                                   # >0 means signals back the trade
    # Four separated confidences (#6)
    p_direction = round(min(.85, max(.15, .5 + .5 * aligned * agreement)), 3)
    exec_conf = round(liq["exec"] * vol["quality"], 2)

    # Sizing off ATR (stock); >=2:1 target by construction.
    atr = (a.get("atr") or {}).get("value") or (price * 0.02)
    stop = round(price - sign * 1.5 * atr, 2)
    t1 = round(price + sign * 2 * 1.5 * atr, 2)
    risk_ps, reward_ps = abs(price - stop), abs(t1 - price)
    stock_cost = price * 0.001                                 # ~0.1% spread+slippage, liquid stock
    ev_stock = _expected_value(p_direction, reward_ps, risk_ps, stock_cost)

    # Option structure: price can be right yet the option loses (theta+IV+spread).
    p_trade = p_direction
    option_block = None
    if opt:
        prem = opt["premium"]; spr = (liq_opt.get("spread_pct") or 0) / 100
        dte = opt.get("days_to_expiry") or 7
        theta_drag = min(.25, (7.0 / max(dte, 1)) * .10)       # short-dated bleeds more
        p_trade = round(max(.1, p_direction - spr * .5 - theta_drag), 3)
        opt_profit = prem * (reward_ps / max(risk_ps, .01)) * .5   # rough delta-scaled
        ev_opt = _expected_value(p_trade, opt_profit, prem, prem * spr)
        option_block = {"contract": opt["label"], "premium": prem, "pct_otm": opt.get("pct_otm"),
                        "spread_pct": liq_opt.get("spread_pct"), "theta_drag": round(theta_drag, 2),
                        "ev_per_contract": round(ev_opt * 100, 2),
                        "verdict": "structure OK" if ev_opt > 0 else "AVOID option — take the stock",
                        # Raw contract data, passed through so downstream evidence
                        # (options_shadow.record) can store real bid/ask/greeks
                        # instead of nulls — this is the SAME contract already
                        # picked and graded above, not a second provider call.
                        "bid": opt.get("bid"), "ask": opt.get("ask"),
                        "strike": opt.get("strike"), "expiry": opt.get("expiry"),
                        "option_type": opt.get("option_type"),
                        "days_to_expiry": opt.get("days_to_expiry"),
                        "volume": opt.get("volume"), "open_interest": opt.get("open_interest"),
                        "breakeven": opt.get("breakeven"),
                        "greeks": {"delta": opt.get("delta"), "iv": opt.get("implied_volatility")}}

    # Overall quality (0-100): reward strength+agreement, gated by data+execution.
    quality = round(100 * (.5 * agreement + .5 * min(1, abs(aligned) * 2))
                    * (0.55 + 0.45 * data_conf) * exec_conf, 1)
    try:
        import calibration
        quality = round(quality * calibration.adjustment(quality), 1)
    except Exception:
        pass
    # Penny/spec names must clear a HIGHER quality threshold (#11).
    spec = liq.get("speculative")
    bar = 65 if spec else 45

    # Freshness (shared classifier) + quantified cross-family disagreement.
    import freshness as _fr
    fresh = _engine_freshness(data_state, a)
    disagree = _disagreement(active, sign)

    # Position sizing: a risk budget scaled by uncertainty + speculation.
    risk_budget = round(balance * (0.03 if not spec else 0.015), 2)   # 3% normal, 1.5% spec
    uncertainty_scale = max(0.3, agreement * data_conf)
    dollar_risk = round(risk_budget * uncertainty_scale, 2)

    # Portfolio-level gate (#13-15): daily/weekly loss, drawdown, cash, sector caps.
    # Checks the `strategy-500` legacy account by default. Callers with their OWN
    # portfolio context (e.g. lab/paper/, which checks the real $500 account's risk
    # state itself, downstream, via broker.check_entry) must pass
    # portfolio_check=False — otherwise this gate would silently reject every trade
    # based on a DIFFERENT account's cash/drawdown, which is a correctness bug, not
    # caution (a candidate would fail here regardless of the caller's real capacity).
    portfolio_gate = None
    if portfolio_check:
        try:
            import risk_engine
            pg = risk_engine.check_new_trade(symbol, dollar_risk, bool(spec))
            portfolio_gate = {"allow": pg["allow"], "reasons": pg["reasons"],
                              "size_cap_usd": pg["size_cap_usd"],
                              "suspended": pg["portfolio"]["suspended"],
                              "drawdown_pct": pg["portfolio"]["drawdown_pct"]}
            if pg["allow"]:
                dollar_risk = min(dollar_risk, pg["size_cap_usd"])
        except Exception:
            pass
    shares = round(dollar_risk / max(risk_ps, .01), 4)

    # ── EV breakdown (explained, net of spread+slippage) ─────────────────────
    ev_gross = round(p_direction * reward_ps - (1 - p_direction) * risk_ps, 3)   # before cost
    ev_net = ev_stock                                                            # after cost
    ev_breakdown = {
        "ev_per_share": ev_net,
        "expected_return_pct": round(ev_net / price * 100, 3) if price else None,
        "expected_r": round(ev_net / risk_ps, 3) if risk_ps else None,      # EV in R units
        "ev_before_costs": ev_gross,
        "ev_after_slippage": ev_net,
        "inputs": {"p_win": p_direction, "reward_per_share": round(reward_ps, 3),
                   "risk_per_share": round(risk_ps, 3), "cost_per_share": round(stock_cost, 4),
                   "assumed_slippage_bps": 10},
        "formula": "EV = p_win*reward_ps - (1-p_win)*risk_ps - cost_ps  (cost = spread+slippage)",
    }

    # ── Max-loss breakdown (never shown without quantity) ────────────────────
    slippage_ps = round(price * 0.0015, 4)        # ~15 bps slippage/gap allowance
    total_planned_loss = round(shares * (risk_ps + slippage_ps), 2)
    max_loss_breakdown = {
        "quantity": shares, "entry": round(price, 2), "stop": stop,
        "stop_distance_per_share": round(risk_ps, 3),
        "slippage_gap_allowance_per_share": slippage_ps,
        "account_risk_budget": risk_budget,
        "total_planned_loss": total_planned_loss,
        "note": "total_planned_loss = quantity × (stop_distance + slippage/gap allowance)",
    }

    # ── Scenario probabilities (mutually exclusive, sum to 100; NOT from P(dir)) ─
    trend_fam = next((f for f in fams if f["family"] == "trend/momentum"), None)
    trend_dir_signed = (trend_fam["dir"] * sign) if trend_fam else 0.0
    scenario_probabilities = _scenario_probabilities(trend_dir_signed, risk_ps, reward_ps,
                                                     atr, data_conf, agreement)

    # ── Coverage-based data quality (Prompt 5B) ──────────────────────────────
    # Data quality is CATEGORY COVERAGE, strategy-aware — NOT the mean family
    # confidence. A throttled/absent TradingView no longer tanks the whole stock:
    # valid Yahoo/Finnhub price+candles+news stand on their own. Missing options
    # does not fail a momentum stock (it only matters for the options profile).
    import data_quality as _dq
    fam_by = {f["family"]: f for f in fams}
    _stale_cov = data_state in ("fallback-provider",)

    def _fam_cov(cat, present_fields, fam):
        fr = fam_by.get(fam)
        conf = fr["conf"] if fr else 0.0
        pres = present_fields if (fr and fr["conf"] > 0) else {}
        return _dq.category_coverage(cat, pres, stale=_stale_cov, confidence=conf)

    has_candles = bool(a.get("atr") or a.get("rsi") or a.get("trend_state"))
    _opt_micro = {"bid": (opt or {}).get("bid"), "ask": (opt or {}).get("ask"),
                  "open_interest": (opt or {}).get("open_interest")} if opt else {}
    coverages = {
        "price": _dq.category_coverage("price", {"price": price}, stale=_stale_cov, confidence=0.9),
        "candles": _dq.category_coverage("candles",
            ({"closes": [1], "highs": [1], "lows": [1]} if has_candles else {}),
            stale=_stale_cov, confidence=0.85),
        "news": _fam_cov("news", {"headlines": [1]}, "catalyst"),
        "analyst": _fam_cov("analyst", {"recommendation": 1}, "analyst-ratings"),
        "filings": _fam_cov("filings", {"cik": 1}, "filings/insider"),
        "macro": _fam_cov("macro", {"series": 1}, "macro-rates"),
        "sector": _dq.category_coverage("sector", {"sector": exchange or "US"}, confidence=0.6),
        "options": _dq.category_coverage("options", _opt_micro, confidence=(0.7 if opt else 0.0)),
    }
    dq_report = _dq.assess(coverages, profile=profile)
    data_quality_pct = round(dq_report["overall"] * 100, 1)

    # ── Options quality — SEPARATE from the stock setup (Prompt 5B) ───────────
    # A weak option NEVER rejects the stock. Only SCORE the option when the live
    # microstructure (bid/ask/spread/OI/DTE) is actually present.
    stock_setup_quality = quality
    option_quality = _grade_option_chain(opt, liq_opt, option_block, bar, stock_setup_quality)

    # ── Explicit decision gates — the ACTION is derived ONLY from these ──────
    min_dq = _min_data_quality()
    conviction_min = _conviction_min()
    allow_fallback = _env_true("DECISION_ALLOW_FALLBACK_TRADEABLE")
    allow_high_disagree = _env_true("DECISION_ALLOW_HIGH_DISAGREEMENT")
    gates: list = []

    def _gate(name, passed, value, requirement, reason, blocking):
        gates.append({"name": name, "passed": bool(passed), "value": value,
                      "requirement": requirement, "reason": ("" if passed else reason),
                      "blocking": bool(blocking)})

    levels_ok = (risk_ps > 0 and reward_ps > 0 and
                 (stop < price < t1 if sign > 0 else t1 < price < stop))
    pg_ok = (portfolio_gate is None) or bool(portfolio_gate.get("allow", True))
    trend_present = bool(trend_fam) and trend_fam["conf"] > 0
    fresh_ok = (not _fr.blocks_tradeable(fresh["state"])) or (fresh["state"] == "fallback" and allow_fallback)
    # Coverage-based data-quality gate (Prompt 5B): fails ONLY if a REQUIRED category
    # for this strategy is missing (blocking gap) OR total category coverage is below
    # the floor. Deliberately NOT based on mean family confidence — that was the
    # "false low score" bug where a throttled TradingView (or a couple of thin
    # families) dragged down valid Yahoo/Finnhub data. Thin conviction is handled
    # separately by the `conviction` gate.
    dq_sufficient = dq_report["sufficient"]
    dq_cov = data_quality_pct / 100.0
    dq_ok = dq_sufficient and dq_cov >= min_dq
    dq_value = {"coverage_pct": data_quality_pct, "mean_family_conf": data_conf,
                "profile": profile, "blocking_gaps": dq_report["blocking_gaps"]}
    dq_reason = ("missing required data: " + ", ".join(dq_report["blocking_gaps"])) if not dq_sufficient \
        else f"data coverage {data_quality_pct}% below minimum {round(min_dq*100)}%"
    dis_ok = disagree["severity"] != "high" or allow_high_disagree

    # HARD gates — failing any forces REJECT.
    _gate("symbol_resolution", bool(price) and data_state != "unavailable", a_source,
          "resolved a live price from a provider", "no price resolved from any provider", True)
    _gate("valid_levels", levels_ok, {"entry": round(price, 2), "stop": stop, "target": t1},
          "entry/stop/target valid & correctly ordered, non-zero risk",
          "invalid or mis-ordered entry/stop/target", True)
    _gate("quality_threshold", quality >= bar, quality, f">= {bar}",
          f"quality {quality} below required {bar}", True)
    _gate("positive_ev", ev_net > 0, ev_net, "> 0 after spread+slippage",
          f"EV/share {ev_net} <= 0 after costs", True)
    _gate("liquidity", exec_conf >= 0.4, exec_conf, ">= 0.40 execution confidence",
          f"execution/liquidity gate low ({exec_conf})", True)
    _gate("signal_alignment", aligned > 0, round(aligned, 3), "> 0 (net signals back the direction)",
          "net signals not aligned with the trade direction", True)
    _gate("risk_limit", pg_ok, (portfolio_gate or {}).get("allow"), "portfolio risk limits not breached",
          "portfolio gate: " + "; ".join((portfolio_gate or {}).get("reasons", []) or ["blocked"]), True)
    _gate("critical_family", trend_present, "trend/momentum" if trend_present else "missing",
          "core trend/momentum family present", "critical trend/momentum family has no data", True)

    # SOFT gates — failing any (with all HARD passing) downgrades to MONITOR.
    _gate("data_quality", dq_ok, dq_value, f"required categories covered for '{profile}' and mean conf >= {min_dq}",
          dq_reason, False)
    _gate("freshness", fresh_ok, fresh["state"], "fresh or ageing (not stale/fallback)",
          f"data is {fresh['label']}", False)
    _gate("signal_disagreement", dis_ok, disagree["severity"], "not high",
          f"high signal disagreement ({disagree['conflicting_family_count']} conflicting families)", False)
    _gate("conviction", quality >= conviction_min, quality, f">= {conviction_min}",
          f"quality {quality} below conviction threshold {conviction_min}", False)

    hard_fail = [g for g in gates if g["blocking"] and not g["passed"]]
    soft_fail = [g for g in gates if not g["blocking"] and not g["passed"]]
    decision = "REJECT" if hard_fail else ("MONITOR" if soft_fail else "TRADEABLE")
    failed_gates = [g["name"] for g in gates if not g["passed"]]

    # Confidence penalties (visible): freshness + disagreement lower overall confidence.
    conf_penalties = {"freshness": fresh["penalty"], "disagreement": disagree["confidence_penalty"]}
    overall_confidence = round(max(0.0, data_conf - fresh["penalty"] - disagree["confidence_penalty"]), 2)

    # What must change to upgrade (for REJECT/MONITOR).
    upgrade_conditions = [g["reason"] for g in (hard_fail + soft_fail) if g["reason"]]

    result = {
        "symbol": symbol, "instrument": "STOCK(+option overlay)" if opt else "STOCK",
        "direction": direction, "price": round(price, 2), "decision": decision,
        "entry_range": [round(price * 0.997, 2), round(price * 1.003, 2)],
        "target": t1, "stop": stop,
        "max_loss_usd": total_planned_loss, "max_loss_breakdown": max_loss_breakdown,
        "risk_reward": round(reward_ps / max(risk_ps, .01), 2),
        "suggested_shares": shares, "hold_horizon": "days-weeks (swing)",
        # Quality SCORE and THRESHOLD are separate values everywhere.
        "confidence_quality": quality, "quality_max": 100,
        "quality_threshold": bar, "quality_bar": bar,
        "conviction_threshold": conviction_min,
        "p_direction": p_direction, "p_trade_profitable": p_trade,
        "data_confidence": data_conf, "execution_confidence": exec_conf,
        # Coverage-based data quality (per-category + strategy-aware + missing fields).
        "data_quality": dq_report, "data_quality_pct": data_quality_pct,
        "overall_confidence": overall_confidence, "confidence_penalties": conf_penalties,
        "expected_value_per_share": ev_net, "ev_breakdown": ev_breakdown,
        "scenario_probabilities": scenario_probabilities,
        "disagreement": disagree,
        "option": option_block,
        # Stock vs option quality are SEPARATE — a weak option never rejects the stock.
        "stock_setup_quality": stock_setup_quality, "option_quality": option_quality,
        "speculative": spec,
        "supporting_signals": [f"{f['family']}: {f['detail']}" for f in active if f["dir"] * sign > 0.05],
        "conflicting_signals": [f"{f['family']}: {f['detail']}" for f in active if f["dir"] * sign < -0.05],
        "missing_data": [f["family"] for f in fams if f["conf"] == 0],
        "explain": [{"family": f["family"], "dir": f["dir"], "conf": f["conf"],
                     "weighted": round(f["dir"] * f["conf"], 3)} for f in active],
        "gates": {"volatility": vol, "liquidity": liq},
        # Auditable decision gates + the derived action.
        "decision_gates": gates,
        "failed_gates": failed_gates,
        "upgrade_conditions": upgrade_conditions,
        "portfolio_gate": portfolio_gate,
        "reject_reasons": [g["reason"] for g in hard_fail],
        "avoid_if": ["spread widens / volume dries up", "regime flips against direction",
                     "gap through stop", "catalyst turns out priced-in (IV crush)"],
        "note": "v1 free-data engine. Action is derived ONLY from decision_gates. "
                "p_* are heuristic priors until journal calibration has enough samples.",
        # Provenance / freshness / degradation.
        "data_state": data_state, "data_source": a_source, "freshness": fresh,
        "engine_ms": _engine_ms,
        "analysis_status": "complete", "pending_families": [],
    }
    if data_state == "fallback-provider":
        result["degraded"] = ("Degraded fallback provider (reduced indicator set); "
                              "confidence is lower.")
    elif data_state == "secondary-provider":
        result["degraded"] = ("Yahoo primary unavailable — technicals from the optional "
                              "TradingView secondary; confidence slightly reduced.")
    try:
        import calibration
        calibration.log_prediction(result)      # every evaluation feeds the calibration loop
    except Exception:
        pass
    return result


_DEEP_FAMILIES = ["catalyst", "options-flow", "social-sentiment", "analyst-ratings",
                  "macro-rates", "short-interest", "filings/insider"]


def evaluate_summary(symbol: str, exchange: str = "NASDAQ", direction: str = "LONG",
                     balance: float = 368.0) -> dict:
    """FAST partial decision for instant stock-page paint. Computes ONLY the two
    cheap families that ride on data we already have — trend/momentum (from the
    base TA call, shared with the scanner + full engine) and regime (cached) — plus
    ATR-based entry/stop/target. Returns immediately with `analysis_status="summary"`
    and the list of families still to compute, so the UI shows something useful in
    ~1.6s cold / <100ms warm while the full multi-family analysis streams in behind
    it. NO slow I/O families (news/options-flow/social/analyst/macro/short/filings)."""
    import time as _t
    t0 = _t.perf_counter()
    a, a_source, data_state = _load_analysis(symbol, exchange)
    if a is None:
        return {"symbol": symbol, "decision": "REJECT", "data_state": "unavailable",
                "analysis_status": "summary", "reason": "no analysis data"}
    price = (a.get("price_data") or {}).get("current_price")
    if not price:
        return {"symbol": symbol, "decision": "REJECT", "data_state": data_state,
                "analysis_status": "summary", "reason": "no price"}

    regime = _safe_regime()
    fams = [_fam_trend(a), _fam_regime(regime, direction)]
    active = [f for f in fams if f["conf"] > 0]
    sign = 1 if direction == "LONG" else -1
    wsum = sum(f["conf"] for f in active) or 1e-9
    raw_dir = sum(f["dir"] * f["conf"] for f in active) / wsum
    denom = sum(abs(f["dir"]) * f["conf"] for f in active) or 1e-9
    agreement = abs(sum(f["dir"] * f["conf"] for f in active)) / denom
    aligned = raw_dir * sign
    p_direction = round(min(.85, max(.15, .5 + .5 * aligned * agreement)), 3)

    atr = (a.get("atr") or {}).get("value") or (price * 0.02)
    stop = round(price - sign * 1.5 * atr, 2)
    t1 = round(price + sign * 2 * 1.5 * atr, 2)
    risk_ps, reward_ps = abs(price - stop), abs(t1 - price)

    vol = _gate_volatility(a)
    liq = _gate_liquidity(price, None)
    exec_conf = round(liq["exec"] * vol["quality"], 2)
    data_conf = round(fmean([f["conf"] for f in active]), 2) if active else 0.0
    # Provisional quality — from the fast families only, so it's honestly lower-
    # confidence; the deep pass may raise or lower it.
    quality = round(100 * (.5 * agreement + .5 * min(1, abs(aligned) * 2))
                    * (0.55 + 0.45 * data_conf) * exec_conf, 1)
    spec = liq.get("speculative")
    bar = 65 if spec else 45
    lean = "bullish" if aligned > 0.1 else ("bearish" if aligned < -0.1 else "neutral")
    # Provisional label uses the SAME vocabulary as the deep gates, but can never
    # reach TRADEABLE here: only 2 of 9 families and none of the soft gates (data
    # quality, freshness, disagreement) have been evaluated yet. If a hard-checkable
    # gate already fails it's REJECT; otherwise MONITOR until the deep pass runs.
    summary_hard_fail = (aligned <= 0) or (quality < bar) or (exec_conf < 0.4) \
        or not (stop < price < t1 if sign > 0 else t1 < price < stop)
    decision = "REJECT" if summary_hard_fail else "MONITOR"

    return {
        "symbol": symbol, "direction": direction, "price": round(price, 2),
        "decision": decision, "provisional": True, "analysis_status": "summary",
        "lean": lean, "confidence_quality": quality, "quality_max": 100,
        "quality_threshold": bar, "quality_bar": bar,
        "p_direction": p_direction,
        "entry_range": [round(price * 0.997, 2), round(price * 1.003, 2)],
        "target": t1, "stop": stop,
        "risk_reward": round(reward_ps / max(risk_ps, .01), 2),
        "data_confidence": data_conf, "execution_confidence": exec_conf,
        "gates": {"volatility": vol, "liquidity": liq},
        "speculative": spec,
        "fast_families": [{"family": f["family"], "dir": f["dir"], "conf": f["conf"],
                           "weighted": round(f["dir"] * f["conf"], 3), "detail": f["detail"]}
                          for f in active],
        "pending_families": list(_DEEP_FAMILIES),
        "data_state": data_state, "data_source": a_source,
        "summary_ms": round((_t.perf_counter() - t0) * 1000, 1),
        "note": "Fast summary (trend + regime). Full multi-family analysis loads next.",
    }


def sweep(symbols, direction: str = "LONG", balance: float = 368.0, max_workers: int = 6) -> dict:
    """Evaluate many symbols CONCURRENTLY — each evaluate() is network-bound and
    independent, so a thread pool cuts a 10-name sweep from ~1 min to ~10s."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    out = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(evaluate, s, "NASDAQ", direction, balance): s for s in symbols}
        for fu in as_completed(futs):
            s = futs[fu]
            try:
                out[s] = fu.result()
            except Exception as e:
                out[s] = {"symbol": s, "decision": "ERROR", "reason": str(e)[:60]}
    return out


if __name__ == "__main__":
    import json, time as _t
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    syms = sys.argv[1:] or ["AAPL", "BAC", "SOFI"]
    if len(syms) > 2:                                  # parallel compact sweep + timing
        t0 = _t.time(); res = sweep(syms)
        for s in syms:
            r = res.get(s, {})
            print(f"  {s:5} {r.get('decision','?'):14} q{r.get('confidence_quality')} "
                  f"EV/sh ${r.get('expected_value_per_share')} {r.get('reason','')}")
        print(f"  [{len(syms)} names in {round(_t.time()-t0,1)}s parallel]")
        sys.exit(0)
    for sym in syms:
        r = evaluate(sym)
        d = r.get("decision")
        print(f"\n=== {sym} · {d} · quality {r.get('confidence_quality')} ===")
        if "reason" in r:
            print(f"  {r['reason']}"); continue
        print(f"  P(dir) {r['p_direction']} | P(trade) {r['p_trade_profitable']} | "
              f"data {r['data_confidence']} | exec {r['execution_confidence']} | EV/sh ${r['expected_value_per_share']}")
        print(f"  entry {r['entry_range']} stop {r['stop']} target {r['target']} R:R {r['risk_reward']} "
              f"maxloss ${r['max_loss_usd']} shares {r['suggested_shares']}")
        if r.get("option"):
            print(f"  option: {r['option']['contract']} EV/contract ${r['option']['ev_per_contract']}")
        if r["reject_reasons"]:
            print(f"  reject: {r['reject_reasons']}")
        print(f"  for: {r['supporting_signals']}")
        print(f"  against: {r['conflicting_signals'] or 'none'} | missing: {r['missing_data']}")
