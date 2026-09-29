"""HIST-001 structural fix #1: a PRICE_TREND_MACRO_V1 run must refuse to start if its macro store is
absent or incomplete, rather than silently falling back to PRICE_TREND_ONLY_V1 behavior while still
claiming the macro tag."""
import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.historical.macro import MacroCoverageError, MacroHistory, VintageObservation, assert_macro_coverage


def _full_history(start: str, end: str) -> MacroHistory:
    obs = []
    for sid in ("DGS10", "DGS2", "VIXCLS"):
        obs.append(VintageObservation(series_id=sid, observation_date=start, realtime_start=start,
                                      realtime_end="9999-12-31", value=1.0))
    return MacroHistory(obs)


def test_none_macro_history_raises():
    with pytest.raises(MacroCoverageError):
        assert_macro_coverage(None, dt.date(2024, 4, 1), dt.date(2024, 6, 30))


def test_full_coverage_does_not_raise():
    h = _full_history("2024-01-01", "2024-06-30")
    assert_macro_coverage(h, dt.date(2024, 4, 1), dt.date(2024, 6, 30))    # does not raise


def test_partial_coverage_raises_with_gap_dates():
    # covers only up to 2024-05-01 -- the back half of the requested range is a gap
    h = MacroHistory([VintageObservation(series_id=sid, observation_date="2024-01-01",
                                         realtime_start="2024-01-01", realtime_end="2024-05-01", value=1.0)
                      for sid in ("DGS10", "DGS2", "VIXCLS")])
    with pytest.raises(MacroCoverageError) as e:
        assert_macro_coverage(h, dt.date(2024, 4, 1), dt.date(2024, 6, 30))
    assert "gap" in str(e.value).lower()


def test_coverage_gaps_ignores_fedfunds_by_default():
    """FEDFUNDS is never used in _fam_macro's own arithmetic -- a gap in it alone must not block a run."""
    h = MacroHistory([VintageObservation(series_id=sid, observation_date="2024-01-01",
                                         realtime_start="2024-01-01", realtime_end="9999-12-31", value=1.0)
                      for sid in ("DGS10", "DGS2", "VIXCLS")])   # no FEDFUNDS rows at all
    assert_macro_coverage(h, dt.date(2024, 4, 1), dt.date(2024, 6, 30))    # does not raise
    gaps = h.coverage_gaps(dt.date(2024, 4, 1), dt.date(2024, 6, 30))
    assert gaps == {}
