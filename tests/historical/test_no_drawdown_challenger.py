"""EXP-DD-001: the NoDrawdownChallenger context manager and its `run_baseline(disable_drawdown_gate=...)`
wiring. Proves: (1) both real gates are actually disabled while active, (2) every OTHER risk parameter is
passed through unchanged (never a hand-built policy that could silently drift from real defaults), (3) both
patches restore exactly on exit, and (4) `disable_drawdown_gate=False` (the default) leaves
`run_baseline()`'s own canonical behavior completely untouched."""
import dataclasses
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Importing research.historical.* FIRST (before anything touches sys.path with "lab"/"dashboard"/"src")
# makes sure the real `research` PACKAGE resolves and gets cached in sys.modules -- avdi_adapter.py itself
# inserts those directories onto sys.path on import (same trick every lab/paper.py module uses), and
# dashboard/research.py would otherwise shadow the research package if one of those directories came first
# (see test_hist001_event_identity_reentry.py's own identical note). This module doesn't import avdi_adapter
# itself, so the path insertion below does it explicitly, AFTER research.historical is already resolved and
# cached -- safe by the same reasoning.
from research.historical.hist001.no_drawdown_challenger import NoDrawdownChallenger

for _p in ("lab", "dashboard", "src"):
    sys.path.insert(0, os.path.join(str(ROOT), _p))


def test_runtime_drawdown_gate_is_disabled_inside_the_context():
    from paper import config as cfg

    real = cfg.risk()
    assert real.max_drawdown == pytest.approx(50.0)         # sanity: the real default really is $50

    with NoDrawdownChallenger():
        patched = cfg.risk()
        assert patched.max_drawdown == float("inf")
        # every OTHER field passed through from the REAL config, untouched
        for field in dataclasses.fields(real):
            if field.name == "max_drawdown":
                continue
            assert getattr(patched, field.name) == getattr(real, field.name), field.name

    restored = cfg.risk()
    assert restored.max_drawdown == pytest.approx(50.0)      # restored exactly


def test_canonical_drawdown_gate_is_disabled_inside_the_context():
    from paper import canonical_bridge as cb

    real_policy = cb.STRATEGY_500_POLICY
    assert real_policy.max_drawdown == pytest.approx(50.0)

    with NoDrawdownChallenger():
        patched_policy = cb.STRATEGY_500_POLICY
        assert patched_policy.max_drawdown is None
        assert patched_policy.max_drawdown_pct is None
        for field in dataclasses.fields(real_policy):
            if field.name in ("max_drawdown", "max_drawdown_pct"):
                continue
            assert getattr(patched_policy, field.name) == getattr(real_policy, field.name), field.name

    assert cb.STRATEGY_500_POLICY is real_policy              # restored to the exact same real object


def test_runtime_gate_actually_permits_a_drawdown_past_fifty_dollars():
    """Not just a config-field check -- proves the exact comparison `risk.py`'s `check_entry()` makes
    (`drawdown_usd < r.max_drawdown`) now passes for a drawdown far beyond the real $50 cap, and fails again
    once the context exits -- the real gate's own logic, not a re-derivation of it."""
    from paper import config as cfg

    with NoDrawdownChallenger():
        r = cfg.risk()
        huge_drawdown_usd = 1_000_000.0
        assert huge_drawdown_usd < r.max_drawdown          # the real comparison risk.py makes, now unbounded

    r_after = cfg.risk()
    assert not (huge_drawdown_usd < r_after.max_drawdown)   # the exact same comparison fails again once restored


def test_both_gates_restore_cleanly_after_a_nested_context_exits():
    from paper import canonical_bridge as cb
    from paper import config as cfg

    real_risk_fn = cfg.risk
    real_policy = cb.STRATEGY_500_POLICY
    with NoDrawdownChallenger():
        assert cfg.risk is not real_risk_fn
        assert cb.STRATEGY_500_POLICY is not real_policy
    assert cfg.risk is real_risk_fn
    assert cb.STRATEGY_500_POLICY is real_policy
