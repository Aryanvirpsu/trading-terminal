"""Override only the two concurrent-position caps for the registered HIST-005 arms.

Use after isolating the paper ledger and before entering the replay. This context
does not create a ledger, place orders, or launch a replay. It changes the runtime
RiskConfig and the canonical bridge's imported policy reference, never production
source or environment variables. Like the historical clock, it is process-local
and must not be shared between concurrent replays in one process.
"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from typing import Optional
from unittest import mock

from ..guards import ProductionIsolationError, guard_all, historical_data_root


class ConcurrentCapacityChallenger:
    """Temporarily set both effective caps to 3 (control) or 4 (Challenger).

    Other settings pass through unchanged. The future runner must separately
    verify the complete frozen configuration and record the registration commit.
    No override is installed unless the active ledger path is historical-only.
    """

    def __init__(self, max_open_positions: int):
        if type(max_open_positions) is not int or max_open_positions not in (3, 4):
            raise ValueError("HIST-005 registers only integer open-position caps 3 and 4")
        self.max_open_positions = max_open_positions
        self._stack: Optional[ExitStack] = None

    def __enter__(self) -> "ConcurrentCapacityChallenger":
        if self._stack is not None:
            raise RuntimeError("this capacity context is already active")
        guard_all()

        # Flat imports must match the real workflow's module identities.
        from paper import canonical_bridge as cb, config as cfg, db

        ledger_path = Path(db.db_path()).resolve()
        guard_all(ledger_path)
        ledger_root = (historical_data_root() / "ledgers").resolve()
        if not ledger_path.is_relative_to(ledger_root) or not ledger_path.name.startswith("historical_"):
            raise ProductionIsolationError("isolate the historical paper ledger before overriding capacity")

        real_risk = cfg.risk
        cap = self.max_open_positions

        def patched_risk():
            return replace(real_risk(), max_open_positions=cap)

        policy = replace(cb.STRATEGY_500_POLICY, max_open_positions=cap)
        # A failed second patch must also undo the first one.
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(cfg, "risk", patched_risk))
            stack.enter_context(mock.patch.object(cb, "STRATEGY_500_POLICY", policy))
            self._stack = stack.pop_all()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if self._stack is not None:
                return self._stack.__exit__(exc_type, exc_value, traceback)
        finally:
            self._stack = None
