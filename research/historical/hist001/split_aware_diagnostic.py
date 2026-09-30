"""HIST-001 Full stage -- DIAGNOSTIC ONLY, never the canonical result.

Finding: `lab.paper.broker.manage_open_positions()` (real, unmodified Champion code) checks an open
position's stored `stop`/`target` against the CURRENT quote with no corporate-action awareness at all. A
confirmed stock split occurring while a position is open therefore compares a PRE-SPLIT stop price against
a POST-SPLIT raw quote -- a ~10x (or whatever the split ratio is) apparent price discontinuity that can
trigger a catastrophically wrong stop-loss with no real economic basis. Confirmed on AVGO's real 2024-07-15
10-for-1 split: the open position's exit ("exit_stop", realized_pnl -$57.18, ~17R against an $5-budgeted
trade) was purely this artifact, and the resulting drawdown then tripped `canonical/account_fit.py`'s real
`max_drawdown` circuit breaker for the REST of the ~2-year Full evaluation window -- see
HIST_001_CHAMPION_BASELINE.md's Full-stage disclosure for the full finding and the user's explicit
authorization for this diagnostic.

This is NOT a Historical Lab replay bug and NOT something this project may silently "fix": changing
`manage_open_positions()` itself would be a real Champion execution-model change, which the standing rule
forbids without explicit authorization. What follows is authorized ONLY as a separate, clearly-labeled
diagnostic re-run: it never modifies `lab.paper.broker.py`, `canonical/account_fit.py`, or any other Champion
file. It reuses `lab.paper.fills.apply_split()` -- a REAL, already-existing, already-tested Champion utility
function that is simply never called anywhere in the current position-management path -- to adjust an open
position's `quantity`/`avg_entry`/`stop`/`target`/`mfe`/`mae` at the exact moment a CONFIRMED split's own bar
appears (the same lookahead boundary `corporate_actions.is_confirmed()`/`known_as_of()` already enforce for
reporting), mirroring exactly what a real broker does to a real open position and a real resting stop order
when the underlying stock splits.

The canonical HIST-001 Full result (`hist001_full_2024_2026` / `hist001_full_2024_2026_fast`) is computed
WITHOUT this patch and is never touched by it. This diagnostic produces a SEPARATE run
(`hist001_full_2024_2026_split_aware_diag`), reported side by side, never blended into the official numbers.
"""
from __future__ import annotations

import datetime as dt
from typing import Dict, Tuple
from unittest import mock

import pandas as pd

from ..corporate_actions import detect_splits, is_confirmed


def confirmed_splits_by_symbol_and_date(daily_df: pd.DataFrame) -> Dict[Tuple[str, str], float]:
    """`{(symbol, ET split_date_iso): inferred_split_ratio}` for every CONFIRMED split in `daily_df` -- the
    exact same detection already run (and disclosed) for HIST-001's own corporate-action reporting, not a
    new/different detector."""
    events = detect_splits(daily_df)
    return {(e.symbol, e.split_date): e.inferred_split_ratio for e in events if is_confirmed(e)}


class SplitAwarePositionDiagnostic:
    """`with SplitAwarePositionDiagnostic(splits_by_symbol_date):` patches `paper.broker.manage_open_positions`
    to apply a real, confirmed split's adjustment to any OPEN position in that symbol on the split's own
    session date, via the real `lab.paper.fills.apply_split()`, BEFORE calling the real, unmodified
    `manage_open_positions()` for stop/target checking -- so the comparison that function makes is against an
    economically-consistent stop/target, exactly as a real broker would maintain for a real resting order
    across a real split. Restores the original function on exit, exactly like every other Historical Lab
    patch. Diagnostic only -- see module docstring."""

    def __init__(self, splits_by_symbol_date: Dict[Tuple[str, str], float]):
        self.splits_by_symbol_date = splits_by_symbol_date
        self.adjustments_applied = []          # audit trail of every adjustment this diagnostic made
        self._already_adjusted: set = set()    # (position_id, session_date) already applied -- see below
        self._stack = None
        self._real_manage_open_positions = None

    def _adjust_positions_for_todays_splits(self, session_date: str) -> None:
        from lab.paper.fills import apply_split
        from paper import db  # flat namespace -- see avdi_adapter.py's module docstring on module identity

        open_positions = db.query("SELECT * FROM positions WHERE status='open'")
        for pos in open_positions:
            ratio = self.splits_by_symbol_date.get((pos["symbol"], session_date))
            if not ratio:
                continue
            # `manage_open_positions()` (and therefore this patch) runs on MANY cycles within the same
            # session_date (the real multi-scan schedule, ~27/day) -- a real bug found running the actual
            # diagnostic: without this guard, the SAME position got the SAME split ratio applied on every
            # one of that day's cycles, compounding 10x into 100x for AVGO's real split. A position may be
            # adjusted for a given (position, date) split exactly once, no matter how many cycles re-check it.
            dedup_key = (pos["position_id"], session_date)
            if dedup_key in self._already_adjusted:
                continue
            self._already_adjusted.add(dedup_key)
            new_qty, new_entry = apply_split(pos["quantity"], pos["avg_entry"], ratio)
            _, new_stop = apply_split(1.0, pos["stop"], ratio) if pos["stop"] is not None else (None, None)
            _, new_target = apply_split(1.0, pos["target"], ratio) if pos["target"] is not None else (None, None)
            _, new_mfe = apply_split(1.0, pos["mfe"], ratio) if pos.get("mfe") is not None else (None, pos.get("mfe"))
            _, new_mae = apply_split(1.0, pos["mae"], ratio) if pos.get("mae") is not None else (None, pos.get("mae"))
            db.execute("UPDATE positions SET quantity=?, avg_entry=?, stop=?, target=?, mfe=?, mae=? "
                      "WHERE position_id=?",
                      (new_qty, new_entry, new_stop, new_target, new_mfe, new_mae, pos["position_id"]))
            self.adjustments_applied.append({
                "position_id": pos["position_id"], "symbol": pos["symbol"], "session_date": session_date,
                "ratio": ratio, "before": {"quantity": pos["quantity"], "avg_entry": pos["avg_entry"],
                                          "stop": pos["stop"], "target": pos["target"]},
                "after": {"quantity": new_qty, "avg_entry": new_entry, "stop": new_stop, "target": new_target},
            })
            db.audit("position", pos["position_id"], "split_adjusted_diagnostic",
                     {"ratio": ratio, "session_date": session_date, "note": "DIAGNOSTIC ONLY -- not the "
                      "canonical HIST-001 result, see split_aware_diagnostic.py"})

    def _patched_manage_open_positions(self, quotes, session_date=None):
        sd = session_date or dt.date.today().isoformat()
        self._adjust_positions_for_todays_splits(sd)
        return self._real_manage_open_positions(quotes, session_date)

    def __enter__(self) -> "SplitAwarePositionDiagnostic":
        from contextlib import ExitStack

        from paper import broker
        self._stack = ExitStack()
        self._real_manage_open_positions = broker.manage_open_positions
        self._stack.enter_context(mock.patch.object(broker, "manage_open_positions",
                                                     self._patched_manage_open_positions))
        return self

    def __exit__(self, *exc) -> None:
        if self._stack is not None:
            self._stack.close()
