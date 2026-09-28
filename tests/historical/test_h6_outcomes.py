"""H6 acceptance: the causal outcome engine resolves target/stop/ambiguous/still-open exits using only
bars strictly after the entry, never favors AVDI on an ambiguous same-bar touch, tracks MFE/MAE and the
+1R/-1R/target/stop timestamps correctly, and can resolve a HYPOTHETICAL (never-entered) candidate too.
"""
import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.historical.clock import HistoricalClock
from research.historical.datasets.base import DatasetAdapter
from research.historical.outcomes import (
    EXIT_AMBIGUOUS, EXIT_NO_DATA, EXIT_STILL_OPEN, EXIT_STOP, EXIT_TARGET, resolve_hypothetical,
    resolve_outcome,
)
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
    """rows: list of (open, high, low, close), one per 5-minute bar."""
    ts = pd.date_range(start, periods=len(rows), freq="5min", tz="UTC")
    return pd.DataFrame({"symbol": symbol, "timestamp": ts,
                         "open": [r[0] for r in rows], "high": [r[1] for r in rows],
                         "low": [r[2] for r in rows], "close": [r[3] for r in rows],
                         "volume": [10000.0] * len(rows)}), ts


@pytest.fixture()
def make_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("AVDI_HISTORICAL_DATA_DIR", str(tmp_path / "historical"))
    counter = {"n": 0}

    def _make(rows, start="2025-06-02T13:35:00Z", clock_at_end=True):
        counter["n"] += 1
        df, ts = _bars("ZZZ", rows, start=start)
        did = f"h6_fixture_{counter['n']}"
        _FixedAdapter(did, df).import_and_store(["ZZZ"], "2025-06-02", "2025-06-03", "5m")
        clk_time = (ts[-1] + pd.Timedelta(minutes=5)).to_pydatetime() if clock_at_end else ts[0].to_pydatetime()
        clk = HistoricalClock(clk_time)
        provider = HistoricalMarketProvider(clk, [did])
        return provider, ts

    return _make


# entry bar itself, before any of the rows below: entry at 100.0, stop 95.0, target 110.0 (R=5)

def test_target_hit_cleanly(make_provider):
    rows = [(100, 101, 99, 100.5), (100.5, 105, 100, 104), (104, 112, 103, 111)]     # bar 3 hits target (112>=110)
    provider, ts = make_provider(rows)
    entry_time = ts[0].to_pydatetime() - dt.timedelta(minutes=5)          # strictly before the first bar
    r = resolve_outcome(provider, "ZZZ", entry_time, 100.0, 95.0, 110.0, direction="LONG")
    assert r.resolved and r.exit_reason == EXIT_TARGET
    assert r.exit_price == 110.0
    assert r.net_r == pytest.approx(2.0)             # (110-100)/5
    assert r.ambiguous is False
    assert r.target_time == ts[2].isoformat()


def test_stop_hit_cleanly(make_provider):
    rows = [(100, 101, 98, 100.5), (100.5, 102, 94, 95)]                  # bar 2 hits stop (94<=95... wait check)
    provider, ts = make_provider(rows)
    entry_time = ts[0].to_pydatetime() - dt.timedelta(minutes=5)
    r = resolve_outcome(provider, "ZZZ", entry_time, 100.0, 95.0, 110.0, direction="LONG")
    assert r.resolved and r.exit_reason == EXIT_STOP
    assert r.exit_price == 95.0
    assert r.net_r == pytest.approx(-1.0)
    assert r.ambiguous is False


def test_ambiguous_when_stop_and_target_both_touched_same_bar_no_gap(make_provider):
    # A single wide bar whose open is BETWEEN stop and target, but whose high/low straddle both.
    rows = [(102, 112, 93, 100)]
    provider, ts = make_provider(rows)
    entry_time = ts[0].to_pydatetime() - dt.timedelta(minutes=5)
    r = resolve_outcome(provider, "ZZZ", entry_time, 100.0, 95.0, 110.0, direction="LONG")
    assert r.resolved and r.exit_reason == EXIT_AMBIGUOUS
    assert r.ambiguous is True
    assert r.exit_price == 95.0                       # conservative: assumed stop, never the favorable target
    assert r.net_r == pytest.approx(-1.0)


def test_gap_open_past_stop_resolves_to_stop_not_ambiguous(make_provider):
    # Opens BELOW stop (gapped through it) -- the open itself orders this: stop first, unambiguously,
    # even though the same bar's high also reaches target later.
    rows = [(90, 112, 88, 105)]
    provider, ts = make_provider(rows)
    entry_time = ts[0].to_pydatetime() - dt.timedelta(minutes=5)
    r = resolve_outcome(provider, "ZZZ", entry_time, 100.0, 95.0, 110.0, direction="LONG")
    assert r.exit_reason == EXIT_STOP
    assert r.ambiguous is False


def test_gap_open_past_target_resolves_to_target_not_ambiguous(make_provider):
    rows = [(115, 120, 93, 118)]        # opens ABOVE target, also dips to touch stop later in the same bar
    provider, ts = make_provider(rows)
    entry_time = ts[0].to_pydatetime() - dt.timedelta(minutes=5)
    r = resolve_outcome(provider, "ZZZ", entry_time, 100.0, 95.0, 110.0, direction="LONG")
    assert r.exit_reason == EXIT_TARGET
    assert r.ambiguous is False


def test_still_open_when_data_runs_out_before_either_level(make_provider):
    rows = [(100, 103, 99, 102), (102, 106, 101, 105)]
    provider, ts = make_provider(rows)
    entry_time = ts[0].to_pydatetime() - dt.timedelta(minutes=5)
    r = resolve_outcome(provider, "ZZZ", entry_time, 100.0, 95.0, 110.0, direction="LONG")
    assert not r.resolved
    assert r.exit_reason == EXIT_STILL_OPEN
    assert r.net_r is None
    assert r.mfe_r == pytest.approx((106 - 100) / 5)


def test_no_data_at_all_after_entry(make_provider):
    rows = [(100, 101, 99, 100.5)]
    provider, ts = make_provider(rows)
    entry_time = ts[-1].to_pydatetime() + dt.timedelta(minutes=5)     # entry AFTER the only bar
    r = resolve_outcome(provider, "ZZZ", entry_time, 100.0, 95.0, 110.0, direction="LONG")
    assert r.exit_reason == EXIT_NO_DATA
    assert r.bars_used == 0


def test_entry_bar_itself_is_excluded(make_provider):
    """A bar timestamped EXACTLY at entry_time must never count as an outcome bar -- only strictly after."""
    rows = [(100, 200, 50, 100)]        # would trivially "hit" both stop and target if counted
    provider, ts = make_provider(rows)
    entry_time = ts[0].to_pydatetime()               # exactly the bar's own timestamp
    r = resolve_outcome(provider, "ZZZ", entry_time, 100.0, 95.0, 110.0, direction="LONG")
    assert r.exit_reason == EXIT_NO_DATA              # excluded -> no bars after entry at all


def test_mfe_mae_and_1r_timestamps_tracked_correctly(make_provider):
    # Note: with r_per_share = |entry-stop|, "-1R" and the stop price are mathematically the SAME level by
    # construction (stop = entry - 1R for a LONG) -- -1R can never be reached without also touching the
    # stop in the same instant. So bar 3 below resolves the trade (stop hit) at the same bar minus_1r_time
    # fires; this test's job is to prove MFE/+1R from the EARLIER bars are captured correctly before that.
    rows = [
        (100, 104, 99, 103),     # +0.8R high
        (103, 106, 98, 99),      # +1.2R high (>=1R -> plus_1r_time), -0.4R low
        (99, 100, 94, 96),       # -1.2R low -> also touches stop (95) in the same bar
    ]
    provider, ts = make_provider(rows)
    entry_time = ts[0].to_pydatetime() - dt.timedelta(minutes=5)
    r = resolve_outcome(provider, "ZZZ", entry_time, 100.0, 95.0, 110.0, direction="LONG")
    assert r.mfe_r == pytest.approx((106 - 100) / 5)
    assert r.plus_1r_time == ts[1].isoformat()
    assert r.minus_1r_time == ts[2].isoformat()
    assert r.stop_time == ts[2].isoformat()             # -1R and the stop coincide by construction
    assert r.resolved and r.exit_reason == EXIT_STOP


def test_short_direction_inverts_target_and_stop_sense(make_provider):
    # SHORT: entry 100, stop 105 (above), target 90 (below). Bar hits target (low<=90).
    rows = [(99, 101, 89, 90)]
    provider, ts = make_provider(rows)
    entry_time = ts[0].to_pydatetime() - dt.timedelta(minutes=5)
    r = resolve_outcome(provider, "ZZZ", entry_time, 100.0, 105.0, 90.0, direction="SHORT")
    assert r.exit_reason == EXIT_TARGET
    assert r.net_r == pytest.approx(2.0)               # (100-90)/5


def test_invalid_direction_rejected(make_provider):
    provider, ts = make_provider([(100, 101, 99, 100)])
    with pytest.raises(ValueError):
        resolve_outcome(provider, "ZZZ", ts[0].to_pydatetime(), 100.0, 95.0, 110.0, direction="SIDEWAYS")


# ── resolve_hypothetical ────────────────────────────────────────────────────────────────────────────────

def test_resolve_hypothetical_uses_evaluate_result_fields(make_provider):
    rows = [(100, 101, 99, 100.5), (100.5, 112, 100, 111)]
    provider, ts = make_provider(rows)
    entry_time = ts[0].to_pydatetime() - dt.timedelta(minutes=5)
    fake_result = {"price": 100.0, "stop": 95.0, "target": 110.0, "direction": "LONG", "decision": "TRADEABLE"}
    r = resolve_hypothetical(provider, "ZZZ", fake_result, entry_time)
    assert r is not None
    assert r.hypothetical is True
    assert r.exit_reason == EXIT_TARGET


def test_resolve_hypothetical_returns_none_without_valid_levels(make_provider):
    provider, ts = make_provider([(100, 101, 99, 100)])
    fake_result = {"price": None, "decision": "REJECT"}
    assert resolve_hypothetical(provider, "ZZZ", fake_result, ts[0].to_pydatetime()) is None
