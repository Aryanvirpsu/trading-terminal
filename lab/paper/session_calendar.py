"""US equity (NYSE) session calendar — holidays and early closes, computed (no yearly list to maintain).

Regular session 09:30-16:00 America/New_York; early close 13:00 ET on the day after Thanksgiving,
on Christmas Eve (weekday, not itself a holiday) and on July 3 (Mon-Thu). Full-day holidays: New Year's
Day, MLK Day, Presidents' Day, Good Friday, Memorial Day, Juneteenth, Independence Day, Labor Day,
Thanksgiving, Christmas — Saturday holidays are observed the prior Friday, Sunday holidays the next
Monday (New Year's Day on a Saturday is NOT observed by NYSE, matching the exchange rule).

Known limit: ad-hoc closures (national days of mourning, weather, emergencies) are not predictable. Add
them via the `AVDI_EXTRA_CLOSED_DATES` env var (comma-separated ISO dates) until the code is updated.
"""
from __future__ import annotations

import datetime as dt
import os
from typing import Optional, Set, Tuple
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
OPEN_T = dt.time(9, 30)
CLOSE_T = dt.time(16, 0)
EARLY_CLOSE_T = dt.time(13, 0)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(weekday - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> dt.date:
    d = dt.date(year + (month == 12), month % 12 + 1, 1) - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: dt.date) -> dt.date:
    if d.weekday() == 5:
        return d - dt.timedelta(days=1)
    if d.weekday() == 6:
        return d + dt.timedelta(days=1)
    return d


def _easter(year: int) -> dt.date:
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def holidays(year: int) -> Set[dt.date]:
    MON, THU = 0, 3
    out = {
        _nth_weekday(year, 1, MON, 3),                      # MLK
        _nth_weekday(year, 2, MON, 3),                      # Presidents' Day
        _easter(year) - dt.timedelta(days=2),               # Good Friday
        _last_weekday(year, 5, MON),                        # Memorial Day
        _observed(dt.date(year, 6, 19)),                    # Juneteenth
        _observed(dt.date(year, 7, 4)),                     # Independence Day
        _nth_weekday(year, 9, MON, 1),                      # Labor Day
        _nth_weekday(year, 11, THU, 4),                     # Thanksgiving
        _observed(dt.date(year, 12, 25)),                   # Christmas
    }
    ny = dt.date(year, 1, 1)
    if ny.weekday() != 5:                                   # Saturday New Year's Day is not observed
        out.add(_observed(ny))
    return out


def _extra_closed() -> Set[dt.date]:
    out = set()
    for tok in os.environ.get("AVDI_EXTRA_CLOSED_DATES", "").split(","):
        tok = tok.strip()
        if tok:
            try:
                out.add(dt.date.fromisoformat(tok))
            except ValueError:
                pass
    return out


def is_holiday(d: dt.date) -> bool:
    return d in holidays(d.year) or d in holidays(d.year + 1) or d in _extra_closed()


def is_trading_day(d: dt.date) -> bool:
    return d.weekday() < 5 and not is_holiday(d)


def is_early_close(d: dt.date) -> bool:
    if not is_trading_day(d):
        return False
    thanksgiving = _nth_weekday(d.year, 11, 3, 4)
    if d == thanksgiving + dt.timedelta(days=1):
        return True
    if d.month == 12 and d.day == 24:
        return True
    if d.month == 7 and d.day == 3 and d.weekday() < 4:
        return True
    return False


def session(d: dt.date) -> Optional[Tuple[dt.datetime, dt.datetime]]:
    """(open, close) as ET-aware datetimes, or None if the market is closed all day."""
    if not is_trading_day(d):
        return None
    close_t = EARLY_CLOSE_T if is_early_close(d) else CLOSE_T
    return (dt.datetime.combine(d, OPEN_T, ET), dt.datetime.combine(d, close_t, ET))


def next_trading_day(d: dt.date) -> dt.date:
    d += dt.timedelta(days=1)
    while not is_trading_day(d):
        d += dt.timedelta(days=1)
    return d


def et_now(now: Optional[dt.datetime] = None) -> dt.datetime:
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.astimezone(ET)
