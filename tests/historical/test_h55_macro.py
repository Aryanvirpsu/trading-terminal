"""H5.5 acceptance: real ALFRED vintage semantics for historical macro replay.

Covers: point-in-time vintage lookahead safety (the directive's own explicit revision example: X initially
published, later revised to Y, replay before/after the revision date), historical_macro_signal reproducing
lab/fred.py's own arithmetic exactly (including the fact that FEDFUNDS is fetched but unused, and that
confidence is a fixed 0.5 regardless of data completeness), series-integrity fail-closed behavior, the
local Parquet+manifest store (never containing the API key), _fam_macro wiring through a real
HistoricalAVDIContext, the new PRICE_TREND_MACRO_V1 capability fingerprint (never overwriting
PRICE_TREND_ONLY_V1), and the load-bearing data_quality arithmetic checked against the real source.
"""
import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.historical.macro import (
    API_VERSION, SERIES, SERIES_INTEGRITY, MacroHistory, SeriesIntegrityError, VintageObservation,
    historical_macro_signal, load_macro_history, save_macro_history, verify_macro_manifest,
)


def _vo(series_id, obs_date, rt_start, rt_end, value):
    return VintageObservation(series_id=series_id, observation_date=obs_date, realtime_start=rt_start,
                              realtime_end=rt_end, value=value)


# ── Series integrity (directive's "Macro-series integrity checks") ────────────────────────────────────────

def test_all_four_series_have_a_recorded_integrity_verdict():
    assert set(SERIES_INTEGRITY.keys()) == set(SERIES.keys())
    for sid, info in SERIES_INTEGRITY.items():
        assert info["verdict"] == "ACCEPTED"
        for field in ("release_frequency", "revision_frequency", "publication_lag", "timezone_semantics"):
            assert info[field]


def test_historical_macro_signal_fails_closed_for_an_unaccepted_series(monkeypatch):
    monkeypatch.setitem(SERIES_INTEGRITY, "DGS10", {**SERIES_INTEGRITY["DGS10"], "verdict": "UNAVAILABLE"})
    h = MacroHistory([])
    with pytest.raises(SeriesIntegrityError):
        historical_macro_signal(h, dt.date(2026, 1, 1))


# ── THE directive's own explicit revision example ──────────────────────────────────────────────────────────

def test_replay_before_revision_sees_the_original_value_never_the_revision():
    """Value X initially published (realtime_start = pub date); later revised to Y (a NEW vintage row,
    realtime_start = revision date). Replay strictly before the revision date must see X; replay ON or
    after the revision date must see Y; Y must NEVER be visible before its own realtime_start."""
    history = MacroHistory([
        _vo("DGS10", "2026-06-01", "2026-06-02", "2026-06-14", 4.10),     # X: published 06-02, current through 06-14
        _vo("DGS10", "2026-06-01", "2026-06-15", "9999-12-31", 4.35),     # Y: revised 06-15, current ever since
        _vo("DGS2", "2026-06-01", "2026-06-02", "9999-12-31", 4.00),
    ])
    before = history.latest_value_as_of("DGS10", dt.date(2026, 6, 14))
    on_revision = history.latest_value_as_of("DGS10", dt.date(2026, 6, 15))
    long_after = history.latest_value_as_of("DGS10", dt.date(2026, 8, 1))

    assert before == ("2026-06-01", 4.10)          # X
    assert on_revision == ("2026-06-01", 4.35)     # Y, exactly on its own vintage start
    assert long_after == ("2026-06-01", 4.35)      # Y stays visible afterward

    # The directive's own phrasing, checked directly: never Y before its vintage date.
    for d in (dt.date(2026, 6, 2), dt.date(2026, 6, 10), dt.date(2026, 6, 14)):
        val = history.latest_value_as_of("DGS10", d)
        assert val is not None and val[1] == 4.10, f"revision leaked backward to {d}"


def test_before_any_publication_the_observation_is_invisible():
    history = MacroHistory([_vo("DGS10", "2026-06-01", "2026-06-02", "9999-12-31", 4.10)])
    assert history.latest_value_as_of("DGS10", dt.date(2026, 6, 1)) is None    # not yet published
    assert history.latest_value_as_of("DGS10", dt.date(2026, 6, 2)) == ("2026-06-01", 4.10)


def test_unknown_series_or_empty_history_returns_none():
    assert MacroHistory([]).latest_value_as_of("DGS10", dt.date(2026, 1, 1)) is None


# ── historical_macro_signal reproduces lab/fred.py's exact arithmetic ────────────────────────────────────

def _flat_history(ten, two, vix, as_of="2026-09-25"):
    pub = "2020-01-01"
    return MacroHistory([
        _vo("DGS10", as_of, pub, "9999-12-31", ten),
        _vo("DGS2", as_of, pub, "9999-12-31", two),
        _vo("VIXCLS", as_of, pub, "9999-12-31", vix),
        _vo("FEDFUNDS", as_of, pub, "9999-12-31", 5.25),   # present but must never affect the result
    ])


def test_risk_off_inverted_curve_and_high_vix():
    h = _flat_history(ten=4.05, two=4.60, vix=28.0)
    sig = historical_macro_signal(h, dt.date(2026, 9, 26), direction="LONG")
    assert sig["family"] == "macro-rates"
    assert sig["dir"] < 0
    assert sig["conf"] == 0.5


def test_risk_on_steep_curve_low_vix():
    h = _flat_history(ten=4.90, two=3.90, vix=14.0)
    sig = historical_macro_signal(h, dt.date(2026, 9, 26), direction="LONG")
    assert sig["dir"] == pytest.approx(1.0)         # spread=1.0 -> tilt clamps to +1.0 exactly


def test_tilt_is_linear_in_spread_up_to_the_clamp():
    h = _flat_history(ten=4.30, two=4.00, vix=10.0)          # spread 0.30 -> tilt 0.30 (no VIX penalty)
    sig = historical_macro_signal(h, dt.date(2026, 9, 26), direction="LONG")
    assert sig["dir"] == pytest.approx(0.30)


def test_direction_flips_the_sign_exactly():
    h = _flat_history(ten=4.90, two=3.90, vix=14.0)
    long_sig = historical_macro_signal(h, dt.date(2026, 9, 26), direction="LONG")
    short_sig = historical_macro_signal(h, dt.date(2026, 9, 26), direction="SHORT")
    assert long_sig["dir"] == pytest.approx(-short_sig["dir"])


def test_fedfunds_never_affects_the_signal_even_when_missing():
    with_fedfunds = _flat_history(ten=4.30, two=4.00, vix=10.0)
    without_fedfunds = MacroHistory([
        _vo("DGS10", "2026-09-25", "2020-01-01", "9999-12-31", 4.30),
        _vo("DGS2", "2026-09-25", "2020-01-01", "9999-12-31", 4.00),
        _vo("VIXCLS", "2026-09-25", "2020-01-01", "9999-12-31", 10.0),
        # no FEDFUNDS row at all
    ])
    a = historical_macro_signal(with_fedfunds, dt.date(2026, 9, 26))
    b = historical_macro_signal(without_fedfunds, dt.date(2026, 9, 26))
    assert a["dir"] == b["dir"]


def test_missing_curve_degrades_gracefully_vix_penalty_still_applies():
    h = MacroHistory([_vo("VIXCLS", "2026-09-25", "2020-01-01", "9999-12-31", 30.0)])   # no 10y/2y at all
    sig = historical_macro_signal(h, dt.date(2026, 9, 26), direction="LONG")
    assert sig["conf"] == 0.5                        # still "available" -- confidence is not data-driven
    assert sig["dir"] == pytest.approx(-0.3)          # curve term 0, VIX penalty -0.3


def test_no_data_at_all_is_zero_confidence_not_fabricated():
    sig = historical_macro_signal(MacroHistory([]), dt.date(2026, 1, 1))
    assert sig["conf"] == 0.0 and sig["dir"] == 0.0


# ── fetch_fred_vintages URL construction (mocked network -- no real key/call needed) ────────────────────────
# Regression guard for a real bug found building HIST-001's Medium macro dataset: a locally-computed
# `realtime_end` of wall-clock "today" was rejected by FRED with HTTP 400 ("can not be after today's date")
# because this process's UTC "today" was one day ahead of FRED's own server clock at call time -- a
# transient, environment-dependent failure mode. Fixed by using FRED's own "9999-12-31" real-time-max
# sentinel instead of a locally-computed date, which the API documents as always valid.

def test_fetch_fred_vintages_never_computes_realtime_end_from_the_wall_clock(monkeypatch):
    from research.historical import macro as macro_mod

    captured_urls = []

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"observations": []}'

    def _fake_urlopen(request, timeout=30):
        captured_urls.append(request.full_url)
        return _FakeResponse()

    monkeypatch.setattr(macro_mod.urllib.request, "urlopen", _fake_urlopen)
    macro_mod.fetch_fred_vintages("DGS10", "2024-01-01", "2024-06-30", api_key="fake-key-not-real")

    assert len(captured_urls) == 1
    assert "&realtime_end=9999-12-31&" in captured_urls[0]  # the sentinel, never a computed calendar date


# ── Local Parquet + manifest store: provenance, reproducibility, never the API key ─────────────────────────

@pytest.fixture()
def isolated_macro_root(tmp_path, monkeypatch):
    monkeypatch.setenv("AVDI_HISTORICAL_DATA_DIR", str(tmp_path / "historical"))
    yield


def test_save_and_load_macro_history_round_trips(isolated_macro_root):
    history = _flat_history(ten=4.30, two=4.00, vix=12.0, as_of="2026-09-25")
    manifest_path = save_macro_history(history, "h55_test_macro", date_range=("2026-09-01", "2026-09-25"))
    assert manifest_path.exists()

    loaded, manifest = load_macro_history("h55_test_macro")
    assert len(loaded) == len(history)
    assert manifest.source == "FRED"
    assert manifest.series_ids == sorted(SERIES.keys())
    assert manifest.api_version == API_VERSION
    assert manifest.row_count == len(history)
    assert manifest.date_range == ["2026-09-01", "2026-09-25"] or manifest.date_range == ("2026-09-01", "2026-09-25")
    assert "vintage" in manifest.vintage_semantics.lower()


def test_manifest_never_contains_the_api_key(isolated_macro_root):
    history = _flat_history(ten=4.30, two=4.00, vix=12.0)
    manifest_path = save_macro_history(history, "h55_no_key_test", date_range=("2026-09-01", "2026-09-25"),
                                       notes="a note mentioning nothing secret")
    text = manifest_path.read_text(encoding="utf-8")
    assert "SUPER_SECRET_FRED_KEY_VALUE" not in text     # sanity: the manifest writer path takes no key input at all
    import inspect

    from research.historical import macro as macro_mod
    assert "api_key" not in inspect.signature(macro_mod.save_macro_history).parameters


def test_verify_macro_manifest_detects_a_tampered_parquet_file(isolated_macro_root):
    history = _flat_history(ten=4.30, two=4.00, vix=12.0)
    save_macro_history(history, "h55_tamper_test", date_range=("2026-09-01", "2026-09-25"))
    v_ok = verify_macro_manifest("h55_tamper_test")
    assert v_ok["ok"]

    from research.historical.macro import _macro_root
    parquet_path = _macro_root() / "h55_tamper_test.parquet"
    with open(parquet_path, "ab") as fh:
        fh.write(b"tampered")
    v_bad = verify_macro_manifest("h55_tamper_test")
    assert not v_bad["ok"]


# ── _fam_macro wiring through a real HistoricalAVDIContext ─────────────────────────────────────────────────

def _tiny_daily_dataset(tmp_path, monkeypatch):
    import pandas as pd

    from research.historical.datasets.base import DatasetAdapter

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
    _FixedAdapter("h55_wiring_fixture", df).import_and_store(["ZZZ"], "2025-01-01", "2025-06-01", "1d")
    return "h55_wiring_fixture", days


def test_fam_macro_is_stubbed_by_default_and_replayed_when_macro_history_given(tmp_path, monkeypatch):
    from research.historical.avdi_adapter import HistoricalAVDIContext
    from research.historical.clock import HistoricalClock
    from research.historical.provider import HistoricalMarketProvider

    dataset_id, days = _tiny_daily_dataset(tmp_path, monkeypatch)
    clk = HistoricalClock(days[59].to_pydatetime())
    provider = HistoricalMarketProvider(clk, [dataset_id])

    with HistoricalAVDIContext(provider) as ctx:
        import decision_engine as de
        result = de._fam_macro("LONG")
        assert result["conf"] == 0.0
        assert not any(c["fn"] == "decision_engine._fam_macro" for c in ctx.calls)

    history = _flat_history(ten=4.90, two=3.90, vix=14.0, as_of="2025-01-01")
    with HistoricalAVDIContext(provider, macro_history=history) as ctx2:
        import decision_engine as de
        result = de._fam_macro("LONG")
        assert result["conf"] == 0.5
        assert result["dir"] == pytest.approx(1.0)
        assert any(c["fn"] == "decision_engine._fam_macro" for c in ctx2.calls)


# ── Capability fingerprint ────────────────────────────────────────────────────────────────────────────────

def test_price_trend_macro_v1_does_not_overwrite_price_trend_only_v1():
    from research.historical.capability import (
        PRICE_TREND_MACRO_V1, PRICE_TREND_ONLY_V1, known_fingerprints, price_trend_macro_v1,
        price_trend_only_v1, validate_fingerprint,
    )

    assert PRICE_TREND_ONLY_V1 != PRICE_TREND_MACRO_V1
    assert {PRICE_TREND_ONLY_V1, PRICE_TREND_MACRO_V1} <= set(known_fingerprints())
    validate_fingerprint(PRICE_TREND_ONLY_V1)
    validate_fingerprint(PRICE_TREND_MACRO_V1)

    old_fp = price_trend_only_v1()
    new_fp = price_trend_macro_v1()
    assert old_fp.tag == PRICE_TREND_ONLY_V1
    assert new_fp.tag == PRICE_TREND_MACRO_V1
    assert "_fam_macro" not in new_fp.stubbed_families
    assert "_fam_macro" in old_fp.stubbed_families
    assert "macro_rates" in new_fp.live_families
    assert "macro_rates" not in old_fp.live_families


# ── The load-bearing data_quality arithmetic, checked against the real source ───────────────────────────────

def test_replaying_macro_alone_crosses_the_data_quality_floor():
    import data_quality as dq

    baseline = {
        "price": dq.category_coverage("price", {"price": 100.0}, confidence=0.9),
        "candles": dq.category_coverage("candles", {"closes": [1], "highs": [1], "lows": [1]}, confidence=0.85),
        "sector": dq.category_coverage("sector", {"sector": "NASDAQ"}, confidence=0.6),
    }
    before = dq.assess(baseline, profile="momentum")
    assert before["overall"] == pytest.approx(0.536, abs=0.001)

    with_macro = dict(baseline)
    with_macro["macro"] = dq.category_coverage("macro", {"series": 1}, confidence=1.0)
    after = dq.assess(with_macro, profile="momentum")
    assert after["overall"] >= 0.55
    assert after["overall"] == pytest.approx(0.588, abs=0.005)
