"""experiments/ch003_time_stop/engine.py — the replay rules PREREG.md fixes, on synthetic 5-minute bars.

The properties that matter for an honest Challenger comparison: costs are never favourable, ambiguity resolves against us, the time
stop fires at the pre-registered moment and only when +1R was never touched, a freed slot is usable only by a LATER cycle, and the
arms differ ONLY by the time stop.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("ch003_engine", _ROOT / "experiments" / "ch003_time_stop" / "engine.py")
E = importlib.util.module_from_spec(_spec)
sys.modules["ch003_engine"] = E
_spec.loader.exec_module(E)

UTC = timezone.utc
D0 = datetime(2026, 10, 5, 9, 30, tzinfo=UTC)          # Monday


def day_bars(day_offset: int, price_path=None, o=100.0, h=100.5, l=99.5, c=100.0, n=6):
    """n 5-minute bars for session `day_offset` (weekday-agnostic: consecutive calendar days are fine for the engine)."""
    start = D0 + timedelta(days=day_offset)
    out = []
    for k in range(n):
        spec = price_path[k] if price_path else (o, h, l, c)
        out.append(E.Bar(start + timedelta(minutes=5 * k), *spec))
    return out


def sess_of(bars):
    days = sorted({b.day for b in bars})
    return {d: i for i, d in enumerate(days)}


def cand(sym="AAA", sector="tech", ts=None, ask=100.0, stop=90.0, target=130.0, rank=1, ev="e1", sdate="2026-10-05"):
    return E.Cand(ts or D0 + timedelta(minutes=2), sdate, sym, sector, ev, rank, stop, target, ask)


P = E.Params()


def test_sizing_is_risk_limited_or_notional_limited_and_survives_slippage():
    fill, qty = E.sizing(ask=100.0, stop=90.0, p=P)
    assert fill == pytest.approx(100.05)
    assert qty == pytest.approx(5.0 / (100.05 - 90.0 * (1 - 0.0005)))         # wide stop -> risk-limited
    assert qty * (fill - 90.0 * (1 - P.slip)) == pytest.approx(5.0)          # the $5 risk holds at the SLIPPED prices
    _, q2 = E.sizing(ask=100.0, stop=99.0, p=P)                               # tight stop -> $125 notional cap binds
    assert q2 == pytest.approx(125.0 / 100.05)


def test_entry_never_uses_the_bar_that_contains_the_decision():
    bars = {"AAA": day_bars(0)}
    bars["AAA"][0] = E.Bar(D0, 100, 100, 89, 95)               # first bar (09:30) would stop the trade out if it counted
    sess = sess_of(bars["AAA"])
    run = E.run_arm([(D0 + timedelta(minutes=2), "2026-10-05", [cand()])], bars, sess, P)
    pos = run["positions"][0]
    assert pos.exit_reason is None and pos.i >= 1                # decision at 09:32 -> first usable bar starts 09:35


def test_same_bar_stop_and_target_resolves_stop_first_and_is_flagged():
    path = [(100, 100, 100, 100)] + [(100, 131, 89, 100)] + [(100, 100, 100, 100)] * 4
    bars = {"AAA": day_bars(0, path)}
    run = E.run_arm([(D0 + timedelta(minutes=1), "2026-10-05", [cand()])], bars, sess_of(bars["AAA"]), P)
    pos = run["positions"][0]
    assert pos.exit_reason == "stop" and pos.ambiguous is True
    assert pos.exit_price == pytest.approx(90.0 * (1 - P.slip))               # slipped, never favourable


def test_gap_through_the_stop_exits_at_the_open_with_the_gap_penalty():
    path = [(100, 100, 100, 100)] + [(85, 86, 84, 85)] + [(85, 85, 85, 85)] * 4
    bars = {"AAA": day_bars(0, path)}
    pos = E.run_arm([(D0 + timedelta(minutes=1), "2026-10-05", [cand()])], bars, sess_of(bars["AAA"]), P)["positions"][0]
    assert pos.exit_reason == "gap_stop" and pos.exit_price == pytest.approx(85.0 * (1 - P.gap))


def _sideways(n_days, touch_1r_on=None):
    bars = []
    for d in range(n_days):
        path = [(100, 101, 99, 100)] * 6
        if d == touch_1r_on:                                                   # 1R level = fill + (fill - stop) ≈ 110.1
            path = [(100, 111, 99, 100)] + [(100, 101, 99, 100)] * 5
        bars += day_bars(d, path)
    return bars


def test_time_stop_fires_at_the_open_after_session_N_and_only_without_plus_1r():
    allbars = _sideways(8)
    bars, sess = {"AAA": allbars}, sess_of(allbars)
    cyc = [(D0 + timedelta(minutes=1), "2026-10-05", [cand()])]
    champ = E.run_arm(cyc, bars, sess, P)["positions"][0]
    assert champ.exit_reason is None                                           # Champion simply holds
    ts3 = E.run_arm(cyc, bars, sess, E.Params(time_stop_n=3))["positions"][0]
    assert ts3.exit_reason == "time_stop"
    assert ts3.exit_ts == allbars[6 * 4].end                                   # first bar of session 4 (entry session = 0, N = 3)
    assert ts3.exit_price == pytest.approx(100.0 * (1 - P.slip))               # sold at that open, slipped
    # a position that DID touch +1R is never time-stopped
    t1 = _sideways(8, touch_1r_on=1)
    kept = E.run_arm(cyc, {"AAA": t1}, sess_of(t1), E.Params(time_stop_n=3))["positions"][0]
    assert kept.touched1r and kept.exit_reason is None


def test_time_stop_pending_at_the_end_of_data_stays_open_not_assumed_sold():
    allbars = _sideways(4)                                                     # session 3 is the last: N=3 would execute at session 4's open
    pos = E.run_arm([(D0 + timedelta(minutes=1), "2026-10-05", [cand()])], {"AAA": allbars}, sess_of(allbars), E.Params(time_stop_n=3))["positions"][0]
    assert pos.exit_reason is None


def test_a_slot_freed_by_an_exit_is_usable_only_by_a_later_cycle():
    path = [(100, 100, 100, 100)] + [(100, 100, 89, 90)] + [(90, 90, 90, 90)] * 4   # AAA stops out in the bar 09:35-09:40
    bars = {"AAA": day_bars(0, path), "BBB": day_bars(0, [(50, 50, 50, 50)] * 6)}
    sess = sess_of(bars["AAA"])
    one = E.Params(max_open=1)
    same = D0 + timedelta(minutes=40 - 30 + 0)                                 # 09:40:00 — the exit bar has just completed
    early = D0 + timedelta(minutes=8)                                          # 09:38 — exit bar (ends 09:40) NOT complete yet
    cycles = [(D0 + timedelta(minutes=1), "2026-10-05", [cand("AAA", "tech")]),
              (early, "2026-10-05", [cand("BBB", "energy", ts=early, ask=50, stop=45, target=70, rank=2)]),
              (same, "2026-10-05", [cand("BBB", "energy", ts=same, ask=50, stop=45, target=70, rank=2)])]
    run = E.run_arm(cycles, bars, sess, one)
    assert [p.symbol for p in run["positions"]] == ["AAA", "BBB"]
    assert run["positions"][1].entry_ts == same                                # entered at 09:40, not at 09:38
    assert any(s["why"] == "max_open_positions" for s in run["skipped"])


def test_capacity_rules_one_per_sector_daily_cap_and_symbol_reentry():
    bars = {s: day_bars(0, [(100, 100.2, 99.8, 100)] * 6) for s in ("A", "B", "C", "D")}
    sess = sess_of(bars["A"])
    cands = [cand("A", "tech", rank=1), cand("B", "tech", rank=2), cand("C", "energy", rank=3), cand("D", "health", rank=4)]
    run = E.run_arm([(D0 + timedelta(minutes=1), "2026-10-05", cands)], bars, sess, P)
    assert [p.symbol for p in run["positions"]] == ["A", "C"]                  # B: same sector; D: daily cap of 2 reached
    whys = {s["symbol"]: s["why"] for s in run["skipped"]}
    assert whys["B"] == "max_positions_per_sector" and whys["D"] == "daily order cap"


def test_arms_are_identical_when_the_time_stop_never_applies():
    allbars = _sideways(11, touch_1r_on=1)                                     # +1R touched on day 1 (day 0's bar 0 is the decision bar, which is ignored) -> time stop can never fire
    bars, sess = {"AAA": allbars}, sess_of(allbars)
    cyc = [(D0 + timedelta(minutes=1), "2026-10-05", [cand()])]
    base = E.position_result(E.run_arm(cyc, bars, sess, P)["positions"][0], allbars, sess)
    for n in (3, 5, 8, 10):
        arm = E.position_result(E.run_arm(cyc, bars, sess, E.Params(time_stop_n=n))["positions"][0], allbars, sess)
        assert arm == base


def test_a_time_stopped_symbol_cannot_be_rebought_during_the_cooldown():
    allbars = _sideways(9)
    bars, sess = {"AAA": allbars}, sess_of(allbars)
    again = D0 + timedelta(days=5, minutes=1)                                  # still TRADEABLE in session 5, right after the time stop
    cyc = [(D0 + timedelta(minutes=1), "2026-10-05", [cand()]),
           (again, "2026-10-10", [cand(ts=again, sdate="2026-10-10", ev="e2")])]
    run = E.run_arm(cyc, bars, sess, E.Params(time_stop_n=3))
    assert len(run["positions"]) == 1
    assert any(s["why"] == "time-stop cooldown" for s in run["skipped"])


def test_hold_outcome_is_the_champion_path_for_regret():
    path = [(100, 100, 100, 100)] * 6
    allbars = day_bars(0, path) + day_bars(1, [(100, 140, 100, 135)] + [(135, 135, 135, 135)] * 5)   # target 130 hit on day 1
    sess = sess_of(allbars)
    fill = 100.05
    out = E.hold_outcome(allbars, D0 + timedelta(minutes=1), 90.0, 130.0, fill, sess, P)
    assert out["state"] == "target" and out["R"] == pytest.approx((130.0 - fill) / (fill - 90.0))


def test_drawdown_and_utilisation_are_computed_from_marks_not_exits_alone():
    path = [(100, 100, 100, 100), (100, 100, 96, 96), (96, 96, 96, 96), (96, 96, 96, 96), (96, 96, 96, 96), (96, 96, 96, 96)]
    bars = {"AAA": day_bars(0, path)}
    run = E.run_arm([(D0 + timedelta(minutes=1), "2026-10-05", [cand()])], bars, sess_of(bars["AAA"]), P)
    timeline = [b.end for b in bars["AAA"]]
    st = E.equity_stats(run["positions"], timeline, P)
    pos = run["positions"][0]
    assert st["max_drawdown_usd"] == pytest.approx(pos.qty * (pos.fill - 96.0), abs=1e-3)    # paper loss counted while still open
    assert 0 < st["mean_utilisation"] <= 1
