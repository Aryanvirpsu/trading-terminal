"""tools/tradingview_companion/generate_overlay_values.py: display-only, reads the real `signals` table
only, writes nothing. Covers: fetching by signal_id, fetching the latest TRADEABLE signal for a symbol
(optionally narrowed by session_date), and that a field AVDI never recorded is reported as None rather than
a silently-inferred placeholder."""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in ("src", "dashboard", "lab"):
    sys.path.insert(0, os.path.join(_ROOT, _p))
sys.path.insert(0, os.path.join(_ROOT, "tools", "tradingview_companion"))

from paper import db  # noqa: E402

from generate_overlay_values import fetch_signal, format_for_pine_inputs, overlay_values  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="papertest_tvc_")
    monkeypatch.setenv("PAPER_DATA_DIR", tmp)
    monkeypatch.setenv("PAPER_LEDGER", "robinhood_500_baseline")
    db.reset_for_tests(tmp)
    yield
    db.close()


def _insert_signal(signal_id, symbol, session_date="2026-10-01", action="TRADEABLE", entry=100.0,
                   stop=95.0, target=110.0, scanner_rank=2, strategy="sector_relative_strength",
                   planned_risk=5.0, market_regime="bullish", created_at=None):
    db.execute(
        "INSERT INTO signals(signal_id, created_at, session_date, symbol, strategy, action, entry, stop, "
        "target, scanner_rank, planned_risk, market_regime) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (signal_id, created_at or db.utcnow(), session_date, symbol, strategy, action, entry, stop, target,
         scanner_rank, planned_risk, market_regime))


def test_fetch_by_signal_id_returns_the_exact_row():
    _insert_signal("sig_1", "NVDA")
    sig = fetch_signal(symbol=None, signal_id="sig_1", session_date=None)
    assert sig["symbol"] == "NVDA"
    assert sig["entry"] == pytest.approx(100.0)


def test_fetch_latest_tradeable_for_symbol_picks_the_most_recent():
    _insert_signal("sig_old", "NVDA", created_at="2026-10-01T13:35:00+00:00", entry=90.0)
    _insert_signal("sig_new", "NVDA", created_at="2026-10-01T14:35:00+00:00", entry=95.0)
    sig = fetch_signal(symbol="NVDA", signal_id=None, session_date=None)
    assert sig["signal_id"] == "sig_new"
    assert sig["entry"] == pytest.approx(95.0)


def test_fetch_ignores_non_tradeable_actions():
    _insert_signal("sig_reject", "NVDA", action="REJECT", entry=None, stop=None, target=None)
    sig = fetch_signal(symbol="NVDA", signal_id=None, session_date=None)
    assert sig is None


def test_fetch_narrowed_by_session_date():
    _insert_signal("sig_day1", "NVDA", session_date="2026-09-30")
    _insert_signal("sig_day2", "NVDA", session_date="2026-10-01")
    sig = fetch_signal(symbol="NVDA", signal_id=None, session_date="2026-09-30")
    assert sig["signal_id"] == "sig_day1"


def test_overlay_values_reads_the_six_real_fields_directly():
    _insert_signal("sig_1", "NVDA", entry=100.0, stop=95.0, target=110.0, scanner_rank=3,
                   strategy="momentum", planned_risk=4.5, market_regime="choppy")
    sig = fetch_signal(symbol="NVDA", signal_id=None, session_date=None)
    v = overlay_values(sig)
    assert v == {"entry_price": 100.0, "stop_price": 95.0, "target_price": 110.0, "scanner_rank": 3,
               "strategy_name": "momentum", "risk_amount": 4.5, "regime_label": "choppy"}


def test_missing_field_is_none_not_a_silent_placeholder():
    """A field AVDI never recorded (e.g. scanner_rank wasn't captured for this signal) must come back as
    None, never a plausible-looking fabricated value."""
    db.execute(
        "INSERT INTO signals(signal_id, created_at, session_date, symbol, strategy, action, entry, stop, "
        "target) VALUES (?,?,?,?,?,?,?,?,?)",
        ("sig_partial", db.utcnow(), "2026-10-01", "NVDA", "momentum", "TRADEABLE", 100.0, 95.0, 110.0))
    sig = fetch_signal(symbol="NVDA", signal_id=None, session_date=None)
    v = overlay_values(sig)
    assert v["scanner_rank"] is None
    assert v["risk_amount"] is None
    assert v["regime_label"] is None
    assert v["entry_price"] == pytest.approx(100.0)       # fields that WERE recorded still come through


def test_format_for_pine_inputs_marks_missing_fields_explicitly():
    values = {"entry_price": 100.0, "stop_price": 95.0, "target_price": 110.0, "scanner_rank": None,
             "strategy_name": "momentum", "risk_amount": None, "regime_label": None}
    out = format_for_pine_inputs(values)
    assert "100.0" in out
    assert "not recorded by AVDI" in out      # the missing-string fields say so explicitly
    assert "Scanner rank:            0" in out  # the missing NUMERIC field is 0 (Pine's own int input default), not invented
