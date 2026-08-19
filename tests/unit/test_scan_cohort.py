"""Scan cohort memory — scan identity, Top-5 capture, event-based status, forward
returns, and the derived-stats layer (`dashboard/scan_cohort.py`).

Fully offline and hermetic: every test gets a throwaway SQLite file (scan_cohort.
reset_for_tests) and every provider call (`research.bars`, `research.quotes`) is
monkeypatched to a deterministic fixture — no network, no real scan.

Run: pytest tests/unit/test_scan_cohort.py -q
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in ("src", "dashboard", "lab"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import scan_cohort as SCO  # noqa: E402

FIXED_TS = "2026-01-05T15:30:00+00:00"
FIXED_DT = datetime.fromisoformat(FIXED_TS)


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="cohorttest_")
    SCO.reset_for_tests(tmp)
    # A controlled, strictly-increasing clock: capture_scan calls _now() exactly
    # once per scan, so the FIRST call in any test lands exactly on FIXED_TS —
    # which is what the bars fixtures below are anchored to for exact forward-
    # return arithmetic — while successive scans in the same test still get
    # distinct, increasing timestamps (as real scans always do).
    counter = {"n": 0}

    def _clock():
        ts = (FIXED_DT + timedelta(minutes=counter["n"])).isoformat()
        counter["n"] += 1
        return ts
    monkeypatch.setattr(SCO, "_now", _clock)
    yield


# ── Fixture builders ──────────────────────────────────────────────────────────

def _cand(symbol: str, score_total: float, price: float, rank_hint: int = 0,
          setup_type: str = "multiday_swing") -> dict:
    """A candidate dict shaped exactly like scanner.scan()'s top10 entries."""
    return {
        "symbol": symbol, "name": symbol, "sector": "technology",
        "sector_name": "Technology", "sector_rank": 2,
        "indicators": {"price": price, "source_timestamp": FIXED_TS},
        "setups": [{"type": setup_type, "strength": 70.0 - rank_hint,
                    "evidence": f"{symbol} test evidence", "horizon": "days to weeks"}],
        "levels": {"state": "ok", "entry_zone": [round(price * 0.99, 2), round(price * 1.01, 2)],
                  "reference_price": price, "invalidation": round(price * 0.95, 2),
                  "invalidation_basis": "1.5 x ATR(14)",
                  "target_1": round(price * 1.05, 2), "target_1_basis": "20-day high",
                  "target_2": round(price * 1.10, 2), "target_2_basis": "measured move",
                  "rr_target_1": 2.0, "rr_target_2": 3.0},
        "score": {"total": score_total, "components": [
            {"component": "relative_strength", "score": 8.0, "max": 12, "note": "strong RS"},
            {"component": "technical_structure", "score": 10.0, "max": 14, "note": "above SMAs"},
        ]},
        "quote": {"price": price, "change_pct": 1.0, "source_timestamp": FIXED_TS, "state": "ok"},
    }


def _scan_result(cands: list, preset: str = "liquid") -> dict:
    top10 = sorted(cands, key=lambda c: -c["score"]["total"])
    return {"state": "ok", "preset": preset, "config": {},
            "regime": {"risk_appetite": "risk-on"},
            "stages": [{"stage": "universe", "count": 100}, {"stage": "eligible", "count": 50},
                      {"stage": "deep", "count": 20}],
            "candidates": top10, "top25": top10, "top10": top10, "finalists": top10[:3],
            "generated_at": FIXED_TS}


def _growth_bars(start_price: float, daily_pct: float, n: int = 15,
                 start_dt: datetime = FIXED_DT) -> dict:
    """n daily bars starting exactly at start_dt, compounding daily_pct/day — enough
    for the anchor bar to land at index 0 when created_at == start_dt."""
    bars = []
    px = start_price
    d = start_dt
    for _ in range(n):
        bars.append({"t": d.isoformat(), "o": px, "h": round(px * 1.02, 4),
                     "l": round(px * 0.98, 4), "c": round(px, 4), "v": 5_000_000})
        px = px * (1 + daily_pct)
        d = d + timedelta(days=1)
    return {"state": "ok", "bars": bars}


def _fake_quotes(prices: dict):
    def _q(symbols, **kw):
        return {s: ({"state": "ok", "price": prices[s]} if s in prices
                    else {"state": "error", "price": None}) for s in symbols}
    return _q


# ── 1. scan_id + capture shape ────────────────────────────────────────────────

def test_scan_creates_unique_scan_id():
    r1 = SCO.capture_scan(_scan_result([_cand("AAA", 80, 100)]))
    r2 = SCO.capture_scan(_scan_result([_cand("AAA", 80, 100)]))
    assert r1["state"] == "ok" and r2["state"] == "ok"
    assert r1["scan_id"] != r2["scan_id"]
    runs = SCO.list_scan_runs()["runs"]
    assert {r["scan_id"] for r in runs} == {r1["scan_id"], r2["scan_id"]}


def test_exactly_top5_captured():
    cands = [_cand(f"SYM{i}", 90 - i, 100 + i, rank_hint=i) for i in range(8)]
    r = SCO.capture_scan(_scan_result(cands))
    assert r["count"] == 5
    got = SCO.cohort(r["scan_id"], live=False)
    assert len(got["observations"]) == 5

    r2 = SCO.capture_scan(_scan_result(cands[:3]))
    assert r2["count"] == 3


def test_rank_ordering_preserved():
    cands = [_cand("LOW", 60, 50), _cand("HIGH", 95, 200), _cand("MID", 78, 100)]
    r = SCO.capture_scan(_scan_result(cands))
    obs = SCO.cohort(r["scan_id"], live=False)["observations"]
    ordered = [(o["rank"], o["symbol"]) for o in obs]
    assert ordered == [(1, "HIGH"), (2, "MID"), (3, "LOW")]


def test_capture_scan_skips_empty_top10():
    r = SCO.capture_scan(_scan_result([]))
    assert r["state"] == "skipped"


# ── 2. old scans stay historical / duplicate tickers ──────────────────────────

def test_old_scans_remain_historical():
    r1 = SCO.capture_scan(_scan_result([_cand("AAA", 80, 100)]))
    before = SCO.cohort(r1["scan_id"], live=False)["observations"]
    SCO.capture_scan(_scan_result([_cand("BBB", 70, 50)]))
    after = SCO.cohort(r1["scan_id"], live=False)["observations"]
    assert before == after
    assert len(SCO.list_scan_runs()["runs"]) == 2


def test_duplicate_ticker_across_scans_creates_separate_observations():
    r1 = SCO.capture_scan(_scan_result([_cand("AAPL", 78, 200)]))
    r2 = SCO.capture_scan(_scan_result([_cand("AAPL", 71, 205)]))
    obs1 = SCO.cohort(r1["scan_id"], live=False)["observations"][0]
    obs2 = SCO.cohort(r2["scan_id"], live=False)["observations"][0]
    assert obs1["obs_id"] != obs2["obs_id"]
    assert obs1["scan_id"] != obs2["scan_id"]
    assert obs1["symbol"] == obs2["symbol"] == "AAPL"
    assert obs1["score"] == 78 and obs2["score"] == 71


def test_reading_cohort_does_not_duplicate_observations():
    """The read path (cohort/list_scan_runs) must be side-effect free — the actual
    once-per-real-scan guarantee lives one layer up: scanner.scan() only calls
    capture_scan from inside the SWR worker function, which the existing cache
    (research.swr/swr_async) only invokes on a real fetch, never on a served-from-
    cache read. This test locks down the half of that contract this module owns."""
    r = SCO.capture_scan(_scan_result([_cand("AAA", 80, 100)]))
    n0 = len(SCO.cohort(r["scan_id"], live=False)["observations"])
    for _ in range(5):
        SCO.cohort(r["scan_id"], live=False)
        SCO.list_scan_runs()
    assert len(SCO.cohort(r["scan_id"], live=False)["observations"]) == n0


# ── 3. status is an event, not a membership filter ────────────────────────────

def test_non_entry_triggered_candidates_remain_tracked(monkeypatch):
    cands = [_cand("AAA", 80, 100), _cand("BBB", 75, 200)]
    r = SCO.capture_scan(_scan_result(cands))
    # AAA entry zone [99,101], target1 105, invalidation 95 — 103 clears all three.
    # BBB entry zone [198,202], target1 210, invalidation 190 — 204 clears all three.
    monkeypatch.setattr(SCO._R, "quotes", _fake_quotes({"AAA": 103.0, "BBB": 204.0}))
    out = SCO.update_observations(r["scan_id"])
    assert out["updated"] >= 0
    obs = SCO.cohort(r["scan_id"], live=False)["observations"]
    assert len(obs) == 2
    assert {o["symbol"] for o in obs} == {"AAA", "BBB"}
    assert all(o["status"] in ("selected", "watching") for o in obs)


def test_entry_trigger_changes_status_not_membership(monkeypatch):
    cand = _cand("AAA", 80, 100.0)
    r = SCO.capture_scan(_scan_result([cand]))
    obs_before = SCO.cohort(r["scan_id"], live=False)["observations"]
    assert obs_before[0]["status"] == "selected"

    # price inside the entry zone [99.0, 101.0]
    monkeypatch.setattr(SCO._R, "quotes", _fake_quotes({"AAA": 100.0}))
    changed = SCO.update_observations(r["scan_id"])
    assert changed["updated"] == 1
    assert changed["changes"][0]["to"] == "entry_triggered"

    obs_after = SCO.cohort(r["scan_id"], live=False)["observations"]
    assert len(obs_after) == 1                       # membership unchanged
    assert obs_after[0]["obs_id"] == obs_before[0]["obs_id"]
    assert obs_after[0]["status"] == "entry_triggered"

    # now push through target 1 (105) — status advances, row still the same one
    monkeypatch.setattr(SCO._R, "quotes", _fake_quotes({"AAA": 106.0}))
    changed2 = SCO.update_observations(r["scan_id"])
    assert changed2["changes"][0]["to"] == "target_1_hit"
    obs_final = SCO.cohort(r["scan_id"], live=False)["observations"]
    assert len(obs_final) == 1
    assert obs_final[0]["obs_id"] == obs_before[0]["obs_id"]


def test_invalidation_and_close_observation(monkeypatch):
    r = SCO.capture_scan(_scan_result([_cand("AAA", 80, 100.0)]))
    monkeypatch.setattr(SCO._R, "quotes", _fake_quotes({"AAA": 94.0}))  # below 95 invalidation
    out = SCO.update_observations(r["scan_id"])
    assert out["changes"][0]["to"] == "invalidated"
    obs_id = SCO.cohort(r["scan_id"], live=False)["observations"][0]["obs_id"]
    # invalidated is terminal — a further update must not touch it
    again = SCO.update_observations(r["scan_id"])
    assert again["checked"] == 0

    closed = SCO.close_observation(obs_id, reason="manual review")
    assert closed["state"] == "ok" and closed["status"] == "manually_closed"


# ── 4. forward returns — exact arithmetic against synthetic bars ─────────────

def test_forward_return_calculation_correct(monkeypatch):
    b = _growth_bars(start_price=100.0, daily_pct=0.01, n=15)  # +1%/day, compounding
    monkeypatch.setattr(SCO._R, "bars", lambda sym, **kw: b)
    r = SCO.capture_scan(_scan_result([_cand("AAA", 80, 100.0)]))
    obs_id = SCO.cohort(r["scan_id"], live=False)["observations"][0]["obs_id"]

    fr = SCO.forward_returns(obs_id, use_cache=False)
    assert fr["state"] == "ok"
    expected_d5 = round(100 * ((1.01 ** 5) - 1), 2)
    expected_d10 = round(100 * ((1.01 ** 10) - 1), 2)
    assert fr["d5"]["pct_return"] == expected_d5
    assert fr["d10"]["pct_return"] == expected_d10
    assert fr["d0"]["pct_return"] == 0.0
    # monotonically increasing series: MFE is the d10 high, MAE is ~0 (never dips)
    assert fr["mfe_pct"] >= expected_d10
    assert fr["mae_pct"] <= 0.5


def test_eod_update_attaches_to_correct_scan_candidate(monkeypatch):
    """Two observations, two different price paths — forward_returns for one must
    never leak into the other."""
    bars_map = {
        "UP": _growth_bars(100.0, 0.02, n=15),     # +2%/day
        "DOWN": _growth_bars(50.0, -0.01, n=15),   # -1%/day
    }
    monkeypatch.setattr(SCO._R, "bars", lambda sym, **kw: bars_map[sym])
    r = SCO.capture_scan(_scan_result([_cand("UP", 90, 100.0), _cand("DOWN", 60, 50.0)]))
    obs = {o["symbol"]: o["obs_id"] for o in SCO.cohort(r["scan_id"], live=False)["observations"]}

    fr_up = SCO.forward_returns(obs["UP"], use_cache=False)
    fr_down = SCO.forward_returns(obs["DOWN"], use_cache=False)
    assert fr_up["symbol"] == "UP" and fr_down["symbol"] == "DOWN"
    assert fr_up["d5"]["pct_return"] > 0
    assert fr_down["d5"]["pct_return"] < 0
    assert fr_up["d5"]["pct_return"] != fr_down["d5"]["pct_return"]


def test_forward_returns_cache_memoizes(monkeypatch):
    calls = {"n": 0}
    b = _growth_bars(100.0, 0.01, n=15)

    def counting_bars(sym, **kw):
        calls["n"] += 1
        return b
    monkeypatch.setattr(SCO._R, "bars", counting_bars)
    r = SCO.capture_scan(_scan_result([_cand("AAA", 80, 100.0)]))
    obs_id = SCO.cohort(r["scan_id"], live=False)["observations"][0]["obs_id"]

    first = SCO.forward_returns(obs_id)
    n_after_first = calls["n"]
    second = SCO.forward_returns(obs_id)          # should hit the memoized snapshot
    assert calls["n"] == n_after_first             # no new fetch
    assert second["d5"]["pct_return"] == first["d5"]["pct_return"]


# ── 5. repetition stats ───────────────────────────────────────────────────────

def test_repeated_candidate_statistics_correct(monkeypatch):
    b = _growth_bars(100.0, 0.0, n=15)
    monkeypatch.setattr(SCO._R, "bars", lambda sym, **kw: b)
    # NVDA appears in 3 of 4 scans, always rank 1; ONE_OFF appears once.
    SCO.capture_scan(_scan_result([_cand("NVDA", 90, 200), _cand("ONE_OFF", 60, 30)]))
    SCO.capture_scan(_scan_result([_cand("NVDA", 88, 205)]))
    SCO.capture_scan(_scan_result([_cand("OTHER", 70, 40)]))
    SCO.capture_scan(_scan_result([_cand("NVDA", 92, 210)]))

    stats = SCO.repetition_stats(lookback_scans=10)
    by_sym = {s["symbol"]: s for s in stats["symbols"]}
    assert stats["scans_considered"] == 4
    assert by_sym["NVDA"]["appearances"] == 3
    assert by_sym["NVDA"]["avg_rank"] == 1.0
    assert by_sym["ONE_OFF"]["appearances"] == 1
    assert by_sym["NVDA"]["repeated_candidate_warning"] is True
    assert by_sym["ONE_OFF"]["repeated_candidate_warning"] is False
    # NVDA: scans newest→oldest are [NVDA-only, OTHER-only, NVDA-only, NVDA+ONE_OFF].
    # The consecutive streak counts back from the MOST RECENT scan, so it stops at 1
    # (the very next scan back, OTHER-only, breaks it) even though NVDA appeared in
    # 3 of the 4 scans overall.
    assert by_sym["NVDA"]["consecutive_streak"] == 1


# ── 6. knowledge/stats use persisted observations, not invention ─────────────

def test_knowledge_stats_use_actual_persisted_observations(monkeypatch):
    bars_map = {
        "WIN": _growth_bars(100.0, 0.02, n=15),
        "FLAT": _growth_bars(100.0, 0.0, n=15),
        "SPY": _growth_bars(500.0, 0.001, n=15),
    }
    monkeypatch.setattr(SCO._R, "bars", lambda sym, **kw: bars_map.get(sym, {"state": "empty", "bars": []}))
    SCO.capture_scan(_scan_result([_cand("WIN", 85, 100.0), _cand("FLAT", 65, 100.0)]))

    rp = SCO.rank_performance("d5")
    assert rp["state"] == "ok"
    # rank 1 was WIN (higher score), rank 2 was FLAT
    expected_win_d5 = round(100 * ((1.02 ** 5) - 1), 2)
    assert rp["groups"]["1"]["n"] == 1
    assert rp["groups"]["1"]["avg_return_pct"] == expected_win_d5
    assert rp["groups"]["2"]["avg_return_pct"] == 0.0

    sb = SCO.score_bucket_performance("d5")
    assert sb["groups"]["80-100"]["n"] == 1
    assert sb["groups"]["60-69"]["n"] == 1

    st = SCO.setup_type_performance("d5")
    assert "multiday_swing" in st["groups"]
    assert st["groups"]["multiday_swing"]["n"] == 2

    # alpha must be candidate return MINUS the benchmark's own return over the same
    # window, not the raw candidate return.
    expected_spy_d5 = round(100 * ((1.001 ** 5) - 1), 2)
    assert rp["groups"]["1"]["avg_alpha_pct"] == round(expected_win_d5 - expected_spy_d5, 2)


def test_daily_review_is_deterministic_from_persisted_data(monkeypatch):
    bars_map = {"A": _growth_bars(100.0, 0.03, n=15), "B": _growth_bars(50.0, -0.02, n=15),
               "SPY": _growth_bars(500.0, 0.001, n=15)}
    monkeypatch.setattr(SCO._R, "bars", lambda sym, **kw: bars_map.get(sym, {"state": "empty", "bars": []}))
    SCO.capture_scan(_scan_result([_cand("A", 90, 100.0), _cand("B", 60, 50.0)]))

    review = SCO.daily_review(FIXED_TS[:10])
    assert review["state"] == "ok"
    assert review["scans"] == 1
    assert review["best"]["symbol"] == "A"
    assert review["worst"]["symbol"] == "B"
    assert review["cohort_avg_return_pct"] is not None

    empty = SCO.daily_review("1999-01-01")
    assert empty["state"] == "empty"
