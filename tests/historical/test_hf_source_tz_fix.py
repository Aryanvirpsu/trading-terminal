"""Regression test for the mislabelled-timestamp defect the fabhaus audit found: when a HF source's own
`timestamp` column is documented as UTC but is actually exchange-local wall-clock time, `source_tz` must
correctly re-localize it — a naive `pd.to_datetime(..., utc=True)` would silently shift every bar by 4-5
hours, corrupting every session/gate alignment downstream."""
import sys
import types
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.historical.datasets.huggingface_equities import HuggingFaceEquitiesAdapter


def _fake_hf_dataset(monkeypatch):
    """A fake `datasets` module whose `load_dataset(...)` returns one bar timestamped '09:30:00' with NO
    timezone marker — exactly like fabhaus's `datetime` field (which the audit found is America/New_York
    wall-clock despite carrying a "Z" suffix; the adapter strips tz info before re-localizing either way)."""
    df = pd.DataFrame({"symbol": ["AAPL"], "timestamp": ["2024-01-02T09:30:00"], "open": [190.0],
                       "high": [190.5], "low": [189.5], "close": [190.2], "volume": [1000]})

    class _FakeDS:
        def to_pandas(self):
            return df

    fake_module = types.SimpleNamespace(load_dataset=lambda *a, **k: _FakeDS())
    monkeypatch.setitem(sys.modules, "datasets", fake_module)


def test_source_tz_correctly_relocalizes_mislabelled_timestamps(monkeypatch):
    _fake_hf_dataset(monkeypatch)
    a = HuggingFaceEquitiesAdapter("t1", "fake/repo", revision="deadbeef", source_tz="America/New_York")
    df = a.fetch(["AAPL"], "2024-01-01", "2024-01-03", "5m")
    assert len(df) == 1
    # 09:30 ET on 2024-01-02 (EST, UTC-5) is 14:30 UTC — NOT 09:30 UTC, which a naive parse would produce
    assert df.iloc[0]["timestamp"] == pd.Timestamp("2024-01-02T14:30:00", tz="UTC")


def test_without_source_tz_the_naive_utc_parse_is_used(monkeypatch):
    _fake_hf_dataset(monkeypatch)
    a = HuggingFaceEquitiesAdapter("t2", "fake/repo", revision="deadbeef")     # no source_tz
    df = a.fetch(["AAPL"], "2024-01-01", "2024-01-03", "5m")
    assert df.iloc[0]["timestamp"] == pd.Timestamp("2024-01-02T09:30:00", tz="UTC")


def test_manifest_records_extended_hf_provenance(monkeypatch, tmp_path):
    monkeypatch.setenv("AVDI_HISTORICAL_DATA_DIR", str(tmp_path / "historical"))
    _fake_hf_dataset(monkeypatch)
    a = HuggingFaceEquitiesAdapter("t3", "fabhaus/equities_5m_stockprices",
                                   revision="f17c0b0c3cf6a455994f93d6a85e76274172ab03",
                                   source_tz="America/New_York", data_files="2024-01.jsonl")
    m = a.import_and_store(["AAPL"], "2024-01-01", "2024-01-03", "5m")
    assert m.hf_repository == "fabhaus/equities_5m_stockprices"
    assert m.hf_revision == "f17c0b0c3cf6a455994f93d6a85e76274172ab03"
    assert m.upstream_files == ["2024-01.jsonl"]
    assert set(m.selected_columns) == {"symbol", "timestamp", "open", "high", "low", "close", "volume"}
    assert m.adapter_version == HuggingFaceEquitiesAdapter.adapter_version
