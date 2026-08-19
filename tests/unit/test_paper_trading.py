"""Paper-trading tests — fill realism, risk controls, journal integrity, failure sims.

Fully offline and hermetic: every test gets a throwaway SQLite file, and no provider is
ever contacted. These exist to satisfy the completion criteria, which explicitly say
"do not claim completion because the application runs".

Run: pytest tests/unit/test_paper_trading.py -q
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in ("src", "dashboard", "lab"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

from paper import broker, config as cfg, db, fills, journal, options_shadow, risk  # noqa: E402
from paper.fills import Quote  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="papertest_")
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
    monkeypatch.setenv("PAPER_MAX_OPTION_PREMIUM", "75")
    db.reset_for_tests(tmp)
    yield
    db.close()


def _result(decision="TRADEABLE", *, gates_ok=True, fresh="fresh", ev=0.8,
            entry=40.0, symbol="AAPL"):
    gates = [{"name": n, "passed": gates_ok, "blocking": True,
              "reason": "" if gates_ok else "failed"}
             for n in ("quality_threshold", "positive_ev", "liquidity")]
    return {"symbol": symbol, "decision": decision, "direction": "LONG", "price": entry,
            "entry_range": [entry, entry * 1.003], "stop": entry * 0.97,
            "target": entry * 1.06, "suggested_shares": 10,
            "decision_gates": gates,
            "failed_gates": [] if gates_ok else ["quality_threshold"],
            "freshness": {"state": fresh, "label": fresh},
            "data_source": "yahoo", "data_state": "fresh",
            "ev_breakdown": {"ev_per_share": ev, "expected_r": 0.3},
            "confidence_quality": 70, "quality_threshold": 45}


def _quote(symbol="AAPL", last=40.0, **kw):
    import time
    d = dict(bid=last - 0.05, ask=last + 0.05, open=last, high=last * 1.01,
             low=last * 0.99, volume=5_000_000, source_ts=time.time(), provider="yahoo")
    d.update(kw)
    return Quote(symbol, last=last, **d)


# ── Fill realism ─────────────────────────────────────────────────────────────

def test_market_buy_above_ask_sell_below_bid_never_midpoint():
    q = Quote("X", last=100.0, bid=99.95, ask=100.05, volume=1e6)
    b = fills.simulate_market("BUY", 10, q)
    s = fills.simulate_market("SELL", 10, q)
    assert b.price > q.ask and s.price < q.bid
    assert b.price > 100.0 > s.price          # the midpoint is never the fill


def test_limit_touch_does_not_fill_trade_through_does():
    q = Quote("X", last=100, bid=99.9, ask=100.1, open=100, high=101, low=99.0, volume=1e6)
    assert fills.simulate_limit("BUY", 10, 99.0, q).status == "pending"   # touch only
    assert fills.simulate_limit("BUY", 10, 99.5, q).status == "filled"    # traded through


def test_stop_gap_fills_worse_than_stop():
    gap = Quote("X", last=90, bid=89.9, ask=90.1, open=92.0, high=93, low=89.0, volume=1e6)
    r = fills.simulate_stop("SELL", 10, 95.0, gap)
    assert r.status == "filled" and r.liquidity == "gap"
    assert r.price < 95.0                      # a stop is a trigger, not a guarantee


def test_stop_normal_trigger_is_not_a_gap():
    q = Quote("X", last=94, bid=93.9, ask=94.1, open=97.0, high=98, low=94.0, volume=1e6)
    r = fills.simulate_stop("SELL", 10, 95.0, q)
    assert r.status == "filled" and r.liquidity == "normal"


def test_partial_fill_when_order_large_vs_volume():
    thin = Quote("X", last=10, bid=9.99, ask=10.01, open=10, high=10.5, low=9.5, volume=1000)
    r = fills.simulate_market("BUY", 500, thin)
    assert r.status == "partial" and r.quantity < 500


def test_rejected_orders():
    q = Quote("X", last=100, bid=99.9, ask=100.1, volume=1e6)
    assert fills.simulate_market("BUY", 0, q).status == "rejected"
    assert fills.simulate_market("BUY", 10, Quote("X")).status == "rejected"
    assert fills.simulate_market("BUY", 10, Quote("X", bid=101, ask=99)).status == "rejected"


def test_fees_are_applied(monkeypatch):
    monkeypatch.setenv("PAPER_FEE_PER_SHARE", "0.01")
    q = Quote("X", last=100, bid=99.9, ask=100.1, volume=1e6)
    assert fills.simulate_market("BUY", 100, q).fees == pytest.approx(1.0, abs=1e-6)


def test_deterministic_ids():
    a = fills.order_id("AAPL", "BUY", "2026-07-30", 1, "s")
    assert a == fills.order_id("AAPL", "BUY", "2026-07-30", 1, "s")
    assert a != fills.order_id("AAPL", "BUY", "2026-07-30", 2, "s")


def test_corporate_actions():
    assert fills.apply_split(10, 100.0, 2.0) == (20.0, 50.0)
    assert fills.apply_cash_dividend(10, 0.25) == 2.5


# ── The entry gate: what must NEVER execute ──────────────────────────────────

@pytest.mark.parametrize("kwargs,expect", [
    ({"decision": "MONITOR"}, "only TRADEABLE"),
    ({"decision": "REJECT", "gates_ok": False}, "only TRADEABLE"),
    ({"gates_ok": False}, "gates failed"),
    ({"fresh": "stale"}, "not acceptable"),
    ({"fresh": "fallback"}, "not acceptable"),
    ({"ev": -0.5}, "not positive"),
    ({"ev": 0.0}, "not positive"),
])
def test_entry_refused(kwargs, expect):
    out = broker.submit_entry(_result(**kwargs), _quote(), strategy="liquid_momentum",
                              sector="technology")
    assert out["executed"] is False
    assert any(expect in r for r in out["reasons"]), out["reasons"]


def test_valid_tradeable_executes_and_fills_above_ask():
    out = broker.submit_entry(_result(), _quote(), strategy="liquid_momentum",
                              sector="technology")
    assert out["executed"] is True
    assert out["fill"]["fill"]["price"] > _quote().ask


def test_stale_source_timestamp_cannot_create_an_order():
    """Display-only data must never become a trade, even when everything else is fine."""
    import time
    q = _quote(source_ts=time.time() - 100000)     # far past the price freshness limit
    out = broker.submit_entry(_result(), q, strategy="liquid_momentum", sector="technology")
    assert out["executed"] is False
    assert any("display-only" in r or "age" in r for r in out["reasons"]), out["reasons"]


def test_missing_provenance_blocks():
    r = _result()
    r["data_source"] = None
    out = broker.submit_entry(r, _quote(), strategy="liquid_momentum", sector="technology")
    assert out["executed"] is False
    assert any("provenance" in x for x in out["reasons"])


def test_invalid_levels_block():
    r = _result()
    r["stop"] = 200.0                      # stop above target for a long
    out = broker.submit_entry(r, _quote(), strategy="liquid_momentum", sector="technology")
    assert out["executed"] is False


# ── Risk controls ────────────────────────────────────────────────────────────

def test_max_open_positions_blocks(monkeypatch):
    monkeypatch.setenv("PAPER_MAX_OPEN", "1")
    monkeypatch.setenv("PAPER_MAX_PER_SECTOR", "9")
    monkeypatch.setenv("PAPER_MAX_ENTRIES_PER_DAY", "9")
    a = broker.submit_entry(_result(symbol="AAPL"), _quote("AAPL"),
                            strategy="s", sector="technology")
    assert a["executed"]
    b = broker.submit_entry(_result(symbol="MSFT"), _quote("MSFT"),
                            strategy="s", sector="technology")
    assert b["executed"] is False and b["stage"] == "risk"
    assert any("open positions" in r for r in b["reasons"])


def test_one_position_per_sector_blocks(monkeypatch):
    monkeypatch.setenv("PAPER_MAX_PER_SECTOR", "1")
    monkeypatch.setenv("PAPER_MAX_OPEN", "9")
    monkeypatch.setenv("PAPER_MAX_ENTRIES_PER_DAY", "9")
    broker.submit_entry(_result(symbol="AAPL"), _quote("AAPL"), strategy="s", sector="technology")
    b = broker.submit_entry(_result(symbol="MSFT"), _quote("MSFT"), strategy="s", sector="technology")
    assert b["executed"] is False
    assert any("technology" in r for r in b["reasons"])


def test_max_entries_per_day_blocks(monkeypatch):
    monkeypatch.setenv("PAPER_MAX_ENTRIES_PER_DAY", "1")
    monkeypatch.setenv("PAPER_MAX_OPEN", "9")
    monkeypatch.setenv("PAPER_MAX_PER_SECTOR", "9")
    broker.submit_entry(_result(symbol="AAPL"), _quote("AAPL"), strategy="s", sector="technology")
    b = broker.submit_entry(_result(symbol="XOM"), _quote("XOM"), strategy="s", sector="energy")
    assert b["executed"] is False
    assert any("entries today" in r for r in b["reasons"])


def test_per_trade_risk_cap_enforced():
    st = risk.account_state()
    sizing = risk.position_size(st["equity"], 40.0, 38.8, buying_power=st["buying_power"])
    assert sizing["planned_risk"] <= 5.0 + 1e-9      # absolute $5 max loss


def test_risk_block_records_exact_reason(monkeypatch):
    monkeypatch.setenv("PAPER_MAX_OPEN", "0")
    out = broker.submit_entry(_result(), _quote(), strategy="s", sector="technology")
    assert out["executed"] is False and out["stage"] == "risk"
    assert out["reasons"] and all(isinstance(r, str) and r for r in out["reasons"])


def test_correlated_position_cap(monkeypatch):
    monkeypatch.setenv("PAPER_MAX_CORRELATED", "1")
    monkeypatch.setenv("PAPER_MAX_PER_SECTOR", "9")
    monkeypatch.setenv("PAPER_MAX_OPEN", "9")
    monkeypatch.setenv("PAPER_MAX_ENTRIES_PER_DAY", "9")
    broker.submit_entry(_result(symbol="NVDA"), _quote("NVDA"), strategy="s", sector="technology")
    b = broker.submit_entry(_result(symbol="AMD"), _quote("AMD"), strategy="s", sector="technology")
    assert b["executed"] is False
    assert any("correlation group" in r for r in b["reasons"])


# ── Journal + shadow tracking ────────────────────────────────────────────────

def test_every_decision_is_journalled_including_refusals():
    broker.submit_entry(_result(decision="MONITOR"), _quote(), strategy="s", sector="technology")
    broker.submit_entry(_result(decision="REJECT", gates_ok=False), _quote(), strategy="s", sector="technology")
    broker.submit_entry(_result(), _quote(), strategy="s", sector="technology")
    sigs = journal.signals()
    assert len(sigs) == 3
    assert {s["action"] for s in sigs} == {"MONITOR", "REJECT", "TRADEABLE"}
    assert sum(s["executed"] for s in sigs) == 1


def test_rejected_signals_are_tracked_forward():
    out = broker.submit_entry(_result(decision="REJECT", gates_ok=False), _quote(),
                              strategy="s", sector="technology")
    sid = out["signal_id"]
    journal.update_excursions(sid, high=42.6, low=39.6)   # would have hit the target
    s = journal.get(sid)
    assert s["outcome"] == "target_hit"
    assert s["mfe"] > 0
    bw = journal.blocked_winners()
    assert bw["count"] == 1                                # the gate's cost is measured


def test_stop_assumed_first_when_bar_hits_both():
    out = broker.submit_entry(_result(decision="MONITOR"), _quote(), strategy="s", sector="technology")
    journal.update_excursions(out["signal_id"], high=42.8, low=38.4)   # both touched
    assert journal.get(out["signal_id"])["outcome"] == "stop_hit"       # never flatter


def test_gate_outcomes_shape():
    broker.submit_entry(_result(), _quote(), strategy="s", sector="technology")
    rows = journal.gate_outcomes()
    assert rows and all("action" in r and "n" in r for r in rows)


# ── P&L reconciliation + position lifecycle ──────────────────────────────────

def test_pnl_reconciles_after_round_trip():
    out = broker.submit_entry(_result(), _quote(), strategy="s", sector="technology")
    assert out["executed"]
    exits = broker.manage_open_positions({"AAPL": _quote("AAPL", last=42.8, high=43.0, low=42.4)})
    assert exits["count"] == 1
    rec = broker.reconcile()
    assert rec["reconciled"], rec
    closed = db.query("SELECT * FROM positions WHERE status='closed'")
    assert len(closed) == 1 and closed[0]["realized_pnl"] > 0


def test_stop_exit_produces_a_loss():
    broker.submit_entry(_result(), _quote(), strategy="s", sector="technology")
    broker.manage_open_positions({"AAPL": _quote("AAPL", last=38.4, open=38.6, high=38.8, low=38.0)})
    closed = db.query("SELECT * FROM positions WHERE status='closed'")
    assert len(closed) == 1 and closed[0]["realized_pnl"] < 0
    assert broker.reconcile()["reconciled"]


def test_audit_trail_exists_for_every_order():
    out = broker.submit_entry(_result(), _quote(), strategy="s", sector="technology")
    trail = db.audit_trail(out["order"]["order_id"])
    assert [t["event"] for t in trail][0] == "created"
    assert any(t["event"].startswith("fill_") for t in trail)


def test_day_orders_expire():
    broker.place_order("AAPL", "BUY", 10, order_type="LIMIT", limit_price=1.0,
                       session_date="2026-01-01", intent="entry")
    assert broker.expire_day_orders("2026-06-01") == 1
    o = db.query("SELECT status FROM orders")[0]
    assert o["status"] == "expired"


# ── Failure simulations ──────────────────────────────────────────────────────

def test_provider_outage_no_quote_degrades_safely():
    out = broker.submit_entry(_result(), Quote("AAPL"), strategy="s", sector="technology")
    assert out["executed"] is False          # refuses rather than inventing a fill
    assert journal.signals()                 # but the signal is still recorded


def test_no_quote_during_management_does_not_crash():
    broker.submit_entry(_result(), _quote(), strategy="s", sector="technology")
    r = broker.manage_open_positions({})     # provider went dark
    assert r["count"] == 0
    assert broker.reconcile()["reconciled"]


def test_equity_snapshot_and_drawdown():
    broker.submit_entry(_result(), _quote(), strategy="s", sector="technology")
    st = broker.snapshot_equity("2026-07-30")
    assert st["equity"] > 0
    assert db.query("SELECT * FROM equity")[0]["session_date"] == "2026-07-30"


# ── Options shadow mode ──────────────────────────────────────────────────────

def test_options_never_enter_the_ledger():
    r = _result()
    r["option"] = {"contract": "AAPL 105C", "bid": 1.0, "ask": 1.1, "strike": 105,
                   "option_type": "CALL", "open_interest": 900, "volume": 120,
                   "days_to_expiry": 20, "greeks": {"delta": 0.4, "iv": 0.3}}
    r["option_quality"] = {"preference": "prefer-option", "gradeable": True, "missing": []}
    broker.submit_entry(r, _quote(), strategy="s", sector="technology")
    options_shadow.record(r, symbol="AAPL")
    s = options_shadow.summary()
    assert s["count"] == 1 and s["in_ledger"] is False
    # the paper ledger holds ONLY the stock position
    assert all("C" not in p["symbol"] for p in db.query("SELECT symbol FROM positions"))


def test_ungradeable_option_is_honest():
    out = options_shadow.evaluate_contract({"bid": 1.0}, underlying_price=100, stock_ok=True)
    assert out["gradeable"] is False and out["missing"]
    assert out["preference"] == "prefer-stock"       # a bad option never kills the stock


def test_conservative_fill_is_the_ask_not_the_mid():
    assert options_shadow.conservative_fill(1.0, 1.20) == 1.20


def test_options_graduation_blocked_until_evidence():
    assert options_shadow.graduation_readiness()["ready"] is False


# ── Config ───────────────────────────────────────────────────────────────────

def test_risk_config_defaults():
    r = cfg.risk()
    assert r.max_loss_per_trade == 5.0 and r.max_position_notional == 125.0
    assert r.max_entries_per_day == 2 and r.max_open_positions == 3
    assert r.max_positions_per_sector == 1 and r.min_cash_reserve == 100.0
    assert r.max_daily_loss == 10.0 and r.max_drawdown == 50.0


def test_only_three_strategies_active():
    assert set(cfg.enabled_strategies()) == {
        "liquid_momentum", "sector_relative_strength", "mean_reversion"}


def test_config_version_changes_with_config(monkeypatch):
    a = db.config_version()
    monkeypatch.setenv("PAPER_MAX_LOSS_PER_TRADE", "9")
    assert db.config_version() != a


# ── $500 real-account simulation ─────────────────────────────────────────────

def test_account_is_500_not_10k():
    a = cfg.account()
    assert a.initial_cash == 500.0 and a.initial_equity == 500.0
    assert a.margin_enabled is False and a.allow_shorting is False
    assert a.allow_naked_options is False
    assert a.ledger == "robinhood_500_baseline"
    assert risk.account_state()["equity"] == 500.0


def test_buying_power_excludes_the_cash_reserve():
    st = risk.account_state()
    assert st["buying_power"] == pytest.approx(500.0 - 100.0)   # reserve is not spendable
    assert st["margin_enabled"] is False


def test_expensive_stock_is_affordable_via_fractional_shares():
    """With fractional shares (Robinhood's real behaviour) a $500 account CAN buy a
    $900 stock — it buys a fraction. Share price is not the binding constraint."""
    out = broker.submit_entry(_result(entry=900.0, symbol="XPEN"),
                              _quote("XPEN", last=900.0), strategy="s", sector="technology")
    assert out["executed"] is True
    assert 0 < out["sizing"]["quantity"] < 1
    assert out["sizing"]["fractional"] is True


def test_expensive_stock_unaffordable_without_fractional(monkeypatch):
    """Whole-share-only is where an expensive name becomes genuinely unreachable."""
    monkeypatch.setenv("PAPER_FRACTIONAL_SHARES", "false")
    out = broker.submit_entry(_result(entry=900.0, symbol="XPEN"),
                              _quote("XPEN", last=900.0), strategy="s", sector="technology")
    assert out["executed"] is False
    assert out["stage"] == "affordability"
    assert "whole share" in out["reasons"][0]


def test_position_capped_at_125_notional():
    out = broker.submit_entry(_result(entry=40.0), _quote(last=40.0),
                              strategy="s", sector="technology")
    assert out["executed"] is True
    assert out["sizing"]["notional"] <= 125.0 + 1e-9
    assert out["realistic_cost"] <= 125.0 + 1e-9


def test_max_loss_per_trade_is_five_dollars():
    out = broker.submit_entry(_result(entry=40.0), _quote(last=40.0),
                              strategy="s", sector="technology")
    assert out["sizing"]["planned_risk"] <= 5.0 + 1e-9


def test_cash_reserve_is_never_breached():
    """Fill the account until the $100 reserve blocks the next entry."""
    import os as _os
    _os.environ["PAPER_MAX_ENTRIES_PER_DAY"] = "9"
    _os.environ["PAPER_MAX_PER_SECTOR"] = "9"
    _os.environ["PAPER_MAX_OPEN"] = "9"
    try:
        for sym in ("AAA", "BBB", "CCC", "DDD", "EEE"):
            broker.submit_entry(_result(entry=40.0, symbol=sym), _quote(sym, last=40.0),
                                strategy="s", sector="technology")
        st = risk.account_state()
        assert st["available_cash"] >= 100.0 - 1e-6      # reserve intact
        assert broker.reconcile()["reconciled"]
    finally:
        for k in ("PAPER_MAX_ENTRIES_PER_DAY", "PAPER_MAX_PER_SECTOR", "PAPER_MAX_OPEN"):
            _os.environ.pop(k, None)


def test_whole_share_only_rejects_unaffordable(monkeypatch):
    monkeypatch.setenv("PAPER_FRACTIONAL_SHARES", "false")
    st = risk.account_state()
    s = risk.position_size(st["equity"], 900.0, 870.0, buying_power=st["buying_power"])
    assert s["quantity"] == 0.0 and s["binding_constraint"] == "whole_share_minimum"


def test_fractional_shares_allow_expensive_names(monkeypatch):
    monkeypatch.setenv("PAPER_FRACTIONAL_SHARES", "true")
    st = risk.account_state()
    s = risk.position_size(st["equity"], 900.0, 870.0, buying_power=st["buying_power"])
    assert 0 < s["quantity"] < 1 and s["fractional"] is True


def test_shorting_is_blocked():
    rk = risk.check_entry("AAPL", "technology", 2.0, 50.0, realistic_cost=50.0, side="SELL")
    assert rk["allow"] is False
    assert any("shorting is disabled" in r for r in rk["reasons"])


def test_daily_loss_limit_is_ten_dollars():
    r = cfg.risk()
    assert r.max_daily_loss == 10.0
    rk = risk.check_entry("AAPL", "technology", 2.0, 50.0, realistic_cost=50.0)
    assert any(c["name"] == "daily_loss_limit" for c in rk["checks"])


# ── Options affordability on $500 ────────────────────────────────────────────

def test_option_over_premium_cap_is_rejected():
    a = options_shadow.affordability(1.20, buying_power=400.0)   # $120 > $75 cap
    assert a["affordable"] is False and "allocation" in a["reason"]


def test_option_within_cap_is_affordable():
    a = options_shadow.affordability(0.50, buying_power=400.0)   # $50 <= $75
    assert a["affordable"] is True and a["total_premium"] == 50.0
    assert a["max_loss"] == 50.0                                  # full premium at risk


def test_option_beyond_buying_power_rejected():
    a = options_shadow.affordability(0.60, buying_power=40.0)     # $60 > $40 BP
    assert a["affordable"] is False and "buying power" in a["reason"]


def test_no_affordable_option_is_a_valid_result():
    opt = {"bid": 1.50, "ask": 1.60, "open_interest": 900, "volume": 120,
           "days_to_expiry": 20, "strike": 45, "option_type": "CALL",
           "greeks": {"delta": 0.4, "iv": 0.3}}
    out = options_shadow.evaluate_contract(opt, underlying_price=40.0,
                                           stock_ok=True, buying_power=400.0)
    assert out["affordable"] is False
    assert out["preference"] == "prefer-stock"        # the stock is still fine
    assert "no affordable option" in out["reason"]


def test_short_and_multileg_options_not_simulated():
    assert options_shadow.evaluate_contract(
        {"position": "short"}, 40.0, stock_ok=True)["gradeable"] is False
    assert options_shadow.evaluate_contract(
        {"legs": [1, 2]}, 40.0, stock_ok=True)["gradeable"] is False


# ── Small-account metrics ────────────────────────────────────────────────────

def test_small_account_metrics_shape(monkeypatch):
    from paper import report
    broker.submit_entry(_result(entry=40.0), _quote(last=40.0), strategy="s", sector="technology")
    monkeypatch.setenv("PAPER_FRACTIONAL_SHARES", "false")   # make a name unreachable
    broker.submit_entry(_result(entry=900.0, symbol="XPEN"), _quote("XPEN", last=900.0),
                        strategy="s", sector="energy")
    m = report.small_account_metrics()
    assert m["starting_cash"] == 500.0
    assert m["rejected_insufficient_buying_power"] >= 1
    assert m["affordable_pct"] is not None
    assert m["avg_capital_required"] is not None
    assert m["cash_remaining"] >= 0
    assert "return_on_starting_equity_pct" in m


def test_ledger_is_named_and_demo_archivable():
    assert db.ledger_name() == "robinhood_500_baseline"
    assert "robinhood_500_baseline" in db.db_path()
    led = db.list_ledgers()
    assert led["active"] == "robinhood_500_baseline"
