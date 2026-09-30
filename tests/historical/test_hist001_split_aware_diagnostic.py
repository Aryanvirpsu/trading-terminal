"""HIST-001 Full stage -- diagnostic-only split-awareness patch (see split_aware_diagnostic.py's own module
docstring for the real finding this exists to investigate: AVGO's real 2024-07-15 10-for-1 split, compared
against a pre-split stop, triggered a ~17R artificial loss that then tripped the account's own real
max_drawdown circuit breaker for the rest of HIST-001 Full's evaluation window).

These tests prove the diagnostic patch itself is correct in isolation -- never that it should be applied to
the canonical result, which it is not."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.historical.clock import HistoricalClock
from research.historical.corporate_actions import SplitEvent
from research.historical.datasets.base import DatasetAdapter
from research.historical.execution import HistoricalExecutionContext, isolate_paper_ledger
from research.historical.hist001.split_aware_diagnostic import (
    SplitAwarePositionDiagnostic, confirmed_splits_by_symbol_and_date,
)
from research.historical.provider import HistoricalMarketProvider

import pandas as pd


class _FixedAdapter(DatasetAdapter):
    source_name = "fixture"
    revision = "test"
    volume_trust = "ABSOLUTE"

    def __init__(self, dataset_id, df):
        super().__init__(dataset_id)
        self._df = df

    def fetch(self, symbols, start, end, timeframe):
        return self._df


def test_confirmed_splits_by_symbol_and_date_matches_detect_splits(tmp_path, monkeypatch):
    """The diagnostic's own split detection must be the SAME detect_splits()/is_confirmed() machinery
    HIST-001's own corporate-action disclosure already uses -- not a second, different detector."""
    monkeypatch.setenv("AVDI_HISTORICAL_DATA_DIR", str(tmp_path / "historical"))
    days = pd.bdate_range("2024-01-02", periods=10, tz="UTC")
    closes = [100.0] * 5 + [10.0] * 5           # a clean 10-for-1 split on day index 5
    volumes = [1_000_000.0] * 5 + [12_000_000.0] * 5   # volume moves inversely -- confirms the split
    df = pd.DataFrame({"symbol": "ZZZ", "timestamp": days, "open": closes, "high": [c + 0.5 for c in closes],
                      "low": [c - 0.5 for c in closes], "close": closes, "volume": volumes})
    splits = confirmed_splits_by_symbol_and_date(df)
    key = ("ZZZ", days[5].strftime("%Y-%m-%d"))
    assert key in splits
    assert splits[key] == pytest.approx(10.0)


def test_split_aware_diagnostic_adjusts_an_open_position_on_the_split_date(tmp_path, monkeypatch):
    """A real open position (opened via the real broker.place_order/process_order path) held through a
    confirmed split's own session date has its quantity/avg_entry/stop/target adjusted by the real, already-
    existing lab.paper.fills.apply_split() -- the same math (and the same production function) a real
    corporate-action-aware broker would apply to a real resting position and stop order."""
    monkeypatch.setenv("AVDI_HISTORICAL_DATA_DIR", str(tmp_path / "historical"))
    days = pd.bdate_range("2024-06-01", periods=5, tz="UTC")
    df = pd.DataFrame({"symbol": "ZZZ", "timestamp": days, "open": [100.0] * 5, "high": [101.0] * 5,
                      "low": [99.0] * 5, "close": [100.0] * 5, "volume": [1_000_000.0] * 5})
    _FixedAdapter("split_diag_fixture", df).import_and_store(["ZZZ"], "2024-06-01", "2024-06-10", "1d")

    isolate_paper_ledger("split_diag_test_1")
    clk = HistoricalClock(days[0].to_pydatetime())
    provider = HistoricalMarketProvider(clk, ["split_diag_fixture"])

    split_date = days[2].strftime("%Y-%m-%d")
    splits = {("ZZZ", split_date): 10.0}

    with HistoricalExecutionContext(provider):
        from paper import broker, db

        order = broker.place_order("ZZZ", "BUY", 1.0, order_type="MARKET", intent="entry", strategy="test",
                                   session_date=days[0].strftime("%Y-%m-%d"))
        q = provider.quote("ZZZ")
        broker.process_order(order["order_id"], q)
        # Manually give the position a real stop/target (place_order/process_order alone don't set one).
        db.execute("UPDATE positions SET stop=?, target=? WHERE symbol='ZZZ'", (95.0, 110.0))
        before = db.query_one("SELECT * FROM positions WHERE symbol='ZZZ'")
        assert before["quantity"] == pytest.approx(1.0)
        before_entry = before["avg_entry"]                            # real fill price (includes synthetic spread)

        with SplitAwarePositionDiagnostic(splits) as diag:
            diag._adjust_positions_for_todays_splits(split_date)
            after = db.query_one("SELECT * FROM positions WHERE symbol='ZZZ'")
            assert after["quantity"] == pytest.approx(10.0)          # 1 share -> 10 shares
            assert after["avg_entry"] == pytest.approx(before_entry / 10.0)
            assert after["stop"] == pytest.approx(9.5)               # $95 -> $9.50
            assert after["target"] == pytest.approx(11.0)            # $110 -> $11
            assert len(diag.adjustments_applied) == 1
            assert diag.adjustments_applied[0]["ratio"] == 10.0

        # a symbol/date with NO matching split is untouched
        diag2 = SplitAwarePositionDiagnostic(splits)
        diag2._adjust_positions_for_todays_splits(days[3].strftime("%Y-%m-%d"))
        unchanged = db.query_one("SELECT * FROM positions WHERE symbol='ZZZ'")
        assert unchanged["quantity"] == pytest.approx(10.0)          # still the post-split value, not re-adjusted


def test_split_aware_diagnostic_prevents_the_artificial_stop_trigger(tmp_path, monkeypatch):
    """The concrete scenario this diagnostic exists to test for: a stop set pre-split, compared against a
    genuinely-declined-but-not-crashed POST-split price, must NOT fire once the stop itself has been
    adjusted by the same ratio -- proving the diagnostic actually prevents the artificial trigger, not just
    that it edits numbers."""
    monkeypatch.setenv("AVDI_HISTORICAL_DATA_DIR", str(tmp_path / "historical"))
    days = pd.bdate_range("2024-06-01", periods=5, tz="UTC")
    # day 2 is the split day: raw close crashes from ~100 to ~10 (10-for-1), consistent with a real split,
    # not a real decline. Day 3's close (9.85) is a MILD, genuine dip from the post-split level, its low
    # (9.55) safely clear of the adjusted 9.5 stop -- not an exact boundary collision.
    closes = [100.0, 100.0, 10.0, 9.85, 9.9]
    df = pd.DataFrame({"symbol": "ZZZ", "timestamp": days,
                      "open": closes, "high": [c + 0.3 for c in closes], "low": [c - 0.3 for c in closes],
                      "close": closes, "volume": [1_000_000.0] * 5})
    _FixedAdapter("split_diag_fixture2", df).import_and_store(["ZZZ"], "2024-06-01", "2024-06-10", "1d")

    split_date = days[2].strftime("%Y-%m-%d")
    splits = {("ZZZ", split_date): 10.0}

    isolate_paper_ledger("split_diag_test_2")
    clk = HistoricalClock(days[0].to_pydatetime())
    provider = HistoricalMarketProvider(clk, ["split_diag_fixture2"])

    with HistoricalExecutionContext(provider) as ctx:
        from paper import broker, db

        order = broker.place_order("ZZZ", "BUY", 1.0, order_type="MARKET", intent="entry", strategy="test",
                                   session_date=days[0].strftime("%Y-%m-%d"))
        broker.process_order(order["order_id"], provider.quote("ZZZ"))
        db.execute("UPDATE positions SET stop=?, target=? WHERE symbol='ZZZ'", (95.0, 110.0))  # pre-split terms

        with SplitAwarePositionDiagnostic(splits):
            # advance the clock through the split date -- the diagnostic-patched manage_open_positions
            # adjusts the stop (95.0 -> 9.5) BEFORE the real function compares it against day 2's raw ~10 close.
            clk.set(days[2].to_pydatetime())
            result_split_day = broker.manage_open_positions({"ZZZ": provider.quote("ZZZ")},
                                                             session_date=split_date)
            pos = db.query_one("SELECT * FROM positions WHERE symbol='ZZZ'")
            assert pos["status"] == "open", "the adjusted stop (9.5) must NOT fire against the ~10 post-split low"
            assert pos["stop"] == pytest.approx(9.5)

            # day 3's genuine further 2% dip (9.8, well above the adjusted 9.5 stop) also must not fire
            clk.set(days[3].to_pydatetime())
            broker.manage_open_positions({"ZZZ": provider.quote("ZZZ")}, session_date=days[3].strftime("%Y-%m-%d"))
            pos2 = db.query_one("SELECT * FROM positions WHERE symbol='ZZZ'")
            assert pos2["status"] == "open"
