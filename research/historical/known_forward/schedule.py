"""H5 step 4: the exact forward discovery schedule for 2026-09-25, as documented in
`docs/UBUNTU_LIVE_ACCEPTANCE_01.md`'s "Schedule vs actual" table -- one 09:15 ET observation-only
premarket_prep cycle, then 15-minute discovery cycles 09:35-14:50 ET (regular, entries allowed) and
15:05-15:50 ET (post_cutoff, observation only) -- 27 cycles total (1 + 22 + 4), matching the doc's
"26 completed (22 regular, 4 post_cutoff)" plus the separate 09:15 scan.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from typing import List
from zoneinfo import ZoneInfo

from .reference import SESSION_DATE

ET = ZoneInfo("America/New_York")


@dataclasses.dataclass(frozen=True)
class ScheduledCycle:
    cycle_id: str
    et_time: dt.datetime
    session_type: str            # "premarket_prep" | "regular" | "post_cutoff"
    allow_entries: bool


def _et(hh: int, mm: int) -> dt.datetime:
    y, m, d = (int(x) for x in SESSION_DATE.split("-"))
    return dt.datetime(y, m, d, hh, mm, tzinfo=ET)


def build_schedule() -> List[ScheduledCycle]:
    cycles: List[ScheduledCycle] = []
    cycles.append(ScheduledCycle(cycle_id=f"{SESSION_DATE}T0915", et_time=_et(9, 15),
                                 session_type="premarket_prep", allow_entries=False))

    # 09:35 through 14:50 ET, every 15 minutes -- regular, entries allowed (matches the doc's "22
    # entry-eligible to 14:50").
    t = _et(9, 35)
    end_regular = _et(14, 50)
    while t <= end_regular:
        cycles.append(ScheduledCycle(cycle_id=f"{SESSION_DATE}T{t.strftime('%H%M')}", et_time=t,
                                     session_type="regular", allow_entries=True))
        t = t + dt.timedelta(minutes=15)

    # 15:05 through 15:50 ET -- post_cutoff, observation only (matches the doc's "4 observation-only
    # post-cutoff").
    t = _et(15, 5)
    end_post = _et(15, 50)
    while t <= end_post:
        cycles.append(ScheduledCycle(cycle_id=f"{SESSION_DATE}T{t.strftime('%H%M')}", et_time=t,
                                     session_type="post_cutoff", allow_entries=False))
        t = t + dt.timedelta(minutes=15)

    return cycles


SCHEDULE: List[ScheduledCycle] = build_schedule()
