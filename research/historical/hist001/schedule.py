"""HIST-001: generalize `known_forward/schedule.py`'s single-day cadence to an arbitrary date range, using
the REAL production trading-calendar (`lab.paper.market_calendar.is_trading_day`, holiday-aware) to
enumerate trading days -- not a naive weekday assumption. Same per-day cycle structure as the known-forward
schedule (one 09:15 ET premarket_prep cycle, 09:35-14:50 ET regular/entries-allowed every 15 minutes,
15:05-15:50 ET post_cutoff/observation-only), repeated for every real trading day in [start, end].
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from typing import List
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


@dataclasses.dataclass(frozen=True)
class ScheduledCycle:
    cycle_id: str
    et_time: dt.datetime
    session_type: str
    allow_entries: bool
    session_date: str          # the trading day (ET date, ISO) this cycle belongs to


def _et(d: dt.date, hh: int, mm: int) -> dt.datetime:
    return dt.datetime(d.year, d.month, d.day, hh, mm, tzinfo=ET)


def _one_day(d: dt.date) -> List[ScheduledCycle]:
    day_iso = d.isoformat()
    cycles = [ScheduledCycle(cycle_id=f"{day_iso}T0915", et_time=_et(d, 9, 15),
                             session_type="premarket_prep", allow_entries=False, session_date=day_iso)]
    t = _et(d, 9, 35)
    end_regular = _et(d, 14, 50)
    while t <= end_regular:
        cycles.append(ScheduledCycle(cycle_id=f"{day_iso}T{t.strftime('%H%M')}", et_time=t,
                                     session_type="regular", allow_entries=True, session_date=day_iso))
        t = t + dt.timedelta(minutes=15)
    t = _et(d, 15, 5)
    end_post = _et(d, 15, 50)
    while t <= end_post:
        cycles.append(ScheduledCycle(cycle_id=f"{day_iso}T{t.strftime('%H%M')}", et_time=t,
                                     session_type="post_cutoff", allow_entries=False, session_date=day_iso))
        t = t + dt.timedelta(minutes=15)
    return cycles


def build_multi_day_schedule(start: str, end: str) -> List[ScheduledCycle]:
    """[start, end] inclusive, ISO dates. Real trading days only (weekends and the real NYSE holiday
    calendar excluded via lab.paper.market_calendar.is_trading_day -- the SAME calendar the forward Ubuntu
    runtime itself uses, not a reimplementation)."""
    from paper import market_calendar as cal   # flat namespace -- see execution.py's module-identity note

    start_d = dt.date.fromisoformat(start)
    end_d = dt.date.fromisoformat(end)
    cycles: List[ScheduledCycle] = []
    d = start_d
    while d <= end_d:
        if cal.is_trading_day(d):
            cycles.extend(_one_day(d))
        d += dt.timedelta(days=1)
    return cycles


def trading_days(start: str, end: str) -> List[str]:
    from paper import market_calendar as cal

    start_d = dt.date.fromisoformat(start)
    end_d = dt.date.fromisoformat(end)
    out = []
    d = start_d
    while d <= end_d:
        if cal.is_trading_day(d):
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out
