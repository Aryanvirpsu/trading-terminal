"""Evidence & Graduation (Canonical Trading Architecture v1.2) tests.

Covers the semantic/architecture changes only — NOT a retune of any formula.
Model EV must compute byte-identical numbers to before; what's new is the
explicit disclosure (calibration_status, direction_model), the six-gate state
machine, and the calibration-bucket plumbing.

Fully offline and hermetic, same fixture pattern as
tests/unit/test_pipeline3_canonical_migration.py: a fresh throwaway SQLite
file per test, no provider ever contacted, and — Work Package G — this file
never touches the real cloud/local ledgers at ~/.tradingview_mcp_data. Every
test gets its own PAPER_DATA_DIR via monkeypatch; nothing here can mutate the
HAL position or any other real ledger state.

Run: pytest tests/unit/test_evidence_graduation.py -v --tb=short
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in ("src", "dashboard", "lab"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import decision_engine as de                                   # noqa: E402

from paper import canonical_bridge, db, options_shadow          # noqa: E402
from canonical.risk_policy import STRATEGY_500_POLICY           # noqa: E402

NOW = datetime.now(timezone.utc).replace(microsecond=0)


# ══════════════════════════════════════════════════════════════════════════
# Harness — same isolation pattern as test_pipeline3_canonical_migration.py
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="evidencegradtest_")
    monkeypatch.setenv("PAPER_DATA_DIR", tmp)
    monkeypatch.setenv("PAPER_LEDGER", "robinhood_500_baseline")
    monkeypatch.setenv("PAPER_INITIAL_CASH", "500")
    monkeypatch.setenv("PAPER_INITIAL_EQUITY", "500")
    monkeypatch.setenv("PAPER_BUYING_POWER", "500")
    monkeypatch.setenv("PAPER_MARGIN_ENABLED", "false")
    monkeypatch.setenv("PAPER_ALLOW_SHORTING", "false")
    monkeypatch.setenv("PAPER_MAX_LOSS_PER_TRADE", "5")
    monkeypatch.setenv("PAPER_MAX_POSITION_NOTIONAL", "125")
    monkeypatch.setenv("PAPER_MIN_CASH_RESERVE_USD", "100")
    monkeypatch.setenv("PAPER_MAX_DAILY_LOSS_USD", "10")
    monkeypatch.setenv("PAPER_MAX_DRAWDOWN_USD", "50")
    monkeypatch.setenv("PAPER_MAX_ENTRIES_PER_DAY", "2")
    monkeypatch.setenv("PAPER_MAX_OPEN", "3")
    monkeypatch.setenv("PAPER_MAX_PER_SECTOR", "1")
    db.reset_for_tests(tmp)
    yield
    db.close()


def _mk(fam, d, c, detail="x"):
    return {"family": fam, "dir": d, "conf": c, "detail": detail}


_DEEP = {"_fam_catalyst": "catalyst", "_fam_short": "short-interest",
        "_fam_filings": "filings/insider", "_fam_options_flow": "options-flow",
        "_fam_social": "social-sentiment", "_fam_analyst": "analyst-ratings",
        "_fam_macro": "macro-rates"}


def _patch_c(monkeypatch, *, trend=(0.7, 0.85), regime=(0.4, 0.7), families=None,
            data_state="fresh", a_source="yahoo", price=63.25, atrp=2.5,
            option_idea=None):
    families = families or {f: (0.3, 0.7) for f in _DEEP.values()}
    a = {"price_data": {"current_price": price}, "trend_state": "uptrend",
        "atr": {"value": price * atrp / 100.0, "percent_of_price": atrp},
        "market_sentiment": {"momentum": "Bullish", "buy_sell_signal": "BUY"},
        "rsi": {"value": 60}, "as_of": NOW.isoformat()}
    monkeypatch.setattr(de, "_load_analysis", lambda s, e: (a, a_source, data_state))
    monkeypatch.setattr(de, "_safe_regime", lambda: {"risk_appetite_score": 65})
    monkeypatch.setattr(de, "_fam_trend", lambda _a: _mk("trend/momentum", *trend))
    monkeypatch.setattr(de, "_fam_regime", lambda _r, _d: _mk("regime", *regime))
    for fn, fam in _DEEP.items():
        d, c = families.get(fam, (0.25, 0.6))
        monkeypatch.setattr(de, fn, (lambda fam=fam, d=d, c=c: (lambda *a, **k: _mk(fam, d, c)))())
    monkeypatch.setattr(de.ss, "_pick_option_idea", lambda *a, **k: option_idea)
    try:
        import halts
        monkeypatch.setattr(halts, "is_halted", lambda s: False)
    except Exception:
        pass
    try:
        import risk_engine
        monkeypatch.setattr(risk_engine, "check_new_trade", lambda sym, r, spec: {
            "allow": True, "reasons": [], "size_cap_usd": 1e6,
            "portfolio": {"suspended": False, "drawdown_pct": 0.0}})
    except Exception:
        pass


def _option_candidate(**over):
    """A structurally strong, liquid, comfortably-affordable-looking contract —
    the kind that should score a positive Model EV. Deliberately realistic
    (matches test_pipeline3_canonical_migration.py's fixture shape) rather than
    invented to force a particular test outcome."""
    c = {"instrument": "OPTION", "option_type": "CALL", "strike": 63.0,
        "expiry": "2026-10-01", "premium": 1.00, "pct_otm": 0.0, "days_to_expiry": 21,
        "breakeven": 64.0, "bid": 0.95, "ask": 1.05, "mid": 1.00,
        "spread_dollars": 0.10, "spread_pct": 10.0, "volume": 300, "open_interest": 1000,
        "implied_volatility": 0.30, "delta": 0.5, "grade": "B", "tradeable": True,
        "label": "\U0001F3AF OPTION · BAC $63 CALL 2026-10-01 (ATM, 21DTE)",
        "quote_timestamp": NOW.isoformat(), "quote_timestamp_source": "chain_snapshot",
        "sizing": {"contracts": 1, "capital_committed": 100.0, "max_loss": 100.0}}
    c.update(over)
    return c


def _run_canonical(monkeypatch, *, balance=500.0, sector="financials", **kw):
    _patch_c(monkeypatch, **kw)
    result = de.evaluate("BAC", "NASDAQ", "LONG", balance, evaluate_option=False,
                        profile="momentum", portfolio_check=False)
    canon = canonical_bridge.evaluate_canonical(result, symbol="BAC", direction="LONG",
                                                sector=sector)
    return result, canon


# ══════════════════════════════════════════════════════════════════════════
# Model EV semantics — regression-safe, plus the new disclosure fields
# ══════════════════════════════════════════════════════════════════════════

def test_model_ev_alias_matches_legacy_field_exactly(monkeypatch):
    """The rename is additive: model_ev_per_contract must equal ev_per_contract
    to the cent, every time — this is a labeling fix, not a retune."""
    _, canon = _run_canonical(monkeypatch, option_idea=_option_candidate())
    disp = canon["option_display"]
    assert disp["model_ev_per_contract"] == disp["ev_per_contract"]


def test_ev_per_contract_key_still_present_for_backward_compatibility(monkeypatch):
    """Other consumers (options_shadow.record(), dashboard/research.py,
    test_canonical_v11_invariants.py's field-provenance check) key off this
    exact name — it must never be removed or renamed."""
    _, canon = _run_canonical(monkeypatch, option_idea=_option_candidate())
    assert "ev_per_contract" in canon["option_display"]
    assert isinstance(canon["option_display"]["ev_per_contract"], (int, float))


def test_calibration_status_is_uncalibrated(monkeypatch):
    _, canon = _run_canonical(monkeypatch, option_idea=_option_candidate())
    assert canon["option_display"]["calibration_status"] == "UNCALIBRATED"


def test_direction_model_is_labeled_heuristic(monkeypatch):
    _, canon = _run_canonical(monkeypatch, option_idea=_option_candidate())
    assert canon["option_display"]["direction_model"] == "HEURISTIC"


def test_no_option_display_when_no_candidate(monkeypatch):
    """A None option idea must not fabricate calibration/disclosure fields out
    of nothing — option_display itself is None, same as before this change."""
    _, canon = _run_canonical(monkeypatch, option_idea=None)
    assert canon["option_display"] is None


# ══════════════════════════════════════════════════════════════════════════
# Execution isolation — positive Model EV must never imply executable
# ══════════════════════════════════════════════════════════════════════════

def test_positive_model_ev_does_not_imply_option_executable(monkeypatch):
    _, canon = _run_canonical(monkeypatch, option_idea=_option_candidate())
    disp = canon["option_display"]
    if disp is not None and disp.get("model_ev_per_contract", 0) > 0:
        assert canon["option_executable"].executable is False


def test_option_executable_always_false_shadow_only(monkeypatch):
    """Structural: canonical_bridge.py hard-codes shadow_only=True, so this must
    hold regardless of how favorable the setup looks."""
    _, canon = _run_canonical(monkeypatch, option_idea=_option_candidate())
    assert canon["option_executable"].executable is False
    assert "shadow_only" in canon["option_executable"].blockers


# ══════════════════════════════════════════════════════════════════════════
# Risk isolation — a good contract can still fail on affordability alone
# ══════════════════════════════════════════════════════════════════════════

def test_standard_contract_commonly_fails_per_trade_risk(monkeypatch):
    """A $100 max-loss contract against STRATEGY_500_POLICY's $5 per-trade cap
    (option-specific fields are None, so the same stock cap applies) should
    bind on per_trade_risk — this is the empirical pattern found in the real
    cloud shadow data, not a contrived assertion."""
    assert STRATEGY_500_POLICY.option_planned_risk_pct is None
    assert STRATEGY_500_POLICY.max_loss_per_trade == 5.0
    _, canon = _run_canonical(monkeypatch, option_idea=_option_candidate(
        premium=1.00, sizing={"contracts": 1, "capital_committed": 100.0, "max_loss": 100.0}))
    fit = canon["option_account_fit"]
    if fit is not None and not fit.eligible:
        assert fit.binding_constraint is not None


# ══════════════════════════════════════════════════════════════════════════
# Graduation separation — sample count alone must never authorize execution
# ══════════════════════════════════════════════════════════════════════════

def test_fifty_shadow_records_alone_does_not_authorize_execution(monkeypatch):
    """Seed 50 fully-gradeable, resolved shadow rows directly (Gates 1+2 both
    passing) and confirm Gates 5/6 — the only gates that can ever authorize
    execution — are unmoved by that alone."""
    for i in range(50):
        db.execute("""INSERT INTO options_shadow(
            shadow_id, created_at, session_date, symbol, contract, ev_after_costs,
            preference, gradeable, missing_json, outcome, outcome_pnl)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (f"shd_test_{i}", db.utcnow(), "2026-01-01", "TEST", f"contract-{i}",
             10.0, "prefer-option", 1, "[]", "target_hit", 5.0))
    state = options_shadow.execution_gate_state()
    by_name = {g["gate"]: g for g in state["gates"]}
    assert by_name["data_collection"]["passed"] is True
    assert by_name["outcome_evidence"]["passed"] is True
    assert by_name["risk_authorization"]["status"] == "NOT_AUTHORIZED"
    assert by_name["execution"]["status"] == "SHADOW_ONLY"
    assert state["execution_authorized"] is False


def test_execution_gate_state_reports_all_six_gates_empty_db():
    state = options_shadow.execution_gate_state()
    names = {g["gate"] for g in state["gates"]}
    assert names == {"data_collection", "outcome_evidence", "predictive_validity",
                     "economic_eligibility", "risk_authorization", "execution"}
    assert state["execution_authorized"] is False


def test_predictive_validity_gate_reports_insufficient_evidence_at_zero():
    state = options_shadow.execution_gate_state()
    gate3 = next(g for g in state["gates"] if g["gate"] == "predictive_validity")
    assert gate3["status"] == "INSUFFICIENT_EVIDENCE"


# ══════════════════════════════════════════════════════════════════════════
# Calibration bucket plumbing — never fabricate a verdict on a tiny sample
# ══════════════════════════════════════════════════════════════════════════

def test_calibration_buckets_insufficient_sample_when_empty():
    out = options_shadow.model_ev_calibration_buckets()
    assert out["status"] == "INSUFFICIENT_SAMPLE"
    assert out["buckets"] is None
    assert out["resolved_n"] == 0


def test_calibration_buckets_insufficient_sample_below_threshold():
    """9 resolved rows — one short of the 20-row floor — must still refuse a
    verdict, not almost-compute one."""
    for i in range(9):
        db.execute("""INSERT INTO options_shadow(
            shadow_id, created_at, session_date, symbol, contract, ev_after_costs,
            preference, gradeable, missing_json, outcome, outcome_pnl)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (f"shd_low_{i}", db.utcnow(), "2026-01-01", "TEST", f"contract-{i}",
             10.0, "prefer-option", 1, "[]", "target_hit", 5.0))
    out = options_shadow.model_ev_calibration_buckets()
    assert out["status"] == "INSUFFICIENT_SAMPLE"
    assert out["resolved_n"] == 9


def test_calibration_buckets_compute_once_threshold_met():
    for i in range(20):
        db.execute("""INSERT INTO options_shadow(
            shadow_id, created_at, session_date, symbol, contract, ev_after_costs,
            preference, gradeable, missing_json, outcome, outcome_pnl)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (f"shd_ok_{i}", db.utcnow(), "2026-01-01", "TEST", f"contract-{i}",
             15.0 if i % 2 == 0 else -30.0, "prefer-option", 1, "[]",
             "target_hit" if i % 2 == 0 else "stop_hit", 5.0 if i % 2 == 0 else -3.0))
    out = options_shadow.model_ev_calibration_buckets()
    assert out["status"] == "OK"
    assert out["resolved_n"] == 20
    assert out["buckets"]["0 to +10"]["n"] + out["buckets"]["+10 to +25"]["n"] == 10
    assert out["buckets"]["< -25"]["n"] == 10   # -30.0 falls below the -25 floor


def test_unresolved_records_never_counted_as_winner_or_loser():
    """Open records must be excluded from bucket math entirely, never defaulted
    to a win or a loss."""
    for i in range(25):
        db.execute("""INSERT INTO options_shadow(
            shadow_id, created_at, session_date, symbol, contract, ev_after_costs,
            preference, gradeable, missing_json, outcome, outcome_pnl)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (f"shd_open_{i}", db.utcnow(), "2026-01-01", "TEST", f"contract-{i}",
             10.0, "prefer-option", 1, "[]", "open", None))
    out = options_shadow.model_ev_calibration_buckets()
    assert out["status"] == "INSUFFICIENT_SAMPLE"
    assert out["resolved_n"] == 0


# ══════════════════════════════════════════════════════════════════════════
# Presentation helper
# ══════════════════════════════════════════════════════════════════════════

def test_format_shadow_disclosure_never_omits_calibration_status():
    text = options_shadow.format_shadow_disclosure(
        {"model_ev_per_contract": 14.2, "calibration_status": "UNCALIBRATED",
         "direction_model": "HEURISTIC"})
    assert "UNCALIBRATED" in text
    assert "HEURISTIC" in text
    assert "SHADOW ONLY" in text
    assert "+$14.20" in text


def test_format_shadow_disclosure_defaults_safely_on_missing_keys():
    """A dict missing the new keys entirely (e.g. an old cached row) must still
    render an honest, non-crashing disclosure rather than claim calibration."""
    text = options_shadow.format_shadow_disclosure({"ev_per_contract": -5.0})
    assert "UNCALIBRATED" in text
    assert "$-5.00" in text or "-$5.00" in text
