"""HIST-001: the generalized, multi-day Champion baseline runner. Extends `known_forward/replay.py`'s
single-day design (real, unmodified H3/H4/H5 decision+execution stack, one shared HistoricalClock, real
`workflow.premarket()`/`market_hours()` multi-scan cycles) to an arbitrary date range and universe, and
stamps every evaluated observation with H7 event/observation/decision identity as it happens (not
reconstructed after the fact from the ledger alone).
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence

from ..capability import PRICE_TREND_MACRO_V1, PRICE_TREND_ONLY_V1
from ..clock import HistoricalClock
from ..event_identity import EventIdentityTracker
from ..execution import HistoricalExecutionContext, isolate_paper_ledger
from ..guards import guard_all
from ..macro import MacroHistory
from ..manifest import load_manifest
from ..provider import HistoricalMarketProvider
from .schedule import ScheduledCycle, build_multi_day_schedule


def run_baseline(*, run_id: str, intraday_dataset_id: str, daily_dataset_id: str, universe_sectors: Sequence[str],
                 start: str, end: str, macro_history: Optional[MacroHistory] = None,
                 seed_cash: float = 500.0, cycles: Optional[Sequence[ScheduledCycle]] = None) -> Dict[str, Any]:
    """Runs the real Champion decision+execution stack over every real trading-day cycle in [start, end],
    starting from a FRESH $500 (or `seed_cash`) account -- this is a NEW baseline, not a reproduction of any
    specific forward session's own boundary, so it does not seed the known-forward $504.66 special value.
    Returns cycle-by-cycle results (each evaluated observation stamped with its H7 event/observation/
    decision ids), the final ledger snapshot (signals/orders/positions), and the run's own capability
    fingerprint."""
    guard_all()
    cycles = list(cycles) if cycles is not None else build_multi_day_schedule(start, end)
    if not cycles:
        raise ValueError(f"no real trading-day cycles in [{start}, {end}]")

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

    with HistoricalExecutionContext(decision_provider, execution_provider=exec_provider,
                                    neutral_sector=list(universe_sectors), macro_history=macro_history) as ctx:
        from paper import broker, db, workflow

        for cyc in cycles:
            shared_clock.set(cyc.et_time)
            pm = workflow.premarket(cyc.session_date, cycle_id=cyc.cycle_id, allow_entries=cyc.allow_entries,
                                    session_type=cyc.session_type, scan_ts=cyc.et_time.isoformat())
            mh = workflow.market_hours(cyc.session_date)

            if pm.get("state") == "ok":
                for ev in pm.get("evaluated", []):
                    ident = tracker.observe(symbol=ev["symbol"], direction="LONG", cycle_id=cyc.cycle_id,
                                            scan_ts=cyc.et_time.isoformat())
                    ev["event_id"] = ident.event_id
                    ev["observation_id"] = ident.observation_id
                    ev["decision_id"] = ident.decision_id
                    if ev.get("executed"):
                        tracker.open_trade(ident.event_id, ev.get("signal_id") or "")

            cycle_results.append({
                "cycle_id": cyc.cycle_id, "session_date": cyc.session_date, "et_time": cyc.et_time.isoformat(),
                "session_type": cyc.session_type, "allow_entries": cyc.allow_entries,
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
        "run_id": run_id, "start": start, "end": end, "trading_days": sorted({c.session_date for c in cycles}),
        "cycles": cycle_results, "account": account, "signals": signals, "orders": orders, "fills": fills,
        "positions": positions, "not_executed": not_executed,
        "event_count": tracker.event_count(), "calls_count": len(calls),
        "capability_fingerprint": PRICE_TREND_MACRO_V1 if macro_history is not None else PRICE_TREND_ONLY_V1,
        "manifests": {"daily": daily_manifest.to_dict(), "intraday": intraday_manifest.to_dict()},
    }
