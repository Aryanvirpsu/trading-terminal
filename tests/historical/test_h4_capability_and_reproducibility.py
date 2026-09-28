"""H4 acceptance: the capability fingerprint names exactly what a historical run can and can't replay,
and re-importing the same source data through the same adapter path is byte-for-byte reproducible
(same rows, same content hash, same manifest) -- the repeatable-ingestion check the audit report requires
before a source can be used for H4."""
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.historical.capability import (
    PRICE_TREND_ONLY_V1, known_fingerprints, price_trend_only_v1, validate_fingerprint,
)
from research.historical.datasets.base import DatasetAdapter
from research.historical.manifest import load_manifest


def test_price_trend_only_v1_discloses_stubbed_families():
    fp = price_trend_only_v1()
    assert fp.tag == PRICE_TREND_ONLY_V1
    assert fp.volume_status == "RELATIVE_ONLY"
    # Every family H3's avdi_adapter actually stubs must be disclosed here -- if avdi_adapter grows a new
    # historical replay, this assertion (and the fingerprint's notes) must be updated deliberately, not
    # silently outgrown.
    from research.historical.avdi_adapter import FAMILIES_WITHOUT_HISTORICAL_REPLAY
    for fam in FAMILIES_WITHOUT_HISTORICAL_REPLAY:
        assert fam in fp.stubbed_families
    assert "sector_breadth_rotation" in fp.stubbed_families
    assert "capacity_and_sector_caps" in fp.live_families


def test_validate_fingerprint_accepts_known_rejects_unknown():
    validate_fingerprint(PRICE_TREND_ONLY_V1)          # does not raise
    assert PRICE_TREND_ONLY_V1 in known_fingerprints()
    with pytest.raises(ValueError):
        validate_fingerprint("FULL_CHAMPION_REPLAY_V1")   # does not exist -- must never be claimed


class _DeterministicAdapter(DatasetAdapter):
    """Stands in for a real HF adapter: same (symbols, start, end, timeframe) -> byte-identical output,
    the property the live fabhaus re-audit (FABHAUS_AUDIT_REPORT.md sec 8) demonstrated by hand against the
    real pinned HF revision. This test proves the property holds through `import_and_store` itself, using
    a fixture so CI never depends on network access."""
    source_name = "fake-hf-source"
    revision = "pinned-rev-abc123"
    hf_dataset_id = "fake/dataset"
    adapter_version = "test-1"

    def __init__(self, dataset_id):
        super().__init__(dataset_id)
        self.upstream_files = ["2024-06.jsonl"]
        self.upstream_sha256 = {"2024-06.jsonl": "f" * 64}
        self.selected_columns = ["symbol", "datetime", "open", "high", "low", "close", "volume"]

    def fetch(self, symbols, start, end, timeframe):
        ts = pd.date_range("2024-06-04T09:00:00Z", periods=6, freq="5min", tz="UTC")
        opens = [480.0, 481, 482, 483, 482.5, 481.8]
        closes = [480.5, 481.5, 482.5, 482.8, 481.9, 481.5]
        highs = [max(o, c) + 2.0 for o, c in zip(opens, closes)]
        lows = [min(o, c) - 2.0 for o, c in zip(opens, closes)]
        return pd.DataFrame({"symbol": ["NVDA"] * 6, "timestamp": ts, "open": opens, "high": highs,
                             "low": lows, "close": closes,
                             "volume": [10000.0, 9500, 11000, 10500, 9800, 10200]})


@pytest.fixture()
def historical_root(tmp_path, monkeypatch):
    monkeypatch.setenv("AVDI_HISTORICAL_DATA_DIR", str(tmp_path / "historical"))
    yield tmp_path / "historical"


def test_repeatable_ingestion_same_inputs_same_outputs(historical_root):
    m1 = _DeterministicAdapter("h4_repro_test").import_and_store(
        ["NVDA"], "2024-06-04", "2024-06-05", "5m", notes="repeatability check run 1")
    m2 = _DeterministicAdapter("h4_repro_test").import_and_store(
        ["NVDA"], "2024-06-04", "2024-06-05", "5m", notes="repeatability check run 2")

    assert m1.rows == m2.rows
    assert m1.sha256 == m2.sha256                      # identical content hash -> identical rows/timestamps
    assert m1.parquet_sha256 == m2.parquet_sha256       # identical bytes on disk
    assert m1.hf_repository == m2.hf_repository == "fake/dataset"
    assert m1.hf_revision == m2.hf_revision == "pinned-rev-abc123"
    assert m1.upstream_sha256 == m2.upstream_sha256
    assert m1.selected_columns == m2.selected_columns
    assert m1.adapter_version == m2.adapter_version

    reloaded = load_manifest("h4_repro_test")
    assert reloaded.sha256 == m2.sha256


def test_repeatable_ingestion_detects_a_real_change(historical_root):
    """The check must also be able to FAIL -- a hash that always matches proves nothing. Changing one
    value must change the content hash."""
    m1 = _DeterministicAdapter("h4_repro_change_test").import_and_store(["NVDA"], "2024-06-04", "2024-06-05", "5m")

    class _MutatedAdapter(_DeterministicAdapter):
        def fetch(self, symbols, start, end, timeframe):
            df = super().fetch(symbols, start, end, timeframe)
            df.loc[0, "close"] = df.loc[0, "close"] + 1.0
            return df

    m2 = _MutatedAdapter("h4_repro_change_test").import_and_store(["NVDA"], "2024-06-04", "2024-06-05", "5m")
    assert m1.sha256 != m2.sha256
