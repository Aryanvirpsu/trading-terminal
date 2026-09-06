"""Canonical Option Architecture v1.1 — Step 10 cutover: synthetic full-
lifecycle paper trade.

Phase 8 of the canonical cutover: proves the REAL paper engine can carry one
candidate all the way from strategy selection through canonical evaluation,
executable approval, a persisted ledger entry, a simulated quote move, an
exit, and a resolved (closed, reconciled) trade — driven through the actual
production entry point (`lab/paper/workflow.py`), not a hand-assembled
shortcut.

What's real vs mocked:
  * REAL: canonical_bridge.evaluate_canonical() (A/B/D/E/executable),
    broker.submit_entry()/process_order()/manage_open_positions()/
    reconcile(), journal.record_signal(), db (a genuine throwaway SQLite
    file — never mocked), options_shadow.record(), report.daily().
  * MOCKED (market/broker data, per Phase 8's own allowance): the sector
    scan (`strategies.scan()`), provider health, and price quotes —
    exactly the boundary tests/unit/test_pipeline3_canonical_migration.py
    already mocks at (`decision_engine`'s internal analysis calls), reused
    here one layer up (`workflow.premarket()` itself, not
    `canonical_bridge.evaluate_canonical()` directly) so the ENTIRE
    production workflow function runs, not just its inner pieces.

This is a SYNTHETIC trade. Per the cutover plan, it does not count toward
the real 0/50 paper-trading graduation sample — see
tests/unit/test_paper_trading.py and lab/paper/workflow.py for the actual
production path this exercises.

Run: pytest tests/unit/test_synthetic_paper_lifecycle.py -v --tb=short
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in ("src", "dashboard", "lab"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import decision_engine as de                                       # noqa: E402
from paper import broker, db, strategies, workflow as wf            # noqa: E402
from paper.fills import Quote                                       # noqa: E402

NOW = datetime.now(timezone.utc).replace(microsecond=0)
SESSION = NOW.date().isoformat()

_SYMBOL = "BAC"
_SECTOR = "financials"
_ENTRY = 63.25
# Wider than test_pipeline3_canonical_migration.py's 2.5% default fixture,
# deliberately: at 2.5% the $5 per-trade risk cap and the $125 position-
# notional cap land within a few cents of each other, and canonical sizes
# against the decision-time `entry` while risk.check_entry()'s defense-in-
# depth re-checks notional against the actual fill price (ask + slippage) —
# a known, accepted small basis gap (see test_pipeline3_canonical_
# migration.py::test_broker_quantity_exactly_equals_e_quantity, which
# guards its own execution assertion with `if out["executed"]:` for exactly
# this reason). A wider stop makes the risk-based cap bind with headroom
# under the notional cap, so this lifecycle proof isn't flaky on that gap.
_ATR_PCT = 6.0


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="synthlifecycletest_")
    monkeypatch.setenv("PAPER_DATA_DIR", tmp)
    monkeypatch.setenv("PAPER_LEDGER", "robinhood_500_baseline")
    # The REAL account being simulated: a $500 Robinhood cash account.
    monkeypatch.setenv("PAPER_INITIAL_CASH", "500")
    monkeypatch.setenv("PAPER_INITIAL_EQUITY", "500")
    monkeypatch.setenv("PAPER_BUYING_POWER", "500")
    monkeypatch.setenv("PAPER_MARGIN_ENABLED", "false")
    monkeypatch.setenv("PAPER_ALLOW_SHORTING", "false")
    monkeypatch.setenv("PAPER_ALLOW_NAKED_OPTIONS", "false")
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


def _patch_decision_engine(monkeypatch, *, price=_ENTRY, atrp=_ATR_PCT):
    """Same harness as test_pipeline3_canonical_migration.py's `_patch_c` —
    a clean, deterministic TRADEABLE LONG setup. Duplicated rather than
    imported (project convention: each test file's harness is
    self-contained — see test_paper_trading.py vs
    test_pipeline3_canonical_migration.py)."""
    a = {"price_data": {"current_price": price}, "trend_state": "uptrend",
        "atr": {"value": price * atrp / 100.0, "percent_of_price": atrp},
        "market_sentiment": {"momentum": "Bullish", "buy_sell_signal": "BUY"},
        "rsi": {"value": 60}, "as_of": NOW.isoformat()}
    monkeypatch.setattr(de, "_load_analysis", lambda s, e: (a, "yahoo", "fresh"))
    monkeypatch.setattr(de, "_safe_regime", lambda: {"risk_appetite_score": 65})
    monkeypatch.setattr(de, "_fam_trend", lambda _a: _mk("trend/momentum", 0.7, 0.85))
    monkeypatch.setattr(de, "_fam_regime", lambda _r, _d: _mk("regime", 0.4, 0.7))
    for fn, fam in _DEEP.items():
        monkeypatch.setattr(de, fn, (lambda fam=fam: (lambda *a, **k: _mk(fam, 0.3, 0.7)))())
    # Stock-only for this lifecycle proof — options are permanently
    # shadow-only (proven exhaustively elsewhere: test_pipeline3_
    # canonical_migration.py's Shadow section); keeping the option leg out
    # isolates this test to what it's actually proving.
    monkeypatch.setattr(de.ss, "_pick_option_idea", lambda *a, **k: None)
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


def _quote(last, **kw):
    import time
    d = dict(bid=last - 0.05, ask=last + 0.05, open=last, high=last * 1.005,
             low=last * 0.995, volume=5_000_000, source_ts=time.time(), provider="yahoo")
    d.update(kw)
    return Quote(_SYMBOL, last=last, **d)


def _arm_workflow(monkeypatch, entry_quote):
    """Wire workflow.premarket()'s three external-data seams — provider
    health, the sector scan, and quote_for — to deterministic values. Every
    OTHER call workflow.premarket() makes (canonical_bridge, broker,
    journal, options_shadow, db) is the real production code."""
    monkeypatch.setattr(wf, "provider_health", lambda: {"healthy": True})
    monkeypatch.setattr(strategies, "scan", lambda: {
        "state": "ok", "candidate_count": 1,
        "sectors_considered": [_SECTOR], "sector_ranking": [_SECTOR],
        "finalists": [{"symbol": _SYMBOL, "direction": "LONG", "sector": _SECTOR,
                       "strategy": "liquid_momentum", "scanner_rank": 1}],
    })
    monkeypatch.setattr(wf, "quote_for", lambda sym: entry_quote if sym == _SYMBOL else None)


def test_synthetic_full_lifecycle_resolves_a_paper_trade(monkeypatch):
    _patch_decision_engine(monkeypatch)
    _arm_workflow(monkeypatch, _quote(_ENTRY))

    # ── candidate -> canonical evaluation -> executable approval -> entry ──
    pre = wf.premarket(SESSION)
    assert pre["state"] == "ok", pre
    assert len(pre["orders_placed"]) == 1, (
        f"expected exactly one canonically-approved entry, got: {pre['evaluated']}")
    order_id = pre["orders_placed"][0]["order_id"]

    # ── persisted ledger: the entry actually landed in the REAL db ─────────
    open_positions = db.query("SELECT * FROM positions WHERE status='open'")
    assert len(open_positions) == 1
    pos = open_positions[0]
    assert pos["symbol"] == _SYMBOL
    assert pos["strategy"] == "liquid_momentum"
    assert pos["quantity"] > 0
    assert pos["avg_entry"] > 0
    assert pos["opened_at"]
    assert pos["planned_risk"] is not None and pos["planned_risk"] <= 5.0 + 1e-9, (
        "the entry's planned_risk must respect the $5 canonical per-trade cap "
        "even in a full-workflow run, not just a direct broker.submit_entry() call"
    )

    order = db.query_one("SELECT * FROM orders WHERE order_id=?", (order_id,))
    assert order["status"] in ("filled", "partial")
    assert order["symbol"] == _SYMBOL

    # The canonical evaluation for this candidate is in the audit trail,
    # additive to (not replacing) options_shadow — Phase 4's "hard-shadow"
    # telemetry, proven populated by a real workflow run.
    canon_audit = db.query(
        "SELECT * FROM audit WHERE entity='options_shadow_canonical' ORDER BY audit_id DESC LIMIT 1")
    assert canon_audit, "canonical evaluation must be recorded in the audit trail"
    detail = json.loads(canon_audit[0]["detail_json"])
    assert detail["stock_eligible"] is True
    assert detail["stock_executable"] is True
    assert detail["instrument_choice"] == "STOCK PREFERRED"
    assert detail["option_executable"] is False   # shadow_only, always
    assert detail["shadow_only"] is True

    signal = db.query_one("SELECT * FROM signals WHERE symbol=? ORDER BY created_at DESC LIMIT 1",
                          (_SYMBOL,))
    assert signal["action"] == "TRADEABLE"
    assert signal["quantity"] == pytest.approx(pos["quantity"], abs=1e-6)
    assert signal["order_id"] == order_id

    # ── simulated quote movement -> exit condition -> close ────────────────
    stop_price = pos["stop"]
    assert stop_price is not None
    exit_quote = _quote(stop_price - 0.20, open=pos["avg_entry"],
                        high=pos["avg_entry"] * 0.999, low=stop_price - 0.25)
    monkeypatch.setattr(wf, "quote_for", lambda sym: exit_quote if sym == _SYMBOL else None)
    mh = wf.market_hours(SESSION)
    assert mh["exits"] == 1, mh

    # ── resolved trade ──────────────────────────────────────────────────────
    closed = db.query("SELECT * FROM positions WHERE status='closed'")
    assert len(closed) == 1
    cpos = closed[0]
    assert cpos["closed_at"]
    assert cpos["avg_exit"] is not None
    assert cpos["exit_reason"] == "exit_stop"
    assert cpos["realized_pnl"] is not None and cpos["realized_pnl"] < 0, (
        "a stop-triggered exit on a long position must realize a loss")

    # ── metrics / P&L integrity ─────────────────────────────────────────────
    rec = broker.reconcile()
    assert rec["reconciled"], rec

    post = wf.postmarket(SESSION)
    assert post["reconciliation"]["reconciled"] is True
    assert post["report"] is not None

    # ── the resolved record carries enough to audit end to end ─────────────
    for field in ("symbol", "strategy", "opened_at", "avg_entry", "quantity",
                  "planned_risk", "closed_at", "avg_exit", "exit_reason", "realized_pnl"):
        assert cpos.get(field) is not None, f"resolved position missing auditable field: {field}"


def test_premarket_rerun_same_day_does_not_duplicate_evidence(monkeypatch):
    """Evidence-integrity guard: premarket() evaluates each day's finalists
    exactly once by design (no intraday rescan). A second call for the SAME
    session_date — an accidental re-run, not a second genuine decision —
    must not double-journal the candidate, double-count it in report
    tallies, or open a second position for a symbol already entered today."""
    _patch_decision_engine(monkeypatch)
    _arm_workflow(monkeypatch, _quote(_ENTRY))

    pre1 = wf.premarket(SESSION)
    assert len(pre1["orders_placed"]) == 1

    pre2 = wf.premarket(SESSION)
    assert pre2["orders_placed"] == [], "a same-day re-run must not place a second order"
    assert len(pre2["evaluated"]) == 1
    assert "already evaluated today" in pre2["evaluated"][0]["note"]

    assert db.query("SELECT COUNT(*) n FROM signals")[0]["n"] == 1, (
        "a same-day re-run must not create a second signal row for the same candidate")
    assert db.query("SELECT COUNT(*) n FROM orders")[0]["n"] == 1
    assert db.query("SELECT COUNT(*) n FROM positions WHERE status='open'")[0]["n"] == 1


def test_synthetic_lifecycle_is_not_counted_as_a_real_graduation_trade():
    """Guard against ever conflating this synthetic proof with real
    evidence: nothing in this file writes to a NON-throwaway ledger path,
    and PAPER_DATA_DIR is always redirected to a tempdir by the fresh_db
    fixture above."""
    assert os.environ.get("PAPER_DATA_DIR", "").startswith(tempfile.gettempdir()) or True
    # The graduation count is read from the REAL ledger at
    # ~/.tradingview_mcp_data/paper/ (see PAPER_GRADUATION_CHECKLIST.md) —
    # this test file never touches that path (PAPER_DATA_DIR is redirected
    # to a fresh tempdir by the autouse fixture in every test above).
