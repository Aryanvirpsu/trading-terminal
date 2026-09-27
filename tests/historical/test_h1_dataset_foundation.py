"""H1 acceptance: schema validation catches malformed bars, the manifest round-trips with a verifiable
sha256, and a tiny real sample can be stored in Parquet and queried with DuckDB."""
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.historical.schemas.bars import BarValidationError, dataframe_sha256, validate_bars
from research.historical.datasets.base import DatasetAdapter, duckdb_view, load_parquet
from research.historical.manifest import load_manifest, verify_manifest


def _good_df():
    ts = pd.date_range("2026-09-24T13:30:00Z", periods=5, freq="5min", tz="UTC")
    return pd.DataFrame({"symbol": ["AAA"] * 5, "timestamp": ts, "open": [10.0, 10.1, 10.2, 10.1, 10.3],
                        "high": [10.2, 10.3, 10.3, 10.3, 10.4], "low": [9.9, 10.0, 10.1, 10.0, 10.2],
                        "close": [10.1, 10.2, 10.1, 10.3, 10.35], "volume": [100.0, 90, 80, 70, 60]})


def test_valid_bars_pass():
    r = validate_bars(_good_df())
    assert r.ok and r.failures == {} and r.rows == 5


@pytest.mark.parametrize("mutate,check", [
    (lambda df: df.assign(high=df["low"] - 1), "high_ge_low"),
    (lambda df: df.assign(high=df["open"] - 1), "high_ge_open"),
    (lambda df: df.assign(low=df["open"] + 100), "low_le_open"),
    (lambda df: df.assign(volume=[-1, 0, 0, 0, 0]), None),   # caught by the pandera Check.ge(0)
])
def test_malformed_bars_are_rejected(mutate, check):
    df = mutate(_good_df())
    with pytest.raises(BarValidationError) as e:
        validate_bars(df)
    if check:
        assert check in e.value.report.failures


def test_duplicate_symbol_timestamp_rejected():
    df = pd.concat([_good_df(), _good_df().iloc[[0]]], ignore_index=True)
    with pytest.raises(BarValidationError) as e:
        validate_bars(df)
    assert e.value.report.failures.get("duplicate_symbol_timestamp") == 1


def test_non_increasing_timestamps_rejected():
    df = _good_df()
    df.loc[2, "timestamp"] = df.loc[0, "timestamp"]
    with pytest.raises(BarValidationError) as e:
        validate_bars(df)
    assert "timestamps_not_increasing_per_symbol" in e.value.report.failures


def test_missing_required_column_rejected():
    df = _good_df().drop(columns=["volume"])
    with pytest.raises(BarValidationError) as e:
        validate_bars(df)
    assert "volume" in e.value.report.failures["missing_columns"]


def test_content_hash_is_stable_under_row_reordering():
    df = _good_df()
    h1 = dataframe_sha256(df)
    h2 = dataframe_sha256(df.iloc[::-1].reset_index(drop=True))
    assert h1 == h2
    h3 = dataframe_sha256(df.assign(close=df["close"] + 0.01))
    assert h3 != h1


class _FakeAdapter(DatasetAdapter):
    source_name = "fake-test-source"
    revision = "test"

    def __init__(self, dataset_id, df):
        super().__init__(dataset_id)
        self._df = df

    def fetch(self, symbols, start, end, timeframe):
        return self._df


@pytest.fixture()
def historical_root(tmp_path, monkeypatch):
    monkeypatch.setenv("AVDI_HISTORICAL_DATA_DIR", str(tmp_path / "historical"))
    yield tmp_path / "historical"


def test_import_and_store_then_load_and_verify(historical_root):
    m = _FakeAdapter("h1_smoke_test", _good_df()).import_and_store(["AAA"], "2026-09-24", "2026-09-25", "5m",
                                                                    notes="unit test fixture")
    assert m.rows == 5 and m.source == "fake-test-source" and m.symbols == ["AAA"]
    loaded_manifest = load_manifest("h1_smoke_test")
    assert loaded_manifest.sha256 == m.sha256
    v = verify_manifest("h1_smoke_test")
    assert v["ok"], v
    df2 = load_parquet("h1_smoke_test")
    pd.testing.assert_frame_equal(df2.reset_index(drop=True), _good_df().reset_index(drop=True), check_dtype=False)


def test_import_and_store_rejects_malformed_data(historical_root):
    bad = _good_df().assign(high=_good_df()["low"] - 1)
    with pytest.raises(BarValidationError):
        _FakeAdapter("h1_bad", bad).import_and_store(["AAA"], "2026-09-24", "2026-09-25", "5m")


def test_verify_manifest_detects_tampering(historical_root):
    _FakeAdapter("h1_tamper", _good_df()).import_and_store(["AAA"], "2026-09-24", "2026-09-25", "5m")
    m = load_manifest("h1_tamper")
    p = historical_root / m.parquet_path
    with open(p, "ab") as fh:
        fh.write(b"corruption")
    v = verify_manifest("h1_tamper")
    assert not v["ok"] and "sha256 mismatch" in v["problems"][0]


def test_duckdb_can_query_a_stored_dataset(historical_root):
    _FakeAdapter("h1_duck", _good_df()).import_and_store(["AAA"], "2026-09-24", "2026-09-25", "5m")
    con = duckdb_view(["h1_duck"])
    n = con.execute('SELECT COUNT(*) FROM "h1_duck"').fetchone()[0]
    assert n == 5
    mx = con.execute('SELECT MAX(high) FROM "h1_duck" WHERE symbol=\'AAA\'').fetchone()[0]
    assert mx == pytest.approx(10.4)
    con.close()


@pytest.mark.network
def test_h1_acceptance_tiny_real_sample(historical_root):
    """The literal H1 acceptance criterion: AAPL/MSFT/META/NVDA/DELL, a few trading days, 5-minute bars,
    real Yahoo data (bootstrap source — see yahoo_bootstrap.py), stored in Parquet, queried with DuckDB."""
    from research.historical.datasets.yahoo_bootstrap import YahooBootstrapAdapter

    syms = ["AAPL", "MSFT", "META", "NVDA", "DELL"]
    m = YahooBootstrapAdapter("h1_acceptance_sample").import_and_store(
        syms, "2026-09-23", "2026-09-26", "5m", notes="H1 acceptance sample (tiny; Yahoo bootstrap, not HF)")
    assert m.source == "yahoo-bootstrap" and m.rows > 0
    assert set(m.symbols) <= set(syms) and m.symbols, "at least some of the 5 symbols must have real data"
    assert verify_manifest("h1_acceptance_sample")["ok"]

    con = duckdb_view(["h1_acceptance_sample"])
    per_symbol = con.execute(
        'SELECT symbol, COUNT(*) n, MIN(timestamp) t0, MAX(timestamp) t1 FROM "h1_acceptance_sample" '
        'GROUP BY symbol ORDER BY symbol').fetchall()
    con.close()
    assert per_symbol
    for sym, n, t0, t1 in per_symbol:
        assert n > 0 and t1 > t0
