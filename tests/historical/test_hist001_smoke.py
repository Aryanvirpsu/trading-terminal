"""HIST-001 smoke-stage acceptance: real fabhaus data (pinned revision, streamed+filtered, never the full
478GB corpus), the real Champion decision+execution stack run over a real month, determinism verified by
an actual repeated run, and the finding that a single month cannot cross the 55-daily-bar trend floor
confirmed directly against the real committed dataset (not asserted from memory).
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.historical.hist001.analysis import (
    capacity_opportunity_cost, ch001_shadow, choice_events, funnel_summary, portfolio_metrics,
    survivorship_bias_note,
)
from research.historical.hist001.baseline import run_baseline
from research.historical.hist001.build_smoke_dataset import DAILY_DATASET_ID, INTRADAY_DATASET_ID
from research.historical.hist001.schedule import build_multi_day_schedule, trading_days
from research.historical.manifest import verify_manifest


# ── schedule ─────────────────────────────────────────────────────────────────────────────────────────────

def test_january_2024_trading_days_excludes_weekends_and_mlk_day():
    days = trading_days("2024-01-01", "2024-01-31")
    assert len(days) == 21
    assert "2024-01-15" not in days          # MLK Day (observed) -- real holiday calendar, not just weekdays
    assert "2024-01-01" not in days          # New Year's Day
    assert "2024-01-06" not in days          # a Saturday
    assert "2024-01-02" in days              # the first real trading day


def test_multi_day_schedule_has_27_cycles_per_trading_day():
    cycles = build_multi_day_schedule("2024-01-01", "2024-01-31")
    assert len(cycles) == 21 * 27
    assert cycles[0].session_type == "premarket_prep"
    assert cycles[0].et_time.strftime("%H:%M") == "09:15"


# ── the committed smoke dataset ─────────────────────────────────────────────────────────────────────────

def test_smoke_datasets_are_committed_and_verify():
    for did in (INTRADAY_DATASET_ID, DAILY_DATASET_ID):
        v = verify_manifest(did)
        assert v["ok"], v


def test_smoke_dataset_manifest_provenance_is_complete():
    from research.historical.manifest import load_manifest

    m = load_manifest(INTRADAY_DATASET_ID)
    assert m.hf_repository == "fabhaus/equities_5m_stockprices"
    assert m.hf_revision == "f17c0b0c3cf6a455994f93d6a85e76274172ab03"
    assert m.volume_trust == "RELATIVE_ONLY"
    assert m.upstream_sha256                            # the real ETag, recorded


def test_no_symbol_has_55_daily_bars_in_a_single_month():
    """The finding the smoke-stage report is built around, confirmed directly against the real data: a
    single calendar month cannot cross strategies._bars()'s own 55-bar trend floor, so scan() structurally
    cannot produce a single candidate this stage -- not a bug, a property of the window length."""
    from research.historical.datasets.base import load_parquet

    df = load_parquet(DAILY_DATASET_ID)
    counts = df.groupby("symbol").size()
    assert (counts < 55).all(), counts.to_dict()
    assert counts.max() <= 25                             # January 2024 has 21 trading days (+ some slack)


# ── the real replay, determinism ────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def smoke_result():
    return run_baseline(run_id="hist001_test_smoke_1", intraday_dataset_id=INTRADAY_DATASET_ID,
                        daily_dataset_id=DAILY_DATASET_ID, universe_sectors=["technology"],
                        start="2024-01-01", end="2024-01-31")


def test_smoke_replay_runs_all_cycles_with_no_exception(smoke_result):
    assert len(smoke_result["trading_days"]) == 21
    assert len(smoke_result["cycles"]) == 567
    assert all(c["premarket"]["state"] == "ok" for c in smoke_result["cycles"])


def test_smoke_replay_produces_zero_candidates_consistent_with_the_daily_bar_finding(smoke_result):
    assert smoke_result["event_count"] == 0
    assert len(smoke_result["signals"]) == 0
    assert len(smoke_result["orders"]) == 0


def test_smoke_replay_is_deterministic_across_two_independent_runs():
    r1 = run_baseline(run_id="hist001_test_determinism_a", intraday_dataset_id=INTRADAY_DATASET_ID,
                      daily_dataset_id=DAILY_DATASET_ID, universe_sectors=["technology"],
                      start="2024-01-01", end="2024-01-10")
    r2 = run_baseline(run_id="hist001_test_determinism_b", intraday_dataset_id=INTRADAY_DATASET_ID,
                      daily_dataset_id=DAILY_DATASET_ID, universe_sectors=["technology"],
                      start="2024-01-01", end="2024-01-10")
    assert r1["event_count"] == r2["event_count"]
    assert len(r1["signals"]) == len(r2["signals"])
    assert r1["account"]["equity"] == r2["account"]["equity"]
    assert [c["premarket"]["state"] for c in r1["cycles"]] == [c["premarket"]["state"] for c in r2["cycles"]]


def test_smoke_replay_starts_from_a_fresh_500_account(smoke_result):
    assert smoke_result["account"]["starting_equity"] == 500.0


# ── analysis functions on real (trivial) smoke output ──────────────────────────────────────────────────

def test_funnel_summary_on_real_zero_decision_output(smoke_result):
    f = funnel_summary(smoke_result)
    assert f["cycles_completed_ok"] == 567
    assert f["raw_observations"] == 0
    assert f["independent_events"] == 0


def test_survivorship_note_reports_full_coverage_for_the_smoke_universe(smoke_result):
    note = survivorship_bias_note(
        ["AAPL", "ADBE", "AMD", "AVGO", "CRM", "CSCO", "DELL", "MSFT", "NVDA", "ORCL"],
        smoke_result["manifests"]["daily"]["symbols"])
    assert note["symbols_missing_from_source"] == []
    assert "SURVIVORSHIP BIAS PRESENT" in note["verdict"]


def test_capacity_choice_ch001_portfolio_all_handle_the_trivial_case_cleanly(smoke_result):
    assert capacity_opportunity_cost(smoke_result, None) == []
    assert choice_events(smoke_result) == []
    shadow = ch001_shadow(smoke_result)
    assert shadow["independent_trades_reaching_plus_1r"] == 0
    metrics = portfolio_metrics(smoke_result)
    assert metrics["starting_equity"] == 500.0
    assert metrics["resolved_trades"] == 0
