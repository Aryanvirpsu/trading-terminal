"""H9 — MLflow experiment tracking, so provenance stops being "hundreds of manually-named folders."

Every run binds exactly the fields the H9 directive asks for: git commit, dataset manifest(s) and their
content hashes, the capability fingerprint, strategy version, parameters, date range, which walk-forward
split this run belongs to, and the outcome metrics (independent events, trades, net R, expectancy, profit
factor, max drawdown, MFE/MAE, funnel losses, capacity blocks).

Tracking store lives under `historical_data_root()/mlruns` (guarded, like every other Historical Lab
output) -- never a function of any production env var, and never installed/pointed at anything the Ubuntu
runtime could see.
"""
from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .guards import assert_not_production_path, guard_all, historical_data_root

EXPERIMENT_NAME = "avdi_historical_lab"


def tracking_uri() -> str:
    # MLflow 3.x's plain filesystem tracking store ("file:...") is in maintenance mode and refuses to
    # start without an explicit opt-out; a SQLite-backed store is the current, supported local option and
    # needs no server of its own -- still just one file under the guarded historical data root.
    root = historical_data_root()
    mlruns_dir = root / "mlruns"
    assert_not_production_path(mlruns_dir)
    mlruns_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(mlruns_dir / 'mlflow.db').as_posix()}"


def current_git_commit(*, cwd: Optional[str] = None) -> Optional[str]:
    """Best-effort short commit hash for provenance. Returns None (never raises) if git isn't available or
    this isn't a git checkout -- a run must still be logged even when this can't be determined, just with
    the gap disclosed as None rather than a fabricated placeholder."""
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, timeout=10)
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return None


@dataclasses.dataclass(frozen=True)
class RunMetadata:
    capability_fingerprint: str
    strategy_version: str
    date_range: Tuple[str, str]
    dataset_manifests: Dict[str, str]          # dataset_id -> content sha256 (manifest.sha256)
    parameters: Dict[str, Any] = dataclasses.field(default_factory=dict)
    walk_forward_split: Optional[str] = None    # a WalkForwardPlan period name, or None outside a fold
    git_commit: Optional[str] = None            # filled from current_git_commit() if not given


@dataclasses.dataclass(frozen=True)
class RunMetrics:
    independent_events: int
    trades: int
    net_r: float
    expectancy_r: float
    max_drawdown_r: float
    mfe_r_avg: float
    mae_r_avg: float
    capacity_blocks: int = 0
    profit_factor: Optional[float] = None       # None when there are no losing trades to divide by
    funnel_losses: Dict[str, int] = dataclasses.field(default_factory=dict)   # stage -> count lost there


def log_run(metadata: RunMetadata, metrics: RunMetrics, *, run_name: Optional[str] = None) -> str:
    """Starts, populates and ends one MLflow run under EXPERIMENT_NAME. Returns the run_id."""
    import mlflow

    guard_all()
    mlflow.set_tracking_uri(tracking_uri())
    mlflow.set_experiment(EXPERIMENT_NAME)

    commit = metadata.git_commit or current_git_commit()
    with mlflow.start_run(run_name=run_name) as run:
        tags = {
            "capability_fingerprint": metadata.capability_fingerprint,
            "strategy_version": metadata.strategy_version,
            "git_commit": commit or "unknown",
            "walk_forward_split": metadata.walk_forward_split or "none",
            "date_range_start": metadata.date_range[0],
            "date_range_end": metadata.date_range[1],
        }
        mlflow.set_tags(tags)

        params: Dict[str, Any] = dict(metadata.parameters)
        for did, sha in metadata.dataset_manifests.items():
            params[f"dataset.{did}.sha256"] = sha
        mlflow.log_params(params)

        metric_values: Dict[str, float] = {
            "independent_events": metrics.independent_events, "trades": metrics.trades,
            "net_r": metrics.net_r, "expectancy_r": metrics.expectancy_r,
            "max_drawdown_r": metrics.max_drawdown_r, "mfe_r_avg": metrics.mfe_r_avg,
            "mae_r_avg": metrics.mae_r_avg, "capacity_blocks": metrics.capacity_blocks,
        }
        if metrics.profit_factor is not None:
            metric_values["profit_factor"] = metrics.profit_factor
        for stage, count in metrics.funnel_losses.items():
            metric_values[f"funnel_loss.{stage}"] = count
        mlflow.log_metrics(metric_values)

        return run.info.run_id


def get_run_summary(run_id: str) -> Dict[str, Any]:
    """Read back a logged run's tags/params/metrics -- for tests, and for a report that needs to cite a
    specific prior run rather than re-deriving its numbers."""
    import mlflow

    mlflow.set_tracking_uri(tracking_uri())
    run = mlflow.get_run(run_id)
    return {"run_id": run_id, "tags": dict(run.data.tags), "params": dict(run.data.params),
           "metrics": dict(run.data.metrics)}


def list_runs(*, max_results: int = 50) -> List[Dict[str, Any]]:
    import mlflow

    mlflow.set_tracking_uri(tracking_uri())
    try:
        exp = mlflow.get_experiment_by_name(EXPERIMENT_NAME)
    except Exception:
        return []
    if exp is None:
        return []
    df = mlflow.search_runs(experiment_ids=[exp.experiment_id], max_results=max_results)
    return df.to_dict("records") if hasattr(df, "to_dict") else []


def net_r_and_expectancy(trade_rs: List[float]) -> Dict[str, Optional[float]]:
    """Small, honest helper: given a list of per-trade net-R outcomes (from outcomes.OutcomeResult.net_r,
    resolved trades only -- never include still_open/no_data rows), compute net R, expectancy (mean R per
    trade), profit factor (gross win R / gross loss R, None if there are no losses to divide by), and max
    drawdown in cumulative-R terms. Kept here (not duplicated at every call site) since every H9 run needs
    exactly this shape."""
    if not trade_rs:
        return {"net_r": 0.0, "expectancy_r": 0.0, "profit_factor": None, "max_drawdown_r": 0.0}
    gross_win = sum(r for r in trade_rs if r > 0)
    gross_loss = -sum(r for r in trade_rs if r < 0)
    net_r = sum(trade_rs)
    expectancy_r = net_r / len(trade_rs)
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else None
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in trade_rs:
        cumulative += r
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
    return {"net_r": round(net_r, 4), "expectancy_r": round(expectancy_r, 4),
           "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
           "max_drawdown_r": round(max_dd, 4)}
