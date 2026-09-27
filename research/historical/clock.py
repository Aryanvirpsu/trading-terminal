"""H2 — HistoricalReplayClock. The single source of "now" for every historical component. Everything
downstream (the provider, the runner, event identity) must ask the clock, never read a wall-clock or a
dataset's own max timestamp, so a walk-forward run over 2024 behaves exactly like a walk-forward run over
2026: "now" is whatever the clock says, nothing else.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional, Union

TimeLike = Union[str, dt.datetime]


def _to_utc(t: TimeLike) -> dt.datetime:
    if isinstance(t, str):
        t = dt.datetime.fromisoformat(t)
    if t.tzinfo is None:
        raise ValueError(f"HistoricalClock requires a timezone-aware time, got naive {t!r}")
    return t.astimezone(dt.timezone.utc)


class HistoricalClock:
    """`clock.now` is always UTC-aware. `.set(...)` jumps to an instant; `.advance(...)` steps forward by
    a timedelta; both refuse to move backward (a walk-forward/replay clock never rewinds) unless
    `allow_rewind=True` is passed explicitly (used only by tests and by a fresh `.reset()`).
    """

    def __init__(self, start_at: TimeLike):
        self._now = _to_utc(start_at)
        self._history = [self._now]

    @property
    def now(self) -> dt.datetime:
        return self._now

    def set(self, t: TimeLike, *, allow_rewind: bool = False) -> None:
        new = _to_utc(t)
        if new < self._now and not allow_rewind:
            raise ValueError(f"HistoricalClock refuses to move backward: {self._now.isoformat()} -> {new.isoformat()} "
                             f"(pass allow_rewind=True if this is a deliberate reset, e.g. a new walk-forward fold)")
        self._now = new
        self._history.append(new)

    def advance(self, delta: dt.timedelta) -> dt.datetime:
        if delta.total_seconds() < 0:
            raise ValueError("HistoricalClock.advance() cannot take a negative timedelta; use .set(allow_rewind=True)")
        self.set(self._now + delta)
        return self._now

    def advance_minutes(self, minutes: float) -> dt.datetime:
        return self.advance(dt.timedelta(minutes=minutes))

    def et_now(self):
        from zoneinfo import ZoneInfo
        return self._now.astimezone(ZoneInfo("America/New_York"))

    def __repr__(self) -> str:
        return f"HistoricalClock({self._now.isoformat()})"
