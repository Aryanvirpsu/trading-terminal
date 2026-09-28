"""H5 step 4: replay the exact 2026-09-25 discovery schedule against the real, unmodified AVDI decision
stack and paper-execution pipeline (H3 + H4's HistoricalExecutionContext), seeded at the exact forward
account boundary, using the KNOWN_FORWARD_2026_09_25 dataset (`build_dataset.py`) -- never
`reference.py` (see that module's docstring: the reference is compared against afterward, never fed in).

One shared `HistoricalClock` drives BOTH the decision provider (daily bars, for trend/RSI) and the
execution provider (5-minute bars, for quote_for/_live_mark_src) -- see H5 blocker #5 in `execution.py`'s
module docstring. Every cycle: advance the shared clock -> `workflow.premarket()` in the REAL multi-scan
mode (`cycle_id`/`allow_entries`/`session_type`/`scan_ts`, the same parameters the Ubuntu runtime's own
15-minute scheduler passes) -> `workflow.market_hours()` for stop/target management of any already-open
position. No forward-ledger reads happen anywhere in this path -- the isolated ledger is the only store
touched.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence

from ..capability import PRICE_TREND_MACRO_V1, PRICE_TREND_ONLY_V1
from ..clock import HistoricalClock
from ..execution import HistoricalExecutionContext, isolate_paper_ledger
from ..guards import guard_all
from ..macro import MacroHistory
from ..manifest import load_manifest
from ..provider import HistoricalMarketProvider
from .build_dataset import DAILY_DATASET_ID, INTRADAY_DATASET_ID
from .reference import REFERENCE_EVENTS, SESSION, SESSION_DATE
from .schedule import SCHEDULE, ScheduledCycle


def _sectors() -> List[str]:
    return sorted({e.sector for e in REFERENCE_EVENTS})


def run(*, run_id: str = "h5_known_forward_2026_09_25", cycles: Optional[Sequence[ScheduledCycle]] = None,
       seed_boundary: bool = True, macro_history: Optional[MacroHistory] = None) -> Dict[str, Any]:
    """Runs the full replay once and returns {"cycles": [...], "account": {...}, "calls": [...],
    "manifests": {...}, "capability_fingerprint": ...}. `cycles` defaults to the full real SCHEDULE; a
    caller may pass a prefix for a faster partial run (e.g. tests). `seed_boundary=True` (default) sets
    PAPER_INITIAL_CASH/PAPER_INITIAL_EQUITY to the documented forward boundary ($504.66) before isolating
    the ledger, exactly matching the reference SESSION's own starting point (step 10's account-state
    equivalence). `macro_history` (H5.5): when given, this run carries PRICE_TREND_MACRO_V1 (real
    historical macro replay via `_fam_macro`) instead of PRICE_TREND_ONLY_V1 -- passed straight through to
    HistoricalExecutionContext, which is what actually wires the replay."""
    guard_all()
    cycles = list(cycles) if cycles is not None else list(SCHEDULE)
    if not cycles:
        raise ValueError("run() requires at least one scheduled cycle")

    daily_manifest = load_manifest(DAILY_DATASET_ID)
    intraday_manifest = load_manifest(INTRADAY_DATASET_ID)

    if seed_boundary:
        os.environ["PAPER_INITIAL_CASH"] = str(SESSION.boundary_cash)
        os.environ["PAPER_INITIAL_EQUITY"] = str(SESSION.boundary_equity)
    isolate_paper_ledger(run_id)

    shared_clock = HistoricalClock(cycles[0].et_time)
    decision_provider = HistoricalMarketProvider(shared_clock, [DAILY_DATASET_ID],
                                                 volume_trust=daily_manifest.volume_trust)
    exec_provider = HistoricalMarketProvider(shared_clock, [INTRADAY_DATASET_ID],
                                             volume_trust=intraday_manifest.volume_trust)

    cycle_results: List[Dict[str, Any]] = []
    with HistoricalExecutionContext(decision_provider, execution_provider=exec_provider,
                                    neutral_sector=_sectors(), macro_history=macro_history) as ctx:
        from paper import broker, workflow

        for cyc in cycles:
            shared_clock.set(cyc.et_time)
            pm = workflow.premarket(SESSION_DATE, cycle_id=cyc.cycle_id, allow_entries=cyc.allow_entries,
                                    session_type=cyc.session_type, scan_ts=cyc.et_time.isoformat())
            mh = workflow.market_hours(SESSION_DATE)
            cycle_results.append({
                "cycle_id": cyc.cycle_id, "et_time": cyc.et_time.isoformat(), "session_type": cyc.session_type,
                "allow_entries": cyc.allow_entries, "premarket": pm, "market_hours": mh,
            })
        account = broker.account(SESSION_DATE)
        calls = list(ctx.calls)

    return {
        "run_id": run_id, "session_date": SESSION_DATE, "cycles": cycle_results, "account": account,
        "calls": calls,
        "manifests": {"daily": daily_manifest.to_dict(), "intraday": intraday_manifest.to_dict()},
        "capability_fingerprint": PRICE_TREND_MACRO_V1 if macro_history is not None else PRICE_TREND_ONLY_V1,
    }


if __name__ == "__main__":
    import json

    result = run()
    print(json.dumps({"cycles": len(result["cycles"]), "account": result["account"]}, indent=2, default=str))
