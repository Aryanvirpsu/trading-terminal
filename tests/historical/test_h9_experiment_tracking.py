"""H9 acceptance: every historical run binds git commit, dataset manifest hashes, capability fingerprint,
strategy version, parameters, date range, walk-forward split, and the outcome metric set -- via MLflow,
under an isolated tracking store, never the production env.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.historical.experiment_tracking import (
    EXPERIMENT_NAME, RunMetadata, RunMetrics, get_run_summary, log_run, net_r_and_expectancy, tracking_uri,
)
from research.historical.guards import ProductionIsolationError


@pytest.fixture()
def isolated_tracking(tmp_path, monkeypatch):
    monkeypatch.setenv("AVDI_HISTORICAL_DATA_DIR", str(tmp_path / "historical"))
    yield


def test_tracking_uri_is_isolated_and_never_a_production_path(isolated_tracking):
    uri = tracking_uri()
    assert "historical" in uri.lower()
    assert "tradingview_mcp_data" not in uri.lower()


def test_tracking_uri_refuses_a_production_shaped_root(monkeypatch, tmp_path):
    monkeypatch.setenv("AVDI_HISTORICAL_DATA_DIR", str(tmp_path / "case1" / "historical"))
    with pytest.raises(ProductionIsolationError):
        tracking_uri()


def test_log_run_binds_every_required_field_and_reads_back(isolated_tracking):
    metadata = RunMetadata(
        capability_fingerprint="PRICE_TREND_ONLY_V1", strategy_version="champion-v1.1",
        date_range=("2026-09-25", "2026-09-25"),
        dataset_manifests={"KNOWN_FORWARD_2026_09_25_DAILY": "abc123", "KNOWN_FORWARD_2026_09_25_5M": "def456"},
        parameters={"max_finalists": 5, "risk_per_trade_pct": 0.01}, walk_forward_split="development_1",
        git_commit="deadbeef")
    metrics = RunMetrics(independent_events=12, trades=0, net_r=0.0, expectancy_r=0.0, max_drawdown_r=0.0,
                         mfe_r_avg=0.15, mae_r_avg=-0.12, capacity_blocks=2,
                         funnel_losses={"data_quality": 3, "sector_capacity": 1})

    run_id = log_run(metadata, metrics, run_name="h9_test_run")
    summary = get_run_summary(run_id)

    assert summary["tags"]["capability_fingerprint"] == "PRICE_TREND_ONLY_V1"
    assert summary["tags"]["strategy_version"] == "champion-v1.1"
    assert summary["tags"]["git_commit"] == "deadbeef"
    assert summary["tags"]["walk_forward_split"] == "development_1"
    assert summary["tags"]["date_range_start"] == "2026-09-25"

    assert summary["params"]["dataset.KNOWN_FORWARD_2026_09_25_DAILY.sha256"] == "abc123"
    assert summary["params"]["max_finalists"] == "5"          # mlflow params are always strings

    assert summary["metrics"]["independent_events"] == 12.0
    assert summary["metrics"]["capacity_blocks"] == 2.0
    assert summary["metrics"]["funnel_loss.data_quality"] == 3.0
    assert summary["metrics"]["funnel_loss.sector_capacity"] == 1.0
    assert "profit_factor" not in summary["metrics"]           # None -> omitted, never logged as 0 or null


def test_log_run_fills_in_git_commit_when_not_given(isolated_tracking):
    metadata = RunMetadata(capability_fingerprint="PRICE_TREND_ONLY_V1", strategy_version="v1",
                           date_range=("2026-01-01", "2026-01-02"), dataset_manifests={})
    metrics = RunMetrics(independent_events=1, trades=0, net_r=0.0, expectancy_r=0.0, max_drawdown_r=0.0,
                         mfe_r_avg=0.0, mae_r_avg=0.0)
    run_id = log_run(metadata, metrics)
    summary = get_run_summary(run_id)
    assert summary["tags"]["git_commit"] != ""                # either a real commit or "unknown", never empty


def test_two_runs_land_in_the_same_experiment(isolated_tracking):
    import mlflow

    metadata = RunMetadata(capability_fingerprint="PRICE_TREND_ONLY_V1", strategy_version="v1",
                           date_range=("2026-01-01", "2026-01-02"), dataset_manifests={})
    metrics = RunMetrics(independent_events=1, trades=0, net_r=0.0, expectancy_r=0.0, max_drawdown_r=0.0,
                         mfe_r_avg=0.0, mae_r_avg=0.0)
    id1 = log_run(metadata, metrics, run_name="run_a")
    id2 = log_run(metadata, metrics, run_name="run_b")
    assert id1 != id2

    mlflow.set_tracking_uri(tracking_uri())
    exp = mlflow.get_experiment_by_name(EXPERIMENT_NAME)
    assert exp is not None
    runs = mlflow.search_runs(experiment_ids=[exp.experiment_id])
    assert len(runs) >= 2


# ── net_r_and_expectancy ─────────────────────────────────────────────────────────────────────────────────

def test_net_r_and_expectancy_empty_trades():
    result = net_r_and_expectancy([])
    assert result == {"net_r": 0.0, "expectancy_r": 0.0, "profit_factor": None, "max_drawdown_r": 0.0}


def test_net_r_and_expectancy_mixed_trades():
    trades = [2.0, -1.0, 1.5, -1.0, 3.0]
    result = net_r_and_expectancy(trades)
    assert result["net_r"] == pytest.approx(4.5)
    assert result["expectancy_r"] == pytest.approx(0.9)
    assert result["profit_factor"] == pytest.approx(6.5 / 2.0)


def test_net_r_and_expectancy_no_losses_profit_factor_is_none():
    result = net_r_and_expectancy([1.0, 2.0, 0.5])
    assert result["profit_factor"] is None


def test_net_r_and_expectancy_max_drawdown_in_cumulative_r():
    # cumulative: 2, 1, 3, 0, 2  -> peak-to-trough drawdowns: (2-1)=1, (3-0)=3, (3-2)=1 -> max 3
    trades = [2.0, -1.0, 2.0, -3.0, 2.0]
    result = net_r_and_expectancy(trades)
    assert result["max_drawdown_r"] == pytest.approx(3.0)
