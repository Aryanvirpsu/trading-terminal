"""HIST-001 directive sec 8: Medium's six-month combination must be invariant to the order the monthly
batches happen to be processed in. Uses a small synthetic three-month fixture (not the real 90-symbol/6-month
fetch, which is a separate real-network step) so this runs fast and deterministically in the default suite.
"""
import json
import sys
from itertools import permutations
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.historical.hist001.build_medium_dataset import _aggregate_daily, load_filtered

MONTHS = ["2024-01", "2024-02", "2024-03"]


def _write_month(data_dir: Path, month: str, rows: list[dict]) -> None:
    path = data_dir / f"medium_{month}_filtered.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")


@pytest.fixture()
def synthetic_months(tmp_path):
    day_by_month = {"2024-01": "2024-01-15", "2024-02": "2024-02-15", "2024-03": "2024-03-15"}
    for month, day in day_by_month.items():
        rows = [
            {"symbol": "AAPL", "datetime": f"{day}T14:30:00Z", "open": 100.0, "high": 101.0, "low": 99.0,
             "close": 100.5, "volume": 1000.0, "trade_count": 10.0},
            {"symbol": "MSFT", "datetime": f"{day}T14:30:00Z", "open": 200.0, "high": 201.0, "low": 199.0,
             "close": 200.5, "volume": 2000.0, "trade_count": 20.0},
        ]
        _write_month(tmp_path, month, rows)
    return tmp_path


def test_load_filtered_is_invariant_to_month_processing_order(synthetic_months):
    baseline = load_filtered(MONTHS, data_dir=synthetic_months)
    for perm in permutations(MONTHS):
        combined = load_filtered(list(perm), data_dir=synthetic_months)
        pd.testing.assert_frame_equal(baseline, combined)


def test_aggregate_daily_is_invariant_to_month_processing_order(synthetic_months):
    baseline_daily = _aggregate_daily(load_filtered(MONTHS, data_dir=synthetic_months))
    for perm in permutations(MONTHS):
        daily = _aggregate_daily(load_filtered(list(perm), data_dir=synthetic_months))
        pd.testing.assert_frame_equal(baseline_daily, daily)


def test_combined_frame_has_exactly_the_expected_rows(synthetic_months):
    combined = load_filtered(MONTHS, data_dir=synthetic_months)
    assert len(combined) == 6                       # 2 symbols x 3 months x 1 bar each
    assert set(combined["symbol"]) == {"AAPL", "MSFT"}
    # sorted by (symbol, timestamp) regardless of which order the months were loaded in
    keys = list(zip(combined["symbol"], combined["timestamp"]))
    assert keys == sorted(keys)
