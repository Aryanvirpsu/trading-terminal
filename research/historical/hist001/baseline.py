"""HIST-001: the generalized, multi-day Champion baseline runner. Extends `known_forward/replay.py`'s
single-day design (real, unmodified H3/H4/H5 decision+execution stack, one shared HistoricalClock, real
`workflow.premarket()`/`market_hours()` multi-scan cycles) to an arbitrary date range and universe, stamps
every evaluated observation with H7 event/observation/decision identity as it happens, and captures the
FULL decision-time record (price/stop/target/quantity/sector/quote) for every symbol evaluated -- not just
executed ones -- so a blocked TRADEABLE's hypothetical outcome is actually resolvable afterward (the H6
integration this file exists to make real, post-Smoke).

Post-Smoke structural fixes (per the HIST-001 directive after the Smoke-stage report):
  1. warmup_start/evaluation_start/evaluation_end, with every cycle/observation/event tagged by phase.
     Warm-up cycles run for real (so ledger/account state and daily-bar depth accumulate correctly) but are
     excluded from HIST-001's own reported metrics -- see `analysis.py`'s phase filtering.
  2. A PRICE_TREND_MACRO_V1 run refuses to start if its macro store does not cover the FULL
     [warmup_start, evaluation_end] range (`macro.assert_macro_coverage`) -- never a silent fallback to
     PRICE_TREND_ONLY_V1 behavior while still claiming the macro tag.
  3. Every evaluated symbol's full decision (not just the executed ones) is captured via a NON-invasive
     recording wrapper around `decision_engine.evaluate` and `canonical_bridge.evaluate_canonical` -- the
     real functions are called unchanged; only their return values are additionally recorded, keyed by
     (cycle_id, symbol), for `analysis.capacity_opportunity_cost()` to resolve afterward.
"""
from __future__ import annotations

import datetime as dt
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple
from unittest import mock

from ..capability import PRICE_TREND_MACRO_V1, PRICE_TREND_ONLY_V1
from ..clock import HistoricalClock
from ..event_identity import EventIdentityTracker
from ..execution import HistoricalExecutionContext, isolate_paper_ledger
from ..guards import guard_all
from ..macro import MacroHistory, assert_macro_coverage
from ..manifest import load_manifest
from ..provider import HistoricalMarketProvider
from .schedule import ScheduledCycle, build_multi_day_schedule


def run_baseline(*, run_id: str, intraday_dataset_id: str, daily_dataset_id: str, universe_sectors: Sequence[str],
                 warmup_start: str, evaluation_start: str, evaluation_end: str,
                 macro_history: Optional[MacroHistory] = None,
                 capability_fingerprint: str = PRICE_TREND_MACRO_V1,
                 seed_cash: float = 500.0, cycles: Optional[Sequence[ScheduledCycle]] = None) -> Dict[str, Any]:
    """Runs the real Champion decision+execution stack over every real trading-day cycle in
    [warmup_start, evaluation_end], starting from a FRESH $500 (or `seed_cash`) account. Cycles whose
    `session_date < evaluation_start` are tagged `phase="warmup"`; the rest `phase="evaluation"`. Warm-up
    cycles run for real (real scan/evaluate/broker calls, real ledger writes) so account state and daily-bar
    depth accumulate correctly into the evaluation window -- they are simply excluded from HIST-001's own
    reported metrics (`analysis.py` filters by phase, never by re-running anything).

    `capability_fingerprint=PRICE_TREND_MACRO_V1` (the default) REQUIRES `macro_history` to cover the full
    [warmup_start, evaluation_end] range (`macro.assert_macro_coverage` raises otherwise) -- this run
    structurally cannot claim the macro tag without real, complete macro data behind it. Pass
    `capability_fingerprint=PRICE_TREND_ONLY_V1` explicitly (and no `macro_history`) for a deliberate
    price-only run instead."""
    guard_all()
    if capability_fingerprint not in (PRICE_TREND_MACRO_V1, PRICE_TREND_ONLY_V1):
        raise ValueError(f"unknown capability_fingerprint {capability_fingerprint!r}")
    warmup_start_d = dt.date.fromisoformat(warmup_start)
    evaluation_start_d = dt.date.fromisoformat(evaluation_start)
    evaluation_end_d = dt.date.fromisoformat(evaluation_end)
    if not (warmup_start_d <= evaluation_start_d <= evaluation_end_d):
        raise ValueError("require warmup_start <= evaluation_start <= evaluation_end")

    if capability_fingerprint == PRICE_TREND_MACRO_V1:
        assert_macro_coverage(macro_history, warmup_start_d, evaluation_end_d)
    else:
        macro_history = None    # an explicit PRICE_TREND_ONLY_V1 run never uses macro, even if one was passed

    cycles = list(cycles) if cycles is not None else build_multi_day_schedule(warmup_start, evaluation_end)
    if not cycles:
        raise ValueError(f"no real trading-day cycles in [{warmup_start}, {evaluation_end}]")

    daily_manifest = load_manifest(daily_dataset_id)
    intraday_manifest = load_manifest(intraday_dataset_id)

    os.environ["PAPER_INITIAL_CASH"] = str(seed_cash)
    os.environ["PAPER_INITIAL_EQUITY"] = str(seed_cash)
    isolate_paper_ledger(run_id)

    shared_clock = HistoricalClock(cycles[0].et_time)
    decision_provider = HistoricalMarketProvider(shared_clock, [daily_dataset_id],
                                                 volume_trust=daily_manifest.volume_trust)
    exec_provider = HistoricalMarketProvider(shared_clock, [intraday_dataset_id],
                                             volume_trust=intraday_manifest.volume_trust)

    tracker = EventIdentityTracker()
    cycle_results: List[Dict[str, Any]] = []
    decision_capture: Dict[Tuple[str, str], Dict[str, Any]] = {}   # (cycle_id, symbol) -> full decision record
    current_cycle_id = [""]

    with HistoricalExecutionContext(decision_provider, execution_provider=exec_provider,
                                    neutral_sector=list(universe_sectors), macro_history=macro_history) as ctx:
        import decision_engine as de
        from paper import broker, canonical_bridge as cb, db, risk as risk_mod, workflow

        real_evaluate = de.evaluate
        real_evaluate_canonical = cb.evaluate_canonical

        def _recording_evaluate(symbol, exchange="NASDAQ", direction="LONG", **kw):
            result = real_evaluate(symbol, exchange, direction, **kw)
            key = (current_cycle_id[0], symbol)
            rec = decision_capture.setdefault(key, {})
            rec.update({"symbol": symbol, "direction": direction, "price": result.get("price"),
                       "stop": result.get("stop"), "target": result.get("target"),
                       "decision": result.get("decision"), "quality": result.get("confidence_quality"),
                       "data_quality": (result.get("data_quality") or {}).get("overall")})
            return result

        def _recording_evaluate_canonical(result, *, symbol, direction, sector, session_date, quote):
            canon = real_evaluate_canonical(result, symbol=symbol, direction=direction, sector=sector,
                                            session_date=session_date, quote=quote)
            key = (current_cycle_id[0], symbol)
            rec = decision_capture.setdefault(key, {})
            rec.update({
                "sector": sector, "session_date": session_date,
                "quote_ask": getattr(quote, "ask", None) if quote is not None else None,
                "quote_bid": getattr(quote, "bid", None) if quote is not None else None,
                "hypothetical_quantity": canon["sizing"].quantity,
                "risk_budget": canon.get("stock_planned_risk"),
                "stock_executable": canon["stock_executable"].executable,
                "instrument_label": canon.get("instrument_label"),
            })
            return canon

        with mock.patch.object(de, "evaluate", _recording_evaluate), \
            mock.patch.object(cb, "evaluate_canonical", _recording_evaluate_canonical):
            for cyc in cycles:
                current_cycle_id[0] = cyc.cycle_id
                shared_clock.set(cyc.et_time)
                phase = "warmup" if cyc.session_date < evaluation_start else "evaluation"

                pm = workflow.premarket(cyc.session_date, cycle_id=cyc.cycle_id, allow_entries=cyc.allow_entries,
                                        session_type=cyc.session_type, scan_ts=cyc.et_time.isoformat())
                mh = workflow.market_hours(cyc.session_date)

                account_snapshot = None
                if pm.get("state") == "ok":
                    account_snapshot = risk_mod.account_state(cyc.session_date)
                    for ev in pm.get("evaluated", []):
                        ident = tracker.observe(symbol=ev["symbol"], direction="LONG", cycle_id=cyc.cycle_id,
                                                scan_ts=cyc.et_time.isoformat())
                        ev["event_id"] = ident.event_id
                        ev["observation_id"] = ident.observation_id
                        ev["decision_id"] = ident.decision_id
                        ev["phase"] = phase
                        key = (cyc.cycle_id, ev["symbol"])
                        rec = decision_capture.get(key)
                        if rec is not None:
                            rec.update({"event_id": ident.event_id, "observation_id": ident.observation_id,
                                       "decision_id": ident.decision_id, "cycle_id": cyc.cycle_id,
                                       "et_time": cyc.et_time.isoformat(), "phase": phase,
                                       "capability_fingerprint": capability_fingerprint,
                                       "account_equity_before": (account_snapshot or {}).get("equity")})
                        if ev.get("executed"):
                            tracker.open_trade(ident.event_id, ev.get("signal_id") or "")

                cycle_results.append({
                    "cycle_id": cyc.cycle_id, "session_date": cyc.session_date, "et_time": cyc.et_time.isoformat(),
                    "session_type": cyc.session_type, "allow_entries": cyc.allow_entries, "phase": phase,
                    "premarket": pm, "market_hours": mh,
                })

        account = broker.account(cycles[-1].session_date)
        signals = db.query("SELECT * FROM signals ORDER BY created_at")
        orders = db.query("SELECT * FROM orders ORDER BY created_at")
        fills = db.query("SELECT * FROM fills ORDER BY filled_at")
        positions = db.query("SELECT * FROM positions ORDER BY opened_at")
        not_executed = db.query("SELECT * FROM audit WHERE event='not_executed' ORDER BY at")
        calls = list(ctx.calls)

    return {
        "run_id": run_id, "warmup_start": warmup_start, "evaluation_start": evaluation_start,
        "evaluation_end": evaluation_end,
        "warmup_trading_days": sorted({c.session_date for c in cycles if c.session_date < evaluation_start}),
        "evaluation_trading_days": sorted({c.session_date for c in cycles if c.session_date >= evaluation_start}),
        "cycles": cycle_results, "account": account, "signals": signals, "orders": orders, "fills": fills,
        "positions": positions, "not_executed": not_executed,
        "decision_capture": {f"{k[0]}|{k[1]}": v for k, v in decision_capture.items()},
        "event_count": tracker.event_count(), "calls_count": len(calls),
        "capability_fingerprint": capability_fingerprint,
        "manifests": {"daily": daily_manifest.to_dict(), "intraday": intraday_manifest.to_dict()},
    }
