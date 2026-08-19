"""Market clock + data-freshness integrity.

Regression cover for the 2026-08-07 report in which the terminal showed
`MARKET opens 67h 50m` at 14:07 ET on a Friday with the exchange OPEN, beside
`DATA stale (28h)` describing an overnight batch artifact as though it were the
live quote feed.

All OFFLINE and deterministic — the clock is fed explicit instants rather than `now`.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

from zoneinfo import ZoneInfo

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in ("src", "dashboard", "lab"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import freshness as FR          # noqa: E402
import market_regime as MR      # noqa: E402

ET = ZoneInfo("America/New_York")


def _et(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


# ══════════════════════ Market clock ══════════════════════

def test_the_reported_friday_afternoon_is_open_not_opens_in_67h():
    """The exact instant from the bug report: Fri 2026-08-07 14:07 ET."""
    s = MR.session_state(_et(2026, 8, 7, 14, 7))
    assert s["state"] == "open"
    assert s["weekday_et"] == "Friday"
    # a session that is OPEN must expose time-to-CLOSE, and the countdown to the next
    # open must not be what the header shows
    assert s["seconds_until_close"] > 0
    assert s["seconds_until_close"] <= 2 * 3600          # 14:07 -> 16:00
    assert s["next_session"]["seconds_until_open"] > 60 * 3600   # Monday, correctly far


def test_session_states_across_a_normal_trading_day():
    day = (2026, 8, 7)                                    # a Friday
    assert MR.session_state(_et(*day, 3, 0))["state"] == "closed"      # before pre-market
    assert MR.session_state(_et(*day, 6, 0))["state"] == "premarket"
    assert MR.session_state(_et(*day, 9, 29))["state"] == "premarket"
    assert MR.session_state(_et(*day, 9, 30))["state"] == "open"
    assert MR.session_state(_et(*day, 15, 59))["state"] == "open"
    assert MR.session_state(_et(*day, 16, 0))["state"] == "afterhours"
    assert MR.session_state(_et(*day, 20, 30))["state"] == "closed"


def test_weekend_is_closed_and_points_at_monday():
    sat = MR.session_state(_et(2026, 8, 8, 12, 0))
    assert sat["state"] == "closed" and "weekend" in sat["reason"]
    assert sat["seconds_until_close"] is None
    assert sat["next_session"]["date"] == "2026-08-10"     # Monday
    sun = MR.session_state(_et(2026, 8, 9, 12, 0))
    assert sun["next_session"]["date"] == "2026-08-10"


def test_holidays_are_closed():
    july4 = MR.session_state(_et(2026, 7, 3, 12, 0))       # Jul 4 2026 = Sat -> observed Fri
    xmas = MR.session_state(_et(2026, 12, 25, 12, 0))
    assert xmas["state"] == "holiday" and xmas["holiday"]
    assert july4["state"] in ("holiday", "closed")
    assert not MR.is_trading_day(_et(2026, 12, 25, 12, 0).date())


def test_early_close_is_honoured():
    """A 13:00 ET early close must not report an open session at 14:00."""
    early = MR.early_closes(2026)
    assert early, "expected at least one early close in the calendar"
    d = sorted(early)[0]
    assert MR.session_state(datetime.combine(d, MR.REGULAR_OPEN, tzinfo=ET)
                            + timedelta(hours=1))["state"] == "open"
    after = MR.session_state(datetime.combine(d, MR.REGULAR_OPEN, tzinfo=ET)
                             + timedelta(hours=5))          # 14:30, past a 13:00 close
    assert after["state"] in ("afterhours", "closed")


def test_dst_is_handled_by_the_zone_not_a_fixed_offset():
    """9:30 ET is 9:30 ET on both sides of the DST boundary."""
    summer = MR.session_state(_et(2026, 7, 15, 10, 0))
    winter = MR.session_state(_et(2026, 1, 15, 10, 0))
    assert summer["state"] == winter["state"] == "open"
    assert summer["now_et"].endswith("-04:00")            # EDT
    assert winter["now_et"].endswith("-05:00")            # EST


def test_app_market_clock_uses_the_authoritative_session_state(monkeypatch):
    """/api/scheduler must not carry a second, private clock implementation."""
    import app
    assert not hasattr(app, "_seconds_to_open"), \
        "the duplicate market clock must stay deleted — one authoritative source"
    clock = app._market_clock()
    live = MR.session_state()
    assert clock["state"] == live["state"]
    assert clock["is_open"] == (live["state"] == "open")
    # an open session reports time-to-close, so the header can pick the right verb
    if clock["is_open"]:
        assert clock["seconds_until_close"] is not None


# ══════════════════════ Per-datatype freshness ══════════════════════

def test_each_data_type_has_its_own_window():
    """A single global age is the bug: 10 minutes is dangerous for a quote and
    irrelevant for an earnings date."""
    q = FR.thresholds_for("quote")
    f = FR.thresholds_for("fundamental")
    assert q["stale"] < f["stale"]
    assert FR.classify_typed("quote", 600)["state"] in ("stale", "critically_stale")
    assert FR.classify_typed("fundamental", 600)["state"] == "fresh"
    assert FR.classify_typed("scanner", 4 * 3600)["state"] == "fresh"
    assert FR.classify_typed("quote", 4 * 3600)["state"] == "critically_stale"


def test_thresholds_are_env_overridable_per_type(monkeypatch):
    monkeypatch.setenv("FRESH_QUOTE_STALE_S", "45")
    assert FR.thresholds_for("quote")["stale"] == 45.0


def test_every_declared_data_type_resolves():
    for kind in FR.DATA_TYPES:
        t = FR.thresholds_for(kind)
        assert t["fresh"] < t["ageing"] < t["stale"], kind


# ══════════════════════ Closed != stale ══════════════════════

def test_market_closed_is_not_reported_as_stale():
    """Friday's close read on Sunday is correct and complete data, not a failure."""
    rec = FR.classify_typed("quote", 28 * 3600, market_state="closed")
    assert rec["presentation"] == "MARKET CLOSED · LAST CLOSE"
    assert rec["blocks_tradeable"] is False        # nothing to block — exchange is shut
    assert "market closed" in rec["label"]


def test_a_provider_failure_still_reads_as_failure_when_closed():
    """Closed-market leniency must not swallow a genuine refresh failure."""
    rec = FR.classify_typed("quote", 28 * 3600, market_state="closed", is_fallback=True)
    assert rec["presentation"] == "STALE · REFRESH FAILED"
    assert rec["blocks_tradeable"] is True


def test_stale_during_an_open_session_is_a_failure():
    rec = FR.classify_typed("quote", 30 * 60, market_state="open")
    assert rec["presentation"] == "STALE · REFRESH FAILED"
    assert rec["blocks_tradeable"] is True


def test_presentation_covers_live_and_delayed():
    assert FR.classify_typed("quote", 5, market_state="open")["presentation"] == "LIVE"
    assert FR.classify_typed("quote", 200, market_state="open")["presentation"] == "DELAYED"
    assert FR.presentation("unknown") == "UNKNOWN"
    assert FR.classify_typed("quote", 5, market_state="premarket")["presentation"] == "LIVE"


def test_header_batch_scan_badge_is_market_aware(monkeypatch):
    """The reported `DATA stale (28h)`: a 28h-old overnight artifact on a Sunday is
    normal. During a live session the same age is not."""
    import app
    monkeypatch.setattr(app, "_fresh", lambda name: {"age_seconds": 28 * 3600, "at": "x"})
    monkeypatch.setattr(MR, "session_state", lambda *a, **k: {"state": "closed"})
    st = app._data_state("last_scan_both.json")
    assert st["scope"] == "batch-scan"
    assert st["presentation"] == "MARKET CLOSED · LAST CLOSE"
    monkeypatch.setattr(MR, "session_state", lambda *a, **k: {"state": "open"})
    st_open = app._data_state("last_scan_both.json")
    assert st_open["presentation"] == "STALE · REFRESH FAILED"


# ══════════════════════ Decision safety ══════════════════════

def _candidate(ts):
    return {"symbol": "X", "score": {"total": 72},
            "indicators": {"price": 100.0, "atr_pct": 2.0, "source_timestamp": ts,
                           "as_of": ts},
            "levels": {"state": "ok", "reference_price": 100.0, "invalidation": 95.0,
                       "entry_zone": [99.5, 100.5], "target_1": 112.0, "target_2": 120.0,
                       "rr_target_1": 2.4, "invalidation_basis": "swing low",
                       "computed_at": ts},
            "setups": [{"type": "momentum", "horizon": "1-2 weeks"}]}


def test_a_verdict_is_withheld_when_a_critical_input_is_stale(monkeypatch):
    import options_desk as OD
    monkeypatch.setattr(MR, "session_state", lambda *a, **k: {"state": "open"})
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    d = OD.decide(candidate=_candidate(old), best_contract=None,
                  account_info={"buying_power": 447.94, "fetched_at": old,
                                "spreads_available": False},
                  cfg=OD.config(), contracts_analysed=10)
    assert any("DECISION STALE" in b for b in d["blockers"])
    assert d["verdict"] in ("watch-only", "reject")
    assert d["decision_freshness"]["ok"] is False
    assert d["decision_freshness"]["status"] == "DECISION STALE — REFRESH REQUIRED"


def test_fresh_inputs_do_not_trip_the_staleness_gate(monkeypatch):
    import options_desk as OD
    monkeypatch.setattr(MR, "session_state", lambda *a, **k: {"state": "open"})
    now = datetime.now(timezone.utc).isoformat()
    d = OD.decide(candidate=_candidate(now), best_contract=None,
                  account_info={"buying_power": 447.94, "fetched_at": now,
                                "spreads_available": False},
                  cfg=OD.config(), contracts_analysed=10)
    assert d["decision_freshness"]["ok"] is True
    assert not any("DECISION STALE" in b for b in d["blockers"])


def test_decision_freshness_reports_each_input_separately(monkeypatch):
    """One global age describes none of the inputs — the reader must see WHICH is old."""
    import options_desk as OD
    monkeypatch.setattr(MR, "session_state", lambda *a, **k: {"state": "open"})
    now = datetime.now(timezone.utc).isoformat()
    d = OD.decide(candidate=_candidate(now), best_contract=None,
                  account_info={"buying_power": 447.94, "fetched_at": now,
                                "spreads_available": False},
                  cfg=OD.config(), contracts_analysed=10)
    names = {c["input"] for c in d["decision_freshness"]["checks"]}
    assert {"underlying quote", "account state", "technical levels"} <= names
    for c in d["decision_freshness"]["checks"]:
        assert c["kind"] in FR.DATA_TYPES
        assert "presentation" in c


def test_closed_market_does_not_withhold_but_is_labelled(monkeypatch):
    """On a shut exchange, age alone must not block — but the result says so."""
    import options_desk as OD
    monkeypatch.setattr(MR, "session_state", lambda *a, **k: {"state": "closed"})
    old = (datetime.now(timezone.utc) - timedelta(hours=20)).isoformat()
    d = OD.decide(candidate=_candidate(old), best_contract=None,
                  account_info={"buying_power": 447.94, "fetched_at": old,
                                "spreads_available": False},
                  cfg=OD.config(), contracts_analysed=10)
    assert d["decision_freshness"]["ok"] is True
    assert d["decision_freshness"]["status"] == "MARKET CLOSED · LAST CLOSE"
