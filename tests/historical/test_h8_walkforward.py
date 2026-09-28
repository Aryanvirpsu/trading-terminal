"""H8 acceptance: walk-forward folds are structurally ordered (development -> validation -> ... ->
exactly one holdout, last), every period access is recorded with a purpose, and the holdout cannot be
inspected for a development purpose without an explicit, permanent acknowledgement.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.historical.walkforward import (
    DEVELOPMENT, HOLDOUT, VALIDATION, HoldoutContaminationError, Period, WalkForwardPlan,
    alternating_dev_validation_plan,
)


def _plan():
    return alternating_dev_validation_plan(
        folds=[("2024-01-01", "2024-07-01", "2024-07-01", "2024-10-01"),
              ("2024-10-01", "2025-04-01", "2025-04-01", "2025-07-01")],
        holdout=("2025-07-01", "2026-01-01"))


def test_plan_requires_exactly_one_holdout():
    with pytest.raises(ValueError):
        WalkForwardPlan([Period("d1", DEVELOPMENT, "2024-01-01", "2024-06-01")])       # no holdout at all
    with pytest.raises(ValueError):
        WalkForwardPlan([Period("h1", HOLDOUT, "2024-01-01", "2024-06-01"),
                        Period("h2", HOLDOUT, "2024-06-01", "2025-01-01")])           # two holdouts


def test_holdout_must_be_the_last_period():
    with pytest.raises(ValueError):
        WalkForwardPlan([Period("holdout", HOLDOUT, "2024-01-01", "2024-06-01"),
                        Period("d1", DEVELOPMENT, "2024-06-01", "2025-01-01")])


def test_period_names_must_be_unique():
    with pytest.raises(ValueError):
        WalkForwardPlan([Period("x", DEVELOPMENT, "2024-01-01", "2024-06-01"),
                        Period("x", HOLDOUT, "2024-06-01", "2025-01-01")])


def test_period_start_must_precede_end():
    with pytest.raises(ValueError):
        Period("bad", DEVELOPMENT, "2024-06-01", "2024-01-01")


def test_convenience_builder_produces_the_expected_period_sequence():
    plan = _plan()
    kinds = [(p.name, p.kind) for p in plan.periods]
    assert kinds == [
        ("development_1", DEVELOPMENT), ("validation_1", VALIDATION),
        ("development_2", DEVELOPMENT), ("validation_2", VALIDATION),
        ("holdout", HOLDOUT),
    ]


def test_development_and_validation_inspection_is_always_recorded():
    plan = _plan()
    r = plan.inspect("development_1", purpose="development_decision", note="tuned quality threshold")
    assert plan.was_ever_inspected("development_1")
    assert plan.history_for("development_1") == [r]
    assert not plan.holdout_contaminated


def test_holdout_refuses_a_development_purpose_via_plain_inspect():
    plan = _plan()
    with pytest.raises(HoldoutContaminationError):
        plan.inspect("holdout", purpose="development_decision")
    assert not plan.holdout_contaminated                 # the refusal itself must not contaminate anything


def test_holdout_allows_final_report_via_plain_inspect_without_contaminating():
    plan = _plan()
    plan.inspect("holdout", purpose="final_report", note="HIST-001 baseline report")
    assert not plan.holdout_contaminated


def test_inspect_holdout_requires_the_explicit_flag():
    plan = _plan()
    with pytest.raises(HoldoutContaminationError):
        plan.inspect_holdout(purpose="development_decision", note="peeking",
                             i_understand_this_ends_the_holdout=False)
    assert not plan.holdout_contaminated


def test_inspect_holdout_with_the_flag_permanently_contaminates():
    plan = _plan()
    plan.inspect_holdout(purpose="development_decision", note="accidentally ran optimizer over full range",
                         i_understand_this_ends_the_holdout=True)
    assert plan.holdout_contaminated
    status = plan.holdout_status()
    assert status["contaminated"] is True
    assert status["contaminated_at"] is not None
    assert "development_decision" in status["contamination_reason"]

    # once contaminated, it STAYS contaminated -- a later "final_report" access changes nothing
    plan.inspect("holdout", purpose="final_report")
    assert plan.holdout_contaminated


def test_full_history_and_summary_capture_every_access():
    plan = _plan()
    plan.inspect("development_1", purpose="development_decision")
    plan.inspect("validation_1", purpose="validation_scoring")
    summary = plan.summary()
    assert len(summary["inspections"]) == 2
    assert summary["holdout"]["contaminated"] is False
    assert len(plan.full_history()) == 2


def test_unknown_period_name_raises():
    plan = _plan()
    with pytest.raises(KeyError):
        plan.inspect("nonexistent", purpose="development_decision")
