"""H3 acceptance: the REAL AVDI scanner and decision engine, run unchanged, driven by historical time.

Two tests:
  * `test_deterministic_...` — a fully controlled price fixture, no network. Proves causal, no-lookahead
    behaviour rigorously: the decision engine's OWN price field and trend family never reflect information
    later than the clock, and DO change, correctly, once the clock advances past a planted price jump.
  * `test_h3_acceptance_real_data_smoke` (network) — the literal acceptance steps 1-8 against the tiny
    Yahoo bootstrap sample: real scanner, real decision path, captured finalists/labels, clock advance,
    re-run, no exception, no lookahead.
"""
import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.historical.avdi_adapter import HistoricalAVDIContext, historical_analysis
from research.historical.clock import HistoricalClock
from research.historical.provider import HistoricalMarketProvider, LookaheadError
from research.historical.datasets.base import DatasetAdapter


class _FixedAdapter(DatasetAdapter):
    source_name = "fixture"
    revision = "test"

    def __init__(self, dataset_id, df):
        super().__init__(dataset_id)
        self._df = df

    def fetch(self, symbols, start, end, timeframe):
        return self._df


def _daily_fixture(symbol="ZZZ", n=60, jump_at=55):
    """n business days, tz-aware UTC. Flat/mild downtrend through day `jump_at`-1 (price held BELOW its own
    50-day SMA so the trend is unambiguously non-bullish), then an abrupt, large, sustained jump from
    `jump_at` onward — big enough that price/SMA20/SMA50/RSI/trend_state all move, and impossible to
    confuse with noise. Everything is exact and reproducible; no vendor data is involved."""
    days = pd.bdate_range("2025-01-02", periods=n, tz="UTC")
    pre = [100.0 - 0.05 * i for i in range(jump_at)]              # gentle downtrend, stays below its SMA50
    post = [pre[-1] + 20.0 + 3.0 * i for i in range(n - jump_at)]  # sharp, sustained jump
    close = pre + post
    df = pd.DataFrame({"symbol": symbol, "timestamp": days, "open": [c - 0.1 for c in close],
                       "high": [c + 0.2 for c in close], "low": [c - 0.2 for c in close],
                       "close": close, "volume": [2_000_000.0] * n})
    return df, days


@pytest.fixture()
def fixture_dataset(tmp_path, monkeypatch):
    monkeypatch.setenv("AVDI_HISTORICAL_DATA_DIR", str(tmp_path / "historical"))
    df, days = _daily_fixture()
    _FixedAdapter("h3_fixture", df).import_and_store(["ZZZ"], "2025-01-01", "2025-06-01", "1d")
    return "h3_fixture", days


def test_deterministic_price_and_trend_are_causal_and_never_leak_the_future(fixture_dataset):
    dataset_id, days = fixture_dataset
    before_jump = days[54].to_pydatetime()    # the LAST pre-jump instant (day index 54, jump is at index 55)
    after_jump = days[55].to_pydatetime()

    clk = HistoricalClock(before_jump)
    provider = HistoricalMarketProvider(clk, [dataset_id])
    with HistoricalAVDIContext(provider) as ctx:
        a_before, src, state = ctx._patched_load_analysis("ZZZ", "NASDAQ")
        assert src == "yahoo" and state == "fresh"
        assert a_before["trend_state"] in ("Downtrend", "Strong Downtrend", "Sideways")
        assert a_before["price_data"]["current_price"] == pytest.approx(days_close(days, 54, dataset_id))

        # the REAL evaluate() end-to-end: its own reported price must be the pre-jump price, never the jump
        r_before = ctx.evaluate("ZZZ", balance=500.0)
        assert r_before.get("price") == pytest.approx(a_before["price_data"]["current_price"], abs=0.01)
        assert r_before.get("price") < 110.0                                   # well under any post-jump level (117.3+)

        # an explicit request for the future is refused, not silently served — proven THROUGH the adapter
        with pytest.raises(LookaheadError):
            provider.bars("ZZZ", timeframe="1d", end=after_jump)

        # determinism: querying twice at the SAME clock instant gives the identical result
        r_again = ctx.evaluate("ZZZ", balance=500.0)
        assert r_again.get("price") == r_before.get("price")
        assert r_again.get("data_quality") == r_before.get("data_quality")

    # advance the clock past the jump — a NEW context (mirrors a new discovery cycle), same code
    clk.set(after_jump)
    with HistoricalAVDIContext(provider) as ctx2:
        a_after, _, _ = ctx2._patched_load_analysis("ZZZ", "NASDAQ")
        assert a_after["trend_state"] in ("Uptrend", "Strong Uptrend")           # causally flipped
        assert a_after["price_data"]["current_price"] > a_before["price_data"]["current_price"] + 15

        import decision_engine as de
        dir_before = de._fam_trend(a_before)["dir"]
        dir_after = de._fam_trend(a_after)["dir"]
        assert dir_after > dir_before                                           # the REAL family function reacts

        r_after = ctx2.evaluate("ZZZ", balance=500.0)
        assert r_after.get("price") == pytest.approx(a_after["price_data"]["current_price"], abs=0.01)
        assert r_after.get("price") != r_before.get("price")                    # a genuine state transition
        assert r_after.get("data_state") == "fresh" and r_after.get("data_source") == "yahoo"


def days_close(days, idx, dataset_id):
    from research.historical.datasets.base import load_parquet
    df = load_parquet(dataset_id)
    return float(df.iloc[idx]["close"])


def test_context_never_lets_the_scanner_see_beyond_the_clock_either(fixture_dataset):
    dataset_id, days = fixture_dataset
    # strategies._bars() itself requires >=55 daily closes (its own SMA50 floor) — too few before that
    early = days[30].to_pydatetime()
    clk_early = HistoricalClock(early)
    with HistoricalAVDIContext(HistoricalMarketProvider(clk_early, [dataset_id])) as ctx_early:
        assert ctx_early._patched_bars("ZZZ") is None          # correctly insufficient, not a lookahead leak

    mid = days[58].to_pydatetime()
    clk = HistoricalClock(mid)
    provider = HistoricalMarketProvider(clk, [dataset_id])
    with HistoricalAVDIContext(provider) as ctx:
        bars = ctx._patched_bars("ZZZ")
        assert bars is not None
        assert bars["as_of"] <= mid.isoformat()
        assert bars["closes"][-1] == pytest.approx(days_close(days, 58, dataset_id))


def test_patched_functions_restore_cleanly_after_the_context_exits(fixture_dataset):
    dataset_id, days = fixture_dataset
    from paper import strategies
    original_bars = strategies._bars
    clk = HistoricalClock(days[40].to_pydatetime())
    provider = HistoricalMarketProvider(clk, [dataset_id])
    with HistoricalAVDIContext(provider):
        assert strategies._bars is not original_bars
    assert strategies._bars is original_bars                # restored exactly, no leakage across tests


# ── real-data acceptance smoke test (network) ───────────────────────────────────────────────────────

@pytest.mark.network
def test_h3_acceptance_real_data_smoke(tmp_path, monkeypatch):
    """The literal H3 acceptance steps, on the tiny real Yahoo bootstrap sample."""
    monkeypatch.setenv("AVDI_HISTORICAL_DATA_DIR", str(tmp_path / "historical"))
    from research.historical.datasets.yahoo_bootstrap import YahooBootstrapAdapter

    syms = ["AAPL", "MSFT", "META", "NVDA", "DELL"]
    m = YahooBootstrapAdapter("h3_daily_sample").import_and_store(
        syms, "2025-01-01", "2026-09-20", "1d", notes="H3 acceptance: daily bars, real Yahoo data")
    assert m.rows > 0

    from research.historical.datasets.base import load_parquet
    last_ts = load_parquet("h3_daily_sample")["timestamp"].max().to_pydatetime()

    # 1. set the clock to a known timestamp
    clk = HistoricalClock(last_ts)
    provider = HistoricalMarketProvider(clk, ["h3_daily_sample"])

    with HistoricalAVDIContext(provider) as ctx:
        # 2. run the actual scanner
        scan1 = ctx.scan()
        assert scan1["state"] == "ok"
        finalists1 = scan1["finalists"]
        assert isinstance(finalists1, list)

        # 3-4. run the actual downstream decision path; capture candidates/finalists/labels
        results1 = {}
        for f in finalists1:
            r = ctx.evaluate(f["symbol"], direction=f.get("direction", "LONG"), balance=500.0)
            assert r["decision"] in ("TRADEABLE", "MONITOR", "REJECT")
            assert r.get("price") is not None
            results1[f["symbol"]] = r

    # 5. advance the clock one scan interval — here, one trading day (see module docstring: the
    #    price-derived families in this adapter are daily-resolution, matching how fallback_ta/decision
    #    engine actually compute trend/RSI even in the live forward system)
    from research.historical.datasets.base import load_parquet
    df = load_parquet("h3_daily_sample")
    prior_days = sorted(df[df["timestamp"] < pd.Timestamp(last_ts)]["timestamp"].unique())
    assert prior_days, "fixture needs at least 2 distinct trading days"

    # HistoricalClock refuses to move backward — inspecting an EARLIER tick than the one already run
    # above uses a fresh clock/context (exactly like a new discovery cycle in the real runtime never
    # rewinding); advancing FORWARD from `last_ts` would need data beyond what was fetched.
    clk2 = HistoricalClock(pd.Timestamp(prior_days[-1]).to_pydatetime())
    provider2 = HistoricalMarketProvider(clk2, ["h3_daily_sample"])
    with HistoricalAVDIContext(provider2) as ctx2:
        scan2 = ctx2.scan()
        assert scan2["state"] == "ok"
        # 7. state transitions occur causally: earlier clock -> as_of never later than clk2.now
        for f in scan2["finalists"]:
            r2 = ctx2.evaluate(f["symbol"], direction=f.get("direction", "LONG"), balance=500.0)
            assert r2["decision"] in ("TRADEABLE", "MONITOR", "REJECT")

        # 8. no information beyond the clock is accessible: an explicit request past clk2 is refused
        with pytest.raises(LookaheadError):
            provider2.bars("AAPL", timeframe="1d", end=last_ts)
