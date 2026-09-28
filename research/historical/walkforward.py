"""H8 — walk-forward orchestration, with a holdout that cannot be casually spent.

Makes it structurally awkward (not merely discouraged) to do:

    2024-2026 -> optimize -> report 2024-2026 performance

Instead: alternating development/validation folds, then exactly one LOCKED HOLDOUT period at the end.
Every access to any period is recorded, tagged by purpose; inspecting the holdout for a development purpose
requires an explicit, named acknowledgement (`inspect_holdout(..., i_understand_this_ends_the_holdout=True)`)
and PERMANENTLY marks it contaminated the instant that happens -- there is no way to "undo" having looked.
A contaminated holdout is not deleted or hidden; its own status just stops being able to claim "this period
was never inspected before this strategy's parameters were finalized," which is the entire point of having
one.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Dict, List, Optional

DEVELOPMENT = "development"
VALIDATION = "validation"
HOLDOUT = "holdout"


class HoldoutContaminationError(RuntimeError):
    """Raised when the holdout is accessed for a development purpose without the explicit
    acknowledgement that doing so permanently ends its usefulness as a holdout."""


@dataclasses.dataclass(frozen=True)
class Period:
    name: str
    kind: str          # DEVELOPMENT | VALIDATION | HOLDOUT
    start: str          # inclusive, ISO date
    end: str            # exclusive, ISO date

    def __post_init__(self):
        if self.kind not in (DEVELOPMENT, VALIDATION, HOLDOUT):
            raise ValueError(f"unknown period kind {self.kind!r}")
        if self.start >= self.end:
            raise ValueError(f"period {self.name!r}: start {self.start!r} must be before end {self.end!r}")


@dataclasses.dataclass(frozen=True)
class InspectionRecord:
    period_name: str
    purpose: str                  # e.g. "development_decision", "validation_scoring", "final_report"
    at: str                       # ISO timestamp this inspection was recorded
    note: str


class WalkForwardPlan:
    """Construct with an explicit, ordered list of Periods (development/validation folds, then exactly one
    holdout last). Every subsequent access goes through `inspect()` (or the stricter `inspect_holdout()`
    for the holdout specifically), so the plan itself becomes the audit trail of what was looked at, when,
    and why -- not something a caller has to remember to log separately."""

    def __init__(self, periods: List[Period]):
        if not periods:
            raise ValueError("a walk-forward plan needs at least one period")
        holdouts = [p for p in periods if p.kind == HOLDOUT]
        if len(holdouts) != 1:
            raise ValueError(f"a walk-forward plan must have EXACTLY one holdout period, got {len(holdouts)}")
        if periods[-1].kind != HOLDOUT:
            raise ValueError("the holdout period must be the LAST period in the plan")
        names = [p.name for p in periods]
        if len(set(names)) != len(names):
            raise ValueError(f"period names must be unique, got {names}")
        self.periods: List[Period] = list(periods)
        self._by_name: Dict[str, Period] = {p.name: p for p in periods}
        self._history: List[InspectionRecord] = []
        self._holdout_contaminated_at: Optional[str] = None
        self._holdout_contamination_reason: Optional[str] = None

    def period(self, name: str) -> Period:
        if name not in self._by_name:
            raise KeyError(f"no such period {name!r}; known periods: {list(self._by_name)}")
        return self._by_name[name]

    @property
    def holdout(self) -> Period:
        return next(p for p in self.periods if p.kind == HOLDOUT)

    def inspect(self, name: str, *, purpose: str, note: str = "") -> InspectionRecord:
        """For a development/validation period: always allowed, always recorded. For the holdout: refuses
        (raises HoldoutContaminationError) unless `purpose` is exactly `"final_report"` -- use
        `inspect_holdout()` for any other reason, which forces the explicit acknowledgement."""
        p = self.period(name)
        if p.kind == HOLDOUT and purpose != "final_report":
            raise HoldoutContaminationError(
                f"refusing to inspect the holdout period {name!r} for purpose {purpose!r} via inspect() -- "
                f"use inspect_holdout(..., i_understand_this_ends_the_holdout=True) if this is deliberate; "
                f"a casual/accidental holdout read must not silently succeed")
        record = InspectionRecord(period_name=name, purpose=purpose,
                                  at=dt.datetime.now(dt.timezone.utc).isoformat(), note=note)
        self._history.append(record)
        return record

    def inspect_holdout(self, *, purpose: str, note: str, i_understand_this_ends_the_holdout: bool) -> InspectionRecord:
        """The ONLY way to inspect the holdout for anything other than a final report. Requires the
        explicit flag; the instant this is called, the holdout is PERMANENTLY marked contaminated -- there
        is no un-inspecting it. This is deliberately not a quiet default-True kwarg: a caller must type the
        acknowledgement out, every time, so it can never be set once and forgotten."""
        if not i_understand_this_ends_the_holdout:
            raise HoldoutContaminationError(
                "inspect_holdout() requires i_understand_this_ends_the_holdout=True -- inspecting the "
                "holdout for a development purpose permanently ends its usefulness as a holdout, and this "
                "call must say so explicitly, not rely on a default")
        h = self.holdout
        record = InspectionRecord(period_name=h.name, purpose=purpose,
                                  at=dt.datetime.now(dt.timezone.utc).isoformat(), note=note)
        self._history.append(record)
        if self._holdout_contaminated_at is None:
            self._holdout_contaminated_at = record.at
            self._holdout_contamination_reason = f"{purpose}: {note}" if note else purpose
        return record

    @property
    def holdout_contaminated(self) -> bool:
        return self._holdout_contaminated_at is not None

    def holdout_status(self) -> Dict[str, object]:
        return {"period": self.holdout.name, "contaminated": self.holdout_contaminated,
               "contaminated_at": self._holdout_contaminated_at,
               "contamination_reason": self._holdout_contamination_reason}

    def history_for(self, name: str) -> List[InspectionRecord]:
        return [r for r in self._history if r.period_name == name]

    def was_ever_inspected(self, name: str) -> bool:
        return any(r.period_name == name for r in self._history)

    def full_history(self) -> List[InspectionRecord]:
        return list(self._history)

    def summary(self) -> Dict[str, object]:
        return {
            "periods": [dataclasses.asdict(p) for p in self.periods],
            "inspections": [dataclasses.asdict(r) for r in self._history],
            "holdout": self.holdout_status(),
        }


def alternating_dev_validation_plan(folds: List[tuple], holdout: tuple) -> WalkForwardPlan:
    """Convenience builder: `folds` is a list of (dev_start, dev_end, val_start, val_end) tuples, in
    chronological order; `holdout` is (start, end) for the final locked period. Produces a WalkForwardPlan
    with periods named development_1/validation_1/development_2/validation_2/.../holdout."""
    periods: List[Period] = []
    for i, (ds, de, vs, ve) in enumerate(folds, start=1):
        periods.append(Period(f"development_{i}", DEVELOPMENT, ds, de))
        periods.append(Period(f"validation_{i}", VALIDATION, vs, ve))
    periods.append(Period("holdout", HOLDOUT, holdout[0], holdout[1]))
    return WalkForwardPlan(periods)
