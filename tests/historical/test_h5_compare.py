"""H5 steps 7-9: the comparison matrix categorizes every reference symbol with a named reason, never
leaves a row unexplained without saying so, and the two summary layers (mechanical/decision equivalence)
reflect the real replay output."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.historical.known_forward import reference as ref
from research.historical.known_forward.compare import (
    DIFFERENCE_REASONS, build_comparison_matrix, compare_symbol, decision_equivalence_summary,
    mechanical_equivalence_summary,
)
from research.historical.known_forward.schedule import SCHEDULE


@pytest.fixture(scope="module")
def replay_result():
    from research.historical.known_forward.replay import run
    return run(run_id="h5_compare_test_run", cycles=SCHEDULE)


def test_every_reference_symbol_gets_a_categorized_row(replay_result):
    rows = build_comparison_matrix(replay_result)
    assert len(rows) == len(ref.REFERENCE_EVENTS)
    for r in rows:
        assert r.difference_reason in DIFFERENCE_REASONS
        assert r.notes                                  # never a silent/empty categorization


def test_no_unexplained_or_replay_bug_rows_today(replay_result):
    """This is the report's own central claim -- pin it down so a future change that silently introduces
    an unexplained divergence is caught immediately."""
    rows = build_comparison_matrix(replay_result)
    bad = [r for r in rows if r.difference_reason in ("UNKNOWN", "REPLAY_BUG")]
    assert bad == [], [r.to_dict() for r in bad]


def test_dell_and_meta_are_classified_as_capability_difference(replay_result):
    dell = compare_symbol(replay_result, "DELL")
    meta = compare_symbol(replay_result, "META")
    assert dell.difference_reason == "CAPABILITY_DIFFERENCE"
    assert meta.difference_reason == "CAPABILITY_DIFFERENCE"
    assert dell.forward_action == "entered" and dell.historical_action != "entered"


def test_symbol_not_in_reference_is_unknown(replay_result):
    row = compare_symbol(replay_result, "ZZZZNOTREAL")
    assert row.difference_reason == "UNKNOWN"


def test_mechanical_equivalence_summary_reports_all_cycles_completed(replay_result):
    summary = mechanical_equivalence_summary(replay_result)
    assert summary["cycles_run"] == len(SCHEDULE) == 27
    assert summary["cycles_completed_ok"] == 27
    assert summary["no_exception_raised"] is True


def test_decision_equivalence_summary_matches_the_matrix(replay_result):
    rows = build_comparison_matrix(replay_result)
    summary = decision_equivalence_summary(rows)
    assert summary["rows"] == len(rows)
    assert sum(summary["by_difference_reason"].values()) == len(rows)
    assert set(summary["capability_difference_symbols"]) == {
        r.symbol for r in rows if r.difference_reason == "CAPABILITY_DIFFERENCE"}
    assert summary["unexplained"] == []
