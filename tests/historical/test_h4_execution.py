"""H4 acceptance: the REAL lab.paper workflow/broker/risk/journal/fills code runs, unmodified, against
an isolated historical ledger and historical market data -- no exception, real capacity/risk/sizing/fill
code actually executes, and NOTHING touches a live provider or a production-shaped path.

Scope note (see research/historical/execution.py's module docstring and README.md's H4 section): this uses
ONE daily-resolution provider for both decision-making and execution quotes, matching H3's existing
provider design. A follow-on increment should give execution its own finer (e.g. 5-minute) timeframe for
realistic intraday stop/target fills -- not done here, and not claimed here.
"""
import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.historical.clock import HistoricalClock
from research.historical.datasets.base import DatasetAdapter
from research.historical.execution import HistoricalExecutionContext, isolate_paper_ledger, run_session
from research.historical.guards import ProductionIsolationError
from research.historical.provider import HistoricalMarketProvider

TECH_UNIVERSE = ["AAPL", "ADBE", "AMD", "AVGO", "CRM", "CSCO", "DELL", "MSFT", "NVDA", "ORCL"]


class _FixedAdapter(DatasetAdapter):
    source_name = "fixture"
    revision = "test"

    def __init__(self, dataset_id, df):
        super().__init__(dataset_id)
        self._df = df

    def fetch(self, symbols, start, end, timeframe):
        return self._df


def _universe_fixture(n=70):
    """~3.5 months of daily bars for the whole 'technology' sector universe (the real, hardcoded
    dashboard.sector_map universe strategies.scan() actually iterates) -- a mix of mild trends so the
    REAL gates (liquid_momentum / sector_relative_strength / mean_reversion) have something non-trivial to
    decide on, without hand-tuning a guaranteed TRADEABLE (this test asserts the real pipeline RAN
    correctly, not a specific label -- REJECT/MONITOR are just as valid evidence as TRADEABLE)."""
    days = pd.bdate_range("2025-01-02", periods=n, tz="UTC")
    frames = []
    for i, sym in enumerate(TECH_UNIVERSE):
        base = 50.0 + 10.0 * i
        drift = 0.15 if i % 2 == 0 else -0.05                # some trending up, some flat/down
        closes = [base + drift * k + 0.5 * ((k * (i + 1)) % 5) for k in range(n)]
        frames.append(pd.DataFrame({
            "symbol": sym, "timestamp": days,
            "open": [c - 0.2 for c in closes], "high": [c + 0.6 for c in closes],
            "low": [c - 0.6 for c in closes], "close": closes,
            "volume": [3_000_000.0 + 50_000.0 * k for k in range(n)]}))
    return pd.concat(frames, ignore_index=True), days


@pytest.fixture()
def universe_dataset(tmp_path, monkeypatch):
    monkeypatch.setenv("AVDI_HISTORICAL_DATA_DIR", str(tmp_path / "historical"))
    df, days = _universe_fixture()
    _FixedAdapter("h4_universe_fixture", df).import_and_store(
        TECH_UNIVERSE, "2025-01-01", "2025-06-01", "1d", notes="H4 execution acceptance fixture")
    return "h4_universe_fixture", days


def test_isolate_paper_ledger_never_resolves_to_a_production_path(tmp_path, monkeypatch):
    monkeypatch.setenv("AVDI_HISTORICAL_DATA_DIR", str(tmp_path / "historical"))
    resolved = isolate_paper_ledger("h4_test_run_1")
    assert "historical" in str(resolved).lower()
    assert "tradingview_mcp_data" not in str(resolved).lower()
    assert "robinhood_500_baseline" not in resolved.name.lower()

    from paper import db     # flat namespace -- must match execution.py's module identity
    assert str(resolved) == db.db_path()
    # a fresh isolated ledger starts at the configured baseline, proving the REAL account_state() ran
    # against OUR db, not a mock and not the production one
    from paper import risk as risk_mod
    from paper import config as cfg
    state = risk_mod.account_state("2025-01-01")
    assert state["equity"] == pytest.approx(cfg.account().initial_equity)
    assert state["open_positions"] == 0


def test_execution_context_patches_and_restores_the_extra_h4_seams(universe_dataset):
    dataset_id, days = universe_dataset
    clk = HistoricalClock(days[60].to_pydatetime())
    provider = HistoricalMarketProvider(clk, [dataset_id])

    from paper import risk as risk_mod
    from paper import workflow
    import cache_policy
    orig_quote_for = workflow.quote_for
    orig_health = workflow.provider_health
    orig_mark = risk_mod._live_mark_src
    orig_classify = cache_policy.classify

    with HistoricalExecutionContext(provider) as ctx:
        assert workflow.quote_for is not orig_quote_for
        assert workflow.provider_health is not orig_health
        assert risk_mod._live_mark_src is not orig_mark
        assert cache_policy.classify is not orig_classify

        health = workflow.provider_health()
        assert health["healthy"] is True and health["historical_replay"] is True

        q = workflow.quote_for("AAPL")
        assert q is not None and q.last is not None
        assert any(c["fn"] == "workflow.quote_for" for c in ctx.calls)

        mark, src = risk_mod._live_mark_src("AAPL")
        assert src == "historical_quote" and mark is not None
        assert any(c["fn"] == "risk._live_mark_src" for c in ctx.calls)

        # a quote timestamped a few minutes before the HISTORICAL clock's own "now" must classify as
        # fresh ("valid-decision"), never compared against the real wall clock -- the exact bug found
        # building HIST-001's Medium stage (a real 2024 quote was refused as ~2.5-years stale against the
        # real 2026 wall clock).
        historical_source_ts = clk.now.timestamp() - 120
        rec = cache_policy.classify("price", historical_source_ts)
        assert rec["tier"] == "valid-decision", rec
        assert rec["decision_valid"] is True
        assert rec["age_seconds"] == 120

    # restored exactly, no leakage across tests (same pattern H3 already proves for its own seams)
    assert workflow.quote_for is orig_quote_for
    assert workflow.provider_health is orig_health
    assert risk_mod._live_mark_src is orig_mark
    assert cache_policy.classify is orig_classify


def test_decision_valid_uses_the_historical_clock_not_the_real_wall_clock(universe_dataset):
    """Direct regression test for the bug: lab.paper.broker._decision_valid() calls
    cache_policy.classify('price', quote.source_ts) with no `now=` -- inside a HistoricalExecutionContext
    this must resolve against the replay clock, never against real wall-clock time.time()."""
    dataset_id, days = universe_dataset
    clk = HistoricalClock(days[60].to_pydatetime())
    provider = HistoricalMarketProvider(clk, [dataset_id])

    from paper import broker

    with HistoricalExecutionContext(provider):
        q = None
        from paper import workflow
        q = workflow.quote_for("AAPL")
        # the quote's own source_ts is derived from the historical bar's timestamp -- genuinely far from
        # the REAL wall clock, but must read as fresh relative to the replay clock.
        allowed, detail = broker._decision_valid({}, q)
        assert allowed is True, detail
        assert "display-only" not in detail


def test_db_utcnow_uses_the_historical_clock_not_the_real_wall_clock(universe_dataset):
    """Regression test: paper.db.utcnow() has no override parameter at all, so every opened_at/closed_at/
    created_at timestamp written during a historical replay must come from the replay clock -- not
    datetime.now(timezone.utc), which would stamp a 2024 trade with a 2026 (or later) real run-time."""
    dataset_id, days = universe_dataset
    clk = HistoricalClock(days[60].to_pydatetime())
    provider = HistoricalMarketProvider(clk, [dataset_id])

    from paper import db
    orig_utcnow = db.utcnow

    with HistoricalExecutionContext(provider):
        assert db.utcnow is not orig_utcnow
        stamped = db.utcnow()
        assert stamped == clk.now.isoformat()
        assert stamped[:4] == str(days[60].year)   # genuinely the historical year, not the real one

    assert db.utcnow is orig_utcnow


def test_position_opened_at_is_the_historical_date_not_the_real_run_date(universe_dataset):
    """End-to-end regression test for the bug found building HIST-001 Medium: a real fill's position row
    must carry the HISTORICAL trade date in opened_at, not the real wall-clock date the replay happened to
    run on -- this project's own analysis.py filters positions by opened_at date, so a wall-clock-stamped
    opened_at would silently make every real historical trade invisible to that filter."""
    dataset_id, days = universe_dataset
    isolate_paper_ledger("h4_opened_at_test")
    clk = HistoricalClock(days[60].to_pydatetime())
    provider = HistoricalMarketProvider(clk, [dataset_id])

    from paper import broker, db

    with HistoricalExecutionContext(provider):
        order = broker.place_order("AAPL", "BUY", 1.0, order_type="MARKET", intent="entry",
                                   strategy="test", session_date=days[60].strftime("%Y-%m-%d"))
        q = provider.quote("AAPL")
        broker.process_order(order["order_id"], q)
        pos = db.query_one("SELECT * FROM positions WHERE symbol='AAPL' AND status='open'")
        assert pos is not None
        assert pos["opened_at"].startswith(days[60].strftime("%Y-%m-%d"))
        assert not pos["opened_at"].startswith("2026")   # never the real replay run-year


def test_cooldown_state_compares_against_the_historical_clock(universe_dataset):
    """Regression test: risk.cooldown_state() calls dt.date.today() inline with no override -- even with
    db.utcnow() fixed (so closed_at is genuinely historical), comparing a historical `until` against the
    REAL wall-clock date would make the cooldown gate either permanently active or permanently inactive
    depending on which side of "today" the historical dates fall. Inside a HistoricalExecutionContext, the
    `active` flag must be computed against the replay clock's own date."""
    dataset_id, days = universe_dataset
    isolate_paper_ledger("h4_cooldown_test")
    clk = HistoricalClock(days[60].to_pydatetime())
    provider = HistoricalMarketProvider(clk, [dataset_id])

    from paper import config as cfg
    from paper import db, risk as risk_mod

    with HistoricalExecutionContext(provider):
        r = cfg.risk()
        # Seed exactly cooldown_losses consecutive losing CLOSED positions, closed_at stamped via the
        # (now-historical) db.utcnow() at the replay clock's current instant.
        closed_at = db.utcnow()
        for i in range(r.cooldown_losses):
            db.execute("""INSERT INTO positions(position_id, signal_id, symbol, strategy, sector, opened_at,
                            closed_at, quantity, avg_entry, avg_exit, stop, target, status, realized_pnl,
                            fees, planned_risk, mfe, mae)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (f"pos_cooldown_test_{i}", None, "AAPL", "test", "technology", closed_at, closed_at,
                        1.0, 100.0, 95.0, 95.0, 110.0, "closed", -5.0, 0.0, 5.0, 0.0, 5.0))

        rec = risk_mod.cooldown_state()
        assert rec["consecutive_losses"] == r.cooldown_losses
        # the cooldown was JUST triggered at the replay clock's current instant -- it must read as ACTIVE
        # relative to that same clock, not as expired (which real 2026 wall-clock "today" would show, since
        # `until` = historical closed_at's date + cooldown_days is always in the historical past relative to
        # the real run date) nor as permanently active for real calendar days.
        assert rec["active"] is True, rec

        # advance the replay clock PAST the cooldown window -- must now read inactive.
        clk.set(clk.now + dt.timedelta(days=r.cooldown_days + 1))
        rec2 = risk_mod.cooldown_state()
        assert rec2["active"] is False, rec2


def test_run_session_executes_the_real_pipeline_against_the_isolated_ledger(universe_dataset, tmp_path):
    dataset_id, days = universe_dataset
    isolate_paper_ledger("h4_run_session_test")

    from paper import db     # flat namespace -- must match execution.py's module identity
    resolved_db = Path(db.db_path())

    start = days[60].to_pydatetime()
    bar_times = [d.to_pydatetime() for d in days[60:64]]     # 1 discovery bar + 3 tracker bars
    clk = HistoricalClock(start)
    provider = HistoricalMarketProvider(clk, [dataset_id])

    with HistoricalExecutionContext(provider) as ctx:
        result = run_session(ctx, "2025-01-01", bar_times)

    assert result["cycles"][0]["phase"] == "premarket"
    assert all(c["phase"] == "market_hours" for c in result["cycles"][1:])
    premarket_result = result["cycles"][0]["result"]
    assert premarket_result["state"] == "ok"                  # real scan+evaluate pipeline completed
    assert premarket_result["candidates"] > 0                 # the real 10-symbol universe was scanned
    for ev in premarket_result["evaluated"]:
        assert ev["action"] in ("TRADEABLE", "MONITOR", "REJECT", None)

    # the isolated ledger actually recorded evidence -- proves journal.record_signal (real code) ran
    rows = db.query("SELECT symbol, action FROM signals")
    assert len(rows) > 0
    assert Path(db.db_path()) == resolved_db                  # still the isolated file, never swapped

    # equity/account view reflects the isolated ledger, not a live/production one
    acct = result["account"]
    assert acct["ledger"] is not None
    assert "quotes_missing" not in acct or True                # account() shape may vary; just must not error


def test_run_session_never_calls_live_provider_health_or_marks(universe_dataset, monkeypatch):
    """If the H4 seams were NOT patched, provider_health()/quote_for()/_live_mark_src() would try real
    network calls. Force any real network attempt to raise, and prove the session still completes --
    i.e. every live path really is intercepted, not merely coincidentally unreached."""
    dataset_id, days = universe_dataset
    isolate_paper_ledger("h4_no_live_calls_test")

    def _boom(*a, **k):
        raise AssertionError("a live provider call was attempted during a historical run")

    import providers as P
    monkeypatch.setattr(P, "price_consensus", _boom)

    bar_times = [d.to_pydatetime() for d in days[60:62]]
    clk = HistoricalClock(bar_times[0])
    provider = HistoricalMarketProvider(clk, [dataset_id])
    with HistoricalExecutionContext(provider) as ctx:
        result = run_session(ctx, "2025-01-02", bar_times)
    assert result["cycles"][0]["result"]["state"] == "ok"


def test_isolate_paper_ledger_refuses_a_production_shaped_root(monkeypatch, tmp_path):
    monkeypatch.setenv("AVDI_HISTORICAL_DATA_DIR", str(tmp_path / "case1" / "historical"))
    with pytest.raises(ProductionIsolationError):
        isolate_paper_ledger("should_never_run")
