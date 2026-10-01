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
import sys
import time
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
from .no_drawdown_challenger import NoDrawdownChallenger
from .schedule import ScheduledCycle, build_multi_day_schedule
from .split_aware_diagnostic import SplitAwarePositionDiagnostic, confirmed_splits_by_symbol_and_date


def run_baseline(*, run_id: str, intraday_dataset_id: str, daily_dataset_id: str, universe_sectors: Sequence[str],
                 warmup_start: str, evaluation_start: str, evaluation_end: str,
                 macro_history: Optional[MacroHistory] = None,
                 capability_fingerprint: str = PRICE_TREND_MACRO_V1,
                 seed_cash: float = 500.0, cycles: Optional[Sequence[ScheduledCycle]] = None,
                 progress_every: Optional[int] = None,
                 split_aware_diagnostic: bool = True,
                 disable_drawdown_gate: bool = False) -> Dict[str, Any]:
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
    price-only run instead.

    `progress_every`: HIST-001 Full stage (27 months, ~16k cycles, multi-hour) has no way to observe progress
    otherwise short of waiting for completion. When set, prints one line to stderr every `progress_every`
    cycles (cycle index, session_date, elapsed seconds, running order/event counts) -- purely observational,
    reads no state this function doesn't already have, changes no decision, and defaults to None (off,
    Medium's exact unchanged behavior) so this is additive, not a behavior change to the accepted
    implementation. This is NOT checkpoint/resume: a killed run still restarts from cycle 0 on an isolated,
    wiped ledger, by the same design `isolate_paper_ledger()` already documents. Genuine interrupt/resume
    (persisting decision_capture/event-identity state and proving a resumed run reproduces an uninterrupted
    one exactly) was scoped OUT of this run as a deliberate, disclosed decision: the same single-process
    design has now completed reliably three times at Medium scale (up to ~68 minutes each), Full is expected
    to run single-digit hours as one uninterrupted background process, and building genuine resumability
    correctly is a substantial standalone effort whose own correctness risk works against the reason this
    audit trail exists in the first place. If a future run needs true multi-day resumability, it should be
    built then, deliberately, not rushed in under this run's own time pressure.

    `split_aware_diagnostic=True` (the DEFAULT as of 2026-09-30 -- promoted from an opt-in diagnostic once
    the user reviewed the Full-stage finding it was built to investigate): layers
    `split_aware_diagnostic.SplitAwarePositionDiagnostic` on top of the real, unmodified execution stack, so
    an OPEN position's stop/target/quantity are adjusted (via the real, already-existing
    `lab.paper.fills.apply_split()`) at the exact session date a CONFIRMED split's own bar appears, before
    `manage_open_positions()` runs its real, unmodified stop/target comparison.

    This is a REPLAY-FIDELITY correctness fix, not a Champion behavior change: `lab.paper.broker.py` is
    never touched by it (the real `manage_open_positions()` still runs completely unmodified, still makes
    the same real stop/target comparison it always has). What this corrects is that a NAIVE historical
    replay -- comparing a stored stop against a raw price series with a real, confirmed ~10x split
    discontinuity in it -- misrepresents what a REAL account/broker would actually show a held position at
    that moment (a real broker adjusts a resting stop order's price and a position's share count
    automatically when the underlying stock splits; nothing about that is a strategy decision). Leaving the
    replay naive here was the actual fidelity defect, discovered via AVGO's real 2024-07-15 10-for-1 split:
    comparing a pre-split stop against a post-split raw price triggered a ~17R artificial "loss" that then
    tripped the account's own real `max_drawdown` circuit breaker for the rest of HIST-001 Full's evaluation
    window -- see `split_aware_diagnostic.py`'s own module docstring and
    `HIST_001_CHAMPION_BASELINE.md`'s Full-stage correction for the full finding. Pass
    `split_aware_diagnostic=False` explicitly to reproduce the OLD (naive, pre-correction) behavior for
    comparison purposes only -- never the default going forward.

    `disable_drawdown_gate=False` (the default -- EVERY canonical HIST-001 Smoke/Medium/Full result uses
    this default, unaffected): when `True`, additionally patches out BOTH of the real `max_drawdown` gates
    (`paper.config.risk()`'s runtime gate and `canonical_bridge.STRATEGY_500_POLICY`'s canonical gate -- see
    `no_drawdown_challenger.py`'s own module docstring for why both, found by reading each real gate's
    source rather than assumed) for the duration of this run only. This is EXP-DD-001's own single-variable
    Challenger knob, never a correctness fix and never promoted to the default -- it exists to answer
    whether the Full corrected run's real $50 max_drawdown lockout (tripped 2025-02-03, never released --
    see `max_drawdown_lockout_check()` in `analysis.py`) protected the account from a strategy that had
    deteriorated, or prevented a later recovery. Every other Champion parameter (sizing, caps, stops,
    targets, fills, strategy) is completely unaffected by this flag."""
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

    from contextlib import ExitStack
    diagnostic_stack = ExitStack()
    if split_aware_diagnostic:
        from ..datasets.base import load_parquet
        splits = confirmed_splits_by_symbol_and_date(load_parquet(daily_dataset_id))
        diagnostic_stack.enter_context(SplitAwarePositionDiagnostic(splits))
    if disable_drawdown_gate:
        diagnostic_stack.enter_context(NoDrawdownChallenger())

    with diagnostic_stack, HistoricalExecutionContext(
            decision_provider, execution_provider=exec_provider,
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

        _progress_started = time.time()
        with mock.patch.object(de, "evaluate", _recording_evaluate), \
            mock.patch.object(cb, "evaluate_canonical", _recording_evaluate_canonical):
            for _cyc_idx, cyc in enumerate(cycles):
                if progress_every and _cyc_idx % progress_every == 0:
                    _orders_so_far = db.query("SELECT COUNT(*) n FROM orders")[0]["n"]
                    print(f"[hist001 progress] cycle {_cyc_idx}/{len(cycles)} session_date={cyc.session_date} "
                         f"elapsed={time.time() - _progress_started:.0f}s events={tracker.event_count()} "
                         f"orders_so_far={_orders_so_far}", file=sys.stderr, flush=True)
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

                # H7 event closure: mirrors the forward runtime's own `shadow_log._closed_signal_ids()`
                # check. Without this, an event this tracker opened above never lets go of its (symbol,
                # direction) key, so a LATER, genuinely independent re-entry (this symbol's prior position
                # has since closed for real, per the ledger) reuses the stale event_id and open_trade()
                # raises on the second, different trade_id -- a real crash found running an ad hoc replay
                # whose non-standard, near-zero warmup window made a within-one-run close-then-reenter
                # sequence far more likely to actually occur before evaluation_end. Cheap: bounded by this
                # run's own total closed-position count, queried fresh each cycle exactly like shadow_log
                # already does in production.
                if tracker.open_trades():
                    closed_trade_ids = {r["signal_id"] for r in db.query(
                        "SELECT DISTINCT signal_id FROM positions WHERE status='closed' AND signal_id IS NOT NULL")}
                    tracker.reconcile_closed_trades(closed_trade_ids)

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
