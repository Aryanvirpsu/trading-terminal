"""Offline gate and cleanup checks; no orders, ledger writes, or market replay."""
import dataclasses
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Cache the research package before dashboard/research.py can shadow it.
from research.historical.guards import ProductionIsolationError
from research.historical.hist005.capacity_challenger import ConcurrentCapacityChallenger

for _part in ("lab", "dashboard", "src"):
    sys.path.insert(0, str(ROOT / _part))

from canonical.risk_policy import STRATEGY_500_POLICY
from paper import canonical_bridge as cb, config as cfg, db, risk


@pytest.fixture(autouse=True)
def isolated_context(monkeypatch, tmp_path):
    # Configure paths without opening or creating any account database.
    monkeypatch.setenv("AVDI_HISTORICAL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PAPER_LEDGER", "historical_hist005_fixture")
    monkeypatch.setattr(db, "_DATA_DIR", str(tmp_path / "ledgers" / "hist005_fixture"))
    for key in tuple(os.environ):
        if key.startswith("PAPER_") and key != "PAPER_LEDGER":
            monkeypatch.delenv(key)
    monkeypatch.delenv("C0_PROFILE", raising=False)
    monkeypatch.setenv("BROKER_PROVIDER", "none")
    monkeypatch.setenv("ROBINHOOD_TRADING_ENABLED", "false")
    # Stub only the external/account-state seams. Both gate implementations run.
    monkeypatch.setattr(db, "audit", lambda *a, **kw: None)
    monkeypatch.setattr(db, "connect", mock.Mock(side_effect=AssertionError("unexpected DB access")))


@pytest.fixture
def state(monkeypatch):
    record = {
        "equity": 500.0, "available_cash": 200.0, "buying_power": 100.0,
        "open_positions": 3, "entries_today": 0,
        "sector_positions": {}, "sector_value": {}, "correlation_positions": {},
        "day_pnl": 0.0, "drawdown_usd": 0.0,
        "cooldown": {"active": False, "consecutive_losses": 0, "until": None},
    }
    monkeypatch.setattr(risk, "account_state", lambda *a, **kw: record)
    return record


def runtime_entry():
    return risk.check_entry(symbol="META", side="BUY", sector="communication",
                            notional=50.0, planned_risk=2.0,
                            realistic_cost=50.0, session_date="2024-04-01")


def canonical_entry(state):
    # Use the actual stock-fit function and the bridge's policy reference.
    return cb.stock_account_fit(
        policy=cb.STRATEGY_500_POLICY, equity=state["equity"], entry=50.0, stop=48.0,
        buying_power=state["available_cash"], open_positions=state["open_positions"],
        sector="communication", sector_open_positions=state["sector_positions"].get("communication", 0),
        sector_exposure=state["sector_value"].get("communication", 0.0),
        current_daily_loss=max(0.0, -state["day_pnl"]), current_drawdown=state["drawdown_usd"],
    )


def test_fourth_entry_passes_both_real_gates_and_fifth_fails(state):
    assert not runtime_entry()["allow"]
    assert not canonical_entry(state).eligible
    with ConcurrentCapacityChallenger(4):
        assert runtime_entry()["allow"]
        assert canonical_entry(state).eligible
        state["open_positions"] = 4
        result = runtime_entry()
        assert not result["allow"]
        assert {c["name"] for c in result["checks"] if not c["passed"]} == {"max_open_positions"}
        fit = canonical_entry(state)
        assert not fit.eligible
        assert {v.check for v in fit.violations} == {"max_open_positions"}


def test_control_arm_keeps_fourth_entry_blocked(state):
    with ConcurrentCapacityChallenger(3):
        assert not runtime_entry()["allow"]
        assert not canonical_entry(state).eligible


def test_daily_entry_three_remains_blocked(state):
    state["entries_today"] = 2
    with ConcurrentCapacityChallenger(4):
        result = runtime_entry()
        assert not result["allow"]
        assert {c["name"] for c in result["checks"] if not c["passed"]} == {"max_entries_per_day"}


@pytest.mark.parametrize("updates,runtime_check,canonical_check", [
    ({"available_cash": 120.0, "buying_power": 20.0}, "affordable", None),
    ({"available_cash": 100.0, "buying_power": 0.0}, "affordable", "buying_power"),
    ({"sector_positions": {"communication": 1}}, "max_positions_per_sector", "max_positions_per_sector"),
    ({"sector_value": {"communication": 150.0}}, "max_sector_exposure", "max_sector_exposure"),
    ({"correlation_positions": {"megacap_tech": 2}}, "max_correlated", None),
    ({"day_pnl": -10.01}, "daily_loss_limit", "daily_loss_limit"),
    ({"drawdown_usd": 50.01}, "max_drawdown", "max_drawdown"),
    ({"cooldown": {"active": True, "consecutive_losses": 3, "until": "2024-04-02"}}, "loss_cooldown", None),
])
def test_unchanged_controls_still_reject(state, updates, runtime_check, canonical_check):
    state.update(updates)
    with ConcurrentCapacityChallenger(4):
        result = runtime_entry()
        assert not result["allow"]
        assert any(c["name"] == runtime_check and not c["passed"] for c in result["checks"])
        if canonical_check:
            fit = canonical_entry(state)
            assert not fit.eligible
            assert canonical_check in {v.check for v in fit.violations}


@pytest.mark.parametrize("updates,check", [
    ({"planned_risk": 5.01}, "per_trade_risk"),
    ({"notional": 125.01, "realistic_cost": 125.01}, "max_position_notional"),
    ({"side": "SELL"}, "no_shorting"),
])
def test_trade_controls_remain_active(state, updates, check):
    state.update(available_cash=500.0, buying_power=400.0)
    kwargs = dict(symbol="META", side="BUY", sector="communication", notional=50.0,
                  planned_risk=2.0, realistic_cost=50.0, session_date="2024-04-01")
    kwargs.update(updates)
    with ConcurrentCapacityChallenger(4):
        result = risk.check_entry(**kwargs)
        assert not result["allow"]
        assert any(c["name"] == check and not c["passed"] for c in result["checks"])


def test_all_other_fields_and_environment_are_preserved(monkeypatch):
    monkeypatch.setenv("PAPER_MAX_LOSS_PER_TRADE", "4.25")
    original_risk, original_policy = cfg.risk(), cb.STRATEGY_500_POLICY
    original_env = dict(os.environ)
    with ConcurrentCapacityChallenger(4):
        assert dataclasses.asdict(cfg.risk()) == {**dataclasses.asdict(original_risk), "max_open_positions": 4}
        assert dataclasses.asdict(cb.STRATEGY_500_POLICY) == {
            **dataclasses.asdict(original_policy), "max_open_positions": 4}
        assert STRATEGY_500_POLICY is original_policy  # the shared source constant is never replaced
        assert dict(os.environ) == original_env
    assert dict(os.environ) == original_env


@pytest.mark.parametrize("raise_in_body", [False, True])
def test_exact_module_references_restore_after_exit(raise_in_body):
    original_risk, original_policy = cfg.risk, cb.STRATEGY_500_POLICY
    context = ConcurrentCapacityChallenger(4)
    try:
        with context:
            assert cfg.risk is not original_risk
            assert cb.STRATEGY_500_POLICY is not original_policy
            if raise_in_body:
                raise LookupError("test body failure")
    except LookupError:
        assert raise_in_body
    else:
        assert not raise_in_body
    assert cfg.risk is original_risk
    assert cb.STRATEGY_500_POLICY is original_policy
    # Sequential reuse also works after either exit path.
    with context:
        assert cfg.risk().max_open_positions == 4
    assert cfg.risk is original_risk


def test_nested_instances_restore_the_outer_arm():
    original_risk, original_policy = cfg.risk, cb.STRATEGY_500_POLICY
    with ConcurrentCapacityChallenger(4):
        outer_risk, outer_policy = cfg.risk, cb.STRATEGY_500_POLICY
        with ConcurrentCapacityChallenger(3):
            assert cfg.risk().max_open_positions == cb.STRATEGY_500_POLICY.max_open_positions == 3
        assert cfg.risk is outer_risk
        assert cb.STRATEGY_500_POLICY is outer_policy
    assert cfg.risk is original_risk
    assert cb.STRATEGY_500_POLICY is original_policy


def test_reentering_same_instance_fails_without_losing_outer_patches():
    context = ConcurrentCapacityChallenger(4)
    original_risk, original_policy = cfg.risk, cb.STRATEGY_500_POLICY
    with context:
        with pytest.raises(RuntimeError, match="already active"):
            context.__enter__()
        assert cfg.risk().max_open_positions == 4
    assert cfg.risk is original_risk
    assert cb.STRATEGY_500_POLICY is original_policy


def test_partial_setup_failure_restores_first_patch(monkeypatch):
    from research.historical.hist005 import capacity_challenger as module

    original_risk, original_policy = cfg.risk, cb.STRATEGY_500_POLICY
    real_patch_object = module.mock.patch.object

    def fail_second_patch(target, attribute, value):
        if target is cb:
            raise RuntimeError("second patch failed")
        return real_patch_object(target, attribute, value)

    monkeypatch.setattr(module.mock.patch, "object", fail_second_patch)
    with pytest.raises(RuntimeError, match="second patch failed"):
        with ConcurrentCapacityChallenger(4):
            pytest.fail("entered a partially patched context")
    assert cfg.risk is original_risk
    assert cb.STRATEGY_500_POLICY is original_policy


@pytest.mark.parametrize("cap", [0, -1, 2, 5, 4.0, "4", True, None])
def test_unregistered_caps_are_rejected(cap):
    with pytest.raises(ValueError):
        ConcurrentCapacityChallenger(cap)


@pytest.mark.parametrize("path_kind", ["production", "outside_historical_root", "wrong_name"])
def test_unsafe_ledger_is_refused_before_patching(monkeypatch, tmp_path, path_kind):
    original_risk, original_policy = cfg.risk, cb.STRATEGY_500_POLICY
    if path_kind == "production":
        monkeypatch.setattr(db, "_DATA_DIR", str(tmp_path / ".tradingview_mcp_data" / "paper"))
    elif path_kind == "outside_historical_root":
        monkeypatch.setattr(db, "_DATA_DIR", str(tmp_path / "other"))
    else:
        monkeypatch.setenv("PAPER_LEDGER", "unregistered")
    with pytest.raises(ProductionIsolationError):
        with ConcurrentCapacityChallenger(4):
            pytest.fail("unsafe context entered")
    assert cfg.risk is original_risk
    assert cb.STRATEGY_500_POLICY is original_policy


def test_production_host_is_refused_before_patching(monkeypatch):
    original_risk, original_policy = cfg.risk, cb.STRATEGY_500_POLICY
    monkeypatch.setenv("C0_PROFILE", "case1")
    with pytest.raises(ProductionIsolationError):
        with ConcurrentCapacityChallenger(4):
            pytest.fail("production context entered")
    assert cfg.risk is original_risk
    assert cb.STRATEGY_500_POLICY is original_policy
