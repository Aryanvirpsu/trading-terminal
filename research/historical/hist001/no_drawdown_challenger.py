"""EXP-DD-001 -- the single-variable "no max_drawdown gate" Challenger (see
`research/historical/reports/EXP_DD_001_PREREGISTRATION.md`, committed before any result).

Diagnostic Challenger, NOT a correctness fix and NOT promoted anywhere: the Full-stage corrected baseline
(`hist001_full_2024_2026_corrected`, the Champion CONTROL) found the account's own real `max_drawdown`
gate tripped on genuine trading losses on 2025-02-03 and never released, freezing the last ~54% of the
2024-01-01..2026-03-10 evaluation window at zero further trades (see `max_drawdown_lockout_check()` in
`analysis.py`). This module exists to answer one question only: if that one gate had not existed, would the
account have made more money by 2026-03-10, or less? Every other Champion parameter stays byte-identical to
the control run -- same $5/trade risk, same $125 notional, same 3 open / 1-per-sector caps, same $10 daily
loss cap, same $100 cash reserve, same stops/targets, same fills, same strategy, same sizing.

THE $50 CAP EXISTS IN TWO INDEPENDENT PLACES, found by inspecting the real execution path (not assumed):
  1. `lab/paper/risk.py`'s `check_entry()` compares `st["drawdown_usd"] < r.max_drawdown` where
     `r = paper.config.risk()` -- a plain float, no None-handling, so "disabled" here must be a very large
     number (`float("inf")`), not `None` (comparing a float to `None` would raise `TypeError`).
  2. `lab/paper/canonical_bridge.py`'s `evaluate_canonical()` calls `canonical.account_fit.stock_account_fit`
     with `policy=STRATEGY_500_POLICY` (a hardcoded `RiskPolicy` constant imported by NAME into
     `canonical_bridge`'s own module namespace via `from canonical.risk_policy import STRATEGY_500_POLICY`). `account_fit.py`'s own `effective_limit(absolute=None, percent=None, ...)`
     returns `LimitResult(None, UNBOUNDED)` and the caller's `if dd_cap.limit is not None:` guard then skips
     the check entirely -- so `None` (not `inf`) is the correct "disabled" value for THIS gate.
Disabling only ONE of the two would still let the other stop the account -- found and confirmed by reading
both gates' real source before writing this module, not assumed from the config value's name alone.

Patches at the SAME two seams the rest of Historical Lab already proves safe to patch (`mock.patch.object`
on the real module attribute, restored exactly on exit) -- `lab/paper/risk.py` and
`lab/paper/canonical_bridge.py` themselves are never modified. `paper.config.risk()` is still called for
every OTHER field (max_loss_per_trade, max_position_notional, max_daily_loss, cooldown, etc.) -- only
`max_drawdown` is overridden, via `dataclasses.replace()`, never a hand-built RiskConfig that could silently
drift from the real defaults. Same discipline for `STRATEGY_500_POLICY`: `dataclasses.replace()` on the
REAL object, overriding only `max_drawdown`/`max_drawdown_pct`, every other field (risk_per_trade_pct,
max_position_notional, max_sector_exposure_pct, max_open_positions, max_positions_per_sector, max_daily_loss,
min_cash_reserve) passed through unchanged.

Never applied by default: `run_baseline(disable_drawdown_gate=True)` is required explicitly; the canonical
HIST-001 Full/Medium/Smoke results are produced with this at its default `False`, byte-for-byte unaffected
by this module's existence (see `test_no_drawdown_challenger.py`'s own parity tests)."""
from __future__ import annotations

import dataclasses
from unittest import mock


class NoDrawdownChallenger:
    """`with NoDrawdownChallenger(): ...` -- patches both real max_drawdown gates for the duration of the
    block, restores both exactly on exit. Every other risk parameter on both the runtime `RiskConfig` and
    the canonical `STRATEGY_500_POLICY` is read from the REAL current config/policy and passed through
    unchanged; only `max_drawdown` (and `max_drawdown_pct`, already `None` in the real policy) is replaced."""

    def __init__(self):
        self._stack = None

    def __enter__(self) -> "NoDrawdownChallenger":
        from contextlib import ExitStack

        from paper import canonical_bridge as cb
        from paper import config as cfg

        self._stack = ExitStack()
        real_risk = cfg.risk

        def _patched_risk():
            return dataclasses.replace(real_risk(), max_drawdown=float("inf"))

        no_dd_policy = dataclasses.replace(cb.STRATEGY_500_POLICY, max_drawdown=None, max_drawdown_pct=None)

        self._stack.enter_context(mock.patch.object(cfg, "risk", _patched_risk))
        self._stack.enter_context(mock.patch.object(cb, "STRATEGY_500_POLICY", no_dd_policy))
        return self

    def __exit__(self, *exc) -> None:
        self._stack.close()
        self._stack = None
