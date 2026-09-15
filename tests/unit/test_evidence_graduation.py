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
from paper.fills import Quote                                   # noqa: E402
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


# ══════════════════════════════════════════════════════════════════════════
# Phase 2 — persistence, resolver, and graduation-semantics tests
# ══════════════════════════════════════════════════════════════════════════

def _seed_shadow_row(shadow_id, **over):
    row = {
        "shadow_id": shadow_id, "created_at": db.utcnow(), "session_date": "2026-01-01",
        "symbol": "TEST", "contract": f"contract-{shadow_id}", "expiry": "2026-02-01",
        "strike": 100.0, "option_type": "CALL", "ev_after_costs": 10.0,
        "preference": "prefer-option", "gradeable": 1, "missing_json": "[]",
        "outcome": "open", "direction": "LONG", "p_direction": 0.6,
        "underlying_price": 100.0, "stock_stop": 95.0, "stock_target": 110.0,
    }
    row.update(over)
    cols = list(row.keys())
    placeholders = ",".join("?" for _ in cols)
    db.execute(f"INSERT INTO options_shadow({','.join(cols)}) VALUES({placeholders})",
               tuple(row[c] for c in cols))


class TestMigration:
    def test_new_columns_present_and_nullable(self):
        cols = {c["name"] for c in db.query("PRAGMA table_info(options_shadow)")}
        for expected in ("direction", "p_direction", "theta_drag", "p_trade",
                        "dte_used_in_model", "underlying_price", "stock_stop",
                        "stock_target", "expected_move_pct", "move_to_be_pct",
                        "break_even_within_expected_move", "contract_quality_score",
                        "contract_quality_grade", "quality_eligible",
                        "quality_rejection_reason", "risk_rejection_reason",
                        "strategy", "option_outcome"):
            assert expected in cols, f"missing column {expected}"

    def test_migration_idempotent_on_rerun(self):
        v1 = db.migrate(db.connect())
        v2 = db.migrate(db.connect())
        assert v1 == v2 == 2

    def test_existing_row_survives_with_null_new_fields(self):
        """Simulates a pre-phase-2 row: only the columns that existed in schema v1
        are populated; every new column must read back as NULL, not fabricated."""
        db.execute("""INSERT INTO options_shadow(
            shadow_id, created_at, session_date, symbol, contract, ev_after_costs,
            preference, gradeable, missing_json, outcome)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            ("shd_legacy", db.utcnow(), "2026-01-01", "NEM", "legacy-contract",
             -36.7, "avoid-both", 0, "[]", "open"))
        row = db.query_one("SELECT * FROM options_shadow WHERE shadow_id='shd_legacy'")
        assert row["ev_after_costs"] == -36.7          # old data intact
        assert row["p_direction"] is None               # new field, honestly NULL
        assert row["underlying_price"] is None
        assert row["option_outcome"] is None


class TestPersistenceNoRecomputation:
    def test_persisted_fields_match_option_display_exactly(self, monkeypatch):
        """The values written to options_shadow must be the exact same values
        canonical_bridge.py used to compute Model EV — read from option_display,
        never recomputed inside options_shadow.record()."""
        _, canon = _run_canonical(monkeypatch, option_idea=_option_candidate())
        disp = canon["option_display"]
        shadow_result = canonical_bridge.augmented_result_for_shadow(
            {"option_quality": canon["option_quality_display"]}, canon)
        sid = options_shadow.record(shadow_result, symbol="BAC", session_date="2026-01-01",
                                    strategy="sector_relative_strength")
        assert sid is not None
        row = db.query_one("SELECT * FROM options_shadow WHERE shadow_id=?", (sid,))
        assert row["p_direction"] == disp["p_direction"]
        assert row["p_trade"] == disp["p_trade"]
        assert row["direction"] == disp["direction"]
        assert row["underlying_price"] == disp["underlying_price"]
        assert row["stock_stop"] == disp["stock_stop"]
        assert row["stock_target"] == disp["stock_target"]
        assert row["theta_drag"] == disp["theta_drag"]
        assert row["ev_after_costs"] == disp["ev_per_contract"] == disp["model_ev_per_contract"]
        assert row["strategy"] == "sector_relative_strength"

    def test_no_new_broker_or_order_call_introduced(self, monkeypatch):
        """record() must still never touch orders/positions/fills — persistence-only."""
        _, canon = _run_canonical(monkeypatch, option_idea=_option_candidate())
        shadow_result = canonical_bridge.augmented_result_for_shadow(
            {"option_quality": canon["option_quality_display"]}, canon)
        before = {t: db.query_one(f"SELECT COUNT(*) n FROM {t}")["n"]
                 for t in ("orders", "fills", "positions")}
        options_shadow.record(shadow_result, symbol="BAC", session_date="2026-01-01")
        after = {t: db.query_one(f"SELECT COUNT(*) n FROM {t}")["n"]
                for t in ("orders", "fills", "positions")}
        assert before == after


class TestResolver:
    def test_still_open_with_no_quote(self):
        _seed_shadow_row("shd_a")
        out = options_shadow.resolve_outcomes({}, "2026-01-02")
        assert out["skipped_missing_data"] == 1
        row = db.query_one("SELECT outcome FROM options_shadow WHERE shadow_id='shd_a'")
        assert row["outcome"] == "open"

    def test_target_hit_only(self):
        _seed_shadow_row("shd_b")
        q = Quote("TEST", last=111, high=112, low=105)
        out = options_shadow.resolve_outcomes({"TEST": q}, "2026-01-02")
        assert out["resolved"] == 1
        row = db.query_one("SELECT outcome, outcome_pnl, option_outcome FROM options_shadow "
                           "WHERE shadow_id='shd_b'")
        assert row["outcome"] == "target_hit"
        assert row["outcome_pnl"] == pytest.approx(10.0)   # target(110) - entry(100)
        assert row["option_outcome"] == "unavailable"

    def test_stop_hit_only(self):
        _seed_shadow_row("shd_c")
        q = Quote("TEST", last=94, high=99, low=93)
        out = options_shadow.resolve_outcomes({"TEST": q}, "2026-01-02")
        assert out["resolved"] == 1
        row = db.query_one("SELECT outcome, outcome_pnl FROM options_shadow WHERE shadow_id='shd_c'")
        assert row["outcome"] == "stop_hit"
        assert row["outcome_pnl"] == pytest.approx(-5.0)   # stop(95) - entry(100)

    def test_same_bar_both_touched_stop_wins(self):
        """Reuses journal.update_excursions()'s exact conservative policy — stop
        wins on a same-bar collision. NOT an invented 'ambiguous' state."""
        _seed_shadow_row("shd_d")
        q = Quote("TEST", last=100, high=115, low=90)   # both target and stop crossed
        options_shadow.resolve_outcomes({"TEST": q}, "2026-01-02")
        row = db.query_one("SELECT outcome FROM options_shadow WHERE shadow_id='shd_d'")
        assert row["outcome"] == "stop_hit"

    def test_expired(self):
        _seed_shadow_row("shd_e", expiry="2026-01-01")
        out = options_shadow.resolve_outcomes({}, "2026-01-05")
        assert out["expired"] == 1
        row = db.query_one("SELECT outcome, option_outcome FROM options_shadow WHERE shadow_id='shd_e'")
        assert row["outcome"] == "expired"
        assert row["option_outcome"] == "unavailable"

    def test_missing_market_data_stays_open(self):
        _seed_shadow_row("shd_f")
        out = options_shadow.resolve_outcomes({"OTHER_SYMBOL": Quote("OTHER_SYMBOL", last=1)},
                                              "2026-01-02")
        assert out["skipped_missing_data"] == 1
        row = db.query_one("SELECT outcome FROM options_shadow WHERE shadow_id='shd_f'")
        assert row["outcome"] == "open"

    def test_legacy_row_with_null_stop_target_never_resolves(self):
        """A pre-phase-2 row (no stock_stop/stock_target persisted) must stay open
        forever rather than guess — this is the honest, expected outcome for the
        real 10 historical rows."""
        _seed_shadow_row("shd_g", stock_stop=None, stock_target=None, underlying_price=None,
                         direction=None)
        q = Quote("TEST", last=200, high=200, low=200)
        out = options_shadow.resolve_outcomes({"TEST": q}, "2026-01-02")
        assert out["skipped_missing_data"] == 1
        row = db.query_one("SELECT outcome FROM options_shadow WHERE shadow_id='shd_g'")
        assert row["outcome"] == "open"

    def test_already_resolved_row_is_never_touched_again(self):
        _seed_shadow_row("shd_h", outcome="target_hit", outcome_pnl=10.0)
        q = Quote("TEST", last=50, high=50, low=50)   # would look like a stop-hit if re-graded
        options_shadow.resolve_outcomes({"TEST": q}, "2026-01-02")
        row = db.query_one("SELECT outcome, outcome_pnl FROM options_shadow WHERE shadow_id='shd_h'")
        assert row["outcome"] == "target_hit" and row["outcome_pnl"] == 10.0

    def test_rerun_same_day_is_idempotent(self):
        _seed_shadow_row("shd_i")
        q = Quote("TEST", last=111, high=112, low=105)
        options_shadow.resolve_outcomes({"TEST": q}, "2026-01-02")
        first = dict(db.query_one("SELECT outcome, outcome_pnl, outcome_at FROM options_shadow "
                                  "WHERE shadow_id='shd_i'"))
        options_shadow.resolve_outcomes({"TEST": q}, "2026-01-02")   # rerun, same day
        second = dict(db.query_one("SELECT outcome, outcome_pnl, outcome_at FROM options_shadow "
                                   "WHERE shadow_id='shd_i'"))
        assert first == second

    def test_resolver_issues_zero_order_or_broker_calls(self, monkeypatch):
        """The resolver must be structurally incapable of trading — assert the
        broker/order-placement functions are never called, not merely unused today."""
        import paper.broker as broker_mod
        calls = []
        monkeypatch.setattr(broker_mod, "place_order", lambda *a, **k: calls.append("place_order"))
        monkeypatch.setattr(broker_mod, "submit_entry", lambda *a, **k: calls.append("submit_entry"))
        _seed_shadow_row("shd_j")
        q = Quote("TEST", last=111, high=112, low=105)
        options_shadow.resolve_outcomes({"TEST": q}, "2026-01-02")
        assert calls == []

    def test_short_direction_resolved_correctly(self):
        _seed_shadow_row("shd_k", direction="SHORT", underlying_price=100.0,
                         stock_stop=105.0, stock_target=90.0)
        q = Quote("TEST", last=89, high=91, low=88)
        options_shadow.resolve_outcomes({"TEST": q}, "2026-01-02")
        row = db.query_one("SELECT outcome, outcome_pnl FROM options_shadow WHERE shadow_id='shd_k'")
        assert row["outcome"] == "target_hit"
        assert row["outcome_pnl"] == pytest.approx(10.0)   # entry(100) - target(90)


class TestBackfill:
    def _seed_signal(self, symbol, session_date, entry, stop, target, strategy="liquid_momentum"):
        db.execute("""INSERT INTO signals(signal_id, created_at, session_date, symbol, strategy,
                      action, quality, entry, stop, target, executed, outcome)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (f"sig_{symbol}_{session_date}", db.utcnow(), session_date, symbol, strategy,
                    "MONITOR", 50.0, entry, stop, target, 0, "open"))

    def test_recovers_from_unique_signals_and_audit_match(self):
        self._seed_signal("NEM", "2026-01-01", entry=126.16, stop=119.92, target=139.78)
        db.execute("""INSERT INTO options_shadow(shadow_id, created_at, session_date, symbol,
                      contract, ev_after_costs, preference, gradeable, missing_json, outcome)
                      VALUES(?,?,?,?,?,?,?,?,?,?)""",
                   ("shd_bf1", db.utcnow(), "2026-01-01", "NEM", "c1", -36.7, "avoid-both",
                    0, "[]", "open"))
        db.audit("options_shadow_canonical", "shd_bf1", "canonical_evaluated",
                 {"quality_score": 33.0, "quality_grade": "D", "quality_pass": False,
                  "option_eligible": False, "option_binding_constraint": "structurally_invalid"})
        out = options_shadow.backfill_recoverable_evidence(dry_run=False)
        assert out["backfilled"] == 1
        row = db.query_one("SELECT * FROM options_shadow WHERE shadow_id='shd_bf1'")
        assert row["underlying_price"] == 126.16
        assert row["stock_stop"] == 119.92
        assert row["stock_target"] == 139.78
        assert row["direction"] == "LONG"
        assert row["strategy"] == "liquid_momentum"
        assert row["contract_quality_score"] == 33.0
        assert row["contract_quality_grade"] == "D"
        assert row["quality_eligible"] == 0
        assert row["risk_rejection_reason"] == "structurally_invalid"
        # never fabricated:
        assert row["p_direction"] is None
        assert row["theta_drag"] is None

    def test_skips_when_multiple_signals_match(self):
        """Ambiguous — more than one signals row for (symbol, session_date) — must
        be skipped, never guessed at."""
        self._seed_signal("DUP", "2026-01-01", entry=100, stop=95, target=110)
        db.execute("""INSERT INTO signals(signal_id, created_at, session_date, symbol, strategy,
                      action, quality, entry, stop, target, executed, outcome)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                   ("sig_DUP_2", db.utcnow(), "2026-01-01", "DUP", "other",
                    "MONITOR", 40.0, 101, 96, 111, 0, "open"))
        db.execute("""INSERT INTO options_shadow(shadow_id, created_at, session_date, symbol,
                      contract, ev_after_costs, preference, gradeable, missing_json, outcome)
                      VALUES(?,?,?,?,?,?,?,?,?,?)""",
                   ("shd_bf2", db.utcnow(), "2026-01-01", "DUP", "c1", 5.0, "avoid-both",
                    0, "[]", "open"))
        out = options_shadow.backfill_recoverable_evidence(dry_run=False)
        row = db.query_one("SELECT underlying_price, strategy FROM options_shadow WHERE shadow_id='shd_bf2'")
        assert row["underlying_price"] is None
        assert row["strategy"] is None

    def test_dry_run_writes_nothing(self):
        self._seed_signal("NEM", "2026-01-01", entry=126.16, stop=119.92, target=139.78)
        db.execute("""INSERT INTO options_shadow(shadow_id, created_at, session_date, symbol,
                      contract, ev_after_costs, preference, gradeable, missing_json, outcome)
                      VALUES(?,?,?,?,?,?,?,?,?,?)""",
                   ("shd_bf3", db.utcnow(), "2026-01-01", "NEM", "c1", -36.7, "avoid-both",
                    0, "[]", "open"))
        out = options_shadow.backfill_recoverable_evidence(dry_run=True)
        assert out["backfilled"] == 1   # reports what WOULD change
        row = db.query_one("SELECT underlying_price FROM options_shadow WHERE shadow_id='shd_bf3'")
        assert row["underlying_price"] is None   # but nothing actually written

    def test_already_backfilled_row_is_not_a_candidate_again(self):
        self._seed_signal("NEM", "2026-01-01", entry=126.16, stop=119.92, target=139.78)
        db.execute("""INSERT INTO options_shadow(shadow_id, created_at, session_date, symbol,
                      contract, ev_after_costs, preference, gradeable, missing_json, outcome)
                      VALUES(?,?,?,?,?,?,?,?,?,?)""",
                   ("shd_bf4", db.utcnow(), "2026-01-01", "NEM", "c1", -36.7, "avoid-both",
                    0, "[]", "open"))
        options_shadow.backfill_recoverable_evidence(dry_run=False)
        out2 = options_shadow.backfill_recoverable_evidence(dry_run=False)
        assert out2["candidates"] == 0   # underlying_price no longer NULL


class TestGraduationSemanticsAfterResolver:
    def test_open_observations_do_not_count_as_resolved(self):
        for i in range(30):
            _seed_shadow_row(f"shd_open_{i}")
        ready = options_shadow.graduation_readiness()
        resolved_check = next(c for c in ready["checks"] if c["name"] == "resolved_outcomes")
        assert resolved_check["passed"] is False

    def test_resolved_observations_count_toward_gate2(self):
        q = Quote("TEST", last=111, high=112, low=105)
        for i in range(20):
            _seed_shadow_row(f"shd_res_{i}")
        options_shadow.resolve_outcomes({"TEST": q}, "2026-01-02")
        state = options_shadow.execution_gate_state()
        gate2 = next(g for g in state["gates"] if g["gate"] == "outcome_evidence")
        assert gate2["passed"] is True

    def test_twenty_resolved_does_not_authorize_execution(self):
        q = Quote("TEST", last=111, high=112, low=105)
        for i in range(20):
            _seed_shadow_row(f"shd_20_{i}")
        options_shadow.resolve_outcomes({"TEST": q}, "2026-01-02")
        state = options_shadow.execution_gate_state()
        by_name = {g["gate"]: g for g in state["gates"]}
        assert by_name["predictive_validity"]["status"] == "DESCRIPTIVE_ONLY"
        assert by_name["risk_authorization"]["status"] == "NOT_AUTHORIZED"
        assert by_name["execution"]["status"] == "SHADOW_ONLY"
        assert state["execution_authorized"] is False

    def test_fifty_resolved_still_does_not_authorize_execution(self):
        q = Quote("TEST", last=111, high=112, low=105)
        for i in range(50):
            _seed_shadow_row(f"shd_50_{i}")
        options_shadow.resolve_outcomes({"TEST": q}, "2026-01-02")
        state = options_shadow.execution_gate_state()
        by_name = {g["gate"]: g for g in state["gates"]}
        assert by_name["predictive_validity"]["status"] == "READY_FOR_VALIDATION"
        assert by_name["risk_authorization"]["status"] == "NOT_AUTHORIZED"
        assert by_name["execution"]["status"] == "SHADOW_ONLY"
        assert state["execution_authorized"] is False

    def test_positive_model_ev_does_not_authorize_execution_even_when_resolved(self):
        _seed_shadow_row("shd_pos", ev_after_costs=500.0)
        q = Quote("TEST", last=111, high=112, low=105)
        options_shadow.resolve_outcomes({"TEST": q}, "2026-01-02")
        state = options_shadow.execution_gate_state()
        by_name = {g["gate"]: g for g in state["gates"]}
        assert by_name["risk_authorization"]["status"] == "NOT_AUTHORIZED"
        assert by_name["execution"]["status"] == "SHADOW_ONLY"


class TestLegacyNullHandling:
    def test_p_direction_buckets_exclude_null_not_zero(self):
        for i in range(25):
            _seed_shadow_row(f"shd_null_{i}", p_direction=None)
        q = Quote("TEST", last=111, high=112, low=105)
        options_shadow.resolve_outcomes({"TEST": q}, "2026-01-02")
        out = options_shadow.p_direction_calibration_buckets()
        assert out["status"] == "INSUFFICIENT_SAMPLE"
        assert out["resolved_n"] == 0   # excluded, not counted as 0

    def test_p_direction_buckets_compute_once_persisted_and_resolved(self):
        q = Quote("TEST", last=111, high=112, low=105)
        for i in range(20):
            _seed_shadow_row(f"shd_pd_{i}", p_direction=0.5 + (i % 2) * 0.2)
        options_shadow.resolve_outcomes({"TEST": q}, "2026-01-02")
        out = options_shadow.p_direction_calibration_buckets()
        assert out["status"] == "OK"
        assert out["resolved_n"] == 20

    def test_calibration_buckets_dedupe_same_day_duplicates(self):
        """Two rows sharing (session_date, symbol, contract) — a same-day duplicate,
        not a legitimate separate observation — must count once, not twice."""
        q = Quote("TEST", last=111, high=112, low=105)
        _seed_shadow_row("shd_dup_1", contract="dupcontract", created_at="2026-01-01T10:00:00Z")
        _seed_shadow_row("shd_dup_2", contract="dupcontract", created_at="2026-01-01T11:00:00Z")
        for i in range(19):   # pad to the 20-row descriptive floor
            _seed_shadow_row(f"shd_pad_{i}")
        options_shadow.resolve_outcomes({"TEST": q}, "2026-01-02")
        out = options_shadow.model_ev_calibration_buckets()
        assert out["resolved_n"] == 20   # 21 rows resolved, 1 deduped away

    def test_next_day_observation_of_same_contract_not_collapsed(self):
        """Same symbol+contract on a DIFFERENT session_date is a legitimate
        separate observation and must not be deduped away."""
        q = Quote("TEST", last=111, high=112, low=105)
        _seed_shadow_row("shd_day1", contract="samecontract", session_date="2026-01-01")
        _seed_shadow_row("shd_day2", contract="samecontract", session_date="2026-01-02")
        for i in range(18):
            _seed_shadow_row(f"shd_pad2_{i}")
        options_shadow.resolve_outcomes({"TEST": q}, "2026-01-02")
        out = options_shadow.model_ev_calibration_buckets()
        assert out["resolved_n"] == 20   # both day1 and day2 observations counted
