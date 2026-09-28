"""H6 integration: resolve outcomes (real or hypothetical) for the 2026-09-25 known-forward session's own
reference events, using the real AVDI decision stack for the entry/stop/target and the real 5-minute
execution feed (advanced through end of session) for the outcome walk. Since this replay's own decisions
never reach TRADEABLE (see H5_FORWARD_REPRODUCTION.md's data_quality-ceiling finding), every outcome
resolved here is `hypothetical=True` by construction -- this IS the H6 acceptance case the directive asks
for: "what happened to the MONITOR just below the gate," "what happened to the TRADEABLE blocked by
capacity."

Deliberately independent of `replay.py`'s own cycle loop (which only records the action LABEL per symbol,
not the full price/stop/target fields `resolve_hypothetical` needs) -- this evaluates each reference
symbol directly at its own documented `first_seen_cycle_et` (reference.py), the earliest point in the
session a real decision for it exists to resolve an outcome from.
"""
from __future__ import annotations

import datetime as dt
from typing import Dict, Optional
from zoneinfo import ZoneInfo

from ..clock import HistoricalClock
from ..execution import HistoricalExecutionContext, isolate_paper_ledger
from ..manifest import load_manifest
from ..outcomes import OutcomeResult, resolve_hypothetical
from ..provider import HistoricalMarketProvider
from .build_dataset import DAILY_DATASET_ID, INTRADAY_DATASET_ID
from .reference import SESSION_DATE, all_symbols, event_by_symbol
from .replay import _sectors
from .schedule import SCHEDULE

ET = ZoneInfo("America/New_York")


def _et_time(hhmm: str) -> dt.datetime:
    hh, mm = int(hhmm[:2]), int(hhmm[3:])
    y, m, d = (int(x) for x in SESSION_DATE.split("-"))
    return dt.datetime(y, m, d, hh, mm, tzinfo=ET)


def resolve_all_reference_symbols(*, run_id: str = "h6_session_outcomes") -> Dict[str, Optional[OutcomeResult]]:
    """For every one of the 12 reference symbols, evaluate it (real decision stack) at its own documented
    first-seen cycle time, then resolve the outcome that decision's own stop/target implies, using only
    real bars strictly after that decision -- through the end of the session's own data."""
    isolate_paper_ledger(run_id)
    daily_manifest = load_manifest(DAILY_DATASET_ID)
    intraday_manifest = load_manifest(INTRADAY_DATASET_ID)
    end_of_session = SCHEDULE[-1].et_time + dt.timedelta(minutes=5)

    results: Dict[str, Optional[OutcomeResult]] = {}
    for sym in all_symbols():
        event = event_by_symbol(sym)
        first_seen = _et_time(event.first_seen_cycle_et) if event and event.first_seen_cycle_et else SCHEDULE[1].et_time

        # Decision-time context: clock at the moment of the decision, exactly like the real replay would see.
        decision_clock = HistoricalClock(first_seen)
        decision_provider = HistoricalMarketProvider(decision_clock, [DAILY_DATASET_ID],
                                                      volume_trust=daily_manifest.volume_trust)
        decision_exec_provider = HistoricalMarketProvider(decision_clock, [INTRADAY_DATASET_ID],
                                                          volume_trust=intraday_manifest.volume_trust)
        with HistoricalExecutionContext(decision_provider, execution_provider=decision_exec_provider,
                                        neutral_sector=_sectors()) as ctx:
            evaluation = ctx.evaluate(sym, direction="LONG", balance=504.66)

        if evaluation.get("price") is None or evaluation.get("stop") is None or evaluation.get("target") is None:
            results[sym] = None
            continue

        # Outcome-time context: a FRESH provider whose clock sits at end-of-session, so the outcome walk
        # can see every bar through the close (still never beyond the real data) without disturbing the
        # decision-time clock above.
        outcome_clock = HistoricalClock(end_of_session)
        outcome_exec_provider = HistoricalMarketProvider(outcome_clock, [INTRADAY_DATASET_ID],
                                                         volume_trust=intraday_manifest.volume_trust)
        results[sym] = resolve_hypothetical(outcome_exec_provider, sym, evaluation, first_seen)
    return results


if __name__ == "__main__":
    import json

    outcomes = resolve_all_reference_symbols()
    for sym, o in outcomes.items():
        print(sym, json.dumps(o.to_dict(), default=str) if o else "no valid levels to resolve")
