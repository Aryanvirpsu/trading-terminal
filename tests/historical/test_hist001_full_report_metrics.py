"""Unit tests for the R/drawdown/concentration/stability/risk-invariant functions added to `analysis.py`
for the corrected HIST-001 Full report (user's plan step 8: prepare the report pipeline before the corrected
replay finishes). These operate purely on a `run_baseline()`-shaped result dict -- constructed directly here,
not via a real replay -- since the functions under test are pure post-hoc computations over that structure,
exactly like every other function in `analysis.py`."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.historical.hist001.analysis import (
    concentration_analysis, drawdown_and_streaks, max_drawdown_lockout_check, r_multiple_metrics,
    risk_invariant_check, stability_breakdown,
)


def _result(positions, fills=None, not_executed=None, starting_equity=500.0, ending_equity=None,
           evaluation_trading_days=(), decision_capture=None):
    """`evaluation_trading_days=()` (empty, the default) disables phase filtering entirely -- every function
    under test here treats an empty `eval_dates` set as "no filter" (`if not eval_dates or ...`), so these
    tests exercise the actual metric computation, not the (separately-tested-elsewhere) phase filter."""
    return {
        "positions": positions, "fills": fills or [], "not_executed": not_executed or [],
        "account": {"starting_equity": starting_equity, "equity": ending_equity if ending_equity is not None else starting_equity},
        "evaluation_trading_days": list(evaluation_trading_days),
        "decision_capture": decision_capture or {},
    }


def _dc(cycle_id, equity):
    return {"cycle_id": cycle_id, "account_equity_before": equity}


def _pos(symbol, opened, closed, pnl, planned_risk, sector="technology", position_id=None, quantity=1.0):
    return {"position_id": position_id or f"pos_{symbol}_{closed}", "symbol": symbol, "sector": sector,
           "status": "closed", "opened_at": f"{opened}T14:35:00+00:00", "closed_at": f"{closed}T18:00:00+00:00",
           "realized_pnl": pnl, "planned_risk": planned_risk, "quantity": quantity}


def test_r_multiple_metrics_matches_shadow_log_convention():
    positions = [
        _pos("AAA", "2024-06-01", "2024-06-02", 10.0, 5.0),      # +2R
        _pos("BBB", "2024-06-02", "2024-06-03", -5.0, 5.0),      # -1R
        _pos("CCC", "2024-06-03", "2024-06-04", 7.5, 5.0),       # +1.5R
    ]
    m = r_multiple_metrics(_result(positions))
    assert m["trades_with_r"] == 3
    assert m["total_r"] == pytest.approx(2.5)
    assert m["expectancy_r"] == pytest.approx(2.5 / 3, abs=1e-3)
    assert m["win_rate_pct"] == pytest.approx(200.0 / 3, abs=0.1)
    assert m["best_trade_r"] == pytest.approx(2.0)
    assert m["worst_trade_r"] == pytest.approx(-1.0)


def test_r_multiple_metrics_excludes_trades_with_no_planned_risk_rather_than_coercing_to_zero():
    positions = [_pos("AAA", "2024-06-01", "2024-06-02", 10.0, 5.0),
                _pos("ZZZ", "2024-06-01", "2024-06-02", -3.0, None)]
    m = r_multiple_metrics(_result(positions))
    assert m["trades_with_r"] == 1
    assert m["excluded_no_planned_risk"] == 1


def test_drawdown_and_streaks_walks_chronological_equity_path():
    # +10, +5, -20, +3 -- peak 515 after first two trades, trough 495 after the loss, dd = 20
    positions = [
        _pos("AAA", "2024-06-01", "2024-06-01", 10.0, 5.0),
        _pos("BBB", "2024-06-01", "2024-06-02", 5.0, 5.0),
        _pos("CCC", "2024-06-02", "2024-06-03", -20.0, 5.0),
        _pos("DDD", "2024-06-03", "2024-06-04", 3.0, 5.0),
    ]
    d = drawdown_and_streaks(_result(positions, starting_equity=500.0))
    assert d["max_drawdown_dollars"] == pytest.approx(20.0)
    assert d["worst_trade_dollars"] == pytest.approx(-20.0)
    assert d["longest_win_streak"] == 2                 # AAA, BBB
    assert d["longest_loss_streak"] == 1                # CCC only
    assert d["ending_equity_from_this_path"] == pytest.approx(500.0 + 10 + 5 - 20 + 3)


def test_concentration_analysis_diagnostic_never_changes_canonical_totals():
    positions = [
        _pos("BEST", "2024-06-01", "2024-06-01", 100.0, 5.0),
        _pos("MID1", "2024-06-01", "2024-06-02", 5.0, 5.0),
        _pos("MID2", "2024-06-01", "2024-06-02", 3.0, 5.0),
        _pos("WORST", "2024-06-01", "2024-06-03", -50.0, 5.0),
    ]
    c = concentration_analysis(_result(positions))
    assert c["total_net_pnl"] == pytest.approx(58.0)                # 100+5+3-50, the canonical, untouched total
    assert c["best_trade"]["symbol"] == "BEST"
    assert c["worst_trade"]["symbol"] == "WORST"
    diag = c["diagnostic_excluding_best_and_worst_trade"]
    assert diag["trades_remaining"] == 2
    assert diag["net_pnl_excl_best_and_worst"] == pytest.approx(8.0)   # MID1+MID2 only
    # the canonical total must be untouched by having computed the diagnostic
    assert c["total_net_pnl"] == pytest.approx(58.0)


def test_stability_breakdown_groups_by_month_quarter_year_symbol_sector():
    positions = [
        _pos("AAA", "2024-01-01", "2024-01-15", 10.0, 5.0, sector="technology"),
        _pos("AAA", "2024-02-01", "2024-02-15", -4.0, 5.0, sector="technology"),
        _pos("BBB", "2024-04-01", "2024-04-15", 6.0, 5.0, sector="energy"),
    ]
    s = stability_breakdown(_result(positions))
    assert s["by_month"]["2024-01"]["trades"] == 1
    assert s["by_month"]["2024-02"]["net_pnl"] == pytest.approx(-4.0)
    assert s["by_quarter"]["2024-Q1"]["trades"] == 2
    assert s["by_quarter"]["2024-Q2"]["trades"] == 1
    assert s["by_year"]["2024"]["net_pnl"] == pytest.approx(12.0)
    assert s["by_symbol"]["AAA"]["trades"] == 2
    assert s["by_sector"]["energy"]["net_pnl"] == pytest.approx(6.0)


def test_risk_invariant_check_passes_on_a_clean_run():
    positions = [_pos("AAA", "2024-06-01", "2024-06-02", 10.0, 5.0, quantity=1.0)]
    r = risk_invariant_check(_result(positions))
    assert r["violations_found"] == 0
    assert r["verdict"].startswith("PASS")


def test_risk_invariant_check_flags_a_loss_far_exceeding_planned_risk():
    # the exact AVGO-cascade shape: realized loss ~17x planned_risk
    positions = [_pos("AVGO", "2024-06-24", "2024-07-15", -57.18, 3.4, quantity=0.39)]
    r = risk_invariant_check(_result(positions))
    assert r["violations_found"] == 1
    assert r["violations"][0]["type"] == "loss_exceeds_1.5x_planned_risk"
    assert r["verdict"].startswith("FAIL")


def test_risk_invariant_check_flags_negative_quantity():
    positions = [_pos("AAA", "2024-06-01", "2024-06-02", 1.0, 5.0, quantity=-1.0)]
    r = risk_invariant_check(_result(positions))
    assert any(v["type"] == "negative_quantity" for v in r["violations"])


def test_risk_invariant_check_counts_drawdown_breaker_fires_without_failing_on_them():
    positions = [_pos("AAA", "2024-06-01", "2024-06-02", 1.0, 5.0)]
    not_executed = [{"detail_json": '{"reason": "max_drawdown circuit breaker"}'},
                    {"detail_json": '{"reason": "sector capacity"}'}]
    r = risk_invariant_check(_result(positions, not_executed=not_executed))
    assert r["max_drawdown_circuit_breaker_fired_count_per_not_executed_text"] == 1
    assert r["violations_found"] == 0             # a breaker firing is not itself a violation


def test_max_drawdown_lockout_check_detects_a_sustained_lockout():
    """Reproduces the exact shape found building the corrected HIST-001 Full report: equity breaches the
    $50 cap, a few more cycles still show movement (trades already in flight resolving), then equity freezes
    for the rest of the series with no further change at all."""
    dc = {}
    cyc = 0
    equity = 500.0
    # climb to a peak
    for _ in range(5):
        cyc += 1
        equity += 20.0
        dc[f"k{cyc}"] = _dc(f"c{cyc:03d}", equity)
    # a losing stretch that breaches the $50 cap from the peak (600.0)
    for _ in range(6):
        cyc += 1
        equity -= 10.0
        dc[f"k{cyc}"] = _dc(f"c{cyc:03d}", equity)
    breach_equity = equity                              # 600 - 60 = 540, a $60 drawdown from the 600 peak
    # a couple more cycles still resolving in-flight trades
    for delta in (-2.0, +1.0):
        cyc += 1
        equity += delta
        dc[f"k{cyc}"] = _dc(f"c{cyc:03d}", equity)
    final_equity = equity
    # a long frozen tail -- no further equity change for the rest of the series
    for _ in range(30):
        cyc += 1
        dc[f"k{cyc}"] = _dc(f"c{cyc:03d}", final_equity)

    result = _result([], decision_capture=dc)
    check = max_drawdown_lockout_check(result, dd_cap=50.0)
    assert check["breach_detected"] is True
    assert check["sustained_lockout"] is True
    assert check["final_equity"] == pytest.approx(final_equity)
    assert check["frozen_tail_cycles_at_window_end"] == 30        # every one of the 30 appended frozen cycles


def test_max_drawdown_lockout_check_no_breach_when_drawdown_stays_under_cap():
    dc = {}
    for i, equity in enumerate([500.0, 510.0, 505.0, 515.0, 520.0, 500.0, 530.0]):
        dc[f"k{i}"] = _dc(f"c{i}", equity)
    result = _result([], decision_capture=dc)
    check = max_drawdown_lockout_check(result, dd_cap=50.0)
    assert check["breach_detected"] is False


def test_max_drawdown_lockout_check_not_sustained_when_equity_keeps_moving():
    """A breach that the account recovers from (equity keeps changing all the way to the end of the series)
    must NOT be reported as a sustained lockout."""
    dc = {}
    cyc = 0
    equity = 500.0
    for _ in range(5):
        cyc += 1; equity += 20.0; dc[f"k{cyc}"] = _dc(f"c{cyc:03d}", equity)
    for _ in range(6):
        cyc += 1; equity -= 10.0; dc[f"k{cyc}"] = _dc(f"c{cyc:03d}", equity)      # breaches $50 from the 600 peak
    for _ in range(20):                                                       # keeps moving all the way through
        cyc += 1; equity += 1.0; dc[f"k{cyc}"] = _dc(f"c{cyc:03d}", equity)
    result = _result([], decision_capture=dc)
    check = max_drawdown_lockout_check(result, dd_cap=50.0)
    assert check["breach_detected"] is True
    assert check["sustained_lockout"] is False
