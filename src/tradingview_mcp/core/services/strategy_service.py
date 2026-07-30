"""$500 Account Strategy Engine.

Codifies the "Trading Strategy v1.0 — $500 Account" doc into an operational
screening + sizing + journaling engine. Philosophy (verbatim from the doc):
risk management over prediction; no single trade materially damages the
account; only high-quality momentum / breakout / high-RVOL / catalyst setups
on liquid U.S. equities and their options; cash is a valid position.

This engine SELECTS and SIZES candidates — it never auto-commits capital. The
doc requires a documented thesis before every trade, so the flow is:
  find_trades() -> you review -> paper_trade / paper_option_trade to execute
  -> log_trade() to journal it -> close_trade() to review the outcome.

Risk model: "Balanced" (chosen by the account owner) —
  * max 5% of equity at risk per trade  (~$25 on $500)
  * max 25% of equity committed per trade (~$125 on $500)
  * minimum 2:1 reward:risk, enforced by construction (ATR stop, 2R/3R targets)
All sizing reads the account's LIVE balance, so limits scale as it grows/shrinks.
"""
from __future__ import annotations

import concurrent.futures as _cf
import json
import math
import os
import threading as _threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from tradingview_mcp.core import portfolio
from tradingview_mcp.core.services.screener_service import analyze_coin
from tradingview_mcp.core.services.options_service import get_options_chain
from tradingview_mcp.core.services.yahoo_finance_service import get_price
from tradingview_mcp.core.services.paper_trading_service import (
    get_paper_portfolio,
    get_paper_option_portfolio,
    paper_trade,
    paper_option_trade,
)

STRATEGY_ACCOUNT = "strategy-500"
INITIAL_BALANCE = 500.0

# Balanced risk model (see module docstring).
MAX_RISK_PCT = 0.05     # fraction of equity at risk per trade
MAX_COMMIT_PCT = 0.25   # fraction of equity committed per trade
MIN_RR = 2.0            # minimum reward:risk (enforced by construction)
ATR_STOP_MULT = 1.5     # stop = entry - 1.5 * ATR (long)
# Option-quality gates (see _pick_option_idea). A small-account capital cap will
# otherwise force cheap FAR-OTM lottery strikes that expire worthless even if the
# stock hits its target — so require the contract to be near-money AND liquid.
# If nothing near-money fits the cap, we return NO option (the name is stock-only)
# rather than surfacing junk. This is the discipline lesson encoded in code.
MAX_OPTION_OTM_PCT = 0.07   # reject strikes more than 7% out-of-the-money
MAX_OPTION_SPREAD_PCT = 0.40  # reject bid/ask wider than 40% of mid (illiquid)

# Curated universe of liquid, optionable U.S. names (tight spreads, real option
# volume) — the strategy explicitly targets these, not thin microcaps. Stored as
# fully-qualified TradingView tickers so the exchange prefix is never guessed.
UNIVERSE: List[str] = [
    # Mega/large-cap tech + semis (NASDAQ)
    "NASDAQ:AAPL", "NASDAQ:MSFT", "NASDAQ:NVDA", "NASDAQ:AMD", "NASDAQ:TSLA",
    "NASDAQ:META", "NASDAQ:AMZN", "NASDAQ:GOOGL", "NASDAQ:NFLX", "NASDAQ:AVGO",
    "NASDAQ:INTC", "NASDAQ:MU", "NASDAQ:QCOM", "NASDAQ:CSCO", "NASDAQ:ADBE",
    "NASDAQ:MRVL", "NASDAQ:PLTR", "NASDAQ:SMCI", "NASDAQ:ARM", "NASDAQ:PYPL",
    "NASDAQ:TXN", "NASDAQ:AMAT", "NASDAQ:LRCX", "NASDAQ:PANW", "NASDAQ:CRWD",
    "NASDAQ:MSTR", "NASDAQ:ABNB", "NASDAQ:COST", "NASDAQ:PEP", "NASDAQ:TSM",
    # Financials (NYSE)
    "NYSE:JPM", "NYSE:BAC", "NYSE:GS", "NYSE:MS", "NYSE:WFC",
    "NYSE:C", "NYSE:V", "NYSE:MA", "NYSE:AXP", "NYSE:SCHW",
    # Consumer / retail / travel (NYSE)
    "NYSE:DIS", "NYSE:WMT", "NYSE:NKE", "NYSE:HD", "NYSE:LOW",
    "NYSE:MCD", "NYSE:KO", "NYSE:UBER", "NYSE:COIN", "NYSE:F",
    # Energy + industrials (NYSE)
    "NYSE:XOM", "NYSE:CVX", "NYSE:OXY", "NYSE:SLB", "NYSE:BA",
    "NYSE:CAT", "NYSE:DE", "NYSE:GE",
    # Healthcare (NYSE)
    "NYSE:LLY", "NYSE:UNH", "NYSE:JNJ", "NYSE:PFE", "NYSE:ABBV",
    # Enterprise software (NYSE)
    "NYSE:ORCL", "NYSE:CRM", "NYSE:NOW",
    # High-volume ETFs (optionable)
    "NASDAQ:QQQ", "AMEX:SPY", "AMEX:IWM", "AMEX:DIA", "AMEX:XLE",
    "AMEX:XLF", "AMEX:XLK",
]

# ── Rate-limit mitigation ────────────────────────────────────────────────────
# TradingView throttles after many scans in a session. Two defenses:
#  1) an in-process TTL cache of per-symbol analysis so repeat/adjacent scans
#     reuse data instead of re-hitting scanner.tradingview.com (the big win —
#     re-running find_trades within the window costs ~0 new TradingView calls);
#  2) a disk cache of the last good candidate set, served as a stale fallback
#     when a fresh scan gets rate-limited into returning almost nothing.
_ANALYSIS_TTL = float(os.environ.get("STRATEGY_ANALYSIS_TTL", "600"))  # 10 min
_ANALYSIS_CACHE: Dict[tuple, tuple] = {}
_CANDIDATES_CACHE_FILE = os.path.join(
    os.path.expanduser("~/.tradingview_mcp_data"), "last_candidates.json"
)


def _analyze_cached(sym: str, exch: str, interval: str) -> Dict[str, Any]:
    """analyze_coin with a short in-process TTL cache. Only successful results
    are cached (errors are not, so a transient failure isn't remembered)."""
    key = (sym.upper(), exch.lower(), interval)
    now = time.time()
    ent = _ANALYSIS_CACHE.get(key)
    if ent and now - ent[0] < _ANALYSIS_TTL:
        return ent[1]
    res = analyze_coin(sym, exch.lower(), interval)
    if isinstance(res, dict) and "error" not in res:
        _ANALYSIS_CACHE[key] = (now, res)
    return res


def _save_candidates(payload: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(_CANDIDATES_CACHE_FILE), exist_ok=True)
        with open(_CANDIDATES_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"saved_at": time.time(), "payload": payload}, f, default=str)
    except Exception:
        pass


def _load_candidates_stale(max_age_s: float = 21600) -> Optional[Dict[str, Any]]:
    try:
        with open(_CANDIDATES_CACHE_FILE, "r", encoding="utf-8") as f:
            blob = json.load(f)
        if time.time() - blob.get("saved_at", 0) <= max_age_s:
            return blob.get("payload")
    except Exception:
        pass
    return None


try:
    from tradingview_mcp.core.services.screener_provider import (
        resilient_get_multiple_analysis as _get_multiple_analysis,
    )
    from tradingview_mcp.core.services.indicators import compute_metrics
    _TA_AVAILABLE = True
except ImportError:
    _TA_AVAILABLE = False


# ── Market tracker (regime / "don't make bad trades" guard) ──────────────────
# A whole-market read from Yahoo (indices + VIX + the 11 sector SPDRs) — NOT the
# rate-limited TradingView scanner. Answers one question: is the tape supportive
# of new long/momentum trades right now, or should we stand down? Cached 60s so
# the dashboard can poll it cheaply.
_REGIME_INDICES = ["^GSPC", "^IXIC", "^DJI"]
_REGIME_SECTORS = ["XLK", "XLF", "XLE", "XLV", "XLY", "XLI", "XLP", "XLU", "XLRE", "XLB", "XLC"]
_SECTOR_NAMES = {
    "XLK": "Tech", "XLF": "Financials", "XLE": "Energy", "XLV": "Healthcare",
    "XLY": "Cons.Disc", "XLI": "Industrials", "XLP": "Cons.Staples",
    "XLU": "Utilities", "XLRE": "RealEstate", "XLB": "Materials", "XLC": "Comm.Svcs",
}
_REGIME_CACHE: Dict[str, tuple] = {}
_REGIME_TTL = 60.0


# Serve a stale regime for up to 15 min while a fresh one is fetched in the
# background — so a scan/stock-page NEVER blocks on the cold 15-symbol fetch.
_REGIME_STALE_TTL = 900.0
_REGIME_REFRESHING = {"on": False}


def _regime_fetch_all() -> Dict[str, Any]:
    """Fetch the 3 indices + VIX + 11 sector ETFs CONCURRENTLY (was 15 sequential
    Yahoo calls ≈ 7.4s — see BASELINE.md). Bounded pool; one slow symbol can't
    serialize the rest."""
    syms = list(_REGIME_INDICES) + ["^VIX"] + list(_REGIME_SECTORS)
    out: Dict[str, Any] = {}
    with _cf.ThreadPoolExecutor(max_workers=min(16, len(syms)), thread_name_prefix="regime") as ex:
        futs = {ex.submit(get_price, s): s for s in syms}
        for fut in _cf.as_completed(futs, timeout=15):
            s = futs[fut]
            try:
                out[s] = fut.result()
            except Exception:
                out[s] = None
    return out


def _regime_compute() -> Dict[str, Any]:
    prices = _regime_fetch_all()
    idx = {s: prices.get(s) for s in _REGIME_INDICES}
    vix = prices.get("^VIX")
    sectors = {s: prices.get(s) for s in _REGIME_SECTORS}

    idx_chgs = [d.get("change_pct") for d in idx.values() if isinstance(d, dict) and d.get("change_pct") is not None]
    avg_idx = round(sum(idx_chgs) / len(idx_chgs), 2) if idx_chgs else 0.0
    sec_rows = []
    for s in _REGIME_SECTORS:
        d = sectors.get(s) or {}
        if d.get("change_pct") is not None:
            sec_rows.append({"sector": _SECTOR_NAMES.get(s, s), "etf": s, "change_pct": d["change_pct"]})
    sec_chgs = [r["change_pct"] for r in sec_rows]
    up = sum(1 for c in sec_chgs if c > 0)
    down = sum(1 for c in sec_chgs if c < 0)
    breadth = round(up / (up + down) * 100, 1) if (up + down) else 50.0
    vix_val = vix.get("price") if isinstance(vix, dict) else None
    vix_chg = vix.get("change_pct") if isinstance(vix, dict) else None

    # Composite 0-100 risk-appetite score.
    score = 50 + avg_idx * 8 + (breadth - 50) * 0.4
    if vix_val is not None:
        if vix_val > 25:
            score -= 22
        elif vix_val > 20:
            score -= 12
    if vix_chg is not None and vix_chg > 8:
        score -= 8
    score = int(max(0, min(100, round(score))))

    if score >= 65:
        regime, posture = "RISK-ON", "Favorable for new longs — momentum/breakout setups tend to work."
    elif score >= 45:
        regime, posture = "NEUTRAL / MIXED", "Be selective — only A+ setups, size down, no chasing."
    else:
        regime, posture = "RISK-OFF", "Defensive — avoid new longs, protect gains. Cash is a position."

    new_longs_ok = score >= 55 and (vix_val is None or vix_val < 22)
    warnings: List[str] = []
    if avg_idx < -0.75:
        warnings.append(f"Indices down {avg_idx}% — broad selling.")
    if vix_val is not None and vix_val > 20:
        warnings.append(f"VIX {vix_val} elevated (fear).")
    if vix_chg is not None and vix_chg > 8:
        warnings.append(f"VIX spiking +{vix_chg}% — risk-off intraday.")
    if breadth < 40:
        warnings.append(f"Weak breadth — only {up}/{up+down} sectors green.")

    sec_rows.sort(key=lambda r: r["change_pct"], reverse=True)
    result = {
        "regime": regime,
        "risk_appetite_score": score,
        "trade_posture": posture,
        "new_longs_ok": new_longs_ok,
        "avg_index_change_pct": avg_idx,
        "vix": {"level": vix_val, "change_pct": vix_chg},
        "sector_breadth_pct": breadth,
        "sectors_green": up,
        "sectors_red": down,
        "leaders": sec_rows[:3],
        "laggards": sec_rows[-3:][::-1],
        "warnings": warnings,
        "note": "Whole-market read (Yahoo). Use it to avoid chasing on a bad tape.",
    }
    return result


def market_regime() -> Dict[str, Any]:
    """Whole-market health read → a clear trade posture. Risk-ON/Neutral/Risk-OFF
    plus a `new_longs_ok` flag. Data from Yahoo (unthrottled).

    Cached and SHARED across every scanner request and stock page:
      * < TTL (60s)        → fresh, returned instantly.
      * < STALE_TTL (15m)  → stale returned instantly, refreshed in the background
                             (so no request ever blocks on the cold fetch).
      * cold miss          → computed once (now a ~1s concurrent fetch, was ~7.4s).
    """
    ent = _REGIME_CACHE.get("r")
    now = time.time()
    if ent and now - ent[0] < _REGIME_TTL:
        return {**ent[1], "cache_state": "fresh"}
    if ent and now - ent[0] < _REGIME_STALE_TTL:
        if not _REGIME_REFRESHING["on"]:
            _REGIME_REFRESHING["on"] = True

            def _bg():
                try:
                    r = _regime_compute()
                    _REGIME_CACHE["r"] = (time.time(), r)
                except Exception:
                    pass
                finally:
                    _REGIME_REFRESHING["on"] = False
            _threading.Thread(target=_bg, name="regime-refresh", daemon=True).start()
        return {**ent[1], "cache_state": "stale"}
    result = _regime_compute()
    _REGIME_CACHE["r"] = (time.time(), result)
    return {**result, "cache_state": "fresh"}


# ── Account ──────────────────────────────────────────────────────────────────

def setup_account(reset: bool = False) -> Dict[str, Any]:
    """Create (or with reset=True, wipe & restart) the $500 strategy account."""
    return portfolio.setup_account(STRATEGY_ACCOUNT, INITIAL_BALANCE, reset=reset)


def _live_balance() -> float:
    """Cash balance of the strategy account (creates it at $500 if missing)."""
    return portfolio.get_or_create_user(STRATEGY_ACCOUNT, INITIAL_BALANCE)


# ── Position sizing ──────────────────────────────────────────────────────────

def _size_stock(entry: float, stop: float, balance: float) -> Optional[Dict[str, Any]]:
    """Size a long stock position under the Balanced caps. Fractional shares
    allowed. Returns None if the setup can't be sized sensibly."""
    risk_per_share = entry - stop
    if risk_per_share <= 0 or entry <= 0:
        return None
    max_risk = balance * MAX_RISK_PCT
    max_commit = balance * MAX_COMMIT_PCT
    shares_by_risk = max_risk / risk_per_share
    shares_by_commit = max_commit / entry
    shares = round(min(shares_by_risk, shares_by_commit), 4)
    if shares <= 0:
        return None
    return {
        "shares": shares,
        "capital_committed": round(shares * entry, 2),
        "dollar_risk": round(shares * risk_per_share, 2),
        "binding_constraint": "risk" if shares_by_risk <= shares_by_commit else "capital",
    }


def _size_option(premium: float, balance: float) -> Optional[Dict[str, Any]]:
    """Size a long option position. For a small account this is realistically
    1 contract; only surfaced if the full premium (the true max loss) fits under
    the capital cap. Planned risk assumes a -50% premium stop."""
    if premium <= 0:
        return None
    max_commit = balance * MAX_COMMIT_PCT
    cost_per_contract = premium * portfolio.OPTION_CONTRACT_MULTIPLIER
    if cost_per_contract > max_commit:
        return None  # even one contract breaches the capital cap
    contracts = int(max_commit // cost_per_contract)
    contracts = max(1, min(contracts, 3))  # cap tiny-account concentration
    cost = round(contracts * cost_per_contract, 2)
    return {
        "contracts": contracts,
        "capital_committed": cost,
        "max_loss": cost,                       # long option: can't lose more than premium
        "planned_risk_50pct_stop": round(cost * 0.5, 2),
    }


# ── Trade card construction ──────────────────────────────────────────────────

def _build_stock_card(analysis: Dict[str, Any], balance: float, direction: str = "LONG") -> Optional[Dict[str, Any]]:
    """Turn a coin_analysis result into a sized, ATR-based trade card with
    a constructed >=2:1 R:R. Returns None if it doesn't qualify."""
    price = (analysis.get("price_data") or {}).get("current_price")
    atr = (analysis.get("atr") or {}).get("value")
    if not price or not atr or atr <= 0:
        return None

    entry = round(price, 2)
    if direction == "LONG":
        stop = round(entry - ATR_STOP_MULT * atr, 2)
        risk = entry - stop
        if risk <= 0: return None
        t1 = round(entry + MIN_RR * risk, 2)
        t2 = round(entry + (MIN_RR + 1) * risk, 2)
    else:
        stop = round(entry + ATR_STOP_MULT * atr, 2)
        risk = stop - entry
        if risk <= 0: return None
        t1 = round(entry - MIN_RR * risk, 2)
        t2 = round(entry - (MIN_RR + 1) * risk, 2)

    sizing = _size_stock(entry, stop, balance)
    if not sizing:
        return None

    setup = analysis.get("trade_setup") or {}
    setup_types = setup.get("setup_types") or []
    sentiment = analysis.get("market_sentiment") or {}
    rsi = analysis.get("rsi") or {}
    sym = analysis.get("symbol", "").split(":")[-1]

    return {
        "symbol": sym,
        "instrument": "STOCK",
        # Explicit, human-obvious label so it's never ambiguous what to trade.
        "label": f"{'📈' if direction == 'LONG' else '📉'} STOCK · {sym}",
        "how_to_trade": (
            f"paper_trade(symbol='{sym}', quantity={sizing['shares']}, side='{'BUY' if direction == 'LONG' else 'SELL'}', "
            f"exchange='NASDAQ', user_id='strategy-500')"
        ),
        "setup_type": (setup_types[0] if setup_types else ("breakout" if direction == "LONG" else "breakdown")),
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "targets": [t1, t2],
        "risk_reward": f"{MIN_RR:.0f}:1 (T1), {MIN_RR + 1:.0f}:1 (T2)",
        "stop_basis": f"{ATR_STOP_MULT}x ATR ({round(atr,2)})",
        "sizing": sizing,
        # One-line summary of the ways to take this name (updated if an option fits).
        "trade_as": f"STOCK — buy {sizing['shares']} shares (~${sizing['capital_committed']}, risk ${sizing['dollar_risk']})",
        "context": {
            "stock_score": analysis.get("stock_score"),
            "grade": analysis.get("grade"),
            "trend_state": analysis.get("trend_state"),
            "rsi": rsi.get("value"),
            "momentum": sentiment.get("momentum"),
            "signal": sentiment.get("buy_sell_signal"),
            "change_pct": (analysis.get("price_data") or {}).get("change_percent"),
        },
    }


def _premium_of(c: Dict[str, Any]) -> Optional[float]:
    """Usable price for a contract: last trade, else bid/ask midpoint."""
    p = c.get("last_price")
    if not p:
        bid, ask = c.get("bid"), c.get("ask")
        if bid and ask:
            p = round((bid + ask) / 2, 2)
    return p


def _pick_option_idea(symbol: str, entry: float, balance: float, direction: str = "LONG") -> Optional[Dict[str, Any]]:
    """Find the best AFFORDABLE option (CALL or PUT) for this name that fits the account.

    Rather than picking one fixed strike and giving up if it's too expensive,
    this walks strikes from near-the-money outward and picks the closest-to-money
    CALL that (a) fits the capital cap, (b) isn't a junk-premium lottery ticket
    (>= $0.10), and (c) has real liquidity (OI or volume >= 50). Scans several
    near expiries (7-60 DTE) until one yields a fitting contract. Returns None if
    nothing usable exists (then the caller keeps it stock-only)."""
    chain = get_options_chain(symbol)
    if "error" in chain:
        return None
    expiries = chain.get("available_expiries") or []
    if not expiries:
        return None

    today = datetime.now(timezone.utc).date()
    max_commit = balance * MAX_COMMIT_PCT

    exp_candidates: List[tuple] = []
    for e in expiries:
        try:
            d = datetime.strptime(e, "%Y-%m-%d").date()
        except ValueError:
            continue
        dte = (d - today).days
        if 7 <= dte <= 60:               # swing horizon; avoids 0-DTE gamma
            exp_candidates.append((dte, e))
    exp_candidates.sort()

    for dte, chosen_expiry in exp_candidates:
        exp_chain = get_options_chain(symbol, chosen_expiry)
        if "error" in exp_chain:
            continue
        target_options = exp_chain.get("calls") if direction == "LONG" else exp_chain.get("puts")
        target_options = target_options or []

        usable: List[tuple] = []
        for c in target_options:
            strike = c.get("strike")
            if strike is None:
                continue
            
            # Skip deep ITM (expensive, no leverage)
            if direction == "LONG" and strike < entry * 0.98:
                continue
            if direction == "SHORT" and strike > entry * 1.02:
                continue
            # Reject FAR-OTM lottery strikes. On expensive names the capital cap
            # otherwise picks a cheap-but-junk strike that needs a huge move just
            # to break even (PANW $390 on a $331 stock, UNH $510 on $433 — both
            # worthless even if the stock hits its target). Require near-money.
            otm = (strike - entry) / entry if direction == "LONG" else (entry - strike) / entry
            if otm > MAX_OPTION_OTM_PCT:
                continue
            premium = _premium_of(c)
            if not premium or premium < 0.10:
                continue                  # skip junk-premium lottery tickets
            if premium * portfolio.OPTION_CONTRACT_MULTIPLIER > max_commit:
                continue                  # doesn't fit the capital cap
            oi = c.get("open_interest") or 0
            vol = c.get("volume") or 0
            if oi < 50 and vol < 50:
                continue                  # require some liquidity
            # Reject illiquid wide spreads — slippage on entry+exit eats the edge
            # (this is what made AXP's 0.50/0.98 quote a bad fill).
            bid, ask = c.get("bid"), c.get("ask")
            if bid and ask and ask > 0:
                spread_pct = (ask - bid) / ((ask + bid) / 2)
                if spread_pct > MAX_OPTION_SPREAD_PCT:
                    continue
            usable.append((strike, premium, c))

        if not usable:
            continue

        # Sort to closest-to-money
        if direction == "LONG":
            usable.sort(key=lambda x: x[0])
        else:
            usable.sort(key=lambda x: x[0], reverse=True)
        strike, premium, best = usable[0]
        sizing = _size_option(premium, balance)
        if not sizing:
            continue

        # Grade the chosen contract with the SHARED grader so the scanner's inline
        # option carries the same bid/ask/mid/spread$/spread%/vol/OI/liquidity/grade/
        # DTE/delta/IV/breakeven/tradeable/rejection fields as /api/symbol/options
        # (fixes the BASELINE.md spread=None / liquidity=None gap).
        try:
            from tradingview_mcp.core.services.options_grading import grade_contract
            graded = grade_contract(best, entry, "CALL" if direction == "LONG" else "PUT", dte)
        except Exception:
            graded = {}

        if direction == "LONG":
            moneyness = "ITM" if strike < entry else ("ATM" if abs(strike - entry) / entry < 0.01 else "OTM")
        else:
            moneyness = "ITM" if strike > entry else ("ATM" if abs(strike - entry) / entry < 0.01 else "OTM")
        
        opt_type = "CALL" if direction == "LONG" else "PUT"
        return {
            "instrument": "OPTION",
            "label": f"🎯 OPTION · {symbol} ${strike} {opt_type} {chosen_expiry} ({moneyness}, {dte}DTE)",
            "option_type": opt_type,
            "strike": strike,
            "expiry": chosen_expiry,
            "premium": premium,
            "moneyness": moneyness,
            "pct_otm": round(max(0.0, ((strike - entry) / entry if direction == "LONG" else (entry - strike) / entry)) * 100, 1),
            "days_to_expiry": dte,
            # Breakeven: calls need spot ABOVE strike+premium; puts BELOW strike-premium.
            "breakeven": round(strike + premium, 2) if direction == "LONG" else round(strike - premium, 2),
            # Full contract-quality grade (shared with /api/symbol/options).
            "bid": graded.get("bid"), "ask": graded.get("ask"), "mid": graded.get("mid"),
            "spread_dollars": graded.get("spread_dollars"), "spread_pct": graded.get("spread_pct"),
            "volume": graded.get("volume", best.get("volume")),
            "open_interest": graded.get("open_interest", best.get("open_interest")),
            "implied_volatility": graded.get("implied_volatility", best.get("implied_volatility")),
            "delta": graded.get("delta"),
            "liquidity_score": graded.get("liquidity_score"), "grade": graded.get("grade"),
            "tradeable": graded.get("tradeable"), "rejection": graded.get("rejection"),
            "sizing": sizing,
            "how_to_trade": (
                f"paper_option_trade(symbol='{symbol}', option_type='{opt_type}', strike={strike}, "
                f"expiry='{chosen_expiry}', quantity={sizing['contracts']}, side='BUY', user_id='strategy-500')"
            ),
            "rationale": "Defined-risk leverage — max loss = premium paid.",
        }
    return None


# ── Screening ────────────────────────────────────────────────────────────────

def _prerank_universe(interval: str, direction: str = "LONG",
                      universe: Optional[List[str]] = None) -> List[str]:
    """One batched TA call over the universe (the CHEAP first pass); rank by
    momentum score and return the strongest (or weakest, if SHORT) tickers to
    deep-analyze. `universe` lets the caller widen the pool beyond the curated
    default (e.g. a preset slice of the security master)."""
    uni = list(universe) if universe else list(UNIVERSE)
    try:
        analysis = _get_multiple_analysis(screener="america", interval=interval, symbols=uni)
    except Exception:
        return uni  # fall back to the given pool on transient failure

    scored: List[Tuple[float, str]] = []
    for ticker, data in analysis.items():
        if data is None:
            continue
        try:
            ind = data.indicators
            metrics = compute_metrics(ind)
            change = metrics["change"] if metrics else 0.0
            rsi = ind.get("RSI") or 50.0
            close = ind.get("close")
            ema50 = ind.get("EMA50")
            trend_bonus = 5.0 if (close and ema50 and close > ema50) else 0.0
            # Healthy-momentum RSI band gets a bonus; overbought is penalized.
            if 55 <= rsi <= 72:
                rsi_bonus = 5.0
            elif rsi > 78:
                rsi_bonus = -5.0
            else:
                rsi_bonus = 0.0
            score = change + trend_bonus + rsi_bonus
            scored.append((score, ticker))
        except Exception:
            continue

    if direction == "LONG":
        scored.sort(reverse=True) # highest score first
    else:
        scored.sort(reverse=False) # lowest score first
    return [t for _, t in scored] or list(UNIVERSE)


def _qualifies(analysis: Dict[str, Any], direction: str = "LONG") -> bool:
    """Strategy quality gate for momentum/breakdown candidates."""
    if not isinstance(analysis, dict) or "error" in analysis:
        return False
    score = analysis.get("stock_score") or 0
    sentiment = analysis.get("market_sentiment") or {}
    trend = (analysis.get("trend_state") or "").lower()
    signal = (sentiment.get("buy_sell_signal") or "").upper()
    rsi = (analysis.get("rsi") or {}).get("value") or 0
    atr_pct = (analysis.get("atr") or {}).get("percent_of_price") or 0

    # Genuine momentum, OR a pullback in a confirmed uptrend that still carries an
    # active buy signal. An uptrend alone with a neutral/sell signal is NOT enough
    # — the doc wants high-conviction setups, not "it's been going up lately".
    if direction == "LONG":
        bullish = (
            sentiment.get("momentum") == "Bullish"
            or ("uptrend" in trend and signal in ("BUY", "STRONG BUY"))
        )
        return bool(
            bullish
            and score >= 55
            and 50 <= rsi <= 78          # momentum, not exhausted
            and 1.2 <= atr_pct <= 9.0    # tradeable volatility
        )
    else:
        bearish = (
            sentiment.get("momentum") == "Bearish"
            or ("downtrend" in trend and signal in ("SELL", "STRONG SELL"))
        )
        return bool(
            bearish
            and score <= 45              # low relative strength
            and 25 <= rsi <= 55          # weak, but not universally oversold yet
            and 1.2 <= atr_pct <= 9.0
        )


def _scan_workers() -> int:
    """Bounded scanner concurrency. Starts at 2 (matches the TA in-flight cap) and
    is configurable via STRATEGY_SCAN_WORKERS."""
    try:
        return max(1, int(os.environ.get("STRATEGY_SCAN_WORKERS", "2")))
    except Exception:
        return 2


def _scan_deep_cap() -> int:
    """Max names to DEEP-analyze (the expensive per-name pass). Prerank is cheap;
    only the top slice gets deep analysis. Configurable via STRATEGY_DEEP_CAP."""
    try:
        return max(5, int(os.environ.get("STRATEGY_DEEP_CAP", "20")))
    except Exception:
        return 20


def _looks_throttled(e: BaseException) -> bool:
    n = type(e).__name__
    m = str(e).lower()
    return ("throttl" in m or "throttl" in n.lower() or "breaker" in m
            or "expecting value" in m or "rate" in m or "timed out" in m)


class _StageTimer:
    """Tiny context-manager stopwatch that records per-stage milliseconds."""
    def __init__(self):
        self.stages: Dict[str, float] = {}
        self._name = None
        self._t0 = 0.0

    def __call__(self, name: str):
        self._name = name
        return self

    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *a):
        self.stages[self._name] = round((time.perf_counter() - self._t0) * 1000, 1)


def find_trades(
    max_candidates: int = 8,
    interval: str = "1D",
    include_options: bool = True,
    deep_scan: int = 22,
    options_only: bool = False,
    universe: Optional[List[str]] = None,
    preset: Optional[str] = None,
    scan_workers: Optional[int] = None,
) -> Dict[str, Any]:
    """Screen the liquid U.S. universe for qualifying momentum/breakdown
    setups (Long or Short, depending on market regime), size each to the $500
    Balanced model, and (optionally) attach a defined-risk option idea. Returns
    ranked trade cards — nothing is executed.

    Args:
        max_candidates: How many final trade cards to return.
        interval: Analysis timeframe (default '1D' — swing horizon).
        include_options: Attach a call-option idea to each stock card.
        deep_scan: How many of the top pre-ranked names to fully analyze.
        options_only: Return ONLY names that have an affordable, liquid call
            fitting the risk cap (the option is the headline). Skips names where
            the only way in is the stock — for when you want leverage/defined
            risk and don't want capital tied up in shares. Scans deeper since
            fewer names qualify.
    """
    if not _TA_AVAILABLE:
        return {"error": "tradingview_ta is missing; run `pip install tradingview-ta`."}

    balance = _live_balance()
    want_options = include_options or options_only
    scan_depth = max(deep_scan, 30) if options_only else deep_scan
    deep_cap = _scan_deep_cap()
    workers = scan_workers or _scan_workers()
    uni = list(universe) if universe else list(UNIVERSE)
    timer = _StageTimer()

    # ── STAGE 1 — market regime (once, shared, cached; no per-ticker recompute) ──
    with timer("regime"):
        try:
            regime = market_regime()
            trade_direction = "LONG" if regime.get("new_longs_ok") else "SHORT"
        except Exception:
            regime = None
            trade_direction = "LONG"

    # ── STAGE 2-4 — universe → cheap local pre-rank (ONE batched TA call) ────────
    with timer("prerank"):
        ranked = _prerank_universe(interval, direction=trade_direction, universe=uni)
    # Deep-analyse ONLY the top slice (cheap prerank did the wide pass).
    shortlist = ranked[:min(len(ranked), max(max_candidates, scan_depth), deep_cap)]

    # ── STAGE 5 — deep analysis of the shortlist, CONCURRENTLY (bounded) ─────────
    scanned = errored = throttled = 0
    qualified: List[Dict[str, Any]] = []

    def _analyze_one(ticker: str):
        exch, _, sym = ticker.partition(":")
        try:
            a = _analyze_cached(sym, exch, interval)
        except Exception as e:  # noqa: BLE001
            return (ticker, None, "throttled" if _looks_throttled(e) else "error")
        if isinstance(a, dict) and "error" in a:
            return (ticker, None, "error")
        return (ticker, a, None)

    with timer("deep_scan"):
        with _cf.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="scan") as ex:
            for ticker, a, err in ex.map(_analyze_one, shortlist):
                if err == "throttled":
                    throttled += 1
                    errored += 1
                    continue
                if a is None:
                    errored += 1
                    continue
                scanned += 1
                if not _qualifies(a, direction=trade_direction):
                    continue
                card = _build_stock_card(a, balance, direction=trade_direction)
                if card:
                    qualified.append(card)

    # ── STAGE 6 — options for the FINAL shortlist only, CONCURRENTLY ─────────────
    cards: List[Dict[str, Any]] = []
    with timer("options"):
        if want_options and qualified:
            def _opt_one(card):
                try:
                    return (card, _pick_option_idea(card["symbol"], card["entry"], balance,
                                                    direction=trade_direction))
                except Exception:
                    return (card, None)
            # Only price options for as many names as we could possibly return.
            pool = qualified[:max(max_candidates * 3, 10)]
            with _cf.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="opt") as ex:
                for card, opt in ex.map(_opt_one, pool):
                    if options_only and not opt:
                        continue  # options-only: skip names with no affordable contract
                    if opt:
                        card["option_idea"] = opt
                        card["trade_as"] += (
                            f"  ——OR——  OPTION: buy {opt['sizing']['contracts']}x "
                            f"{opt.get('symbol', card['symbol'])} ${opt['strike']} {opt['option_type']} "
                            f"{opt['expiry']} @ ${opt['premium']} "
                            f"(~${opt['sizing']['capital_committed']}, max loss ${opt['sizing']['max_loss']})"
                        )
                        card["available_as"] = ["STOCK", "OPTION"]
                    else:
                        card["available_as"] = ["STOCK"]
                        card["option_note"] = "No option contract fit the risk cap — stock only."
                    cards.append(card)
                    if len(cards) >= max_candidates:
                        break
        else:
            for card in qualified:
                card["available_as"] = ["STOCK"]
                cards.append(card)
                if len(cards) >= max_candidates:
                    break

    timer.stages["total"] = round(sum(v for k, v in timer.stages.items()), 1)
    try:
        from tradingview_mcp.core.services.screener_provider import breaker_status
        _breaker = breaker_status()
    except Exception:
        _breaker = None
    provider_status = ("throttled" if (throttled and scanned == 0)
                       else "degraded" if throttled else "ok")
    # Graceful degradation: name the missing/degraded pieces instead of a bare empty.
    degradation = {
        "provider_status": provider_status,
        "names_throttled": throttled,
        "names_errored": errored - throttled,
        "breaker": (_breaker or {}).get("state") if _breaker else None,
        "skipped_families": (["technicals (TradingView throttled → shortlist reduced)"]
                             if throttled else []),
        "confidence_penalty": round(min(0.4, throttled / max(1, len(shortlist)) * 0.4), 2) if throttled else 0.0,
    }

    # Rate-limit fallback: if almost nothing analyzed (TradingView throttled us —
    # high error rate and no results), serve the last good candidate set stale
    # instead of returning an empty/misleading scan.
    if scanned == 0 and errored >= 3:
        stale = _load_candidates_stale()
        if stale is not None:
            stale = dict(stale)
            stale["stale"] = True
            stale["degradation"] = degradation
            stale["timings"] = timer.stages
            stale["stale_note"] = (
                "TradingView rate-limited this scan — showing the last good candidate set. "
                "Prices/grades may be minutes-to-hours old. Re-run later when the limit clears."
            )
            return stale

    with_options = sum(1 for c in cards if "option_idea" in c)
    payload = {
        "account": STRATEGY_ACCOUNT,
        "mode": "OPTIONS_ONLY" if options_only else "STOCKS_AND_OPTIONS",
        "equity": round(balance, 2),
        "risk_model": "Balanced",
        # Whole-market guard so you don't chase into a bad tape.
        "market_context": ({
            "regime": regime["regime"],
            "score": regime["risk_appetite_score"],
            "posture": regime["trade_posture"],
            "new_longs_ok": regime["new_longs_ok"],
            "warnings": regime["warnings"],
        } if regime else None),
        "limits": {
            "max_risk_per_trade": round(balance * MAX_RISK_PCT, 2),
            "max_capital_per_trade": round(balance * MAX_COMMIT_PCT, 2),
            "min_reward_risk": MIN_RR,
        },
        "universe_size": len(uni),
        "preset": preset or "default",
        "names_shortlisted": len(shortlist),
        "names_deep_scanned": scanned,
        "candidates_found": len(cards),
        # Per-stage wall-clock (ms) — exposed for the dashboard + logs.
        "timings": timer.stages,
        "degradation": degradation,
        "scan_workers": workers,
        # Explicit breakdown so the stock/option split is obvious at a glance.
        "candidate_breakdown": {
            "tradeable_as_stock": len(cards),
            "also_tradeable_as_option": with_options,
            "stock_only": len(cards) - with_options,
        },
        "candidates": cards,
        "legend": "Each candidate is a STOCK setup (📈). If an affordable option fits, it also "
                  "carries an OPTION alternative (🎯) under `option_idea` — check each card's "
                  "`available_as` and `trade_as` fields to see exactly how you can take it.",
        "how_to_act": (
            "These are candidates, not orders. Each card's `how_to_trade` field is the exact call "
            "to execute it — paper_trade for the STOCK, paper_option_trade for the OPTION (both with "
            "user_id='strategy-500') — then strategy_log_trade to journal it. Cash is a valid "
            "position; skip anything that doesn't excite you."
        ),
        "disclaimer": "Educational simulation. Not financial advice. Do your own research.",
    }
    # Per-stage timings to the log (mirrors the API `timings` field).
    try:
        import sys as _sys
        _st = timer.stages
        print(f"[strategy] find_trades stages(ms): regime={_st.get('regime')} "
              f"prerank={_st.get('prerank')} deep_scan={_st.get('deep_scan')} "
              f"options={_st.get('options')} total={_st.get('total')} | "
              f"shortlist={len(shortlist)} scanned={scanned} throttled={throttled} "
              f"found={len(cards)} workers={workers}", file=_sys.stderr)
    except Exception:
        pass
    # Cache a non-empty result so it can serve as a stale fallback next time a
    # scan gets rate-limited.
    if cards:
        _save_candidates(payload)
    return payload


# ── Position management (exits) ──────────────────────────────────────────────

def _current_price_for_entry(entry: Dict[str, Any]) -> Optional[float]:
    """Live price for a journal entry's instrument — stock last price, or the
    option's current premium (last, or bid/ask mid) from the live chain."""
    if entry["instrument_type"] == "STOCK":
        q = get_price(entry["symbol"])
        return q.get("price") if isinstance(q, dict) and "error" not in q else None

    strike, expiry, otype = entry.get("option_strike"), entry.get("option_expiry"), entry.get("option_type")
    if not (strike and expiry and otype):
        return None
    chain = get_options_chain(entry["symbol"], expiry)
    if "error" in chain:
        return None
    contracts = chain.get("calls" if otype == "CALL" else "puts", [])
    match = next((c for c in contracts if c.get("strike") == strike), None)
    if not match:
        return None
    premium = match.get("last_price")
    if not premium and match.get("bid") and match.get("ask"):
        premium = round((match["bid"] + match["ask"]) / 2, 2)
    return premium


def _option_expired(entry: Dict[str, Any]) -> bool:
    """True if a journaled OPTION entry is past its expiration date — a dead
    contract that no longer trades and must be settled at intrinsic value.
    (The live chain returns no quote for an expired strike, so without this the
    position is trapped as UNKNOWN and stays 'open' in the books forever.)"""
    exp = entry.get("option_expiry")
    if not exp:
        return False
    try:
        exp_d = datetime.strptime(exp, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return False
    return datetime.now(timezone.utc).date() > exp_d


def _settle_expired_option(entry: Dict[str, Any], intrinsic: float, opt_pos: dict) -> Optional[Dict[str, Any]]:
    """Close an expired option's paper position at its intrinsic value and close its
    journal entry. Falls back to a journal-only close if the paper lot is already
    gone, so the trade can't linger open in the books either way."""
    note = f"Auto-settle: expired {entry.get('option_expiry')} at ${intrinsic} intrinsic."
    key = (entry["symbol"], entry.get("option_type"), entry.get("option_strike"), entry.get("option_expiry"))
    pos = opt_pos.get(key)
    if pos and pos["quantity"] > 0:
        res = paper_option_trade(entry["symbol"], entry["option_type"], entry["option_strike"],
                                 entry["option_expiry"], pos["quantity"], "SELL",
                                 premium=intrinsic, user_id=STRATEGY_ACCOUNT)
        if "error" in res:
            return None
        realized, settled_qty = res.get("realized_pnl", 0), pos["quantity"]
    else:
        # No live paper lot (already sold) — still close the journal so it can't
        # stay open. P&L = (intrinsic - premium paid) * 100 * contracts.
        settled_qty = entry.get("quantity") or 1
        realized = round((intrinsic - entry["entry"]) * 100 * settled_qty, 2)
    portfolio.close_journal_entry(STRATEGY_ACCOUNT, entry["id"], realized, note)
    return {"journal_id": entry["id"], "symbol": entry["symbol"], "instrument": "OPTION",
            "settled_intrinsic": intrinsic, "sold_qty": settled_qty, "realized_pnl": realized}


def _review_expired_option(entry: Dict[str, Any], opt_pos: dict, execute_stops: bool,
                           actions_taken: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Classify an expired option at its intrinsic value and, when execute_stops is
    on, settle it (paper SELL at intrinsic + journal close). A call settles to
    max(0, underlying-strike), a put to max(0, strike-underlying); OTM = worthless."""
    review: Dict[str, Any] = {
        "journal_id": entry["id"], "symbol": entry["symbol"], "instrument": "OPTION",
        "entry": entry["entry"], "stop": entry["stop"], "targets": entry.get("targets") or [],
    }
    q = get_price(entry["symbol"])
    u_px = q.get("price") if isinstance(q, dict) and "error" not in q else None
    strike, otype, exp = entry.get("option_strike"), entry.get("option_type"), entry.get("option_expiry")
    if u_px is None or strike is None:
        review["current"] = None
        review["action"] = "EXPIRED_UNPRICED"
        review["note"] = f"Expired {exp} — could not price the underlying to settle; check manually."
        return review

    intrinsic = round(max(0.0, (u_px - strike) if otype == "CALL" else (strike - u_px)), 2)
    review["current"] = intrinsic
    review["action"] = "EXPIRED_ITM" if intrinsic > 0 else "EXPIRED_WORTHLESS"
    review["note"] = (f"Expired {exp} — settled at ${intrinsic} intrinsic "
                      f"(underlying ${round(u_px, 2)} vs ${strike} {otype}).")
    if execute_stops:
        taken = _settle_expired_option(entry, intrinsic, opt_pos)
        if taken:
            review["executed"] = taken
            actions_taken.append(taken)
    return review


def manage_positions(execute_stops: bool = False) -> Dict[str, Any]:
    """Review every open journaled trade against its predefined stop and targets
    — the exit discipline the strategy doc requires. For each, fetch the live
    price and classify: HOLD / SCALE_OUT_T1 / TAKE_PROFIT_FINAL / EXIT_STOP,
    with the current unrealized R-multiple.

    If execute_stops=True, positions whose stop has been hit are automatically
    closed (paper sell + journal close) — the one action worth automating for
    capital preservation. Profit-taking stays discretionary (recommended, not
    auto-executed) so you keep control of winners.
    """
    journal = portfolio.get_journal(STRATEGY_ACCOUNT)
    open_entries = [e for e in journal["entries"] if e["status"] == "open"]

    stock_pos = {p["symbol"]: p for p in portfolio.get_portfolio(STRATEGY_ACCOUNT)["positions"]}
    opt_pos = {
        (p["underlying_symbol"], p["option_type"], p["strike"], p["expiry"]): p
        for p in portfolio.get_option_portfolio(STRATEGY_ACCOUNT)["positions"]
    }

    reviews: List[Dict[str, Any]] = []
    actions_taken: List[Dict[str, Any]] = []

    for e in open_entries:
        # Expired options are DEAD contracts, not holdable positions — settle at
        # intrinsic so they can't sit "open" forever (an expired strike has no live
        # quote, which otherwise traps it as UNKNOWN and skips it every run).
        if e["instrument_type"] == "OPTION" and _option_expired(e):
            reviews.append(_review_expired_option(e, opt_pos, execute_stops, actions_taken))
            continue

        current = _current_price_for_entry(e)
        entry, stop = e["entry"], e["stop"]
        targets = e["targets"] or []
        risk = entry - stop
        review: Dict[str, Any] = {
            "journal_id": e["id"], "symbol": e["symbol"], "instrument": e["instrument_type"],
            "entry": entry, "stop": stop, "targets": targets, "current": current,
        }
        if current is None:
            review["action"] = "UNKNOWN"
            review["note"] = "Could not fetch a live price — check manually."
            reviews.append(review)
            continue

        review["unrealized_R"] = round((current - entry) / risk, 2) if risk > 0 else None
        review["unrealized_pct"] = round((current - entry) / entry * 100, 2) if entry else None
        first_t = targets[0] if targets else None
        last_t = targets[-1] if targets else None

        if current <= stop:
            review["action"] = "EXIT_STOP"
            review["note"] = "Stop hit — cut the loss per the plan."
        elif last_t and current >= last_t:
            review["action"] = "TAKE_PROFIT_FINAL"
            review["note"] = "Final target hit — take profit."
        elif first_t and current >= first_t:
            review["action"] = "SCALE_OUT_T1"
            review["note"] = "First target hit — trim and trail the stop up to entry (breakeven)."
        else:
            review["action"] = "HOLD"
            review["note"] = "Between stop and first target — let it work."

        if execute_stops and review["action"] == "EXIT_STOP":
            taken = _auto_exit(e, current, stock_pos, opt_pos)
            if taken:
                review["executed"] = taken
                actions_taken.append(taken)

        reviews.append(review)

    by_action: Dict[str, int] = {}
    for r in reviews:
        by_action[r["action"]] = by_action.get(r["action"], 0) + 1

    return {
        "account": STRATEGY_ACCOUNT,
        "open_positions_reviewed": len(reviews),
        "summary": by_action,
        "stops_auto_executed": len(actions_taken),
        "reviews": reviews,
        "actions_taken": actions_taken,
        "note": ("execute_stops was OFF — EXIT_STOP items are recommendations only. "
                 "Re-run with execute_stops=True to auto-close stopped-out positions."
                 if not execute_stops else
                 "execute_stops was ON — stopped-out positions were auto-closed."),
    }


def _auto_exit(entry: Dict[str, Any], current: float, stock_pos: dict, opt_pos: dict) -> Optional[Dict[str, Any]]:
    """Close a stopped-out position (full size) and close its journal entry."""
    if entry["instrument_type"] == "STOCK":
        pos = stock_pos.get(entry["symbol"])
        if not pos or pos["quantity"] <= 0:
            return None
        res = paper_trade(entry["symbol"], pos["quantity"], "SELL",
                          exchange="NASDAQ", price=current, user_id=STRATEGY_ACCOUNT)
        if "error" in res:
            return None
        realized = res.get("realized_pnl", 0)
        portfolio.close_journal_entry(STRATEGY_ACCOUNT, entry["id"], realized, "Auto-exit: stop hit.")
        return {"journal_id": entry["id"], "symbol": entry["symbol"], "instrument": "STOCK",
                "sold_qty": pos["quantity"], "exit_price": current, "realized_pnl": realized}

    key = (entry["symbol"], entry.get("option_type"), entry.get("option_strike"), entry.get("option_expiry"))
    pos = opt_pos.get(key)
    if not pos or pos["quantity"] <= 0:
        return None
    res = paper_option_trade(entry["symbol"], entry["option_type"], entry["option_strike"],
                             entry["option_expiry"], pos["quantity"], "SELL",
                             premium=current, user_id=STRATEGY_ACCOUNT)
    if "error" in res:
        return None
    realized = res.get("realized_pnl", 0)
    portfolio.close_journal_entry(STRATEGY_ACCOUNT, entry["id"], realized, "Auto-exit: stop hit.")
    return {"journal_id": entry["id"], "symbol": entry["symbol"], "instrument": "OPTION",
            "sold_qty": pos["quantity"], "exit_premium": current, "realized_pnl": realized}


def daily_run(execute_stops: bool = False) -> Dict[str, Any]:
    """The repeatable daily routine — one call that makes this an ongoing system,
    not a one-off scan:
      1. record an equity snapshot (builds the performance curve over time),
      2. review/manage every open position against its stops & targets,
      3. surface fresh candidates for new capital.
    Run it once per trading day (or on a schedule — see the guide).
    """
    status = account_status()
    portfolio.record_equity_snapshot(STRATEGY_ACCOUNT, status["total_equity"])
    management = manage_positions(execute_stops=execute_stops)
    candidates = find_trades(max_candidates=5, include_options=True)
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "equity": status["total_equity"],
        "total_return_pct": status["total_return_pct"],
        "open_positions": len(status["stock_positions"]) + len(status["option_positions"]),
        "management": management,
        "new_candidates": candidates,
        "reminder": "Manage first, then add new risk only if a candidate genuinely excites you. "
                    "Cash is a valid position.",
    }


# ── Status / journal ─────────────────────────────────────────────────────────

def account_status() -> Dict[str, Any]:
    """Full snapshot of the $500 account: cash, stock + option positions with
    live mark-to-market P&L, combined equity, and journal stats. This is the
    same data the dashboard renders."""
    stock_pf = get_paper_portfolio(exchange="NASDAQ", user_id=STRATEGY_ACCOUNT)
    option_pf = get_paper_option_portfolio(user_id=STRATEGY_ACCOUNT)
    journal = portfolio.get_journal(STRATEGY_ACCOUNT)

    cash = stock_pf["balance"]
    stock_mv = sum(p.get("market_value") or 0 for p in stock_pf.get("positions", []))
    option_mv = sum(p.get("market_value") or 0 for p in option_pf.get("positions", []))
    total_equity = round(cash + stock_mv + option_mv, 2)
    total_unrealized = round(
        (stock_pf.get("total_unrealized_pnl") or 0) + (option_pf.get("total_unrealized_pnl") or 0), 2
    )

    return {
        "account": STRATEGY_ACCOUNT,
        "initial_balance": INITIAL_BALANCE,
        "cash": round(cash, 2),
        "stock_market_value": round(stock_mv, 2),
        "option_market_value": round(option_mv, 2),
        "total_equity": total_equity,
        "total_return_pct": round((total_equity - INITIAL_BALANCE) / INITIAL_BALANCE * 100, 2),
        "total_unrealized_pnl": total_unrealized,
        "stock_positions": stock_pf.get("positions", []),
        "option_positions": option_pf.get("positions", []),
        "journal": journal,
        "equity_curve": portfolio.get_equity_curve(STRATEGY_ACCOUNT),
        "risk_model": "Balanced",
    }


def log_trade(
    symbol: str,
    instrument_type: str,
    setup_type: str,
    thesis: str,
    entry: float,
    stop: float,
    targets: List[float],
    quantity: float,
    notes: str = "",
    option_type: Optional[str] = None,
    option_strike: Optional[float] = None,
    option_expiry: Optional[str] = None,
) -> Dict[str, Any]:
    """Journal a committed trade (thesis + entry/stop/targets + R:R + risk).

    For OPTION trades pass option_type/strike/expiry so the position can be
    re-priced by manage_positions from the live chain. For a stock+option combo,
    log two entries (one STOCK, one OPTION)."""
    risk_per_unit = entry - stop
    rr = None
    if risk_per_unit > 0 and targets:
        rr = round((targets[0] - entry) / risk_per_unit, 2)
    mult = portfolio.OPTION_CONTRACT_MULTIPLIER if instrument_type.upper() == "OPTION" else 1
    planned_risk = round(abs(risk_per_unit) * quantity * mult, 2)
    return portfolio.add_journal_entry(
        STRATEGY_ACCOUNT, symbol, instrument_type, setup_type, thesis,
        entry, stop, targets, rr, planned_risk, quantity, notes,
        option_type=option_type, option_strike=option_strike, option_expiry=option_expiry,
    )


def close_trade(journal_id: int, outcome_pnl: float, notes: str = "") -> Dict[str, Any]:
    """Close a journaled trade with its realized P&L and review notes."""
    return portfolio.close_journal_entry(STRATEGY_ACCOUNT, journal_id, outcome_pnl, notes)


# ── Missed-trade / "what-if" tracker (the shadow journal) ────────────────────

def log_missed_trade(
    symbol: str,
    instrument_type: str,
    reference_price: float,
    reason: str,
    option_type: Optional[str] = None,
    strike: Optional[float] = None,
    expiry: Optional[str] = None,
) -> Dict[str, Any]:
    """Record a setup we PASSED on (with the price/premium at decision time), so
    we can later see whether skipping it saved or cost us money."""
    return portfolio.log_missed_trade(
        STRATEGY_ACCOUNT, symbol, instrument_type, reference_price, reason,
        option_type=option_type, option_strike=strike, option_expiry=expiry,
    )


def _live_price_for_missed(m: Dict[str, Any]) -> Optional[float]:
    """Current price/premium for a missed-trade record (stock or option)."""
    if m["instrument_type"] == "OPTION" and m.get("option_strike") and m.get("option_expiry"):
        chain = get_options_chain(m["symbol"], m["option_expiry"])
        if "error" in chain:
            return None
        side = "calls" if (m.get("option_type") or "CALL") == "CALL" else "puts"
        c = next((x for x in chain.get(side, []) if x.get("strike") == m["option_strike"]), None)
        if not c:
            return None
        return c.get("last_price") or (round((c["bid"] + c["ask"]) / 2, 2) if c.get("bid") and c.get("ask") else None)
    q = get_price(m["symbol"])
    return q.get("price") if isinstance(q, dict) and "error" not in q else None


def missed_trades() -> Dict[str, Any]:
    """Every passed setup with its live 'what-if' return — a discipline scorecard.
    Tells you whether skipping trades has, on balance, dodged losses (good gate)
    or missed gains (gate too strict). This is how we learn from what we DON'T do."""
    rows = portfolio.get_missed_trades_raw(STRATEGY_ACCOUNT)
    enriched: List[Dict[str, Any]] = []
    dodged = missed_gain = 0
    net_pct = 0.0
    scored = 0

    for m in rows:
        ref = m["reference_price"]
        current = _live_price_for_missed(m)
        rec: Dict[str, Any] = {
            "id": m["id"], "symbol": m["symbol"], "instrument": m["instrument_type"],
            "reason": m["reason"], "decided_at": m["decided_at"],
            "reference_price": ref, "current_price": current,
        }
        if m["instrument_type"] == "OPTION":
            rec["contract"] = f"${m.get('option_strike')} {m.get('option_type')} {m.get('option_expiry')}"
        if current is not None and ref:
            whatif = round((current - ref) / ref * 100, 1)
            rec["whatif_return_pct"] = whatif
            rec["verdict"] = "✅ DODGED A LOSS" if whatif < 0 else ("➖ FLAT" if whatif == 0 else "❌ MISSED A GAIN")
            net_pct += whatif
            scored += 1
            if whatif < 0:
                dodged += 1
            elif whatif > 0:
                missed_gain += 1
        else:
            rec["verdict"] = "price unavailable"
        enriched.append(rec)

    good = dodged  # skips that avoided a loss = correct passes
    discipline_pct = round(good / scored * 100, 1) if scored else None
    return {
        "account": STRATEGY_ACCOUNT,
        "count": len(enriched),
        "scorecard": {
            "evaluated": scored,
            "dodged_losses": dodged,      # skipping was RIGHT
            "missed_gains": missed_gain,  # skipping was WRONG
            "avg_whatif_return_pct": round(net_pct / scored, 1) if scored else None,
            "discipline_hit_rate_pct": discipline_pct,
            "read": (
                "No data yet." if not scored else
                f"Skipping was right {dodged}/{scored} times. "
                + ("Gate is protecting you — passed trades mostly would've lost."
                   if discipline_pct and discipline_pct >= 50 else
                   "Heads up: passed trades mostly would've WON — the gate may be too strict; review it.")
            ),
        },
        "missed": enriched,
        "note": "Negative what-if = skipping saved you money (good). Positive = you left money on the table.",
    }
