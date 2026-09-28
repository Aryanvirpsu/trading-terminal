"""H5 findings (surfaced by the DELL/META reproduction, see H5_FORWARD_REPRODUCTION.md): two production
seams read the REAL wall clock even inside a HistoricalAVDIContext -- lab.freshness.bar_age_seconds() and
dashboard.market_regime.session_state() -- and a third bug (daily bars mislabelled at midnight instead of
their own session close) let a same-day intraday replay see a full day's OHLC before that day had actually
closed. All three are now fixed; these tests pin the fixes down.
"""
import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.historical.avdi_adapter import HistoricalAVDIContext, historical_analysis
from research.historical.clock import HistoricalClock
from research.historical.datasets.base import DatasetAdapter
from research.historical.provider import HistoricalMarketProvider

ET = ZoneInfo("America/New_York")


class _FixedAdapter(DatasetAdapter):
    source_name = "fixture"
    revision = "test"

    def __init__(self, dataset_id, df):
        super().__init__(dataset_id)
        self._df = df

    def fetch(self, symbols, start, end, timeframe):
        return self._df


def _daily_fixture(symbol="ZZZ", n=70, base=100.0):
    """Daily bars correctly labelled at each date's real 16:00 ET close (the H5-fixed convention)."""
    days_et = pd.bdate_range("2025-01-02", periods=n, tz=ET)
    closes = [base + 0.2 * k + 1.0 * ((k * 7) % 5) for k in range(n)]
    closes_utc = (days_et + pd.Timedelta(hours=16)).tz_convert("UTC")
    return pd.DataFrame({"symbol": symbol, "timestamp": closes_utc, "open": [c - 0.1 for c in closes],
                         "high": [c + 0.3 for c in closes], "low": [c - 0.3 for c in closes],
                         "close": closes, "volume": [2_000_000.0] * n}), days_et


@pytest.fixture()
def daily_dataset(tmp_path, monkeypatch):
    monkeypatch.setenv("AVDI_HISTORICAL_DATA_DIR", str(tmp_path / "historical"))
    df, days_et = _daily_fixture()
    _FixedAdapter("h5_freshness_fixture", df).import_and_store(["ZZZ"], "2025-01-01", "2025-06-01", "1d")
    return "h5_freshness_fixture", days_et


# ── bar_age_seconds clock-awareness ─────────────────────────────────────────────────────────────────────

def test_bar_age_is_relative_to_the_historical_clock_not_real_wall_clock(daily_dataset):
    dataset_id, days_et = daily_dataset
    last_close_utc = (days_et[59] + pd.Timedelta(hours=16)).tz_convert("UTC").to_pydatetime()
    # Clock sits exactly 5 minutes after the 59th day's own close.
    clk_now = last_close_utc + dt.timedelta(minutes=5)
    clk = HistoricalClock(clk_now)
    provider = HistoricalMarketProvider(clk, [dataset_id])
    with HistoricalAVDIContext(provider) as ctx:
        age = ctx._patched_bar_age_seconds(days_et[59].tz_convert("UTC").isoformat())
        # 00:00 UTC label with no shift needed here -- exercise the real code path via the adapter's own
        # as_of instead (closes_utc already 16:00 ET-based) to avoid re-deriving the midnight special case.
        analysis, src, state = ctx._patched_load_analysis("ZZZ", "NASDAQ")
        assert state == "fresh"
        assert analysis["as_of"] == last_close_utc.isoformat()


def test_bar_age_midnight_labelled_bar_uses_et_aware_close_not_naive_utc_offset(daily_dataset):
    """The original bug: adding `session_close_hour` UTC-hours to a UTC-midnight timestamp lands at
    16:00 UTC (noon ET in EDT), not the real 16:00 ET (20:00 UTC) close. Verified directly against the
    patched function with a synthetic midnight-labelled as_of (the dataset itself is irrelevant here --
    only a valid provider/context is needed to reach the patched method)."""
    dataset_id, _days_et = daily_dataset
    clk = HistoricalClock(dt.datetime(2025, 6, 2, 20, 5, tzinfo=dt.timezone.utc))     # 16:05 ET (EDT)
    provider = HistoricalMarketProvider(clk, [dataset_id])
    with HistoricalAVDIContext(provider) as ctx:
        age = ctx._patched_bar_age_seconds("2025-06-02T00:00:00+00:00")     # midnight-labelled, EDT date
        assert age == pytest.approx(300, abs=1)         # 5 minutes after the REAL 16:00 ET close, not noon


def test_intraday_synthesis_of_todays_daily_bar_is_only_todays_real_visible_bars(tmp_path, monkeypatch):
    from research.historical.avdi_adapter import _synthesize_todays_daily_bar

    monkeypatch.setenv("AVDI_HISTORICAL_DATA_DIR", str(tmp_path / "historical"))
    ts = pd.date_range("2025-06-02T13:35:00Z", periods=6, freq="5min", tz="UTC")     # 09:35-10:00 ET
    df = pd.DataFrame({"symbol": "ZZZ", "timestamp": ts, "open": [100.0, 101, 102, 101.5, 103, 104],
                       "high": [101.0, 102, 103, 102.5, 104, 105], "low": [99.5, 100.5, 101.5, 101, 102.5, 103.5],
                       "close": [100.5, 101.5, 102.0, 102.5, 103.5, 104.5], "volume": [1000.0] * 6})
    _FixedAdapter("h5_intraday_synth_fixture", df).import_and_store(["ZZZ"], "2025-06-02", "2025-06-03", "5m")
    clk = HistoricalClock(ts[-1].to_pydatetime())
    provider = HistoricalMarketProvider(clk, ["h5_intraday_synth_fixture"])
    synth = _synthesize_todays_daily_bar(provider, "ZZZ", ts[3].to_pydatetime(), last_daily_et_date=None)
    # as_of = ts[3] -> only the first 4 bars are visible
    assert synth["o"] == 100
    assert synth["h"] == 103          # max of first 4 highs (101,102,103,102.5)
    assert synth["l"] == 99.5
    assert synth["c"] == 102.5        # close of the 4th (last visible) bar
    assert synth["v"] == 4000


def test_intraday_synthesis_returns_none_when_todays_daily_bar_already_exists():
    from research.historical.avdi_adapter import _synthesize_todays_daily_bar
    import datetime as _dt

    as_of = _dt.datetime(2025, 6, 2, 14, 0, tzinfo=_dt.timezone.utc)
    result = _synthesize_todays_daily_bar(object(), "ZZZ", as_of, last_daily_et_date=as_of.date())
    assert result is None


def test_intraday_synthesis_returns_none_with_no_intraday_provider():
    from research.historical.avdi_adapter import _synthesize_todays_daily_bar
    import datetime as _dt

    assert _synthesize_todays_daily_bar(None, "ZZZ", _dt.datetime.now(_dt.timezone.utc), None) is None


# ── market_regime.session_state clock-awareness ─────────────────────────────────────────────────────────

def test_session_state_uses_historical_clock_not_real_now(daily_dataset):
    dataset_id, _days_et = daily_dataset
    # A real, known Friday regular-session instant.
    friday_noon_et = dt.datetime(2025, 6, 6, 12, 0, tzinfo=ET)
    clk = HistoricalClock(friday_noon_et)
    provider = HistoricalMarketProvider(clk, [dataset_id])
    with HistoricalAVDIContext(provider) as ctx:
        import market_regime as mr
        state = mr.session_state()               # no explicit `now` -- must use the historical clock
        assert state["state"] == "open"

    # A known weekend instant -- must report closed, regardless of when this test actually runs.
    saturday = dt.datetime(2025, 6, 7, 12, 0, tzinfo=ET)
    clk2 = HistoricalClock(saturday)
    with HistoricalAVDIContext(HistoricalMarketProvider(clk2, [dataset_id])) as ctx2:
        import market_regime as mr
        state2 = mr.session_state()
        assert state2["state"] == "closed"


def test_session_state_explicit_now_argument_still_honored(daily_dataset):
    """A caller that DOES pass an explicit `now` must not be silently overridden by the clock."""
    dataset_id, _days_et = daily_dataset
    clk = HistoricalClock(dt.datetime(2025, 6, 6, 12, 0, tzinfo=ET))
    with HistoricalAVDIContext(HistoricalMarketProvider(clk, [dataset_id])):
        import market_regime as mr
        explicit_weekend = dt.datetime(2025, 6, 7, 12, 0, tzinfo=ET)
        state = mr.session_state(explicit_weekend)
        assert state["state"] == "closed"          # honored the explicit argument, not the clock's Friday


def test_patches_restore_cleanly(daily_dataset):
    dataset_id, _days_et = daily_dataset
    import freshness
    import market_regime as mr

    orig_age = freshness.bar_age_seconds
    orig_session = mr.session_state
    clk = HistoricalClock(dt.datetime(2025, 6, 6, 12, 0, tzinfo=ET))
    with HistoricalAVDIContext(HistoricalMarketProvider(clk, [dataset_id])):
        assert freshness.bar_age_seconds is not orig_age
        assert mr.session_state is not orig_session
    assert freshness.bar_age_seconds is orig_age
    assert mr.session_state is orig_session
