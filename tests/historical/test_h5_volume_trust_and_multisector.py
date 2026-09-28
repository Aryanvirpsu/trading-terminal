"""H5 blockers #5/#6 acceptance: volume trust is enforced at runtime (not just documented), sector
coverage can span more than one sector without claiming real rotation, and execution can read a different
(finer) provider than decisions without either leaking into the other.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.historical.avdi_adapter import HistoricalAVDIContext
from research.historical.clock import HistoricalClock
from research.historical.datasets.base import DatasetAdapter
from research.historical.provider import HistoricalMarketProvider
from research.historical.volume_trust import ALL_STRATEGIES, VolumeTrust, enabled_strategies_for


class _FixedAdapter(DatasetAdapter):
    source_name = "fixture"
    revision = "test"

    def __init__(self, dataset_id, df):
        super().__init__(dataset_id)
        self._df = df

    def fetch(self, symbols, start, end, timeframe):
        return self._df


def _trending_series(symbol, n=70, base=100.0, drift=0.3):
    """A real uptrend (last > SMA20 > SMA50) with enough day-to-day give-back that RSI stays moderate --
    a PURELY monotonic climb saturates RSI near 100 regardless of drift size and would always fail
    score_liquid_momentum's own 'already extended' (RSI > 78) gate, which is not what this fixture is for."""
    days = pd.bdate_range("2025-01-02", periods=n, tz="UTC")
    closes = [base + drift * k + 1.5 * ((k * 7) % 5) for k in range(n)]
    return pd.DataFrame({"symbol": symbol, "timestamp": days, "open": [c - 0.1 for c in closes],
                         "high": [c + 0.3 for c in closes], "low": [c - 0.3 for c in closes],
                         "close": closes, "volume": [2_000_000.0] * n})


# ── volume_trust.py unit-level behavior ────────────────────────────────────────────────────────────────

def test_active_strategies_config_matches_volume_trust_module():
    """Guards against drift: if strategies.py ever grows a new strategy, this must fail loudly rather than
    silently leaving it untouched by the volume-trust filter."""
    from paper import config as cfg
    assert set(cfg.ACTIVE_STRATEGIES) == set(ALL_STRATEGIES)


@pytest.mark.parametrize("trust", [VolumeTrust.RELATIVE_ONLY, VolumeTrust.UNKNOWN, "RELATIVE_ONLY", "UNKNOWN"])
def test_liquid_momentum_excluded_for_untrusted_volume(trust):
    chosen = enabled_strategies_for(trust)
    assert "liquid_momentum" not in chosen
    assert "sector_relative_strength" in chosen and "mean_reversion" in chosen


def test_all_strategies_kept_for_absolute_volume():
    chosen = enabled_strategies_for(VolumeTrust.ABSOLUTE)
    assert set(chosen) == set(ALL_STRATEGIES)


def test_caller_requested_subset_still_filtered():
    chosen = enabled_strategies_for(VolumeTrust.RELATIVE_ONLY, requested=("liquid_momentum", "mean_reversion"))
    assert chosen == ("mean_reversion",)


# ── runtime enforcement through the REAL scan() path ───────────────────────────────────────────────────

@pytest.fixture()
def two_symbol_dataset(tmp_path, monkeypatch):
    monkeypatch.setenv("AVDI_HISTORICAL_DATA_DIR", str(tmp_path / "historical"))
    # AAPL: strongly trending + high volume -> would pass score_liquid_momentum's dollar-volume floor
    # and trend/RSI gates if that strategy were allowed to run.
    df = pd.concat([_trending_series("AAPL", drift=0.6), _trending_series("META", drift=0.6)], ignore_index=True)
    _FixedAdapter("h5_volume_trust_fixture", df).import_and_store(["AAPL", "META"], "2025-01-01", "2025-06-01", "1d")
    return "h5_volume_trust_fixture"


def _scan_strategies_seen(dataset_id, volume_trust, tmp_path=None):
    clk = HistoricalClock(pd.Timestamp("2025-04-10", tz="UTC"))
    provider = HistoricalMarketProvider(clk, [dataset_id], volume_trust=volume_trust)
    with HistoricalAVDIContext(provider, neutral_sector=["technology", "communication"]) as ctx:
        scan = ctx.scan()
    assert scan["state"] == "ok"
    return {c["strategy"] for c in scan["candidates"]}


def test_relative_only_provider_never_produces_a_liquid_momentum_candidate(two_symbol_dataset):
    strategies_seen = _scan_strategies_seen(two_symbol_dataset, VolumeTrust.RELATIVE_ONLY)
    assert "liquid_momentum" not in strategies_seen


def test_unknown_provider_defaults_to_untrusted_too(two_symbol_dataset):
    strategies_seen = _scan_strategies_seen(two_symbol_dataset, VolumeTrust.UNKNOWN)
    assert "liquid_momentum" not in strategies_seen


def test_absolute_provider_can_produce_a_liquid_momentum_candidate(two_symbol_dataset):
    strategies_seen = _scan_strategies_seen(two_symbol_dataset, VolumeTrust.ABSOLUTE)
    # A strongly trending, high-volume fixture should clear score_liquid_momentum's gates when it is
    # actually allowed to run -- proving the RELATIVE_ONLY/UNKNOWN tests above are testing a real
    # exclusion, not just an accidentally-never-firing strategy.
    assert "liquid_momentum" in strategies_seen


def test_workflow_premarket_also_respects_volume_trust(two_symbol_dataset):
    """The enforcement must cover workflow.premarket()'s own internal strategies.scan() call (no explicit
    strategies= override), not only a direct ctx.scan()."""
    from research.historical.execution import HistoricalExecutionContext, isolate_paper_ledger

    isolate_paper_ledger("h5_volume_trust_premarket_test")
    clk = HistoricalClock(pd.Timestamp("2025-04-10", tz="UTC"))
    provider = HistoricalMarketProvider(clk, [two_symbol_dataset], volume_trust=VolumeTrust.RELATIVE_ONLY)
    with HistoricalExecutionContext(provider, neutral_sector=["technology", "communication"]) as ctx:
        from paper import workflow
        result = workflow.premarket("2025-04-10")
    assert result["state"] == "ok"
    liquid_momentum_calls = [c for c in ctx.calls if c.get("fn") == "config.enabled_strategies"]
    assert liquid_momentum_calls, "cfg.enabled_strategies() was never consulted -- test fixture problem"
    assert all("liquid_momentum" not in c["enabled"] for c in liquid_momentum_calls)


# ── multi-sector membership (no rotation claimed) ──────────────────────────────────────────────────────

def test_multi_sector_scan_sees_members_of_every_listed_sector(two_symbol_dataset):
    """AAPL is 'technology' and META is 'communication' in the real sector map; a single neutral_sector
    would only ever see one of them. This proves both are reachable when both sectors are listed."""
    clk = HistoricalClock(pd.Timestamp("2025-04-10", tz="UTC"))
    provider = HistoricalMarketProvider(clk, [two_symbol_dataset], volume_trust=VolumeTrust.ABSOLUTE)
    with HistoricalAVDIContext(provider, neutral_sector=["technology", "communication"]) as ctx:
        scan = ctx.scan()
    symbols_seen = {c["symbol"] for c in scan["candidates"]}
    assert "AAPL" in symbols_seen
    assert "META" in symbols_seen


def test_single_sector_scan_only_reaches_that_sectors_symbols(two_symbol_dataset):
    clk = HistoricalClock(pd.Timestamp("2025-04-10", tz="UTC"))
    provider = HistoricalMarketProvider(clk, [two_symbol_dataset], volume_trust=VolumeTrust.ABSOLUTE)
    with HistoricalAVDIContext(provider, neutral_sector="technology") as ctx:      # single string, old form
        scan = ctx.scan()
    symbols_seen = {c["symbol"] for c in scan["candidates"]}
    assert "AAPL" in symbols_seen
    assert "META" not in symbols_seen        # communication was never listed -> unreachable, as before H5


# ── separate decision vs. execution providers ──────────────────────────────────────────────────────────

def test_execution_provider_is_used_for_quotes_not_the_decision_provider(tmp_path, monkeypatch):
    from research.historical.execution import HistoricalExecutionContext

    monkeypatch.setenv("AVDI_HISTORICAL_DATA_DIR", str(tmp_path / "historical"))
    decision_df = _trending_series("AAPL", base=100.0, drift=0.1)          # decision feed: ~100s
    exec_df = _trending_series("AAPL", base=900.0, drift=0.1)              # execution feed: ~900s (distinguishable)
    _FixedAdapter("h5_decision_feed", decision_df).import_and_store(["AAPL"], "2025-01-01", "2025-06-01", "1d")
    _FixedAdapter("h5_execution_feed", exec_df).import_and_store(["AAPL"], "2025-01-01", "2025-06-01", "1d")

    ts = pd.Timestamp("2025-04-10", tz="UTC")
    decision_clk = HistoricalClock(ts)
    exec_clk = HistoricalClock(ts)
    decision_provider = HistoricalMarketProvider(decision_clk, ["h5_decision_feed"])
    exec_provider = HistoricalMarketProvider(exec_clk, ["h5_execution_feed"])

    with HistoricalExecutionContext(decision_provider, execution_provider=exec_provider) as ctx:
        from paper import workflow
        q = workflow.quote_for("AAPL")
        assert q is not None
        assert q.last > 500                     # came from the execution feed (~900s), not decision (~100s)

        a, _, _ = ctx._patched_load_analysis("AAPL", "NASDAQ")
        assert a["price_data"]["current_price"] < 500      # decision analysis still uses the decision feed


def test_no_execution_provider_falls_back_to_the_single_provider(tmp_path, monkeypatch):
    """Backward compatibility: a caller that never passes execution_provider (all of H3/H4's existing
    tests) must see identical behavior to before this change."""
    from research.historical.execution import HistoricalExecutionContext

    monkeypatch.setenv("AVDI_HISTORICAL_DATA_DIR", str(tmp_path / "historical"))
    df = _trending_series("AAPL")
    _FixedAdapter("h5_single_feed", df).import_and_store(["AAPL"], "2025-01-01", "2025-06-01", "1d")
    clk = HistoricalClock(pd.Timestamp("2025-04-10", tz="UTC"))
    provider = HistoricalMarketProvider(clk, ["h5_single_feed"])
    with HistoricalExecutionContext(provider) as ctx:
        assert ctx.execution_provider is provider
        from paper import workflow
        q = workflow.quote_for("AAPL")
        assert q is not None
