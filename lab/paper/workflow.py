"""Daily paper-trading workflow — pre-market, market hours, post-market.

Deliberately bounded: the pre-market pass narrows by sector, deep-analyses at most 5
finalists and plans at most 3 orders. Market hours processes fills and exits and
re-evaluates only on material change — it does NOT continuously rescan the market.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in ("..", os.path.join("..", "..", "dashboard"), os.path.join("..", "..", "src")):
    sys.path.insert(0, os.path.join(_HERE, _p))

from . import broker, config as cfg, db, journal, options_shadow, risk as risk_mod, strategies
from .fills import Quote


def _today() -> str:
    return dt.date.today().isoformat()


def provider_health() -> Dict[str, Any]:
    """Pre-flight: are the providers we depend on actually up?"""
    out: Dict[str, Any] = {"checked_at": db.utcnow()}
    try:
        import research as R
        out["providers"] = R.provider_health()
    except Exception as e:
        out["providers"] = {"error": str(e)[:120]}
    try:
        import providers as P
        out["tradingview_enabled"] = P.tv_enabled()
        out["tradingview_role"] = "optional confirmation only"
        pc = P.price_consensus("SPY")
        out["benchmark_quote"] = {"value": pc.get("value"), "provider": pc.get("provider"),
                                  "state": pc.get("state"),
                                  "agreeing": pc.get("agreeing_providers"),
                                  "conflicting": pc.get("conflicting_providers")}
        out["healthy"] = pc.get("value") is not None
    except Exception as e:
        out["healthy"] = False
        out["error"] = str(e)[:120]
    return out


def quote_for(symbol: str) -> Optional[Quote]:
    """Build an execution quote from the primary providers (Yahoo/Finnhub consensus)
    plus today's bar for range-dependent fills. Never invents a price."""
    import time
    price = source_ts = provider = None
    try:
        import providers as P
        pc = P.price_consensus(symbol)
        price, provider = pc.get("value"), pc.get("provider")
        source_ts = pc.get("source_timestamp")
    except Exception:
        pass
    o = h = l = v = None
    try:
        import research as R
        hist = R.price_history(symbol, "1M")
        if hist.get("state") == "ok" and hist.get("points"):
            bar = hist["points"][-1]
            o, h, l, v = bar.get("o"), bar.get("h"), bar.get("l"), bar.get("v")
            if price is None:
                price, provider, source_ts = bar.get("c"), "yahoo-history", None
    except Exception:
        pass
    if price is None:
        return None
    return Quote(symbol, last=price, open=o, high=h, low=l, volume=v,
                 source_ts=source_ts if source_ts else time.time(), provider=provider)


# ── Pre-market ────────────────────────────────────────────────────────────────

def premarket(session_date: Optional[str] = None, dry_run: bool = False) -> Dict[str, Any]:
    session_date = session_date or _today()
    started = db.utcnow()
    health = provider_health()
    db.audit("system", session_date, "premarket_start", {"health": health.get("healthy")})

    if not health.get("healthy"):
        db.audit("system", session_date, "premarket_aborted", {"reason": "providers unhealthy"})
        return {"session_date": session_date, "state": "aborted",
                "reason": "provider health check failed — no orders planned",
                "provider_health": health}

    scan = strategies.scan()
    if scan.get("state") != "ok":
        return {"session_date": session_date, "state": "no_scan",
                "reason": scan.get("reason", scan.get("state")),
                "provider_health": health}

    import decision_engine as de
    regime = None
    try:
        regime = (de._safe_regime() or {}).get("regime")
    except Exception:
        pass

    planned: List[Dict[str, Any]] = []
    evaluated: List[Dict[str, Any]] = []
    max_orders = cfg.risk().max_entries_per_day

    for f in scan["finalists"]:
        sym = f["symbol"]
        try:
            # portfolio_check=False: the engine's own risk_limit gate checks the
            # UNRELATED legacy strategy-500 account. This pipeline's real, $500-
            # correct risk check (per-trade loss, position cap, sector exposure,
            # daily loss, drawdown, cash reserve, cooldown) runs downstream in
            # broker.submit_entry -> risk.check_entry, against the actual account.
            #
            # evaluate_option=True: the funnel already limits the expensive full
            # evaluate() call to <=5 finalists/day specifically so this is affordable
            # (see PAPER_TRADING_PLAN.md's funnel). With it False, options_shadow
            # NEVER sees a real chain — every record was a "no option idea" stub
            # regardless of whether a good contract existed. Options stay
            # shadow-mode-only either way; this only changes whether we actually LOOK.
            result = de.evaluate(sym, "NASDAQ", f.get("direction", "LONG"),
                                 balance=risk_mod.account_state(session_date)["equity"],
                                 evaluate_option=True, profile="momentum",
                                 portfolio_check=False)
        except Exception as e:
            db.audit("system", sym, "evaluate_failed", {"error": str(e)[:160]})
            continue

        q = quote_for(sym)
        # Options are recorded in SHADOW MODE only — never placed into the ledger.
        try:
            options_shadow.record(result, symbol=sym, session_date=session_date)
        except Exception:
            pass

        if dry_run or len(planned) >= max_orders:
            sid = journal.record_signal(
                result, strategy=f["strategy"], sector=f.get("sector"),
                market_regime=regime, scanner_rank=f.get("scanner_rank"),
                session_date=session_date)
            evaluated.append({"symbol": sym, "action": result.get("decision"),
                              "signal_id": sid,
                              "note": "dry-run" if dry_run else "daily order cap reached"})
            continue

        if q is None:
            sid = journal.record_signal(result, strategy=f["strategy"],
                                        sector=f.get("sector"), market_regime=regime,
                                        scanner_rank=f.get("scanner_rank"),
                                        session_date=session_date)
            evaluated.append({"symbol": sym, "action": result.get("decision"),
                              "signal_id": sid, "note": "no executable quote"})
            continue

        out = broker.submit_entry(result, q, strategy=f["strategy"],
                                  sector=f.get("sector"), market_regime=regime,
                                  scanner_rank=f.get("scanner_rank"),
                                  session_date=session_date)
        evaluated.append({"symbol": sym, "action": result.get("decision"),
                          "executed": out["executed"], "signal_id": out["signal_id"],
                          "reasons": out.get("reasons", [])})
        if out["executed"]:
            planned.append({"symbol": sym, "order_id": out["order"]["order_id"],
                            "strategy": f["strategy"]})

    broker.snapshot_equity(session_date)
    db.audit("system", session_date, "premarket_done",
             {"finalists": len(scan["finalists"]), "executed": len(planned)})
    return {"session_date": session_date, "state": "ok", "started_at": started,
            "provider_health": health, "market_regime": regime,
            "sectors_considered": scan.get("sectors_considered"),
            "sector_ranking": scan.get("sector_ranking"),
            "candidates": scan.get("candidate_count"),
            "finalists": scan.get("finalists"),
            "evaluated": evaluated, "orders_placed": planned,
            "config_version": db.config_version()}


# ── Market hours ──────────────────────────────────────────────────────────────

def market_hours(session_date: Optional[str] = None) -> Dict[str, Any]:
    """Process fills, stops and targets. Only touches symbols we already care about —
    open positions, pending orders and tracked signals. No market-wide rescan."""
    session_date = session_date or _today()
    symbols = set()
    for p in db.query("SELECT symbol FROM positions WHERE status='open'"):
        symbols.add(p["symbol"])
    for o in db.query("SELECT symbol FROM orders WHERE status IN ('pending','partial')"):
        symbols.add(o["symbol"])
    tracked = db.query("SELECT signal_id, symbol FROM signals WHERE outcome='open'")
    for s in tracked:
        symbols.add(s["symbol"])

    quotes: Dict[str, Quote] = {}
    for sym in symbols:
        q = quote_for(sym)
        if q:
            quotes[sym] = q

    # 1) pending orders
    filled = []
    for o in db.query("SELECT order_id, symbol FROM orders WHERE status IN ('pending','partial')"):
        q = quotes.get(o["symbol"])
        if q:
            filled.append(broker.process_order(o["order_id"], q))

    # 2) stops / targets on open positions
    managed = broker.manage_open_positions(quotes, session_date)

    # 3) shadow tracking — MFE/MAE for EVERY tracked signal, including REJECT/MONITOR
    tracked_updates = 0
    for s in tracked:
        q = quotes.get(s["symbol"])
        if not q:
            continue
        hi = q.high if q.high is not None else q.last
        lo = q.low if q.low is not None else q.last
        if hi is None or lo is None:
            continue
        journal.update_excursions(s["signal_id"], hi, lo)
        tracked_updates += 1

    broker.snapshot_equity(session_date)
    return {"session_date": session_date, "symbols_watched": sorted(symbols),
            "orders_processed": len(filled), "exits": managed["count"],
            "exit_actions": managed["actions"], "tracked_updated": tracked_updates,
            "quotes_missing": sorted(symbols - set(quotes))}


# ── Post-market ───────────────────────────────────────────────────────────────

def postmarket(session_date: Optional[str] = None) -> Dict[str, Any]:
    session_date = session_date or _today()
    expired = broker.expire_day_orders(session_date)
    stale_tracking = journal.expire_stale_tracking()
    broker.snapshot_equity(session_date)
    rec = broker.reconcile()
    db.audit("system", session_date, "postmarket",
             {"expired_orders": expired, "reconciled": rec["reconciled"]})
    from . import report
    rep = report.daily(session_date)
    return {"session_date": session_date, "expired_orders": expired,
            "expired_tracking": stale_tracking, "reconciliation": rec, "report": rep}


def run_all(session_date: Optional[str] = None, dry_run: bool = False) -> Dict[str, Any]:
    """One-shot: the whole day. Used by the scheduler and by integration tests."""
    session_date = session_date or _today()
    return {"premarket": premarket(session_date, dry_run=dry_run),
            "market_hours": market_hours(session_date),
            "postmarket": postmarket(session_date)}
