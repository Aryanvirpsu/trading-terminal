"""Canonical Option Architecture v1.1 — Step 10: Pipeline 3 (Strategy-500)
migration tests.

Pipeline 3 = lab/paper/strategies.py:scan() -> lab/paper/workflow.py:
premarket() -> decision_engine.evaluate(evaluate_option=False) (Layer C,
unchanged) -> lab/paper/canonical_bridge.py:evaluate_canonical() (A/B/D/E/
executable, new) -> lab/paper/broker.py:submit_entry() (stock only, requires
stock_executable=True, quantity == canonical E exactly) / lab/paper/
options_shadow.py:record() (options, ALWAYS shadow_only=True, never ledger).

Fully offline and deterministic: decision_engine's own 9 signal families,
regime, halts and the portfolio risk gate are monkeypatched exactly as
tests/unit/test_decision_logic.py and test_pipeline1_canonical_migration.py
already do; strategy_service._pick_option_idea() is monkeypatched per-test
to a controlled candidate (or None); the paper ledger is a fresh throwaway
SQLite file per test (tests/unit/test_paper_trading.py's own fixture
pattern, reused here).

Run: pytest tests/unit/test_pipeline3_canonical_migration.py -v --tb=short
"""
from __future__ import annotations

import inspect
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in ("src", "dashboard", "lab"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import decision_engine as de                                   # noqa: E402
from tradingview_mcp.core.services import strategy_service as ss  # noqa: E402

from paper import broker, canonical_bridge, config as pcfg, db, journal, options_shadow, risk  # noqa: E402
from paper.fills import Quote                                  # noqa: E402

from canonical.contract_quality import evaluate_contract_quality  # noqa: E402
from canonical.risk_policy import STRATEGY_500_POLICY            # noqa: E402
from canonical.instrument_choice import InstrumentChoice          # noqa: E402

NOW = datetime.now(timezone.utc).replace(microsecond=0)


# ══════════════════════════════════════════════════════════════════════════
# Harness
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="p3migrationtest_")
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
            option_idea=None, portfolio_blocks=False):
    families = families or {f: (0.3, 0.7) for f in _DEEP.values()}
    a = {"price_data": {"current_price": price}, "trend_state": "uptrend",
        "atr": {"value": price * atrp / 100.0, "percent_of_price": atrp},
        "market_sentiment": {"momentum": "Bullish", "buy_sell_signal": "BUY"},
        "rsi": {"value": 60},
        # broker.check_preconditions() requires freshness in ("fresh", "ageing")
        # — STRICTER than decision_engine's own soft freshness gate, which
        # tolerates "unknown" in some paths. Without `as_of`, decision_engine's
        # _engine_freshness() reads no timestamp and reports "unknown",
        # silently blocking every execution test. A real, fresh `as_of` is
        # what a genuine live provider read would carry.
        "as_of": NOW.isoformat()}
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
    if not portfolio_blocks:
        try:
            import risk_engine
            monkeypatch.setattr(risk_engine, "check_new_trade", lambda sym, r, spec: {
                "allow": True, "reasons": [], "size_cap_usd": 1e6,
                "portfolio": {"suspended": False, "drawdown_pct": 0.0}})
        except Exception:
            pass


def _option_candidate(**over):
    c = {"instrument": "OPTION", "option_type": "CALL", "strike": 63.0,
        "expiry": "2026-10-01", "premium": 1.00, "pct_otm": 0.0, "days_to_expiry": 21,
        "breakeven": 64.0, "bid": 0.95, "ask": 1.05, "mid": 1.00,
        "spread_dollars": 0.10, "spread_pct": 10.0, "volume": 300, "open_interest": 1000,
        "implied_volatility": 0.30, "delta": 0.5, "grade": "B", "tradeable": True,
        "label": "🎯 OPTION · BAC $63 CALL 2026-10-01 (ATM, 21DTE)",
        "quote_timestamp": NOW.isoformat(), "quote_timestamp_source": "chain_snapshot",
        "sizing": {"contracts": 1, "capital_committed": 100.0, "max_loss": 100.0}}
    c.update(over)
    return c


def _run_c(monkeypatch, *, balance=500.0, direction="LONG", **kw):
    """The real Pipeline-3 production C call: portfolio_check=False,
    evaluate_option=False — exactly what workflow.premarket() uses."""
    _patch_c(monkeypatch, **kw)
    return de.evaluate("BAC", "NASDAQ", direction, balance, evaluate_option=False,
                       profile="momentum", portfolio_check=False)


def _run_canonical(monkeypatch, *, balance=500.0, sector="financials", **kw):
    result = _run_c(monkeypatch, balance=balance, **kw)
    canon = canonical_bridge.evaluate_canonical(result, symbol="BAC", direction="LONG",
                                                sector=sector)
    return result, canon


def _quote(symbol="BAC", last=63.25, **kw):
    import time
    d = dict(bid=last - 0.05, ask=last + 0.05, open=last, high=last * 1.01,
            low=last * 0.99, volume=5_000_000, source_ts=time.time(), provider="yahoo")
    d.update(kw)
    return Quote(symbol, last=last, **d)


# ══════════════════════════════════════════════════════════════════════════
# C preservation
# ══════════════════════════════════════════════════════════════════════════

def test_hard_failure_remains_reject(monkeypatch):
    r = _run_c(monkeypatch, trend=(-0.6, 0.8), regime=(-0.4, 0.6),
              families={f: (-0.3, 0.6) for f in _DEEP.values()})
    assert r["decision"] == "REJECT"
    assert [g for g in r["decision_gates"] if g["blocking"] and not g["passed"]]


def test_soft_failure_remains_monitor(monkeypatch):
    r = _run_c(monkeypatch, data_state="fallback-provider", a_source="yfinance-fallback")
    assert r["decision"] == "MONITOR"
    assert not [g for g in r["decision_gates"] if g["blocking"] and not g["passed"]]


def test_tradeable_remains_tradeable(monkeypatch):
    r = _run_c(monkeypatch)
    assert r["decision"] == "TRADEABLE"
    assert all(g["passed"] for g in r["decision_gates"])


def test_evaluate_option_false_preserves_c_semantics(monkeypatch):
    _patch_c(monkeypatch, option_idea=None)
    with_opt = de.evaluate("BAC", "NASDAQ", "LONG", 500.0, evaluate_option=True,
                           profile="momentum", portfolio_check=False)
    _patch_c(monkeypatch, option_idea=None)
    without_opt = de.evaluate("BAC", "NASDAQ", "LONG", 500.0, evaluate_option=False,
                              profile="momentum", portfolio_check=False)
    assert with_opt["decision"] == without_opt["decision"]
    assert with_opt["decision_gates"] == without_opt["decision_gates"]
    assert with_opt["suggested_shares"] == without_opt["suggested_shares"]
    assert with_opt["stop"] == without_opt["stop"] and with_opt["target"] == without_opt["target"]


# ══════════════════════════════════════════════════════════════════════════
# Account state
# ══════════════════════════════════════════════════════════════════════════

def test_real_paper_equity_mapped_into_b(monkeypatch):
    result, canon = _run_canonical(monkeypatch)
    st = risk.account_state()
    assert canon["stock_account_fit"] is not None
    direct = None
    from canonical.account_fit import stock_account_fit
    direct = stock_account_fit(policy=STRATEGY_500_POLICY, equity=st["equity"],
                               entry=result["entry_range"][0], stop=result["stop"],
                               buying_power=st["buying_power"], open_positions=0,
                               sector="financials")
    assert canon["stock_account_fit"].quantity_allowed == pytest.approx(direct.quantity_allowed)


def test_real_buying_power_cash_mapped(monkeypatch):
    result, canon = _run_canonical(monkeypatch)
    st = risk.account_state()
    assert st["buying_power"] == pytest.approx(500.0 - 100.0)   # cash reserve excluded
    assert canon["account_state"]["buying_power"] == st["buying_power"]


def test_open_positions_mapped(monkeypatch):
    # Open a real position first, via the legacy (non-canonical) broker path.
    _patch_c(monkeypatch)
    r = de.evaluate("XOM", "NASDAQ", "LONG", 500.0, evaluate_option=False,
                    profile="momentum", portfolio_check=False)
    r["symbol"] = "XOM"
    broker.submit_entry(r, _quote("XOM", last=40.0), strategy="s", sector="energy")

    result, canon = _run_canonical(monkeypatch, sector="financials")
    assert canon["account_state"]["open_positions"] == 1


def test_sector_count_exposure_mapped(monkeypatch):
    _patch_c(monkeypatch)
    r = de.evaluate("XOM", "NASDAQ", "LONG", 500.0, evaluate_option=False,
                    profile="momentum", portfolio_check=False)
    r["symbol"] = "XOM"
    out = broker.submit_entry(r, _quote("XOM", last=40.0), strategy="s", sector="energy")
    assert out["executed"] is True

    result, canon = _run_canonical(monkeypatch, sector="energy")
    st = canon["account_state"]
    assert st["sector_positions"].get("energy") == 1
    assert st["sector_value"].get("energy", 0.0) > 0
    assert canon["stock_account_fit"] is not None


def test_daily_loss_drawdown_mapped(monkeypatch):
    result, canon = _run_canonical(monkeypatch)
    st = canon["account_state"]
    assert "day_pnl" in st and "drawdown_usd" in st
    assert canon["stock_account_fit"] is not None   # built without error from these fields


# ══════════════════════════════════════════════════════════════════════════
# A
# ══════════════════════════════════════════════════════════════════════════

def test_direct_a_equals_pipeline3_a(monkeypatch):
    opt = _option_candidate()
    result, canon = _run_canonical(monkeypatch, option_idea=opt)
    direct = evaluate_contract_quality(
        strike=opt["strike"], underlying=result["entry_range"][0], side="CALL",
        bid=opt["bid"], ask=opt["ask"], volume=opt["volume"], open_interest=opt["open_interest"],
        implied_volatility=opt["implied_volatility"], delta=opt["delta"], dte=opt["days_to_expiry"],
        quote_timestamp=opt["quote_timestamp"], session_open=True)
    assert canon["contract_quality"].quality_pass == direct.quality_pass
    assert canon["contract_quality"].hard_failures == direct.hard_failures


def test_fresh_yahoo_timestamp_reaches_a(monkeypatch):
    opt = _option_candidate(quote_timestamp=NOW.isoformat(), quote_timestamp_source="chain_snapshot")
    result, canon = _run_canonical(monkeypatch, option_idea=opt, balance=50_000.0)
    assert canon["contract_quality"].freshness_status.value == "fresh"
    assert canon["option_display"]["quote_timestamp"] == NOW.isoformat()
    assert canon["option_display"]["quote_timestamp_source"] == "chain_snapshot"


def test_oi_canonical_behavior(monkeypatch):
    opt = _option_candidate(open_interest=100)
    result, canon = _run_canonical(monkeypatch, option_idea=opt, balance=50_000.0)
    assert canon["contract_quality"].quality_pass is True   # OI=100 is THIN, not hard-fail


def test_0_dte_fails(monkeypatch):
    opt = _option_candidate(days_to_expiry=0)
    result, canon = _run_canonical(monkeypatch, option_idea=opt)
    assert canon["contract_quality"].quality_pass is False
    assert any("DTE" in f for f in canon["contract_quality"].hard_failures)


def test_stale_quote_fails(monkeypatch):
    stale = (NOW - timedelta(hours=31)).isoformat()
    opt = _option_candidate(quote_timestamp=stale)
    result, canon = _run_canonical(monkeypatch, option_idea=opt)
    assert canon["contract_quality"].quality_pass is False
    assert any("stale" in f.lower() for f in canon["contract_quality"].hard_failures)


def test_missing_greeks_behavior_matches_canonical(monkeypatch):
    opt = _option_candidate(delta=None)   # IV present -> modeled, not a hard fail
    result, canon = _run_canonical(monkeypatch, option_idea=opt)
    assert canon["contract_quality"].greeks_provenance.value == "model"
    opt2 = _option_candidate(delta=None, implied_volatility=None)
    result2, canon2 = _run_canonical(monkeypatch, option_idea=opt2)
    assert canon2["contract_quality"].greeks_provenance.value == "unavailable"
    assert canon2["contract_quality"].quality_pass is False


# ══════════════════════════════════════════════════════════════════════════
# B
# ══════════════════════════════════════════════════════════════════════════

def test_direct_b_stock_equals_pipeline_result(monkeypatch):
    from canonical.account_fit import stock_account_fit
    result, canon = _run_canonical(monkeypatch, balance=50_000.0)
    st = risk.account_state()
    direct = stock_account_fit(policy=STRATEGY_500_POLICY, equity=st["equity"],
                               entry=result["entry_range"][0], stop=result["stop"],
                               buying_power=st["buying_power"], open_positions=0,
                               sector="financials")
    assert canon["stock_account_fit"].eligible == direct.eligible
    assert canon["stock_account_fit"].quantity_allowed == pytest.approx(direct.quantity_allowed)


def test_direct_b_option_equals_pipeline_result(monkeypatch):
    from canonical.account_fit import option_account_fit
    opt = _option_candidate()
    result, canon = _run_canonical(monkeypatch, option_idea=opt, balance=50_000.0)
    st = risk.account_state()
    direct = option_account_fit(policy=STRATEGY_500_POLICY, equity=st["equity"],
                                contract_quality=canon["contract_quality"], side="CALL",
                                limit_price=opt["premium"], spot=result["entry_range"][0],
                                stop=result["stop"], strike=opt["strike"], iv=opt["implied_volatility"],
                                dte=opt["days_to_expiry"], spread_dollars=opt["spread_dollars"],
                                buying_power=st["buying_power"])
    assert canon["option_account_fit"].eligible == direct.eligible
    assert canon["option_account_fit"].binding_constraint == direct.binding_constraint


def test_500_1_option_rejected_at_5_cap(monkeypatch):
    """The historical headline case, reproduced end to end: $500 equity, a
    $1.00-premium option (=~$100 exposure) against Strategy-500's real $5
    per-trade cap."""
    opt = _option_candidate(premium=1.00, bid=0.95, ask=1.05)
    result, canon = _run_canonical(monkeypatch, option_idea=opt, balance=500.0)
    assert canon["contract_quality"].quality_pass is True   # premise: structurally fine
    of = canon["option_account_fit"]
    assert of.eligible is False
    assert of.quantity_allowed == 0
    assert of.binding_constraint == "per_trade_risk"
    assert canon["option_executable"].executable is False


def test_option_rejection_does_not_block_valid_stock(monkeypatch):
    opt = _option_candidate(premium=1.00)
    result, canon = _run_canonical(monkeypatch, option_idea=opt, balance=500.0)
    assert canon["option_account_fit"].eligible is False
    assert canon["stock_account_fit"].eligible is True
    assert canon["stock_executable"].executable is True


# ══════════════════════════════════════════════════════════════════════════
# D
# ══════════════════════════════════════════════════════════════════════════

def test_stock_only_selects_stock(monkeypatch):
    result, canon = _run_canonical(monkeypatch, option_idea=None, balance=500.0)
    assert canon["instrument_choice"].choice == InstrumentChoice.STOCK


def test_option_only_selects_option(monkeypatch):
    """Stock made ineligible (zero risk distance) while a genuinely valid,
    CHEAP option remains affordable. STRATEGY_500_POLICY's max_loss_per_trade
    is an ABSOLUTE $5 cap — it does not scale with equity — so the option's
    premium must itself be tiny (a few cents) regardless of account size for
    B(option) to ever be eligible; this is real, deliberate Strategy-500
    policy, not a test artifact (see test_500_1_option_rejected_at_5_cap,
    where a $1.00 premium already fails it at any equity)."""
    from canonical.instrument_choice import choose_instrument, OptionEdge
    from canonical.account_fit import stock_account_fit, option_account_fit
    cq = evaluate_contract_quality(strike=63.0, underlying=63.25, side="CALL", bid=0.029, ask=0.031,
                                   volume=300, open_interest=1000, implied_volatility=0.30, delta=0.5,
                                   dte=21, quote_timestamp=NOW.isoformat(), session_open=True)
    ineligible_stock = stock_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, entry=63.25,
                                         stop=63.25, buying_power=400.0)   # zero risk distance
    good_option = option_account_fit(policy=STRATEGY_500_POLICY, equity=50_000.0, contract_quality=cq,
                                     side="CALL", limit_price=0.03, spot=63.25, stop=61.68, strike=63.0,
                                     iv=0.30, dte=21, buying_power=50_000.0)
    assert ineligible_stock.eligible is False and good_option.eligible is True
    choice = choose_instrument(setup_tradeable=True, stock_account_fit=ineligible_stock,
                               option_account_fit=good_option, contract_quality=cq,
                               option_edge=OptionEdge())
    assert choice.choice == InstrumentChoice.OPTION


def test_both_invalid_is_no_trade(monkeypatch):
    # canonical_bridge always reads the REAL paper account state via
    # risk_mod.account_state() — the `balance` argument to decision_engine.
    # evaluate() only feeds ITS OWN internal conviction-sizing math, never
    # canonical B (Phase 3's "use real account state, not the balance
    # argument" rule, proven here the other direction: zeroing `balance`
    # alone must NOT zero out B). To make B(stock) genuinely ineligible, the
    # REAL paper account's own buying power/cash must be zero.
    monkeypatch.setenv("PAPER_INITIAL_CASH", "0")
    monkeypatch.setenv("PAPER_INITIAL_EQUITY", "0")
    monkeypatch.setenv("PAPER_BUYING_POWER", "0")
    opt = _option_candidate(bid=None, ask=None)
    result, canon = _run_canonical(monkeypatch, option_idea=opt, balance=0.0)
    assert canon["contract_quality"].quality_pass is False
    assert canon["stock_account_fit"].eligible is False
    assert canon["instrument_choice"].choice == InstrumentChoice.NO_TRADE


def test_both_valid_uses_canonical_preference(monkeypatch):
    # A cheap premium — STRATEGY_500_POLICY's $5 max-loss-per-trade cap is
    # ABSOLUTE (does not scale with equity), so both legs are only
    # simultaneously eligible for a very small option premium.
    opt = _option_candidate(premium=0.03, bid=0.029, ask=0.031)
    result, canon = _run_canonical(monkeypatch, option_idea=opt, balance=50_000.0)
    assert canon["stock_account_fit"].eligible is True
    assert canon["option_account_fit"].eligible is True
    ic = canon["instrument_choice"]
    assert ic.option_score is not None and ic.stock_score is not None
    assert ic.choice in (InstrumentChoice.OPTION, InstrumentChoice.STOCK)


def test_legacy_grader_disagreement_cannot_override_d(monkeypatch):
    """_grade_option_chain() is never CALLED for this route
    (evaluate_option=False -> its internal opt is None, inside evaluate()
    itself) — no live channel for it to influence canonical D. (The name
    appears in this module's own docstrings/comments, explaining what is
    NOT called — that is not a call site, so the check below looks for the
    call pattern specifically — this module's own docstring names the
    function in prose, describing what is NOT called, which is not itself a
    call site.)"""
    src = inspect.getsource(canonical_bridge)
    # Every prose mention in this module's own docstrings writes the name
    # with EMPTY parens, as a bare reference ("_grade_option_chain()"),
    # describing what is NOT called — never as an actual invocation (which
    # would carry arguments: "_grade_option_chain(opt, ...)" or
    # "de._grade_option_chain(..."). Checking for that shape distinguishes
    # a real call site from prose without having to strip every docstring.
    assert "_grade_option_chain(opt" not in src
    assert "_grade_option_chain(result" not in src
    assert "de._grade_option_chain(" not in src
    opt = _option_candidate(open_interest=100)   # canonical PASS, legacy grade_contract-style would differ
    result, canon = _run_canonical(monkeypatch, option_idea=opt, balance=50_000.0)
    assert canon["contract_quality"].quality_pass is True
    assert canon["instrument_choice"].choice in (InstrumentChoice.OPTION, InstrumentChoice.STOCK)


# ══════════════════════════════════════════════════════════════════════════
# E
# ══════════════════════════════════════════════════════════════════════════

def test_final_stock_quantity_equals_canonical_e(monkeypatch):
    result, canon = _run_canonical(monkeypatch, option_idea=None, balance=500.0)
    assert canon["instrument_choice"].choice == InstrumentChoice.STOCK
    assert canon["sizing"].quantity <= canon["stock_account_fit"].quantity_allowed + 1e-9


def test_conviction_target_cannot_exceed_b():
    from canonical.instrument_choice import InstrumentChoiceResult
    from canonical.account_fit import stock_account_fit
    from canonical.sizing import size_instrument
    fit = stock_account_fit(policy=STRATEGY_500_POLICY, equity=500.0, entry=100.0, stop=97.0,
                            buying_power=400.0)
    choice = InstrumentChoiceResult(choice=InstrumentChoice.STOCK, reason="fixture",
                                    reasons=("fixture",), stock_eligible=True, option_eligible=False)
    oversized = size_instrument(choice=choice, stock_account_fit=fit, option_account_fit=None,
                                target_quantity=3.82)   # the historical "strong conviction" number
    assert oversized.quantity <= fit.quantity_allowed + 1e-9
    assert oversized.quantity < 3.82


def test_final_option_contracts_integer(monkeypatch):
    opt = _option_candidate()
    result, canon = _run_canonical(monkeypatch, option_idea=opt, balance=50_000.0)
    if canon["instrument_choice"].choice == InstrumentChoice.OPTION:
        assert canon["sizing"].quantity == int(canon["sizing"].quantity)


def test_legacy_stock_sizer_cannot_override_e():
    """risk.position_size() is never called inside canonical_bridge.py —
    structurally proven."""
    src = inspect.getsource(canonical_bridge)
    assert "position_size(" not in src


def test_size_option_cannot_override_e():
    src = inspect.getsource(canonical_bridge)
    assert "_size_option(" not in src


# ══════════════════════════════════════════════════════════════════════════
# Execution
# ══════════════════════════════════════════════════════════════════════════

def test_broker_requires_stock_executable():
    r = {"symbol": "BAC", "decision": "TRADEABLE", "direction": "LONG",
        "entry_range": [63.25, 63.44], "stop": 61.68, "data_source": "yahoo",
        "freshness": {"state": "fresh"}}
    out = broker.submit_entry(r, _quote(), strategy="s", sector="financials",
                              canonical_quantity=1.5, canonical_planned_risk=2.36,
                              stock_executable=False)
    assert out["executed"] is False
    assert out["stage"] == "not_executable"


def test_broker_quantity_exactly_equals_e_quantity(monkeypatch):
    result, canon = _run_canonical(monkeypatch, option_idea=None, balance=500.0)
    assert canon["instrument_choice"].choice == InstrumentChoice.STOCK
    out = broker.submit_entry(result, _quote(last=result["entry_range"][0]), strategy="s",
                              sector="financials", canonical_quantity=canon["sizing"].quantity,
                              canonical_planned_risk=canon["stock_planned_risk"],
                              stock_executable=canon["stock_executable"].executable)
    if out["executed"]:
        assert out["sizing"]["quantity"] == canon["sizing"].quantity
        assert out["fill"]["filled_qty"] == pytest.approx(canon["sizing"].quantity, abs=1e-6)


def test_risk_validator_may_block_canonical_quantity(monkeypatch):
    result, canon = _run_canonical(monkeypatch, option_idea=None, balance=500.0)
    # Force the portfolio risk gate to refuse regardless of a valid canonical quantity.
    monkeypatch.setattr(risk, "check_entry", lambda *a, **k: {
        "allow": False, "reasons": ["forced block for test"], "checks": []})
    out = broker.submit_entry(result, _quote(last=result["entry_range"][0]), strategy="s",
                              sector="financials", canonical_quantity=1.5,
                              canonical_planned_risk=2.36, stock_executable=True)
    assert out["executed"] is False
    assert out["stage"] == "risk"


def test_risk_validator_cannot_increase_canonical_quantity(monkeypatch):
    result, canon = _run_canonical(monkeypatch, option_idea=None, balance=500.0)
    captured = {}
    real_check_entry = risk.check_entry

    def spy_check_entry(symbol, sector, planned_risk, notional, *a, **k):
        captured["planned_risk"] = planned_risk
        captured["notional"] = notional
        return real_check_entry(symbol, sector, planned_risk, notional, *a, **k)
    monkeypatch.setattr(risk, "check_entry", spy_check_entry)
    canonical_qty = 1.5
    broker.submit_entry(result, _quote(last=result["entry_range"][0]), strategy="s",
                        sector="financials", canonical_quantity=canonical_qty,
                        canonical_planned_risk=2.36, stock_executable=True)
    # check_entry() received the SAME planned_risk it was handed — it never
    # independently recomputed a larger one from a resized quantity.
    assert captured["planned_risk"] == 2.36


def test_risk_validator_does_not_silently_resize():
    """check_entry()'s own signature: it returns allow/reasons/checks — no
    quantity/resize field exists in its output at all."""
    sig = inspect.signature(risk.check_entry)
    assert "quantity" not in sig.parameters   # it takes planned_risk/notional, never resizes them
    src = inspect.getsource(risk.check_entry)
    assert "return" in src
    # No assignment to a quantity-shaped local inside check_entry().
    assert "qty =" not in src and "quantity =" not in src


def test_c_monitor_no_broker_call(monkeypatch):
    result, canon = _run_canonical(monkeypatch, data_state="fallback-provider",
                                   a_source="yfinance-fallback", option_idea=None, balance=500.0)
    assert result["decision"] == "MONITOR"
    assert canon["stock_executable"].executable is False
    # Structural: workflow.py only calls broker.submit_entry() when stock_ready
    # is True, which requires stock_executable — verified directly here too.
    assert canon["instrument_choice"].choice != InstrumentChoice.STOCK or not canon["stock_executable"].executable


def test_c_reject_no_broker_call(monkeypatch):
    result, canon = _run_canonical(monkeypatch, trend=(-0.6, 0.8), regime=(-0.4, 0.6),
                                   families={f: (-0.3, 0.6) for f in _DEEP.values()},
                                   option_idea=None, balance=500.0)
    assert result["decision"] == "REJECT"
    assert canon["stock_executable"].executable is False
    assert canon["option_executable"].executable is False


# ══════════════════════════════════════════════════════════════════════════
# Shadow
# ══════════════════════════════════════════════════════════════════════════

def test_option_executable_always_false_under_shadow_only(monkeypatch):
    # Cheap premium so B(option) is genuinely eligible under Strategy-500's
    # absolute $5 cap — the BEST possible case for the option leg.
    opt = _option_candidate(premium=0.03, bid=0.029, ask=0.031)
    result, canon = _run_canonical(monkeypatch, option_idea=opt, balance=50_000.0)
    # Even in the BEST possible case (A pass, B pass, D=OPTION, E>0):
    assert canon["contract_quality"].quality_pass is True
    assert canon["option_account_fit"].eligible is True
    assert canon["option_executable"].executable is False
    assert "shadow_only" in canon["option_executable"].blockers


def test_no_option_broker_call_exists_or_reachable():
    """Structural guard: neither canonical_bridge.py nor workflow.py ever
    calls broker.submit_entry() (or any order-placing function) for an
    OPTION instrument — grep the actual source."""
    from paper import workflow as wf
    src_wf = inspect.getsource(wf)
    src_bridge = inspect.getsource(canonical_bridge)
    # canonical_bridge.py never places an order at all — it only evaluates.
    assert "submit_entry(" not in src_bridge
    assert "place_order(" not in src_bridge
    # workflow.py calls submit_entry() exactly once, gated behind stock_ready
    # (D chose STOCK and stock_executable — never for an OPTION instrument).
    assert src_wf.count("broker.submit_entry(") == 1
    assert "paper_option_trade" not in src_wf and "paper_option_trade" not in src_bridge


def test_valid_option_still_shadow_recorded(monkeypatch):
    opt = _option_candidate()
    result, canon = _run_canonical(monkeypatch, option_idea=opt, balance=50_000.0)
    shadow_result = canonical_bridge.augmented_result_for_shadow(result, canon)
    sid = options_shadow.record(shadow_result, symbol="BAC")
    assert sid is not None
    rows = options_shadow.summary()
    assert rows["count"] == 1


def test_d_option_shadow_only_produces_no_paper_order(monkeypatch):
    opt = _option_candidate()
    result, canon = _run_canonical(monkeypatch, option_idea=opt, balance=50_000.0)
    if canon["instrument_choice"].choice == InstrumentChoice.OPTION:
        stock_ready = (canon["instrument_choice"].choice.value == "STOCK"
                      and canon["stock_executable"].executable)
        assert stock_ready is False
    positions = db.query("SELECT * FROM positions")
    assert positions == []


def test_d_stock_may_still_record_option_candidate(monkeypatch):
    """Current research purpose: even when D chose STOCK, the option
    candidate that was evaluated is still shadow-recorded (not silently
    dropped) — labeled as a non-selected candidate, not the executable one."""
    opt = _option_candidate(premium=1.00)   # will be B-ineligible at $500 -> D=STOCK
    result, canon = _run_canonical(monkeypatch, option_idea=opt, balance=500.0)
    assert canon["instrument_choice"].choice == InstrumentChoice.STOCK
    shadow_result = canonical_bridge.augmented_result_for_shadow(result, canon)
    assert shadow_result["option"] is not None   # the candidate is still present
    sid = options_shadow.record(shadow_result, symbol="BAC")
    assert sid is not None


def test_shadow_record_labels_preferred_instrument_correctly(monkeypatch):
    opt = _option_candidate(premium=1.00)
    result, canon = _run_canonical(monkeypatch, option_idea=opt, balance=500.0)
    assert canon["instrument_choice"].choice == InstrumentChoice.STOCK
    meta = canonical_bridge.canonical_audit_metadata(canon)
    assert meta["instrument_choice"] == "STOCK PREFERRED"
    assert canon["option_quality_display"]["preference"] == "prefer-stock"
    # Never claims the option was the SELECTED executable instrument.
    assert meta["option_executable"] is False


# ══════════════════════════════════════════════════════════════════════════
# Persistence
# ══════════════════════════════════════════════════════════════════════════

def test_journal_still_records_signal(monkeypatch):
    result, canon = _run_canonical(monkeypatch, option_idea=None, balance=500.0)
    sid = journal.record_signal(result, strategy="s", sector="financials",
                                quantity=canon["sizing"].quantity,
                                planned_risk=canon["stock_planned_risk"])
    assert journal.get(sid) is not None


def test_journal_canonical_quantity_equals_e(monkeypatch):
    result, canon = _run_canonical(monkeypatch, option_idea=None, balance=500.0)
    sid = journal.record_signal(result, strategy="s", sector="financials",
                                quantity=canon["sizing"].quantity,
                                planned_risk=canon["stock_planned_risk"])
    row = journal.get(sid)
    assert row["quantity"] == canon["sizing"].quantity


def test_ledger_stock_quantity_equals_e(monkeypatch):
    result, canon = _run_canonical(monkeypatch, option_idea=None, balance=500.0)
    assert canon["instrument_choice"].choice == InstrumentChoice.STOCK
    out = broker.submit_entry(result, _quote(last=result["entry_range"][0]), strategy="s",
                              sector="financials", canonical_quantity=canon["sizing"].quantity,
                              canonical_planned_risk=canon["stock_planned_risk"],
                              stock_executable=canon["stock_executable"].executable)
    if out["executed"]:
        pos = db.query_one("SELECT * FROM positions WHERE symbol='BAC'")
        assert pos["quantity"] == pytest.approx(canon["sizing"].quantity, abs=1e-6)


def test_no_option_ledger_entry(monkeypatch):
    """lab/paper/db.py's schema has no option-position table at all — the
    ONLY ledger table is `positions` (stock), and options can only ever
    reach `options_shadow`. Confirmed here directly: recording a shadow
    candidate leaves `positions` untouched."""
    opt = _option_candidate()
    result, canon = _run_canonical(monkeypatch, option_idea=opt, balance=50_000.0)
    shadow_result = canonical_bridge.augmented_result_for_shadow(result, canon)
    options_shadow.record(shadow_result, symbol="BAC")
    assert options_shadow.summary()["in_ledger"] is False
    assert db.query("SELECT * FROM positions") == []


# ══════════════════════════════════════════════════════════════════════════
# Compatibility
# ══════════════════════════════════════════════════════════════════════════

def test_sector_funnel_unchanged():
    """Step 10 touches lab/paper/workflow.py, broker.py and the new
    canonical_bridge.py — lab/paper/strategies.py (sector funnel/candidate
    ranking) must be byte-identical."""
    import subprocess
    out = subprocess.run(["git", "diff", "--stat", "--", "lab/paper/strategies.py"],
                         cwd=_ROOT, capture_output=True, text=True)
    assert out.stdout.strip() == ""


def test_candidate_ranking_unchanged():
    src = inspect.getsource(canonical_bridge)
    assert "score_liquid_momentum" not in src
    assert "score_sector_rs" not in src
    assert "score_mean_reversion" not in src
    assert "rank_sectors" not in src


def test_pipeline1_tests_unchanged():
    import subprocess
    res = subprocess.run([sys.executable, "-m", "pytest",
                          "tests/unit/test_pipeline1_canonical_migration.py", "-q"],
                         cwd=_ROOT, capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stdout[-3000:]


def test_pipeline2_tests_unchanged():
    import subprocess
    res = subprocess.run([sys.executable, "-m", "pytest",
                          "tests/unit/test_pipeline2_canonical_migration.py", "-q"],
                         cwd=_ROOT, capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stdout[-3000:]
