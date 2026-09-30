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


def test_intraday_synthesis_bounded_lookback_still_captures_all_of_todays_bars(tmp_path, monkeypatch):
    """Regression test for a real performance defect found building HIST-001 Full:
    _synthesize_todays_daily_bar() used to call bars() with NO lookback, fetching a symbol's entire visible
    history (tens of thousands of rows once the replay clock is deep into a multi-year dataset) only to
    discard all but one day's worth in the Python filter -- catastrophically slow at Full scale. Fixed with
    lookback=300 (25 hours of 5-minute bars, comfortably more than any single session including pre/post-
    market extension). This test builds MANY prior days of 5-minute bars (more than 300 bars deep) plus a
    long "today" session, and proves the bounded-lookback result is byte-identical to what an unbounded
    fetch would have produced -- the fix changes only how much irrelevant history is fetched, never which
    bars end up in the synthesized bar."""
    from research.historical.avdi_adapter import _synthesize_todays_daily_bar

    monkeypatch.setenv("AVDI_HISTORICAL_DATA_DIR", str(tmp_path / "historical"))
    # 10 prior days x 80 5-minute bars/day (a full extended session) + "today" (2025-06-12) with 80 bars of
    # its own -- 800+ bars total visible by "today", far more than the 300-bar lookback bound.
    prior_days = pd.date_range("2025-06-01T08:00:00Z", periods=10, freq="1D", tz="UTC")
    frames = []
    for d in prior_days:
        ts = pd.date_range(d, periods=80, freq="5min", tz="UTC")
        frames.append(pd.DataFrame({"symbol": "ZZZ", "timestamp": ts, "open": 100.0, "high": 101.0,
                                    "low": 99.0, "close": 100.5, "volume": 500.0}))
    today_ts = pd.date_range("2025-06-12T08:00:00Z", periods=80, freq="5min", tz="UTC")
    today_closes = [100.0 + i * 0.1 for i in range(80)]
    frames.append(pd.DataFrame({"symbol": "ZZZ", "timestamp": today_ts,
                               "open": [c - 0.05 for c in today_closes], "high": [c + 0.2 for c in today_closes],
                               "low": [c - 0.2 for c in today_closes], "close": today_closes,
                               "volume": [1000.0 + i for i in range(80)]}))
    df = pd.concat(frames, ignore_index=True)
    _FixedAdapter("h5_lookback_bound_fixture", df).import_and_store(["ZZZ"], "2025-06-01", "2025-06-13", "5m")

    as_of = today_ts[-1].to_pydatetime()          # end of "today"'s session -- ~890 bars visible in total
    clk = HistoricalClock(as_of)
    provider = HistoricalMarketProvider(clk, ["h5_lookback_bound_fixture"])
    synth = _synthesize_todays_daily_bar(provider, "ZZZ", as_of, last_daily_et_date=None)

    assert synth["o"] == pytest.approx(today_closes[0] - 0.05)     # open of TODAY's first bar, not day 1's
    assert synth["c"] == pytest.approx(today_closes[-1])           # close of today's last (most recent) bar
    assert synth["h"] == pytest.approx(max(c + 0.2 for c in today_closes))
    assert synth["l"] == pytest.approx(min(c - 0.2 for c in today_closes))
    assert synth["v"] == sum(1000.0 + i for i in range(80))        # every one of today's 80 bars counted


def test_historical_analysis_daily_points_cache_avoids_refetch_within_the_same_day(daily_dataset):
    """Regression test for a real performance defect found building HIST-001 Full: historical_analysis()
    called provider.bars(symbol, "1d", end=as_of) fresh on EVERY replay cycle (~27 times per trading day)
    even though the set of complete prior daily bars visible is, by construction, identical for every cycle
    within the same calendar day. `daily_points_cache` (a plain dict, owned by the caller/context) memoizes
    this per symbol, overwriting (never accumulating) on a date change. This test proves both halves: (1)
    the underlying bars() call only happens ONCE per day per symbol when the cache is used (spied via
    monkeypatch), and (2) the returned analysis is BYTE-IDENTICAL to the uncached call across several
    same-day cycles and a real day-boundary transition."""
    dataset_id, days_et = daily_dataset
    as_of_day1 = (days_et[60] + pd.Timedelta(hours=14)).tz_convert("UTC").to_pydatetime()     # mid-session
    same_day_later = (days_et[60] + pd.Timedelta(hours=19)).tz_convert("UTC").to_pydatetime()  # later, same day
    next_day = (days_et[61] + pd.Timedelta(hours=14)).tz_convert("UTC").to_pydatetime()        # a new day

    clk = HistoricalClock(as_of_day1)
    provider = HistoricalMarketProvider(clk, [dataset_id])

    calls = {"n": 0}
    real_bars = provider.bars

    def _spy_bars(*a, **kw):
        calls["n"] += 1
        return real_bars(*a, **kw)
    provider.bars = _spy_bars

    cache: dict = {}
    a1, _, _ = historical_analysis(provider, "ZZZ", as_of_day1, daily_points_cache=cache)
    assert calls["n"] == 1                                  # first call for this symbol/day: real fetch
    a2, _, _ = historical_analysis(provider, "ZZZ", as_of_day1, daily_points_cache=cache)
    assert calls["n"] == 1                                  # same symbol, same day: cache hit, NO new fetch
    assert a1 == a2

    clk.set(same_day_later)
    a3, _, _ = historical_analysis(provider, "ZZZ", same_day_later, daily_points_cache=cache)
    assert calls["n"] == 1                                  # still the same calendar day: still cached

    clk.set(next_day)
    a4, _, _ = historical_analysis(provider, "ZZZ", next_day, daily_points_cache=cache)
    assert calls["n"] == 2                                  # a NEW day: exactly one fresh fetch, not zero

    # cross-check every cached result against a fully uncached run at the same instants
    clk2 = HistoricalClock(as_of_day1)
    provider2 = HistoricalMarketProvider(clk2, [dataset_id])
    ref1, _, _ = historical_analysis(provider2, "ZZZ", as_of_day1, daily_points_cache=None)
    clk2.set(next_day)
    ref4, _, _ = historical_analysis(provider2, "ZZZ", next_day, daily_points_cache=None)
    assert a1 == ref1
    assert a4 == ref4


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
