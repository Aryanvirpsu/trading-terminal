"""HIST-002's breakeven_resolver.py: resolve_breakeven_after_plus1r(). Covers clean target/stop exits
(before any +1R trigger), the breakeven-move mechanic itself (trigger bar, THEN a later bar checks the
moved stop), all three ambiguous-bar cases the pre-registration defines (trigger-vs-original-stop always
ambiguous, moved-stop-vs-target always ambiguous, original-stop-vs-target using the SAME gap-open-ordering
policy as outcomes.resolve_outcome()), and formula parity with outcomes._r_multiple."""
import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.historical.clock import HistoricalClock
from research.historical.datasets.base import DatasetAdapter
from research.historical.hist002.breakeven_resolver import (
    EXIT_AMBIGUOUS, EXIT_BREAKEVEN, EXIT_STILL_OPEN, EXIT_STOP, EXIT_TARGET, resolve_breakeven_after_plus1r,
)
from research.historical.outcomes import _r_multiple as real_r_multiple
from research.historical.provider import HistoricalMarketProvider


class _FixedAdapter(DatasetAdapter):
    source_name = "fixture"
    revision = "test"

    def __init__(self, dataset_id, df):
        super().__init__(dataset_id)
        self._df = df

    def fetch(self, symbols, start, end, timeframe):
        return self._df


def _bars(symbol, rows, start="2025-06-02T13:35:00Z"):
    ts = pd.date_range(start, periods=len(rows), freq="5min", tz="UTC")
    return pd.DataFrame({"symbol": symbol, "timestamp": ts,
                        "open": [r[0] for r in rows], "high": [r[1] for r in rows],
                        "low": [r[2] for r in rows], "close": [r[3] for r in rows],
                        "volume": [10000.0] * len(rows)}), ts


@pytest.fixture()
def make_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("AVDI_HISTORICAL_DATA_DIR", str(tmp_path / "historical"))
    counter = {"n": 0}

    def _make(rows, start="2025-06-02T13:35:00Z"):
        counter["n"] += 1
        df, ts = _bars("ZZZ", rows, start=start)
        did = f"h2r_fixture_{counter['n']}"
        _FixedAdapter(did, df).import_and_store(["ZZZ"], "2025-06-02", "2025-06-03", "5m")
        clk = HistoricalClock((ts[-1] + pd.Timedelta(minutes=5)).to_pydatetime())
        provider = HistoricalMarketProvider(clk, [did])
        return provider, ts

    return _make


# entry at 100.0, stop 95.0 (R=5), target 110.0 -- +1R level = 105.0

def test_formula_parity_with_outcomes_r_multiple():
    assert _module_r_multiple_matches()


def _module_r_multiple_matches():
    from research.historical.hist002.breakeven_resolver import _r_multiple as local_r_multiple
    cases = [("LONG", 100.0, 110.0, 5.0), ("LONG", 100.0, 95.0, 5.0), ("SHORT", 100.0, 90.0, 5.0),
            ("SHORT", 100.0, 105.0, 5.0), ("LONG", 100.0, 100.0, 5.0)]
    return all(local_r_multiple(*c) == real_r_multiple(*c) for c in cases)


def test_target_hit_before_any_plus1r_trigger_behaves_like_champion(make_provider):
    rows = [(100, 103, 99, 102), (102, 112, 101, 111)]      # bar 2 hits target (112>=110) without ever hitting 105 first... actually 112>=105 too
    provider, ts = make_provider(rows)
    entry_time = ts[0].to_pydatetime() - dt.timedelta(minutes=5)
    r = resolve_breakeven_after_plus1r(provider, "ZZZ", "LONG", 100.0, 95.0, 110.0, entry_time)
    # bar 2's high (112) clears BOTH +1R (105) and target (110) in the same bar -- trigger-and-target in one
    # bar is not one of the three defined ambiguous cases (target touched, stop not touched, pre-move) --
    # falls through cleanly to a target exit, matching Champion's own real target-hit behavior exactly.
    assert r.resolved and r.exit_reason == EXIT_TARGET
    assert r.exit_price == 110.0
    assert r.variant_net_r == pytest.approx(2.0)


def test_stop_hit_before_any_plus1r_trigger_behaves_like_champion(make_provider):
    rows = [(100, 101, 98, 100.5), (100.5, 102, 94, 95)]
    provider, ts = make_provider(rows)
    entry_time = ts[0].to_pydatetime() - dt.timedelta(minutes=5)
    r = resolve_breakeven_after_plus1r(provider, "ZZZ", "LONG", 100.0, 95.0, 110.0, entry_time)
    assert r.resolved and r.exit_reason == EXIT_STOP
    assert r.exit_price == 95.0
    assert r.variant_net_r == pytest.approx(-1.0)
    assert r.stop_moved_to_entry is False


def test_breakeven_move_then_flat_exit_at_entry(make_provider):
    # bar 1 triggers +1R (high=106 >= 105) without touching the original stop; bar 2 then drops to the
    # moved (breakeven) stop at 100, without touching target -- a real "cut the loss to flat" case.
    rows = [(100, 106, 99, 104), (104, 104.5, 99.5, 100.2)]
    provider, ts = make_provider(rows)
    entry_time = ts[0].to_pydatetime() - dt.timedelta(minutes=5)
    r = resolve_breakeven_after_plus1r(provider, "ZZZ", "LONG", 100.0, 95.0, 110.0, entry_time)
    assert r.plus_1r_time == ts[0].isoformat()
    assert r.stop_moved_to_entry is True
    assert r.resolved and r.exit_reason == EXIT_BREAKEVEN
    assert r.exit_price == 100.0
    assert r.variant_net_r == pytest.approx(0.0)
    assert not r.ambiguous


def test_breakeven_move_then_hits_target(make_provider):
    # bar 1 triggers +1R; bar 2 (the NEXT bar) hits target cleanly without touching breakeven first.
    rows = [(100, 106, 99, 104), (104, 112, 103, 111)]
    provider, ts = make_provider(rows)
    entry_time = ts[0].to_pydatetime() - dt.timedelta(minutes=5)
    r = resolve_breakeven_after_plus1r(provider, "ZZZ", "LONG", 100.0, 95.0, 110.0, entry_time)
    assert r.stop_moved_to_entry is True
    assert r.resolved and r.exit_reason == EXIT_TARGET
    assert r.exit_price == 110.0
    assert r.variant_net_r == pytest.approx(2.0)


def test_ambiguous_when_trigger_and_original_stop_touch_same_bar(make_provider):
    # a single wide bar whose range covers both +1R (105) and the original stop (95), before any move --
    # always ambiguous per the pre-registration, no gap-open exception for THIS case.
    rows = [(100, 106, 94, 100)]
    provider, ts = make_provider(rows)
    entry_time = ts[0].to_pydatetime() - dt.timedelta(minutes=5)
    r = resolve_breakeven_after_plus1r(provider, "ZZZ", "LONG", 100.0, 95.0, 110.0, entry_time)
    assert r.resolved and r.exit_reason == EXIT_AMBIGUOUS
    assert r.ambiguous is True
    assert r.exit_price == 95.0                        # conservative: assumed stop
    assert r.ambiguous_note == "trigger and original stop in one bar"


def test_ambiguous_when_moved_stop_and_target_touch_same_bar(make_provider):
    rows = [(100, 106, 99, 104),           # bar 1: trigger
           (104, 112, 99, 105)]            # bar 2: covers both breakeven (100) and target (110) in range
    provider, ts = make_provider(rows)
    entry_time = ts[0].to_pydatetime() - dt.timedelta(minutes=5)
    r = resolve_breakeven_after_plus1r(provider, "ZZZ", "LONG", 100.0, 95.0, 110.0, entry_time)
    assert r.resolved and r.exit_reason == EXIT_AMBIGUOUS
    assert r.ambiguous is True
    assert r.ambiguous_note == "breakeven level and target in one bar"
    assert r.variant_net_r == pytest.approx(0.0)        # conservative: assumed flat, never the favorable target


def test_original_stop_and_target_same_bar_resolves_via_gap_open_like_outcomes(make_provider):
    # Target (101.0) deliberately set CLOSER than the +1R trigger (105.0) -- an unusual, low reward:risk
    # setup, but the only configuration where "stop and target in one bar, pre-move" is reachable at all
    # without also tripping the (higher-priority) trigger-vs-stop ambiguity: touching a target below the
    # trigger level never implies the trigger was also touched. Opens BELOW the original stop (a real gap
    # down) -- orders this unambiguously as a stop exit, exactly like outcomes.resolve_outcome()'s own
    # gap-open policy, even though the same bar's high also reaches target later.
    rows = [(90, 102, 88, 96)]
    provider, ts = make_provider(rows)
    entry_time = ts[0].to_pydatetime() - dt.timedelta(minutes=5)
    r = resolve_breakeven_after_plus1r(provider, "ZZZ", "LONG", 100.0, 95.0, 101.0, entry_time)
    assert r.exit_reason == EXIT_STOP
    assert r.ambiguous is False


def test_original_stop_and_target_same_bar_true_straddle_is_ambiguous(make_provider):
    # Same low-target setup as above (target below the +1R trigger, so this case is reachable at all).
    # Open is BETWEEN stop and target, but the bar's range straddles both -- genuinely ambiguous, no gap
    # to resolve it (matches outcomes.resolve_outcome()'s own identical test shape).
    rows = [(98, 102, 93, 96)]
    provider, ts = make_provider(rows)
    entry_time = ts[0].to_pydatetime() - dt.timedelta(minutes=5)
    r = resolve_breakeven_after_plus1r(provider, "ZZZ", "LONG", 100.0, 95.0, 101.0, entry_time)
    assert r.exit_reason == EXIT_AMBIGUOUS
    assert r.ambiguous is True
    assert r.ambiguous_note == "original stop and target in one bar (pre-move)"


def test_still_open_when_data_runs_out_without_any_exit(make_provider):
    rows = [(100, 102, 99, 101), (101, 103, 100, 102)]
    provider, ts = make_provider(rows)
    entry_time = ts[0].to_pydatetime() - dt.timedelta(minutes=5)
    r = resolve_breakeven_after_plus1r(provider, "ZZZ", "LONG", 100.0, 95.0, 110.0, entry_time)
    assert not r.resolved
    assert r.exit_reason == EXIT_STILL_OPEN
    assert r.variant_net_r is None
