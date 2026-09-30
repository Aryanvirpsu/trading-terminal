"""Performance fix (perf/historical-fast-v2): `HistoricalAVDIContext._patched_scan()` memoizes
`strategies.scan()`'s entire real, unmodified return value once per ET calendar date. Profiled as ~65% of a
HIST-001 Full replay cycle's own wall time (`strategies.scan()` calling `_bars()` once per universe symbol,
~27 times/day, for a provably identical answer each time within a day -- see `_patched_scan`'s own
docstring for why this is safe: every input `scan()` reads in this patched context is day-invariant).

Proves: (1) two calls within the SAME calendar date hit the cache and return the exact same object,
(2) the cached result is byte-identical to what an uncached call produces (parity, not just "doesn't
crash"), (3) advancing the clock to a NEW calendar date invalidates the cache and recomputes."""
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.historical.avdi_adapter import HistoricalAVDIContext
from research.historical.clock import HistoricalClock
from research.historical.datasets.base import DatasetAdapter
from research.historical.provider import HistoricalMarketProvider

ET = ZoneInfo("America/New_York")


class _FixedAdapter(DatasetAdapter):
    source_name = "fixture"
    revision = "test"
    volume_trust = "ABSOLUTE"

    def __init__(self, dataset_id, df):
        super().__init__(dataset_id)
        self._df = df

    def fetch(self, symbols, start, end, timeframe):
        return self._df


def _uptrend_daily(symbol, n=70, base=100.0, drift=0.6):
    """Real technology-sector symbols (AAPL/MSFT), enough daily bars (>=55) for `_bars()`'s own SMA50
    floor, a genuine uptrend so `scan()` actually produces non-empty candidates/finalists -- exercising the
    cache against a REAL, non-trivial answer, not just an empty one."""
    days = pd.bdate_range("2024-01-02", periods=n, tz="UTC")
    closes = [base + drift * k + 1.5 * ((k * 7) % 5) for k in range(n)]
    closes_et = (pd.to_datetime(days.date).tz_localize(ET) + pd.Timedelta(hours=16)).tz_convert("UTC")
    return pd.DataFrame({"symbol": symbol, "timestamp": closes_et, "open": [c - 0.5 for c in closes],
                        "high": [c + 2.0 for c in closes], "low": [c - 2.0 for c in closes], "close": closes,
                        "volume": [5_000_000.0 + 10_000.0 * k for k in range(n)]})


@pytest.fixture()
def fixture_dataset(tmp_path, monkeypatch):
    monkeypatch.setenv("AVDI_HISTORICAL_DATA_DIR", str(tmp_path / "historical"))
    df = pd.concat([_uptrend_daily("AAPL"), _uptrend_daily("MSFT", base=200.0, drift=0.9)], ignore_index=True)
    _FixedAdapter("h3_scan_cache_fixture", df).import_and_store(["AAPL", "MSFT"], "2024-01-01", "2024-05-01", "1d")
    return "h3_scan_cache_fixture", df


def test_scan_cache_hits_within_the_same_calendar_date_and_matches_the_uncached_answer(fixture_dataset):
    dataset_id, _ = fixture_dataset
    # far enough in to clear the 55-bar floor for both symbols; ascending clock order (the clock refuses to
    # move backward, correctly -- see HistoricalClock.set()).
    clk = HistoricalClock(pd.Timestamp("2024-04-02T09:35:00", tz="UTC").to_pydatetime())
    provider = HistoricalMarketProvider(clk, [dataset_id])

    with HistoricalAVDIContext(provider, neutral_sector="technology") as ctx:
        from paper import strategies

        first = strategies.scan()                                 # first cycle of the day -- real computation
        clk.set(pd.Timestamp("2024-04-02T15:55:00", tz="UTC").to_pydatetime())
        second = strategies.scan()                                # a later cycle, SAME calendar date
        uncached = ctx._real_scan()                                # ground truth, computed directly, same instant

        assert second == uncached                                  # parity: cached path == uncached ground truth
        assert second is first                                     # literally the same cached object, not just ==

        scan_calls = [c for c in ctx.calls if c["fn"] == "strategies.scan"]
        assert [c["cache_hit"] for c in scan_calls] == [False, True]   # only the FIRST cycle actually computed it


def test_scan_cache_invalidates_on_a_new_calendar_date(fixture_dataset):
    dataset_id, _ = fixture_dataset
    clk = HistoricalClock(pd.Timestamp("2024-04-02T14:35:00", tz="UTC").to_pydatetime())
    provider = HistoricalMarketProvider(clk, [dataset_id])

    with HistoricalAVDIContext(provider, neutral_sector="technology") as ctx:
        from paper import strategies

        day1 = strategies.scan()
        clk.set(pd.Timestamp("2024-04-03T14:35:00", tz="UTC").to_pydatetime())    # next trading day
        day2 = strategies.scan()

        assert day1 is not day2                                   # a new date must never reuse yesterday's cache
        # both real days' own AAPL close differs (the fixture's own linear drift), so the finalists' own
        # `as_of`/price-derived fields should differ too -- a cheap, concrete non-triviality check.
        assert day1["finalists"] != day2["finalists"]

        scan_calls = [c for c in ctx.calls if c["fn"] == "strategies.scan"]
        assert [c["cache_hit"] for c in scan_calls] == [False, False]


def test_patched_scan_restores_cleanly_after_the_context_exits(fixture_dataset):
    dataset_id, _ = fixture_dataset
    from paper import strategies
    original_scan = strategies.scan
    clk = HistoricalClock(pd.Timestamp("2024-04-02T14:35:00", tz="UTC").to_pydatetime())
    provider = HistoricalMarketProvider(clk, [dataset_id])
    with HistoricalAVDIContext(provider, neutral_sector="technology"):
        assert strategies.scan is not original_scan
    assert strategies.scan is original_scan
