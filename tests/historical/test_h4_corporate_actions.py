"""H4 acceptance: corporate-action detection, the raw/split_adjusted view, the lookahead boundary on
`split_adjusted`, and quarantine-not-remap for symbols with no further data."""
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.historical.corporate_actions import (
    apply_ticker_mapping, detect_quarantine_candidates, detect_splits, is_confirmed, known_as_of,
    split_adjusted_view,
)


def _daily_series(symbol: str, start: str, closes, volumes=None):
    """One 16:00Z daily-close-like bar per date -- enough for detect_splits' daily aggregation."""
    ts = pd.date_range(start, periods=len(closes), freq="1D", tz="UTC")
    vols = volumes or [1_000_000.0] * len(closes)
    return pd.DataFrame({"symbol": [symbol] * len(closes), "timestamp": ts,
                         "open": closes, "high": [c * 1.01 for c in closes], "low": [c * 0.99 for c in closes],
                         "close": closes, "volume": vols})


def test_forward_split_detected_with_volume_confirmation():
    # NVDA-like 10-for-1: close drops to ~1/10, volume rises ~10x the same day.
    closes = [480.0, 481.0, 482.5, 48.2, 48.5]
    vols = [1_000_000.0, 1_050_000.0, 980_000.0, 10_200_000.0, 9_800_000.0]
    df = _daily_series("NVDA", "2024-06-04", closes, vols)
    events = detect_splits(df)
    assert len(events) == 1
    ev = events[0]
    assert ev.symbol == "NVDA"
    assert ev.split_date == "2024-06-07"           # the 4th bar, 2024-06-04 + 3 days
    assert ev.inferred_split_ratio == 10.0
    assert ev.confidence == "high"
    assert ev.volume_confirms is True
    assert is_confirmed(ev)


def test_reverse_split_detected():
    # 1-for-10 reverse split: close rises ~10x, volume falls ~10x.
    closes = [5.0, 5.1, 4.9, 49.0, 50.0]
    vols = [10_000_000.0, 9_500_000.0, 10_500_000.0, 1_000_000.0, 1_050_000.0]
    df = _daily_series("ZZZ", "2024-02-01", closes, vols)
    events = detect_splits(df)
    assert len(events) == 1
    ev = events[0]
    assert ev.inferred_split_ratio == pytest.approx(0.1, rel=1e-6)
    assert is_confirmed(ev)


def test_ordinary_volatile_day_is_not_flagged():
    # A 40% single-day move is well inside [0.4, 2.5] and must never be flagged.
    closes = [10.0, 10.0, 13.5, 13.6]
    df = _daily_series("VOL", "2024-05-01", closes)
    assert detect_splits(df) == []


def test_low_confidence_split_is_not_auto_confirmed():
    # A jump that exists but doesn't round cleanly to a common factor, and has no volume corroboration.
    closes = [100.0, 100.0, 100.0, 27.0]           # ratio ~0.27, not close to any of 1.5..20
    df = _daily_series("MEH", "2024-05-01", closes, volumes=[1e6, 1e6, 1e6, 1e6])   # flat volume, no confirmation
    events = detect_splits(df)
    assert len(events) == 1
    assert events[0].confidence == "low"
    assert not is_confirmed(events[0])


def test_split_adjusted_view_scales_pre_split_bars_only():
    closes = [480.0, 481.0, 482.5, 48.2, 48.5]
    vols = [1_000_000.0] * 3 + [10_000_000.0] * 2
    df = _daily_series("NVDA", "2024-06-04", closes, vols)
    events = detect_splits(df)
    adj = split_adjusted_view(df, events)          # no as_of -> "apply every confirmed split" reporting mode
    adj = adj.sort_values("timestamp").reset_index(drop=True)
    # Pre-split bars (dates < 2024-06-07) divided by 10, volume x10.
    assert adj.loc[0, "close"] == pytest.approx(48.0, rel=1e-6)
    assert adj.loc[0, "volume"] == pytest.approx(10_000_000.0, rel=1e-6)
    # Post-split bars (>= split date) untouched.
    assert adj.loc[3, "close"] == pytest.approx(48.2, rel=1e-6)
    assert adj.loc[3, "volume"] == pytest.approx(10_000_000.0, rel=1e-6)
    # RAW input is never mutated.
    assert df.loc[0, "close"] == 480.0


def test_split_adjusted_view_respects_lookahead_boundary():
    closes = [480.0, 481.0, 482.5, 48.2, 48.5]
    vols = [1_000_000.0] * 3 + [10_000_000.0] * 2
    df = _daily_series("NVDA", "2024-06-04", closes, vols)
    events = detect_splits(df)
    ev = events[0]
    assert ev.split_date == "2024-06-07"

    # As of the day BEFORE the split is knowable, split_adjusted must equal raw exactly -- nobody at that
    # instant could have known the split was coming.
    before = split_adjusted_view(df, events, as_of_date="2024-06-06")
    before = before.sort_values("timestamp").reset_index(drop=True)
    assert before.loc[0, "close"] == pytest.approx(480.0, rel=1e-9)

    # As of the split date itself (inclusive), it IS knowable.
    on_date = split_adjusted_view(df, events, as_of_date="2024-06-07")
    on_date = on_date.sort_values("timestamp").reset_index(drop=True)
    assert on_date.loc[0, "close"] == pytest.approx(48.0, rel=1e-6)

    assert known_as_of(events, "2024-06-06") == []
    assert len(known_as_of(events, "2024-06-07")) == 1


def test_quarantine_flags_symbol_with_no_further_data():
    closes = [10.0, 10.1, 10.2]
    df = _daily_series("DEAD", "2024-01-01", closes)
    events = detect_quarantine_candidates(df, ["DEAD", "ALIVE"], as_of_date="2024-02-01", stale_after_days=10)
    reasons = {e.symbol: e for e in events}
    assert "DEAD" in reasons
    assert reasons["DEAD"].reason == "no_further_data"
    assert reasons["DEAD"].last_price == pytest.approx(10.2, rel=1e-6)
    assert "ALIVE" in reasons                        # never had any data at all -> also quarantined


def test_no_ticker_remap_without_explicit_dated_mapping():
    df = _daily_series("OLD", "2024-01-01", [10.0, 10.1, 10.2])
    # No mapping supplied -> rows are left exactly as-is (the safe default).
    out = apply_ticker_mapping(df, {}, {})
    assert set(out["symbol"]) == {"OLD"}

    # An explicit, dated mapping renames only rows before the effective date.
    mapping = {"OLD": "NEW"}
    confirmed = {"OLD": "2024-01-02"}
    out2 = apply_ticker_mapping(df, mapping, confirmed)
    out2 = out2.sort_values("timestamp").reset_index(drop=True)
    assert out2.loc[0, "symbol"] == "NEW"             # 2024-01-01 < effective date
    assert out2.loc[1, "symbol"] == "OLD"             # 2024-01-02 is not < effective date
