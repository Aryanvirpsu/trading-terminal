"""H2 acceptance: the HistoricalReplayClock and the hard lookahead guard. If replay time is 10:05 ET, a
10:10 bar, the day's FINAL high/low, or any information timestamped after 10:05 must be unreachable."""
import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.historical.clock import HistoricalClock
from research.historical.provider import HistoricalMarketProvider, LookaheadError
from research.historical.datasets.base import DatasetAdapter


class _FixedAdapter(DatasetAdapter):
    source_name = "fixture"
    revision = "test"

    def __init__(self, dataset_id, df):
        super().__init__(dataset_id)
        self._df = df

    def fetch(self, symbols, start, end, timeframe):
        return self._df


DAY = "2026-06-17"


def _full_day_bars(symbol="AAPL"):
    # 09:30 .. 15:55 ET, 5-minute bars, a day that trends up then reverses — its FINAL high/low are far
    # from what is knowable at 10:05, which is exactly what must stay unreachable.
    times = pd.date_range(f"{DAY}T09:30:00", f"{DAY}T15:55:00", freq="5min", tz="America/New_York")
    times = times.tz_convert("UTC")
    n = len(times)
    close = [100.0 + i * 0.05 for i in range(n // 2)] + [100.0 + (n // 2) * 0.05 - i * 0.2 for i in range(n - n // 2)]
    df = pd.DataFrame({"symbol": symbol, "timestamp": times,
                       "open": [c - 0.02 for c in close], "high": [c + 0.05 for c in close],
                       "low": [c - 0.05 for c in close], "close": close,
                       "volume": [1000.0] * n})
    df.loc[df.index[-1], "high"] = 999.0     # the day's FINAL high — must never be visible before the close
    df.loc[df.index[-1], "low"] = 1.0        # the day's FINAL low — same
    return df


@pytest.fixture()
def dataset(tmp_path, monkeypatch):
    monkeypatch.setenv("AVDI_HISTORICAL_DATA_DIR", str(tmp_path / "historical"))
    _FixedAdapter("h2_fixture", _full_day_bars()).import_and_store(["AAPL"], DAY, DAY, "5m")
    return "h2_fixture"


def _et(hhmm: str) -> dt.datetime:
    from zoneinfo import ZoneInfo
    h, m = int(hhmm[:2]), int(hhmm[2:])
    return dt.datetime(2026, 6, 17, h, m, tzinfo=ZoneInfo("America/New_York"))


# ── clock ────────────────────────────────────────────────────────────────────────────────────────────

def test_clock_set_and_advance():
    clk = HistoricalClock(_et("0935"))
    assert clk.et_now().strftime("%H:%M") == "09:35"
    clk.advance_minutes(15)
    assert clk.et_now().strftime("%H:%M") == "09:50"


def test_clock_refuses_to_move_backward():
    clk = HistoricalClock(_et("1005"))
    with pytest.raises(ValueError):
        clk.set(_et("0935"))
    with pytest.raises(ValueError):
        clk.advance(dt.timedelta(minutes=-5))


def test_clock_allows_deliberate_rewind_for_a_new_fold():
    clk = HistoricalClock(_et("1500"))
    clk.set(_et("0935"), allow_rewind=True)
    assert clk.et_now().strftime("%H:%M") == "09:35"


def test_clock_requires_tz_aware_input():
    with pytest.raises(ValueError):
        HistoricalClock(dt.datetime(2026, 6, 17, 9, 35))       # naive — must be rejected


# ── the literal acceptance scenario ─────────────────────────────────────────────────────────────────

def test_at_1005_the_1010_bar_is_impossible(dataset):
    clk = HistoricalClock(_et("1005"))
    p = HistoricalMarketProvider(clk, [dataset])
    bars = p.bars("AAPL", end=clk.now)
    assert all(dt.datetime.fromisoformat(b["t"]) <= clk.now for b in bars)
    assert not any(dt.datetime.fromisoformat(b["t"]).astimezone(dt.timezone.utc).astimezone(
        __import__("zoneinfo").ZoneInfo("America/New_York")).strftime("%H:%M") == "10:10" for b in bars)
    # an explicit request for the 10:10 instant is refused outright, not silently trimmed
    with pytest.raises(LookaheadError):
        p.bars("AAPL", end=_et("1010"))
    with pytest.raises(LookaheadError):
        p.quote("AAPL", as_of=_et("1010"))


def test_at_1005_the_days_final_high_and_low_are_unreachable(dataset):
    clk = HistoricalClock(_et("1005"))
    p = HistoricalMarketProvider(clk, [dataset])
    session_start = _et("0930").astimezone(dt.timezone.utc)
    hi = p.session_high_so_far("AAPL", session_start)
    lo = p.session_low_so_far("AAPL", session_start)
    assert hi is not None and hi < 999.0          # the planted end-of-day high must not leak
    assert lo is not None and lo > 1.0            # the planted end-of-day low must not leak
    # sanity: at the actual close, the full-day extremes ARE visible — proving the 10:05 result was a
    # genuine clock effect, not a bug that always hides the last bar
    clk.set(_et("1555"))
    p2 = HistoricalMarketProvider(HistoricalClock(_et("1555")), [dataset])
    assert p2.session_high_so_far("AAPL", session_start) == 999.0
    assert p2.session_low_so_far("AAPL", session_start) == 1.0


def test_quote_only_reflects_the_most_recent_visible_bar(dataset):
    clk = HistoricalClock(_et("1005"))
    p = HistoricalMarketProvider(clk, [dataset])
    q = p.quote("AAPL")
    assert q is not None
    from zoneinfo import ZoneInfo
    q_et = dt.datetime.fromtimestamp(q.source_ts, dt.timezone.utc).astimezone(ZoneInfo("America/New_York"))
    assert q_et.strftime("%H:%M") == "10:05"                  # the bar AT clock.now is visible (bar-close convention)
    assert q.bid < q.last < q.ask                              # a real (non-zero) synthetic spread


def test_quote_before_any_data_exists_returns_none(dataset):
    clk = HistoricalClock(_et("0900"))                         # before the session even starts
    p = HistoricalMarketProvider(clk, [dataset])
    assert p.quote("AAPL") is None
    assert p.bars("AAPL") == []


def test_advancing_the_clock_makes_previously_future_data_visible(dataset):
    clk = HistoricalClock(_et("1005"))
    p = HistoricalMarketProvider(clk, [dataset])
    with pytest.raises(LookaheadError):
        p.quote("AAPL", as_of=_et("1010"))
    clk.advance_minutes(5)
    q = p.quote("AAPL", as_of=clk.now)                         # now legal: 10:10 <= clock.now (10:10)
    assert q is not None


def test_unknown_symbol_is_empty_not_an_error(dataset):
    clk = HistoricalClock(_et("1005"))
    p = HistoricalMarketProvider(clk, [dataset])
    assert p.quote("ZZZZ") is None
    assert p.bars("ZZZZ") == []


def test_lookback_can_only_shrink_never_extend_the_visible_window(dataset):
    clk = HistoricalClock(_et("1005"))
    p = HistoricalMarketProvider(clk, [dataset])
    all_visible = p.bars("AAPL")
    only_two = p.bars("AAPL", lookback=2)
    assert len(only_two) == 2 and only_two == all_visible[-2:]


def test_naive_datetime_end_is_rejected_not_silently_assumed_utc(dataset):
    clk = HistoricalClock(_et("1005"))
    p = HistoricalMarketProvider(clk, [dataset])
    with pytest.raises(ValueError):
        p.bars("AAPL", end=dt.datetime(2026, 6, 17, 10, 0))
