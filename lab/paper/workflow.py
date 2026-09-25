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

from . import shadow_log
from . import (broker, canonical_bridge, config as cfg, db, journal, options_shadow,
              risk as risk_mod, strategies)
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
    import math
    price = source_ts = provider = None
    try:
        import providers as P
        pc = P.price_consensus(symbol)
        price, provider = pc.get("value"), pc.get("provider")
        source_ts = pc.get("source_timestamp")
    except Exception:
        pass
    # A quote is only executable if the provider told us WHEN the price was
    # observed. Never stamp a missing/invalid source time with "now" and never
    # promote a historical close into a live quote — that launders stale data.
    try:
        source_ts = float(source_ts)
        ts_ok = math.isfinite(source_ts) and source_ts > 0
    except (TypeError, ValueError):
        ts_ok = False
    if price is None or not ts_ok:
        return None
    o = h = l = v = None
    try:
        import research as R
        hist = R.price_history(symbol, "1M")
        if hist.get("state") == "ok" and hist.get("points"):
            bar = hist["points"][-1]
            # Range-dependent fills (stops/targets) may only see a bar from the
            # SAME session as the quote; an older bar would trigger phantom exits.
            if _bar_is_current(bar.get("t"), source_ts):
                o, h, l, v = bar.get("o"), bar.get("h"), bar.get("l"), bar.get("v")
    except Exception:
        pass
    return Quote(symbol, last=price, open=o, high=h, low=l, volume=v,
                 source_ts=source_ts, provider=provider)


def _bar_is_current(bar_t: Any, quote_ts: float) -> bool:
    """True when the bar's calendar date is the quote's session date (ET or UTC)."""
    if not bar_t:
        return False
    try:
        from zoneinfo import ZoneInfo
        moment = dt.datetime.fromtimestamp(quote_ts, dt.timezone.utc)
        days = {moment.date().isoformat(),
                moment.astimezone(ZoneInfo("America/New_York")).date().isoformat()}
    except Exception:
        return False
    return str(bar_t)[:10] in days


def _not_executed(signal_id: str, reason: str) -> None:
    """Record WHY a journaled signal never reached the broker (audit trail)."""
    db.audit("signal", signal_id, "not_executed", {"reason": reason})


# ── Pre-market ────────────────────────────────────────────────────────────────

def premarket(session_date: Optional[str] = None, dry_run: bool = False, *,
              cycle_id: Optional[str] = None, allow_entries: bool = True,
              session_type: str = "regular", scan_ts: Optional[str] = None) -> Dict[str, Any]:
    """One discovery scan.

    Legacy mode (`cycle_id=None`): once per session; symbols already journalled today are skipped.
    Multi-scan mode (`cycle_id` given, used by the Ubuntu runtime every ~15 minutes):
      * every finalist is re-evaluated with a FRESH quote, sizing and account/risk checks each cycle;
      * the ledger journals a signal only on the FIRST observation or when the decision label CHANGES
        (e.g. MONITOR -> TRADEABLE); an unchanged observation is recorded in the shadow log only;
      * an unexecuted TRADEABLE is re-attempted (reusing its journal row) when capacity frees up;
      * a symbol with an open position/order, or already entered this session, is never entered again;
      * the daily-entry cap counts entries already persisted in the ledger (survives restarts);
      * `allow_entries=False` (prep / after the entry cutoff) records observations only.
    """
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
    multi = cycle_id is not None
    if multi:
        try:
            done_today = risk_mod.account_state(session_date)["entries_today"]
            max_orders = max(0, max_orders - done_today)     # persisted entries already consumed today
            if allow_entries and not broker.reconcile()["reconciled"]:
                allow_entries = False                        # fail closed on ledger inconsistency
                db.audit("system", session_date, "entries_disabled", {"reason": "ledger not reconciled"})
        except Exception as e:
            allow_entries = False
            db.audit("system", session_date, "entries_disabled", {"reason": f"account state unavailable: {e}"[:160]})

    # Idempotency guard (evidence integrity): premarket() evaluates each
    # day's finalists exactly once by design (this function does not rescan
    # intraday — see module docstring); a second call for the SAME
    # session_date can only be an accidental re-run (a retried scheduler
    # invocation, a crash-recovery re-run, a manual re-invocation), not a
    # second genuine decision. Without this guard a re-run would re-evaluate
    # every finalist, double-journalling each one under a fresh signal_id
    # (journal.record_signal()'s id is minted from the call timestamp, not
    # the decision content) — silently double-counting every TRADEABLE/
    # MONITOR/REJECT tally report.daily() produces, and, had the day's
    # capital/position caps not happened to block it, capable of opening a
    # SECOND real position for a symbol already entered today. Symbols
    # already journalled today are skipped and reported as such, exactly
    # like any other "not evaluated" case below.
    already_today = set() if multi else {s["symbol"] for s in db.query(
        "SELECT DISTINCT symbol FROM signals WHERE session_date=?", (session_date,))}

    # Shadow telemetry (append-only, separate DB, read-only over the ledger; never affects trading).
    shadow_rows: List[Dict[str, Any]] = []
    shadow_cap = None
    if not dry_run:
        try:
            shadow_cap = shadow_log.capacity_snapshot(session_date)
        except Exception:
            shadow_cap = None

    for f in scan["finalists"]:
        sym = f["symbol"]
        direction = f.get("direction", "LONG")
        sector = f.get("sector")
        if sym in already_today:
            evaluated.append({"symbol": sym, "action": None, "signal_id": None,
                              "instrument": None,
                              "note": f"already evaluated today ({session_date}) — skipping re-run"})
            continue
        try:
            # portfolio_check=False: the engine's own risk_limit gate checks the
            # UNRELATED legacy strategy-500 account. This pipeline's real, $500-
            # correct risk check (per-trade loss, position cap, sector exposure,
            # daily loss, drawdown, cash reserve, cooldown) runs downstream via
            # canonical B(stock)/B(option) (real account state) and, as
            # defense-in-depth, broker.submit_entry -> risk.check_entry.
            #
            # Canonical Option Architecture v1.1, Step 10: evaluate_option=False
            # — the legacy option overlay (_grade_option_chain(), fed by
            # ss._pick_option_idea() internally) is bypassed for this route.
            # Layer C (price/stop/target, the 8 hard + 4 soft gates, quality/
            # EV/disagreement/freshness) is IDENTICAL either way — none of
            # that computation reads the option candidate. The SAME
            # ss._pick_option_idea() candidate generator is still called,
            # once, by canonical_bridge.evaluate_canonical() below, so
            # options_shadow still sees a real chain exactly as before.
            result = de.evaluate(sym, "NASDAQ", direction,
                                 balance=risk_mod.account_state(session_date)["equity"],
                                 evaluate_option=False, profile="momentum",
                                 portfolio_check=False)
        except Exception as e:
            db.audit("system", sym, "evaluate_failed", {"error": str(e)[:160]})
            continue

        q = quote_for(sym)

        # ---- canonical A/B(stock)/B(option)/D/E/executable (Step 10) ------
        try:
            canon = canonical_bridge.evaluate_canonical(
                result, symbol=sym, direction=direction, sector=sector,
                session_date=session_date, quote=q)
        except Exception as e:
            db.audit("system", sym, "canonical_evaluate_failed", {"error": str(e)[:160]})
            continue

        if shadow_cap is not None:
            try:
                shadow_rows.append(shadow_log.candidate_row(f, result, canon, q))
            except Exception:
                pass

        prev_sid = None
        if multi:
            label = result.get("decision")
            prev = db.query_one("SELECT signal_id, action, executed FROM signals WHERE session_date=? AND symbol=? "
                                "ORDER BY created_at DESC LIMIT 1", (session_date, sym))
            held = (db.query_one("SELECT 1 x FROM positions WHERE symbol=? AND status='open'", (sym,))
                    or db.query_one("SELECT 1 x FROM orders WHERE symbol=? AND status IN ('pending','partial')", (sym,)))
            note = None
            if not allow_entries:
                note = f"entries disabled ({session_type}) - observation only"
            elif held:
                note = "existing position/open order - no duplicate entry"
            elif prev and prev["executed"]:
                note = "already entered this session - no duplicate entry"
            elif prev and prev["action"] == label and label != "TRADEABLE":
                note = "unchanged observation - not re-journaled"
            if note:
                evaluated.append({"symbol": sym, "action": label, "signal_id": prev["signal_id"] if prev else None,
                                  "instrument": canon["instrument_label"], "note": note})
                continue
            if prev and prev["action"] == label and label == "TRADEABLE":
                prev_sid = prev["signal_id"]              # re-attempt the same signal; never journal it twice

        # Options are recorded in SHADOW MODE only — never placed into the ledger.
        # The augmented result carries the SAME option/option_quality shape
        # options_shadow.record() has always read, now canonically sourced.
        shadow_result = canonical_bridge.augmented_result_for_shadow(result, canon)
        shadow_id = None
        try:
            # strategy is a workflow-loop concept (f["strategy"]), not something
            # canonical_bridge.py knows about — passed through here rather than
            # threaded into evaluate_canonical(), same as journal.record_signal
            # and the stock order call further below already receive it.
            shadow_id = options_shadow.record(shadow_result, symbol=sym, session_date=session_date,
                                              strategy=f["strategy"])
        except Exception:
            pass
        # Canonical metadata (quality_pass, eligibility, binding constraint,
        # instrument choice, canonical quantity, executables, shadow_only,
        # quote provenance) — additive, via the existing audit trail, no
        # options_shadow schema change (Phase 15).
        db.audit("options_shadow_canonical", shadow_id or sym, "canonical_evaluated",
                 canonical_bridge.canonical_audit_metadata(canon))

        stock_ready = (canon["instrument_choice"].choice.value == "STOCK"
                       and canon["stock_executable"].executable)
        canon_qty = canon["sizing"].quantity
        canon_planned_risk = canon["stock_planned_risk"]

        if dry_run or len(planned) >= max_orders:
            sid = prev_sid or journal.record_signal(
                result, strategy=f["strategy"], sector=sector,
                market_regime=regime, scanner_rank=f.get("scanner_rank"),
                quantity=canon_qty, planned_risk=canon_planned_risk,
                session_date=session_date)
            evaluated.append({"symbol": sym, "action": result.get("decision"),
                              "signal_id": sid, "instrument": canon["instrument_label"],
                              "note": "dry-run" if dry_run else "daily order cap reached"})
            _not_executed(sid, "dry-run" if dry_run else (
                f"daily entry cap reached ({cfg.risk().max_entries_per_day} per day, incl. persisted entries)"))
            continue

        if q is None:
            sid = prev_sid or journal.record_signal(
                result, strategy=f["strategy"], sector=sector, market_regime=regime,
                scanner_rank=f.get("scanner_rank"), quantity=canon_qty,
                planned_risk=canon_planned_risk, session_date=session_date)
            evaluated.append({"symbol": sym, "action": result.get("decision"),
                              "signal_id": sid, "instrument": canon["instrument_label"],
                              "note": "no executable quote"})
            _not_executed(sid, "no executable quote")
            continue

        if not stock_ready:
            # D did not choose STOCK (NO_TRADE, or OPTION — which, under
            # shadow_only=True, is NEVER executable — see Phase 10/14) or
            # canonical B/executable found the stock leg itself unsound.
            # No broker call: falling back to STOCK here would be a SECOND,
            # undocumented instrument-choice policy living outside D.
            sid = prev_sid or journal.record_signal(
                result, strategy=f["strategy"], sector=sector, market_regime=regime,
                scanner_rank=f.get("scanner_rank"), quantity=canon_qty,
                planned_risk=canon_planned_risk, session_date=session_date)
            evaluated.append({
                "symbol": sym, "action": result.get("decision"),
                "signal_id": sid, "instrument": canon["instrument_label"],
                "note": ("option preferred but shadow-only — no execution"
                        if canon["instrument_choice"].choice.value == "OPTION"
                        else "stock leg not canonically executable")})
            _not_executed(sid, evaluated[-1]["note"])
            continue

        out = broker.submit_entry(result, q, strategy=f["strategy"],
                                  sector=sector, market_regime=regime,
                                  scanner_rank=f.get("scanner_rank"),
                                  session_date=session_date,
                                  canonical_quantity=canon_qty,
                                  canonical_planned_risk=canon_planned_risk,
                                  stock_executable=canon["stock_executable"].executable,
                                  existing_signal_id=prev_sid)
        evaluated.append({"symbol": sym, "action": result.get("decision"),
                          "executed": out["executed"], "signal_id": out["signal_id"],
                          "instrument": canon["instrument_label"],
                          "reasons": out.get("reasons", [])})
        if out["executed"]:
            planned.append({"symbol": sym, "order_id": out["order"]["order_id"],
                            "strategy": f["strategy"]})

    if shadow_cap is not None:
        try:                                            # shadow telemetry must NEVER affect the Champion
            shadow_log.record_cycle(session_date, shadow_rows, evaluated, shadow_cap,
                                    cycle_id=cycle_id, scan_ts=scan_ts, session_type=session_type, scan=scan)
        except Exception:
            pass

    broker.snapshot_equity(session_date)
    db.audit("system", session_date, "premarket_done",
             {"finalists": len(scan["finalists"]), "executed": len(planned)})
    return {"session_date": session_date, "state": "ok", "started_at": started, "cycle_id": cycle_id,
            "entries_allowed": allow_entries,
            "provider_health": health, "market_regime": regime,
            "sectors_considered": scan.get("sectors_considered"),
            "sector_ranking": scan.get("sector_ranking"),
            "candidates": scan.get("candidate_count"),
            "finalists": scan.get("finalists"),
            "evaluated": evaluated, "orders_placed": planned,
            "config_version": db.config_version()}


# ── Market hours ──────────────────────────────────────────────────────────────

def _max_quote_age(quotes: Dict[str, Quote]) -> Optional[float]:
    """Oldest provider timestamp among this pass's quotes (seconds) — feeds the stale-data health check."""
    import time as _t
    ages = [_t.time() - q.source_ts for q in quotes.values() if q.source_ts]
    return round(max(ages), 1) if ages else None


def market_hours(session_date: Optional[str] = None, scope: str = "all") -> Dict[str, Any]:
    """Process fills, stops and targets. Only touches symbols we already care about —
    open positions, pending orders and tracked signals. No market-wide rescan.

    scope="positions" (fast tracker, ~60 s): ONLY open positions and pending orders — fresh quotes,
    fills, stop/target management. scope="all" (full tracker, ~5 min): also every tracked signal
    (MFE/MAE), option-shadow resolution and raw bar collection for the shadow log."""
    session_date = session_date or _today()
    symbols = set()
    for p in db.query("SELECT symbol FROM positions WHERE status='open'"):
        symbols.add(p["symbol"])
    for o in db.query("SELECT symbol FROM orders WHERE status IN ('pending','partial')"):
        symbols.add(o["symbol"])
    full = scope != "positions"
    tracked = db.query("SELECT signal_id, symbol FROM signals WHERE outcome='open'") if full else []
    for s in tracked:
        symbols.add(s["symbol"])
    shadow_open = db.query("SELECT DISTINCT symbol FROM options_shadow WHERE outcome='open'") if full else []
    for s in shadow_open:
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

    # 3b) raw daily + 5-minute bars for shadow candidates (append-only, separate DB, non-fatal)
    if full:
        shadow_log.update_bars(session_date)

    # 4) shadow-option evidence resolution — grades hypothetical observations
    # only; never places, modifies, or cancels an order. Same lifecycle stage
    # as (3) above, on purpose (Evidence & Graduation v1.2 phase 2).
    shadow_resolved = options_shadow.resolve_outcomes(quotes, session_date) if full else 0

    broker.snapshot_equity(session_date)
    return {"session_date": session_date, "symbols_watched": sorted(symbols),
            "orders_processed": len(filled), "exits": managed["count"],
            "exit_actions": managed["actions"], "tracked_updated": tracked_updates,
            "shadow_resolved": shadow_resolved, "scope": scope,
            "quote_age_s_max": _max_quote_age(quotes),
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
