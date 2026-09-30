"""Integration regression test for the real crash found running `hist001.baseline.run_baseline()` with a
non-standard warmup window (warmup_start=evaluation_start='2024-06-01', evaluation_end='2024-07-20'): NEE's
Champion position closed for real and NEE was then legitimately re-entered later in the same run, but
`baseline.py` never called `EventIdentityTracker.close_event()` -- so the tracker still considered the
FIRST event open, and `open_trade()` raised on the second, different trade_id
(`ValueError: event 'evt_NEE_LONG_202406030915' already has an open trade ... an event may carry at most
one open trade at a time`).

`tests/historical/test_h7_event_identity.py` reproduces and fixes this directly against the tracker.
This file proves the fix at the level that actually matters: `run_baseline()`'s own wiring, using the real,
isolated paper ledger (via `isolate_paper_ledger()`) and the real committed Smoke dataset -- `workflow.
premarket()`/`market_hours()` are stubbed only to script WHEN a symbol is entered and WHEN its position
closes deterministically, without depending on the real Champion gates ever cooperating (they are not the
thing under test here; `event_identity.py`'s wiring into `baseline.py` is)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Importing research.historical.* first (before anything touches sys.path with "lab"/"dashboard"/"src")
# makes sure the real `research` PACKAGE resolves -- `research.historical.avdi_adapter` itself later inserts
# those directories onto sys.path (same trick every lab/*.py module uses), and `dashboard/research.py`
# would otherwise shadow the `research` package if one of those directories came first.
from research.historical.capability import PRICE_TREND_ONLY_V1
from research.historical.hist001.baseline import run_baseline
from research.historical.hist001.build_smoke_dataset import DAILY_DATASET_ID, INTRADAY_DATASET_ID
from research.historical.hist001.schedule import build_multi_day_schedule


@pytest.fixture()
def _scripted_reentry(monkeypatch):
    """Stubs `paper.workflow.premarket`/`market_hours` (module-identity-correct: `from paper import
    workflow`, the SAME flat-namespace object `baseline.py` itself imports and calls) to script: cycle 1
    enters NEE (signal `sig_first`); cycle 2's market-hours processing closes that position for real, in
    the real isolated ledger; a later cycle legitimately re-enters NEE (signal `sig_second`). Every other
    cycle observes nothing."""
    from paper import db as paperdb
    from paper import workflow as wf

    calls = {"n": 0}
    REENTRY_AT = 5

    def fake_premarket(session_date, dry_run=False, *, cycle_id=None, allow_entries=True,
                       session_type="regular", scan_ts=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"state": "ok", "evaluated": [{"symbol": "NEE", "executed": True, "signal_id": "sig_first"}]}
        if calls["n"] == REENTRY_AT:
            return {"state": "ok", "evaluated": [{"symbol": "NEE", "executed": True, "signal_id": "sig_second"}]}
        return {"state": "ok", "evaluated": []}

    def fake_market_hours(session_date, scope="all"):
        if calls["n"] == 2:          # right after the first entry was observed -- NEE's position exits for real
            # positions.signal_id has a real FOREIGN KEY into signals(signal_id) -- a minimal row satisfies it.
            paperdb.execute(
                "INSERT INTO signals(signal_id, created_at, session_date, symbol, strategy, action) "
                "VALUES (?,?,?,?,?,?)",
                ("sig_first", paperdb.utcnow(), "2024-06-03", "NEE", "momentum", "TRADEABLE"))
            paperdb.execute(
                "INSERT INTO positions(position_id, signal_id, symbol, opened_at, closed_at, quantity, "
                "avg_entry, status) VALUES (?,?,?,?,?,?,?,?)",
                ("pos_test_nee_1", "sig_first", "NEE", paperdb.utcnow(), paperdb.utcnow(), 1.0, 65.0, "closed"))
        return {}

    monkeypatch.setattr(wf, "premarket", fake_premarket)
    monkeypatch.setattr(wf, "market_hours", fake_market_hours)
    assert REENTRY_AT > 2                # the reentry must be scripted strictly after the close is recorded
    return calls


def test_baseline_no_longer_crashes_on_a_legitimate_reentry_after_a_real_close(_scripted_reentry):
    cycles = build_multi_day_schedule("2024-06-03", "2024-06-05")     # real trading days, 27 cycles/day
    result = run_baseline(
        run_id="hist001_test_h7_reentry", intraday_dataset_id=INTRADAY_DATASET_ID,
        daily_dataset_id=DAILY_DATASET_ID, universe_sectors=["technology"],
        warmup_start="2024-06-03", evaluation_start="2024-06-03", evaluation_end="2024-06-05",
        capability_fingerprint=PRICE_TREND_ONLY_V1, cycles=cycles)

    # the actual crash this test guards against: run_baseline() completing at all is the headline assertion
    assert result["event_count"] == 2          # two independent pieces of evidence, not one collapsed/crashed
    nee_events = {ev["event_id"] for c in result["cycles"] for ev in c["premarket"].get("evaluated", [])
                 if ev.get("symbol") == "NEE"}
    assert len(nee_events) == 2, nee_events     # the reentry minted a genuinely NEW event_id, not the stale one


def test_standard_case_with_nothing_ever_closing_is_unaffected(monkeypatch):
    """The fix must be a true no-op when no position ever closes (the ordinary case every already-completed
    HIST-001 Smoke/Medium/Full run exercised): reconciliation runs every cycle but finds nothing to close."""
    from paper import workflow as wf

    calls = {"n": 0}

    def fake_premarket(session_date, dry_run=False, *, cycle_id=None, allow_entries=True,
                       session_type="regular", scan_ts=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"state": "ok", "evaluated": [{"symbol": "NEE", "executed": True, "signal_id": "sig_only"}]}
        return {"state": "ok", "evaluated": []}

    def fake_market_hours(session_date, scope="all"):
        return {}

    monkeypatch.setattr(wf, "premarket", fake_premarket)
    monkeypatch.setattr(wf, "market_hours", fake_market_hours)

    cycles = build_multi_day_schedule("2024-06-03", "2024-06-05")
    result = run_baseline(
        run_id="hist001_test_h7_no_reentry", intraday_dataset_id=INTRADAY_DATASET_ID,
        daily_dataset_id=DAILY_DATASET_ID, universe_sectors=["technology"],
        warmup_start="2024-06-03", evaluation_start="2024-06-03", evaluation_end="2024-06-05",
        capability_fingerprint=PRICE_TREND_ONLY_V1, cycles=cycles)

    assert result["event_count"] == 1
