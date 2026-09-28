"""H5.5 step "Re-run H5": the load-bearing REPLAY BEHAVIOR test (not static arithmetic) -- rerunning the
real KNOWN_FORWARD_2026_09_25 replay under PRICE_TREND_MACRO_V1, with the REAL, committed FRED-sourced
macro dataset (no network access at test time), actually crosses the data_quality floor and produces real
TRADEABLE decisions the unchanged Champion code would not otherwise reach.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.historical.capability import PRICE_TREND_MACRO_V1, PRICE_TREND_ONLY_V1
from research.historical.known_forward.compare import build_comparison_matrix
from research.historical.known_forward.replay import run
from research.historical.known_forward.schedule import SCHEDULE
from research.historical.macro import load_macro_history, verify_macro_manifest

MACRO_DATASET_ID = "KNOWN_FORWARD_2026_09_25_MACRO"


def test_real_macro_dataset_is_committed_and_verifies():
    v = verify_macro_manifest(MACRO_DATASET_ID)
    assert v["ok"], v
    history, manifest = load_macro_history(MACRO_DATASET_ID)
    assert len(history) > 0
    assert manifest.source == "FRED"
    assert set(manifest.series_ids) == {"DGS10", "DGS2", "VIXCLS", "FEDFUNDS"}


def test_rerun_under_price_trend_macro_v1_crosses_the_floor_for_dell_meta_tmo():
    """The directive's own success criterion, checked against real replay behavior: DELL and META (the
    two real forward entries) and TMO (the third symbol H5 found capped by the SAME data_quality ceiling)
    all reach TRADEABLE once real historical macro is replayed -- an unchanged Champion, unchanged
    thresholds, real data crossing a real gate."""
    history, _m = load_macro_history(MACRO_DATASET_ID)

    result_old = run(run_id="h55_test_rerun_old", macro_history=None)
    result_new = run(run_id="h55_test_rerun_new", macro_history=history)

    assert result_old["capability_fingerprint"] == PRICE_TREND_ONLY_V1
    assert result_new["capability_fingerprint"] == PRICE_TREND_MACRO_V1

    rows_old = {r.symbol: r for r in build_comparison_matrix(result_old)}
    rows_new = {r.symbol: r for r in build_comparison_matrix(result_new)}

    for sym in ("DELL", "META", "TMO"):
        assert rows_old[sym].historical_classification == "MONITOR", (sym, rows_old[sym])
        assert rows_old[sym].difference_reason == "CAPABILITY_DIFFERENCE"
        assert rows_new[sym].historical_classification == "TRADEABLE", (sym, rows_new[sym])
        assert rows_new[sym].difference_reason == "MATCH"


def test_rerun_zero_unexplained_rows_under_the_new_fingerprint_too():
    """Every row must still land on a NAMED reason -- adding a real evidence family must not introduce a
    silent, unexplained divergence, even where it changes a classification (see the module-level note on
    PRICE_DATA_DIFFERENCE covering a NEW-under-macro TRADEABLE too)."""
    history, _m = load_macro_history(MACRO_DATASET_ID)
    result_new = run(run_id="h55_test_rerun_unexplained_check", macro_history=history)
    rows = build_comparison_matrix(result_new)
    bad = [r for r in rows if r.difference_reason == "UNKNOWN"]
    assert bad == [], [r.to_dict() for r in bad]


def test_data_quality_actually_crosses_0_55_in_real_replay_not_just_static_math():
    """Directive: 'do not treat static arithmetic as proof of behavior.' This calls the real evaluate()
    through the real HistoricalExecutionContext with the real macro dataset and reads data_quality directly
    off its output -- not lab/data_quality.py's functions in isolation."""
    import datetime as dt
    from zoneinfo import ZoneInfo

    from research.historical.clock import HistoricalClock
    from research.historical.execution import HistoricalExecutionContext, isolate_paper_ledger
    from research.historical.known_forward.build_dataset import DAILY_DATASET_ID, INTRADAY_DATASET_ID
    from research.historical.known_forward.reference import event_by_symbol
    from research.historical.known_forward.replay import _sectors
    from research.historical.manifest import load_manifest
    from research.historical.provider import HistoricalMarketProvider

    history, _m = load_macro_history(MACRO_DATASET_ID)
    isolate_paper_ledger("h55_test_real_dq_check")
    dm = load_manifest(DAILY_DATASET_ID)
    im = load_manifest(INTRADAY_DATASET_ID)
    event = event_by_symbol("DELL")
    hh, mm = int(event.first_seen_cycle_et[:2]), int(event.first_seen_cycle_et[3:])
    clk = HistoricalClock(dt.datetime(2026, 9, 25, hh, mm, tzinfo=ZoneInfo("America/New_York")))
    dp = HistoricalMarketProvider(clk, [DAILY_DATASET_ID], volume_trust=dm.volume_trust)
    ep = HistoricalMarketProvider(clk, [INTRADAY_DATASET_ID], volume_trust=im.volume_trust)

    with HistoricalExecutionContext(dp, execution_provider=ep, neutral_sector=_sectors()) as ctx:
        without_macro = ctx.evaluate("DELL", direction="LONG", balance=504.66)
    with HistoricalExecutionContext(dp, execution_provider=ep, neutral_sector=_sectors(),
                                    macro_history=history) as ctx2:
        with_macro = ctx2.evaluate("DELL", direction="LONG", balance=504.66)

    assert without_macro["data_quality"]["overall"] < 0.55
    assert with_macro["data_quality"]["overall"] >= 0.55
