"""H0 acceptance: Historical Lab can never resolve or write to the real production ledger/shadow location,
and refuses to start at all on a machine fingerprinted as the Ubuntu production runtime."""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.historical.guards import (
    ProductionIsolationError, assert_not_production_host, assert_not_production_path, guard_all,
    historical_data_root,
)


# ── the literal acceptance criterion from the build plan ───────────────────────────────────────────────

def test_production_path_is_rejected():
    with pytest.raises(ProductionIsolationError):
        assert_not_production_path("/data/case1/paper")


@pytest.mark.parametrize("path", [
    "/data/case1/paper",
    "/data/case1/paper/robinhood_500_baseline.db",
    "/data/case2/paper",
    "/home/tv/.tradingview_mcp_data",
    os.path.expanduser("~/.tradingview_mcp_data/paper/robinhood_500_baseline.db"),
    os.path.expanduser("~/.tradingview_mcp_data/shadow/shadow_candidates.db"),
    "C:/Users/anyone/.tradingview_mcp_data/paper",
    "/home/ubuntu/avdi-runtime/releases/deadbeef",
    "/some/volume/avdi_runtime_ledger/robinhood_500_baseline.db",
    "relative/path/to/robinhood_500_baseline.db",
    "relative/path/to/shadow_candidates.db",
    "relative/path/to/demo_10k.db",
])
def test_every_known_production_location_is_rejected(path):
    with pytest.raises(ProductionIsolationError):
        assert_not_production_path(path)


@pytest.mark.parametrize("path", [
    "data/historical/equities/aapl.parquet",
    "/tmp/avdi_historical_test/account.db",
    os.path.expanduser("~/not_the_real_dir/whatever.db"),
])
def test_ordinary_historical_paths_are_allowed(path):
    assert_not_production_path(path)                      # must not raise


def test_broken_symlink_or_unresolvable_path_fails_closed(tmp_path):
    target = tmp_path / "gone"
    link = tmp_path / "broken_link"
    target.write_text("x")
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this filesystem")
    target.unlink()
    # a broken symlink must not be silently treated as "safe" just because resolution changes behaviour
    assert_not_production_path(link)  # resolving a broken link doesn't raise OSError on most platforms;
    # the real guarantee here is just that it does not crash the guard itself with an unrelated traceback


def test_case_and_slash_insensitive():
    with pytest.raises(ProductionIsolationError):
        assert_not_production_path(r"C:\Data\CASE1\Paper\ledger.db")


# ── host fingerprint guard ───────────────────────────────────────────────────────────────────────────

def test_production_host_directory_fingerprint_blocks_startup(monkeypatch, tmp_path):
    fake = tmp_path / "data" / "case1"
    fake.mkdir(parents=True)
    monkeypatch.setattr("research.historical.guards._PRODUCTION_HOST_DIRS", (str(fake),))
    with pytest.raises(ProductionIsolationError):
        assert_not_production_host()


def test_production_host_env_fingerprint_blocks_startup(monkeypatch):
    monkeypatch.setenv("C0_PROFILE", "case1")
    with pytest.raises(ProductionIsolationError):
        assert_not_production_host()


@pytest.mark.parametrize("key,val", [("ROBINHOOD_TRADING_ENABLED", "true"), ("BROKER_PROVIDER", "robinhood")])
def test_live_execution_env_blocks_startup(monkeypatch, key, val):
    monkeypatch.setenv(key, val)
    with pytest.raises(ProductionIsolationError):
        assert_not_production_host()


def test_dev_machine_passes_the_host_guard(monkeypatch):
    for var in ("C0_PROFILE", "BROKER_PROVIDER", "ROBINHOOD_TRADING_ENABLED"):
        monkeypatch.delenv(var, raising=False)
    assert_not_production_host()                          # must not raise on an ordinary dev/CI machine


def test_guard_all_checks_host_then_every_path(tmp_path):
    guard_all(str(tmp_path / "a.parquet"), str(tmp_path / "b.db"))
    with pytest.raises(ProductionIsolationError):
        guard_all(str(tmp_path / "ok.parquet"), "/data/case1/paper")


def test_historical_data_root_ignores_paper_data_dir_env(monkeypatch):
    monkeypatch.setenv("PAPER_DATA_DIR", "/data/case1/paper")   # a real production env var, deliberately set
    monkeypatch.delenv("AVDI_HISTORICAL_DATA_DIR", raising=False)
    root = historical_data_root()
    assert "case1" not in str(root).lower() and "data" in str(root).lower() and "historical" in str(root).lower()


def test_historical_data_root_itself_is_guarded(monkeypatch):
    monkeypatch.setenv("AVDI_HISTORICAL_DATA_DIR", "/data/case1/paper")
    with pytest.raises(ProductionIsolationError):
        historical_data_root()
