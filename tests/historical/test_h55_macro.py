"""H5.5 acceptance: point-in-time macro lookup never leaks a future observation, historical_macro_signal
reproduces lab/fred.py's own arithmetic, _fam_macro is genuinely replayed (not stubbed) when a MacroHistory
is supplied, and -- the load-bearing claim from H5_FORWARD_REPRODUCTION.md -- replaying macro (or filings)
alone is enough to cross the 0.55 data_quality floor, checked directly against the real lab/data_quality.py
arithmetic, not merely asserted in prose.
"""
import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.historical.macro import MacroHistory, MacroObservation, historical_macro_signal


def _history():
    return MacroHistory([
        MacroObservation(date="2026-09-20", values={"10y": 4.20, "2y": 4.10, "vix": 15.0, "fedfunds": 5.25}),
        MacroObservation(date="2026-09-24", values={"10y": 4.30, "2y": 4.60, "vix": 28.0, "fedfunds": 5.25}),
        MacroObservation(date="2026-09-25", values={"10y": 4.35, "2y": 4.05, "vix": 16.0, "fedfunds": 5.25}),
    ])


# ── MacroHistory.as_of: lookahead safety ────────────────────────────────────────────────────────────────

def test_as_of_never_returns_a_future_observation():
    h = _history()
    obs = h.as_of(dt.date(2026, 9, 21))     # 1 day after the 2026-09-20 row, before the 2026-09-24 row
    assert obs is not None and obs.date == "2026-09-20"


def test_as_of_respects_the_publication_lag_not_same_day():
    h = _history()
    # Requesting exactly the 2026-09-25 date: with a 1-day lag, that SAME day's own row is not yet
    # "published" as of its own date -- the latest KNOWABLE row is still 2026-09-24's.
    obs = h.as_of(dt.date(2026, 9, 25))
    assert obs.date == "2026-09-24"


def test_as_of_returns_none_before_any_data_exists():
    h = _history()
    assert h.as_of(dt.date(2020, 1, 1)) is None


def test_as_of_returns_the_latest_available_once_lag_has_passed():
    h = _history()
    obs = h.as_of(dt.date(2026, 9, 26))
    assert obs.date == "2026-09-25"


# ── historical_macro_signal reproduces fred.py's own tilt/VIX arithmetic ───────────────────────────────

def test_risk_off_inverted_curve_and_high_vix():
    h = _history()
    sig = historical_macro_signal(h, dt.date(2026, 9, 25), direction="LONG")     # sees 2026-09-24: 2y>10y, VIX 28
    assert sig["family"] == "macro-rates"
    assert sig["dir"] < 0                     # inverted curve + high VIX -> risk-off -> negative for LONG
    assert sig["conf"] == 0.5


def test_risk_on_steep_curve_low_vix():
    h = _history()
    sig = historical_macro_signal(h, dt.date(2026, 9, 21), direction="LONG")     # sees 2026-09-20: steep, calm
    assert sig["dir"] > 0


def test_direction_flips_the_sign():
    h = _history()
    long_sig = historical_macro_signal(h, dt.date(2026, 9, 21), direction="LONG")
    short_sig = historical_macro_signal(h, dt.date(2026, 9, 21), direction="SHORT")
    assert long_sig["dir"] == pytest.approx(-short_sig["dir"])


def test_no_data_returns_zero_confidence_not_a_fabricated_value():
    h = MacroHistory([])
    sig = historical_macro_signal(h, dt.date(2026, 1, 1), direction="LONG")
    assert sig["conf"] == 0.0 and sig["dir"] == 0.0


# ── HistoricalAVDIContext wiring: _fam_macro is genuinely replayed, not stubbed, when given a history ──

def test_fam_macro_is_stubbed_by_default_and_replayed_when_macro_history_given(tmp_path, monkeypatch):
    import pandas as pd

    from research.historical.avdi_adapter import HistoricalAVDIContext
    from research.historical.clock import HistoricalClock
    from research.historical.datasets.base import DatasetAdapter
    from research.historical.provider import HistoricalMarketProvider

    monkeypatch.setenv("AVDI_HISTORICAL_DATA_DIR", str(tmp_path / "historical"))

    class _FixedAdapter(DatasetAdapter):
        source_name = "fixture"
        revision = "test"

        def __init__(self, dataset_id, df):
            super().__init__(dataset_id)
            self._df = df

        def fetch(self, symbols, start, end, timeframe):
            return self._df

    days = pd.bdate_range("2025-01-02", periods=60, tz="UTC")
    closes = [100.0 + 0.1 * k for k in range(60)]
    df = pd.DataFrame({"symbol": "ZZZ", "timestamp": days, "open": closes, "high": [c + 0.5 for c in closes],
                       "low": [c - 0.5 for c in closes], "close": closes, "volume": [1_000_000.0] * 60})
    _FixedAdapter("h55_fixture", df).import_and_store(["ZZZ"], "2025-01-01", "2025-06-01", "1d")

    clk = HistoricalClock(days[59].to_pydatetime())
    provider = HistoricalMarketProvider(clk, ["h55_fixture"])

    # Default: macro stubbed (H3/H4/H5 behavior, unchanged).
    with HistoricalAVDIContext(provider) as ctx:
        import decision_engine as de
        result = de._fam_macro("LONG")
        assert result["conf"] == 0.0
        assert not any(c["fn"] == "decision_engine._fam_macro" for c in ctx.calls)

    # With a macro_history: genuinely replayed. Dated well before the clock's own date so it is
    # unambiguously visible regardless of exactly which business day `days[59]` lands on.
    history = MacroHistory([MacroObservation(date="2025-01-01", values={"10y": 4.5, "2y": 4.0, "vix": 14.0,
                                                                        "fedfunds": 5.0})])
    with HistoricalAVDIContext(provider, macro_history=history) as ctx2:
        import decision_engine as de
        result = de._fam_macro("LONG")
        assert result["conf"] == 0.5
        assert any(c["fn"] == "decision_engine._fam_macro" for c in ctx2.calls)


# ── The load-bearing claim: replaying macro alone crosses the 0.55 floor ────────────────────────────────

def test_replaying_macro_alone_crosses_the_data_quality_floor():
    """Checked directly against the REAL lab/data_quality.py arithmetic (not reimplemented/approximated) --
    this is the exact calculation H5_FORWARD_REPRODUCTION.md's finding and H5.5's macro-first
    recommendation both rest on."""
    import data_quality as dq

    # PRICE_TREND_ONLY_V1 baseline coverages, exactly as decision_engine.py builds them for a fresh,
    # reasonably-covered symbol (price/candles near-maxed, sector at its usual ~0.8, everything else 0).
    baseline = {
        "price": dq.category_coverage("price", {"price": 100.0}, confidence=0.9),
        "candles": dq.category_coverage("candles", {"closes": [1], "highs": [1], "lows": [1]}, confidence=0.85),
        "sector": dq.category_coverage("sector", {"sector": "NASDAQ"}, confidence=0.6),
        # news/analyst/filings/macro/options: no historical replay -> zero coverage, exactly the fallback
        # assess() itself applies when a category key is missing.
    }
    before = dq.assess(baseline, profile="momentum")
    assert before["overall"] == pytest.approx(0.536, abs=0.001)
    assert not before["sufficient"] or before["overall"] < 0.55     # capped below the floor either way

    with_macro = dict(baseline)
    with_macro["macro"] = dq.category_coverage("macro", {"series": 1}, confidence=1.0)   # full coverage, replayed
    after = dq.assess(with_macro, profile="momentum")
    assert after["overall"] >= 0.55
    assert after["overall"] == pytest.approx(0.588, abs=0.005)


def test_replaying_filings_alone_also_crosses_the_floor():
    import data_quality as dq

    baseline = {
        "price": dq.category_coverage("price", {"price": 100.0}, confidence=0.9),
        "candles": dq.category_coverage("candles", {"closes": [1], "highs": [1], "lows": [1]}, confidence=0.85),
        "sector": dq.category_coverage("sector", {"sector": "NASDAQ"}, confidence=0.6),
    }
    with_filings = dict(baseline)
    with_filings["filings"] = dq.category_coverage("filings", {"cik": "0000320193"}, confidence=1.0)
    after = dq.assess(with_filings, profile="momentum")
    assert after["overall"] >= 0.55


def test_fundamentals_category_is_never_populated_by_decision_engine_today():
    """The corrected H5.5 finding: 'fundamentals' isn't a dead lever specific to Historical Lab -- it is
    never wired into decision_engine.py's own `coverages` dict at all, so it defaults to zero coverage in
    LIVE production too. This test pins that down directly against the real source (evaluate()'s own
    `coverages = {...}` block), so if a future change to decision_engine.py ever wires it, this test (not
    just the module docstring) notices."""
    import inspect

    import decision_engine as de
    src = inspect.getsource(de.evaluate)
    start = src.index("coverages = {")
    end = src.index("\n    }", start)
    coverages_block = src[start:end]
    assert '"fundamentals"' not in coverages_block
