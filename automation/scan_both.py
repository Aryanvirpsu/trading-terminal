"""Both-direction auto-scanner for the $500 strategy account.

The MCP `strategy_find_trades` tool screens ONE direction per run — it picks
LONG or SHORT from the market regime. This module runs BOTH sides in a single
pass so you get puts AND longs together, each:

  * quality-gated by the exact same discipline as find_trades (_qualifies),
  * sized to the Balanced risk model,
  * carried with a defined-risk OPTION idea (CALL for longs, PUT for shorts),
  * enriched with a news/context line (symbol headline if any + macro driver),
  * and a SIMULATED-returns block (stock P&L at stops/targets, plus an option
    payoff estimate at each stock target).

It executes NOTHING. Per the strategy's discipline you review, then commit via
paper_trade / paper_option_trade (user_id='strategy-500') and journal it.

Rate-limit aware: reuses strategy_service's batched prerank (one screener call
per side) and its 10-minute per-symbol analysis cache. Longs and shorts share
that cache, so overlap is free.

Run standalone:   python automation/scan_both.py
Import:           from automation.scan_both import scan_both, format_report
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tradingview_mcp.core.services import strategy_service as ss
from tradingview_mcp.core.services import news_service

DATA_DIR = os.path.expanduser("~/.tradingview_mcp_data")
SCAN_FILE = os.path.join(DATA_DIR, "last_scan_both.json")


# ── Simulated returns ─────────────────────────────────────────────────────────

def _crude_delta(entry: float, strike: float, is_call: bool) -> float:
    """Distance-aware |delta| proxy. ATM ~0.5, decaying as the strike moves OTM
    (~-4 per unit of relative distance), floored at 0.03 for far-OTM lottery
    strikes. ITM strikes get ~0.6. No greeks available, so this is deliberately
    crude — just enough to stop the sim exploding on cheap deep-OTM premiums."""
    otm_dist = (strike - entry) / entry if is_call else (entry - strike) / entry
    if otm_dist <= 0:            # ITM
        return 0.60
    return max(0.03, min(0.50, 0.50 - 4.0 * otm_dist))


def _stock_simulation(card: Dict[str, Any]) -> Dict[str, Any]:
    """P&L on the sized share position at stop / T1 / T2 — exact, not modeled
    (stops & targets are fixed 1R/2R/3R points by construction)."""
    entry = card["entry"]
    stop = card["stop"]
    t1, t2 = card["targets"]
    shares = card["sizing"]["shares"]
    direction = card["direction"]
    sign = 1 if direction == "LONG" else -1

    def leg(price: float) -> Dict[str, Any]:
        move_pct = round(sign * (price - entry) / entry * 100, 2)
        pnl = round(sign * (price - entry) * shares, 2)
        return {"price": price, "move_pct": move_pct, "pnl_usd": pnl}

    return {
        "shares": shares,
        "capital_committed": card["sizing"]["capital_committed"],
        "stop": {**leg(stop), "R": -1.0},
        "target1": {**leg(t1), "R": ss.MIN_RR},
        "target2": {**leg(t2), "R": ss.MIN_RR + 1},
    }


def _option_simulation(card: Dict[str, Any], opt: Dict[str, Any]) -> Dict[str, Any]:
    """Estimate the option's P&L at each stock target, two honest ways:

      early_est   — if the target is hit WELL before expiry: first-order delta
                    move, est_prem = max(intrinsic, prem + delta*stock_$move).
                    Additive (not the old multiplicative leverage) so it can't
                    explode on cheap deep-OTM premiums.
      expiry_val  — if HELD to expiry: intrinsic value only. 0 if it finishes OTM
                    → -100% of premium. This is the number that exposes a strike
                    too far OTM (worthless even if the stock hits its target).

    Plus a `lottery` flag when the strike is >7% OTM (per the account's
    discipline: cheap far-OTM contracts are lottery tickets, not defined-risk
    trades), and the strategy's mechanical 2x/3x-premium exit levels.
    """
    entry = card["entry"]
    t1, t2 = card["targets"]
    prem = opt["premium"]
    strike = opt["strike"]
    contracts = opt["sizing"]["contracts"]
    is_call = opt["option_type"] == "CALL"
    delta = _crude_delta(entry, strike, is_call)
    mult = 100  # standard US option contract multiplier

    otm_dist = round(((strike - entry) / entry if is_call else (entry - strike) / entry), 4)
    lottery = otm_dist > 0.07  # >7% OTM: cheap-but-junk lottery strike

    def intrinsic(stock_px: float) -> float:
        return max(0.0, (stock_px - strike) if is_call else (strike - stock_px))

    def leg(stock_px: float) -> Dict[str, Any]:
        dollar_move = (stock_px - entry) if is_call else (entry - stock_px)  # +toward target
        intr = round(intrinsic(stock_px), 2)
        early = max(intr, round(prem + delta * dollar_move, 2))
        early_pnl = round((early - prem) * mult * contracts, 2)
        early_pct = round((early - prem) / prem * 100, 1) if prem else 0.0
        exp_pnl = round((intr - prem) * mult * contracts, 2)
        exp_pct = round((intr - prem) / prem * 100, 1) if prem else 0.0
        return {
            "stock_price": stock_px,
            "early_est_premium": early,
            "early_pnl_usd": early_pnl,
            "early_pct": early_pct,
            "expiry_value": intr,
            "expiry_pnl_usd": exp_pnl,
            "expiry_pct": exp_pct,
            "worthless_at_target": intr == 0.0,
        }

    total_cost = round(prem * mult * contracts, 2)
    return {
        "contracts": contracts,
        "premium_paid": prem,
        "capital_committed": total_cost,
        "max_loss_usd": round(-total_cost, 2),  # option can go to zero
        "breakeven_stock": opt.get("breakeven"),
        "pct_otm": round(otm_dist * 100, 1),
        "lottery": lottery,
        "assumed_delta": delta,
        "at_stock_target1": leg(t1),
        "at_stock_target2": leg(t2),
        "mechanical_rule": {
            "exit_2x_premium_pnl_usd": round(prem * mult * contracts, 2),   # +100%
            "exit_3x_premium_pnl_usd": round(2 * prem * mult * contracts, 2),  # +200%
            "note": "Strategy rule: scale >=half at 2x premium (T1), rest by 3x (T2).",
        },
        "disclaimer": "Estimates. Swing uses a delta proxy; expiry uses intrinsic only. "
                      "Real fills move with IV, theta and spread.",
    }


# ── News / context enrichment ─────────────────────────────────────────────────

_SECTOR_HINT = {
    # very rough symbol -> driver, used only to caption the macro backdrop
    "JPM": "Financials", "BAC": "Financials", "GS": "Financials", "WFC": "Financials",
    "C": "Financials", "MS": "Financials", "XOM": "Energy", "CVX": "Energy",
    "AAPL": "Tech", "MSFT": "Tech", "NVDA": "Tech", "AMD": "Tech", "GOOGL": "Tech",
    "META": "Tech", "AMZN": "Cons.Disc", "TSLA": "Cons.Disc", "NFLX": "Cons.Disc",
}


def _news_for(symbol: str, macro_line: str) -> Dict[str, Any]:
    """A symbol-specific headline if the free RSS feeds carry one, plus the macro
    driver line so every card is 'backed with news/other info' even when there's
    no single-name headline (common for the free feeds)."""
    headline = None
    try:
        items = news_service.fetch_news(symbol=symbol, category="stocks", limit=3)
        items = [i for i in items if "error" not in i]
        if items:
            top = items[0]
            headline = {"title": top["title"], "source": top.get("source"), "url": top.get("url")}
    except Exception:
        pass
    return {
        "symbol_headline": headline,  # may be None — free feeds rarely tag single names
        "macro_backdrop": macro_line,
        "sector": _SECTOR_HINT.get(symbol.upper(), "—"),
    }


def _macro_line(regime: Optional[Dict[str, Any]], direction: str) -> str:
    if not regime:
        return "Market regime unavailable."
    lead = regime.get("leaders", [])
    lag = regime.get("laggards", [])
    if direction == "LONG":
        drv = ", ".join(f"{s['sector']} {s['change_pct']:+.1f}%" for s in lead[:2])
        return (f"Regime {regime['regime']} (score {regime['risk_appetite_score']}). "
                f"Relative-strength leaders today: {drv}. Longs are counter-to-regime "
                f"unless in a leading sector — demand real strength.") \
            if not regime.get("new_longs_ok") else \
            (f"Regime {regime['regime']} (score {regime['risk_appetite_score']}) supports longs. "
             f"Leaders: {drv}.")
    else:
        drv = ", ".join(f"{s['sector']} {s['change_pct']:+.1f}%" for s in lag[:2])
        return (f"Regime {regime['regime']} (score {regime['risk_appetite_score']}). "
                f"Weakest sectors driving shorts: {drv}. Puts are WITH the tape.")


# ── Core scan ─────────────────────────────────────────────────────────────────

def _scan_side(direction: str, interval: str, deep_scan: int, max_per_side: int,
               balance: float, regime: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    ranked = ss._prerank_universe(interval, direction=direction)[:deep_scan]
    macro = _macro_line(regime, direction)
    out: List[Dict[str, Any]] = []
    scanned = 0
    errored = 0
    for ticker in ranked:
        if len(out) >= max_per_side:
            break
        exch, _, sym = ticker.partition(":")
        try:
            analysis = ss._analyze_cached(sym, exch, interval)
        except Exception:
            errored += 1
            continue
        if not isinstance(analysis, dict) or "error" in analysis:
            errored += 1
            continue
        scanned += 1
        if not ss._qualifies(analysis, direction=direction):
            continue
        card = ss._build_stock_card(analysis, balance, direction=direction)
        if not card:
            continue

        card["news"] = _news_for(card["symbol"], macro)
        card["simulated_returns"] = {"stock": _stock_simulation(card)}

        opt = ss._pick_option_idea(card["symbol"], card["entry"], balance, direction=direction)
        if opt:
            card["option_idea"] = opt
            card["available_as"] = ["STOCK", "OPTION"]
            card["simulated_returns"]["option"] = _option_simulation(card, opt)
        else:
            card["available_as"] = ["STOCK"]
            card["option_note"] = "No liquid contract fit the risk cap — stock only."
        out.append(card)
    return {"cards": out, "scanned": scanned, "errored": errored, "ranked": len(ranked)}


def scan_both(max_per_side: int = 4, interval: str = "1D", deep_scan: int = 16) -> Dict[str, Any]:
    """Screen the universe for BOTH long (calls) and short (put) setups at once.

    Args:
        max_per_side: max candidate cards per direction (default 4).
        interval:     analysis timeframe (default '1D' swing).
        deep_scan:    how many top pre-ranked names to deep-analyze per side.
    """
    balance = ss._live_balance()
    try:
        regime = ss.market_regime()
    except Exception:
        regime = None

    long_res = _scan_side("LONG", interval, deep_scan, max_per_side, balance, regime)
    short_res = _scan_side("SHORT", interval, deep_scan, max_per_side, balance, regime)
    longs, shorts = long_res["cards"], short_res["cards"]

    # Journal-weighting: annotate each card with the account's realized expectancy
    # for that setup_type, so the scanner leans toward what's actually working.
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lab"))
        import journal_lab
        edges = journal_lab.setup_edge()
        for c in longs + shorts:
            c["journal_edge_usd"] = edges.get(c.get("setup_type"))
    except Exception:
        pass

    total_scanned = long_res["scanned"] + short_res["scanned"]
    total_errored = long_res["errored"] + short_res["errored"]
    # Throttle signature: almost nothing analyzed + high error rate (the empty-body
    # JSONDecodeError storm). Say so plainly instead of implying a genuinely quiet tape.
    throttled = total_scanned == 0 and total_errored >= 3

    result = {
        "account": "strategy-500",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "equity": round(balance, 2),
        "risk_model": "Balanced",
        "scan_health": {
            "names_analyzed": total_scanned,
            "names_errored": total_errored,
            "throttled": throttled,
            "note": ("TradingView rate-limited this scan (empty responses). Re-run at the "
                     "next market-open cycle; the scheduled task will retry."
                     if throttled else "OK"),
        },
        "market_context": ({
            "regime": regime["regime"],
            "score": regime["risk_appetite_score"],
            "posture": regime["trade_posture"],
            "new_longs_ok": regime["new_longs_ok"],
            "warnings": regime.get("warnings", []),
        } if regime else None),
        "longs": longs,     # calls / share buys
        "shorts": shorts,   # puts / share shorts
        "counts": {"longs": len(longs), "shorts": len(shorts)},
        "how_to_act": (
            "Candidates, not orders. Longs are counter-trend on a RISK-OFF tape — "
            "only take a long if it's a genuine relative-strength leader. Shorts/puts "
            "are with the tape. Commit via paper_trade / paper_option_trade "
            "(user_id='strategy-500'), then strategy_log_trade. Cash is a position."
        ),
        "disclaimer": "Educational simulation. Not financial advice.",
    }
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(SCAN_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, default=str)
    except Exception:
        pass
    return result


# ── Human-readable report ─────────────────────────────────────────────────────

def _fmt_card(c: Dict[str, Any]) -> str:
    sim = c["simulated_returns"]
    s = sim["stock"]
    lines = [
        f"  {c['label']}  score {c['context']['stock_score']} · {c['context']['grade']} · "
        f"RSI {c['context']['rsi']} · {c['context'].get('change_pct', 0)}% today",
        f"    Entry {c['entry']}  Stop {c['stop']}  Targets {c['targets'][0]}/{c['targets'][1]}  ({c['risk_reward']})",
        f"    Setup: {c['setup_type']} · {c['context']['trend_state']} · signal {c['context']['signal']}",
    ]
    nh = c["news"]["symbol_headline"]
    if nh:
        lines.append(f"    News: \"{nh['title']}\" — {nh['source']}")
    lines.append(f"    Context: {c['news']['macro_backdrop']}")
    lines.append(
        f"    SIM stock ({s['shares']} sh, ${s['capital_committed']}): "
        f"T1 {s['target1']['pnl_usd']:+}$ ({s['target1']['move_pct']:+}%) · "
        f"T2 {s['target2']['pnl_usd']:+}$ ({s['target2']['move_pct']:+}%) · "
        f"Stop {s['stop']['pnl_usd']:+}$"
    )
    if "option" in sim:
        o = sim["option"]
        opt = c["option_idea"]
        t1 = o["at_stock_target1"]
        flag = "  ⚠ LOTTERY (far-OTM)" if o.get("lottery") else ""
        lines.append(
            f"    {opt['label']}  prem ${opt['premium']} · {o['contracts']}x · cost ${o['capital_committed']} · "
            f"max loss ${o['max_loss_usd']} · {o['pct_otm']}% OTM{flag}"
        )
        worthless = "  → WORTHLESS even if T1 hits" if t1.get("worthless_at_target") else ""
        lines.append(
            f"    SIM option @stockT1: if-early ~{t1['early_pnl_usd']:+}$ ({t1['early_pct']:+}%) · "
            f"at-expiry {t1['expiry_pnl_usd']:+}$ ({t1['expiry_pct']:+}%){worthless}"
        )
    return "\n".join(lines)


def format_report(res: Dict[str, Any]) -> str:
    mc = res.get("market_context") or {}
    health = res.get("scan_health") or {}
    out = [
        f"=== BOTH-DIRECTION SCAN · {res['generated_at']} ===",
        f"Equity ${res['equity']} · Regime {mc.get('regime','?')} (score {mc.get('score','?')}) · {mc.get('posture','')}",
    ]
    if health.get("throttled"):
        out += ["", f"!! {health['note']}", ""]
    out += ["", f"[LONGS / CALLS] ({res['counts']['longs']}):"]
    out += [_fmt_card(c) for c in res["longs"]] or ["  (none passed the gate)"]
    out += ["", f"[SHORTS / PUTS] ({res['counts']['shorts']}):"]
    out += [_fmt_card(c) for c in res["shorts"]] or ["  (none passed the gate)"]
    out += ["", res["how_to_act"]]
    return "\n".join(out)


if __name__ == "__main__":
    # Console on Windows may be cp1252 — force UTF-8 so em-dashes etc. don't crash.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    result = scan_both()
    print(format_report(result))
    print(f"\n[written] {SCAN_FILE}")
