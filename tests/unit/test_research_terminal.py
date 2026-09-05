"""Research-terminal tests — session/regime, scanner, options desk, tracker.

All OFFLINE and deterministic: no network, no live Robinhood call, no credentials.
Chain and account fixtures are hand-built from the REAL response shapes observed on
2026-08-06 (Robinhood quotes arrive as strings, Yahoo supplies no Greeks), so the
parsing paths are exercised against the shapes they will actually meet.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in ("src", "dashboard", "lab"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import market_regime as MR      # noqa: E402
import scanner as SC            # noqa: E402
import options_desk as OD       # noqa: E402
import research as R            # noqa: E402
from zoneinfo import ZoneInfo   # noqa: E402

ET = ZoneInfo("America/New_York")


def _et(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=ET)


# ══════════════════════ Symbol normalization ══════════════════════

def test_share_class_and_non_equity_symbols_map_correctly():
    assert R.to_yahoo_any("BRK.B") == "BRK-B"
    assert R.to_yahoo_any("BRK-B") == "BRK-B"
    # indices / futures / FX are NOT equities: the share-class rule must not touch them
    assert R.to_yahoo_any("^VIX") == "^VIX"
    assert R.to_yahoo_any("^TNX") == "^TNX"
    assert R.to_yahoo_any("CL=F") == "CL=F"
    assert R.to_yahoo_any("GC=F") == "GC=F"
    # regression: to_yahoo turned the dollar index into the nonexistent DX-Y-NYB
    assert R.to_yahoo_any("DX-Y.NYB") == "DX-Y.NYB"
    assert R.to_yahoo("DX-Y.NYB") == "DX-Y-NYB"      # the equity mapper still does this
    assert R.to_yahoo_any("") == ""


# ══════════════════════ Market calendar / session ══════════════════════

@pytest.mark.parametrize("d,name", [
    ("2026-01-01", "New Year's Day"),
    ("2026-01-19", "Martin Luther King Jr. Day"),
    ("2026-02-16", "Washington's Birthday"),
    ("2026-04-03", "Good Friday"),
    ("2026-05-25", "Memorial Day"),
    ("2026-06-19", "Juneteenth"),
    ("2026-09-07", "Labor Day"),
    ("2026-11-26", "Thanksgiving Day"),
    ("2026-12-25", "Christmas Day"),
])
def test_market_holidays_are_computed_not_tabulated(d, name):
    dd = date.fromisoformat(d)
    assert MR.market_holidays(dd.year).get(dd) == name
    assert MR.is_trading_day(dd) is False


def test_holiday_observance_shifts_weekend_dates():
    # July 4 2026 is a Saturday -> observed Friday July 3
    assert MR.market_holidays(2026).get(date(2026, 7, 3)) == "Independence Day"
    assert MR.market_holidays(2026).get(date(2026, 7, 4)) is None
    # Jan 1 2022 was a Saturday -> New Year's is observed on Friday Dec 31 2021, which
    # lands in the PREVIOUS calendar year but belongs to 2022's holiday set
    assert MR.market_holidays(2022).get(date(2021, 12, 31)) == "New Year's Day"
    assert MR.market_holidays(2022).get(date(2022, 1, 1)) is None


def test_early_close_is_1300_and_flips_the_session():
    assert MR.early_closes(2026).get(date(2026, 11, 27)) == "day after Thanksgiving"
    assert MR.close_time_for(date(2026, 11, 27)).strftime("%H:%M") == "13:00"
    s = MR.session_state(_et("2026-11-27T13:30"))
    assert s["state"] == "afterhours"          # already closed at 13:00
    assert s["early_close"] == "day after Thanksgiving"


@pytest.mark.parametrize("t,state", [
    ("2026-08-06T03:00", "closed"),
    ("2026-08-06T04:00", "premarket"),
    ("2026-08-06T09:29", "premarket"),
    ("2026-08-06T09:30", "open"),
    ("2026-08-06T15:59", "open"),
    ("2026-08-06T16:00", "afterhours"),
    ("2026-08-06T19:59", "afterhours"),
    ("2026-08-06T20:00", "closed"),
    ("2026-08-08T12:00", "closed"),          # Saturday
    ("2026-12-25T11:00", "holiday"),
])
def test_session_state_boundaries(t, state):
    assert MR.session_state(_et(t))["state"] == state


def test_next_session_is_the_one_that_has_not_opened_yet():
    """Regression: during and after a session, today's already-opened session was
    reported as 'next', so the countdown read as though the open were still ahead."""
    assert MR.session_state(_et("2026-08-06T05:00"))["next_session"]["date"] == "2026-08-06"
    assert MR.session_state(_et("2026-08-06T10:00"))["next_session"]["date"] == "2026-08-07"
    assert MR.session_state(_et("2026-08-06T17:00"))["next_session"]["date"] == "2026-08-07"
    # Friday evening -> Monday
    assert MR.session_state(_et("2026-08-07T18:00"))["next_session"]["date"] == "2026-08-10"
    # Christmas Day 2026 (Friday) -> Monday the 28th
    assert MR.session_state(_et("2026-12-25T11:00"))["next_session"]["date"] == "2026-12-28"


def test_session_reports_both_clocks_and_time_to_close():
    s = MR.session_state(_et("2026-08-06T10:00"))
    assert s["now_et"].startswith("2026-08-06T10:00")
    assert "+05:30" in s["now_ist"]
    assert s["seconds_until_close"] == 6 * 3600
    assert MR.session_state(_et("2026-08-06T17:00"))["seconds_until_close"] is None


# ══════════════════════ Regime classification ══════════════════════

def _tape(**over):
    base = {s: {"symbol": s, "label": m["label"], "kind": m["kind"], "state": "ok",
                "price": 100.0, "change_pct": 0.0, "source_timestamp": "2026-08-05T20:00:00+00:00",
                "stats": {"state": "ok", "trend_efficiency": 0.5, "pos_in_20d_range_pct": 50.0,
                          "range_20d_pct": 5.0, "realized_vol_20d": 14.0, "realized_vol_60d": 14.0,
                          "vol_ratio_20_60": 1.0, "above_sma20": True, "above_sma50": True,
                          "chg_20d": 2.0}}
            for s, m in MR.TAPE.items()}
    for k, v in over.items():
        base[k] = {**base[k], **v}
    return base


def test_regime_risk_on_and_risk_off_are_evidence_backed():
    on = MR.classify(_tape(SPY={"change_pct": 1.2}, QQQ={"change_pct": 1.5},
                           IWM={"change_pct": 1.4}, DIA={"change_pct": 1.0},
                           **{"^VIX": {"change_pct": -8.0, "price": 14.0}}))
    assert on["risk_appetite"] == "risk-on"
    assert any(e["factor"] == "risk_appetite" for e in on["evidence"])
    off = MR.classify(_tape(SPY={"change_pct": -1.2}, QQQ={"change_pct": -1.6},
                            IWM={"change_pct": -1.8}, DIA={"change_pct": -1.0},
                            **{"^VIX": {"change_pct": 15.0, "price": 26.0}}))
    assert off["risk_appetite"] == "risk-off"


def test_mixed_tape_is_not_forced_onto_one_side():
    """A tape with the indices disagreeing must read 'mixed', not be tipped over by a
    single loud contributor."""
    c = MR.classify(_tape(SPY={"change_pct": -0.2}, QQQ={"change_pct": -0.9},
                          IWM={"change_pct": -0.64}, DIA={"change_pct": 0.44},
                          **{"GC=F": {"change_pct": 1.94}}))
    assert c["risk_appetite"] == "mixed"


def test_trending_vs_range_bound_uses_path_efficiency():
    tr = MR.classify(_tape(SPY={"stats": {"state": "ok", "trend_efficiency": 0.72,
                                          "above_sma20": True, "above_sma50": True,
                                          "realized_vol_20d": 12.0, "realized_vol_60d": 12.0}}))
    assert tr["structure"] == "trending"
    rb = MR.classify(_tape(SPY={"stats": {"state": "ok", "trend_efficiency": 0.18,
                                          "pos_in_20d_range_pct": 40.0, "range_20d_pct": 6.0,
                                          "realized_vol_20d": 12.0, "realized_vol_60d": 12.0}}))
    assert rb["structure"] == "range-bound"


def test_volatility_and_event_regimes():
    hi = MR.classify(_tape(**{"^VIX": {"price": 30.0, "change_pct": 2.0}}))
    assert hi["volatility_regime"] == "high-volatility"
    lo = MR.classify(_tape(**{"^VIX": {"price": 12.0, "change_pct": 0.5}}))
    assert lo["volatility_regime"] == "low-volatility"
    ev = MR.classify(_tape(**{"^VIX": {"price": 20.0, "change_pct": 22.0}}))
    assert ev["event_driven"] is True
    assert any(e["factor"] == "event_risk" for e in ev["evidence"])


def test_missing_instruments_reduce_confidence_and_are_named():
    t = _tape()
    t["^VIX"] = {**t["^VIX"], "state": "error", "price": None, "change_pct": None}
    t["GC=F"] = {**t["GC=F"], "state": "error", "price": None, "change_pct": None}
    c = MR.classify(t)
    assert set(c["missing_instruments"]) == {"^VIX", "GC=F"}
    assert c["data_coverage"] < 1.0
    assert c["confidence"] < 1.0


def test_regime_unknown_when_nothing_resolves():
    t = {s: {"symbol": s, "label": m["label"], "kind": m["kind"], "state": "error",
             "price": None, "change_pct": None, "stats": {"state": "error"}}
         for s, m in MR.TAPE.items()}
    c = MR.classify(t)
    assert c["risk_appetite"] == "unknown" and c["structure"] == "unknown"
    assert c["risk_score"] is None


# ══════════════════════ Scanner: eligibility filters ══════════════════════

CFG = SC.config()


def _q(price=50.0, volume=5_000_000, ts=None, state="ok"):
    ts = ts or datetime.now(timezone.utc).replace(hour=20, minute=0, second=0).isoformat()
    return {"state": state, "price": price, "volume": volume, "source_timestamp": ts}


def test_eligibility_accepts_a_liquid_name():
    ok, why, facts = SC.eligibility("AAPL", _q(), CFG, "Apple Inc.")
    assert ok and why == []
    assert facts["dollar_volume"] == 250_000_000


def test_eligibility_rejects_cheap_illiquid_and_stale():
    ok, why, _ = SC.eligibility("PENNY", _q(price=2.0), CFG)
    assert not ok and any("below" in w and "minimum" in w for w in why)
    ok, why, _ = SC.eligibility("THIN", _q(price=50.0, volume=1_000), CFG)
    assert not ok and any("share volume" in w for w in why)
    old = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
    ok, why, _ = SC.eligibility("STALE", _q(ts=old), CFG)
    assert not ok and any("old" in w for w in why)


def test_eligibility_rejects_leveraged_products_unless_enabled():
    ok, why, _ = SC.eligibility("TQQQ", _q(), CFG)
    assert not ok and any("leveraged" in w for w in why)
    ok2, _, _ = SC.eligibility("TQQQ", _q(), {**CFG, "leveraged_allowed": True})
    assert ok2
    # also caught by name for symbols not on the list
    ok3, why3, _ = SC.eligibility("XYZ", _q(), CFG, "Direxion Daily 3X Bull")
    assert not ok3 and any("leveraged" in w for w in why3)


def test_eligibility_requires_a_source_timestamp():
    ok, why, _ = SC.eligibility("NOTS", {"state": "ok", "price": 50.0, "volume": 5_000_000,
                                         "source_timestamp": None}, CFG)
    assert not ok and any("no source timestamp" in w for w in why)


def test_eligibility_rejects_a_broken_quote():
    ok, why, _ = SC.eligibility("HALT", {"state": "error", "price": None, "reason": "halted"}, CFG)
    assert not ok and any("no usable quote" in w for w in why)


# ══════════════════════ Scanner: indicators, setups, scoring ══════════════════════

def _series(n=260, start=100.0, drift=0.4, vol=1.0, last_vol=1_000_000):
    bars, px = [], start
    for i in range(n):
        px += drift + ((i % 5) - 2) * vol * 0.1
        bars.append({"t": f"2026-01-01T00:00:00+00:00", "o": px - 0.3, "h": px + 0.8,
                     "l": px - 0.9, "c": round(px, 2), "v": last_vol})
    return {"state": "ok", "bars": bars, "source_timestamp": "2026-08-05T20:00:00+00:00"}


def test_indicators_need_enough_history_and_say_so():
    out = SC.indicators("X", {"state": "ok", "bars": [{"c": 1, "h": 1, "l": 1, "o": 1, "v": 1}] * 10})
    assert out["state"] == "insufficient_history" and "history" in out["missing"]


def test_indicators_compute_sma200_with_a_year_of_bars():
    out = SC.indicators("X", _series(260))
    assert out["state"] == "ok"
    assert out["sma200"] is not None and out["above_sma200"] is True
    assert "sma200" not in out["missing"]
    assert out["realized_vol_20d"] is not None       # close-to-close, for the IV yardstick


def test_no_setup_is_a_valid_outcome():
    """A flat, featureless series must produce NO setup — nothing is forced."""
    # a series that cycles inside a band and ends in the MIDDLE of it: no trend, no
    # breakout, not oversold, not above its averages, no sector rank -> nothing matches
    cycle = [0.0, 0.5, 1.0, 0.5, 0.0, -0.5, -1.0]
    bars = []
    for i in range(259):
        c = 100.0 + cycle[i % len(cycle)]
        bars.append({"t": "", "o": c, "h": c + 0.1, "l": c - 0.1, "c": c, "v": 1_000_000})
    flat = {"state": "ok", "source_timestamp": "2026-08-05T20:00:00+00:00", "bars": bars}
    ind = SC.indicators("FLAT", flat)
    assert ind["state"] == "ok"
    assert SC.detect_setups(ind, None, 11, 0.0) == []


def test_rsi_is_undefined_when_the_series_never_moves():
    """A perfectly flat series has no RSI. Returning 100 there reported a
    never-moving stock as maximally overbought."""
    assert SC._rsi([100.0] * 30) is None
    assert SC._rsi([100.0 + i for i in range(30)]) == 100.0      # only-up IS 100


def test_breakout_requires_volume_confirmation():
    ind = SC.indicators("B", _series(260))
    ind = {**ind, "high_20d": ind["price"], "rel_volume": 2.0}
    kinds = {s["type"] for s in SC.detect_setups(ind, 1, 11, 0.0)}
    assert "breakout_volume" in kinds
    ind_no_vol = {**ind, "rel_volume": 1.0}
    kinds2 = {s["type"] for s in SC.detect_setups(ind_no_vol, 1, 11, 0.0)}
    assert "breakout_volume" not in kinds2
    assert "breakout_unconfirmed" in kinds2      # named honestly, not silently promoted


def test_score_components_sum_to_the_total_and_are_all_shown():
    ind = SC.indicators("S", _series(260))
    lv = SC._levels(ind, CFG)
    setups = SC.detect_setups(ind, 1, 11, 0.0)
    sc = SC.score(ind, sector={"name": "Technology", "rs_vs_spy_1m": 1.0, "perf_5d": 3.0,
                               "breadth": {"advancers": 8, "counted": 10, "complete": True}},
                  sector_rank=1, sector_count=11,
                  regime={"risk_appetite": "risk-on", "structure": "trending"},
                  setups=setups, levels=lv, spy_20d=1.0)
    assert set(c["component"] for c in sc["components"]) == set(SC.WEIGHTS)
    assert sum(c["max"] for c in sc["components"]) == 100
    assert abs(sum(c["score"] for c in sc["components"]) - sc["subtotal"]) < 0.01
    assert abs(sc["subtotal"] + sc["penalty_total"] - sc["total"]) < 0.06   # total shown to 1dp
    assert all(c["note"] for c in sc["components"])      # every component explains itself


def test_conflicting_signals_apply_a_penalty():
    ind = SC.indicators("C", _series(260))
    lv = SC._levels(ind, CFG)
    setups = [{"type": "gap_continuation", "strength": 50, "evidence": "", "horizon": "days"},
              {"type": "gap_reversal", "strength": 40, "evidence": "", "horizon": "days"}]
    sc = SC.score(ind, sector=None, sector_rank=None, sector_count=11,
                  regime={"risk_appetite": "mixed", "structure": "range-bound"},
                  setups=setups, levels=lv, spy_20d=1.0)
    assert any(p["penalty"] == "conflict" for p in sc["penalties"])
    assert sc["penalty_total"] < 0


def test_event_driven_regime_penalises_single_name_setups():
    ind = SC.indicators("E", _series(260))
    sc = SC.score(ind, sector=None, sector_rank=None, sector_count=11,
                  regime={"risk_appetite": "mixed", "structure": "trending", "event_driven": True},
                  setups=[], levels=SC._levels(ind, CFG), spy_20d=1.0)
    assert any(p["penalty"] == "event_risk" for p in sc["penalties"])


def test_unknown_sector_scores_zero_credit_never_a_guess():
    ind = SC.indicators("U", _series(260))
    sc = SC.score(ind, sector=None, sector_rank=None, sector_count=11,
                  regime={"risk_appetite": "mixed", "structure": "trending"},
                  setups=[], levels=SC._levels(ind, CFG), spy_20d=1.0)
    comp = {c["component"]: c for c in sc["components"]}
    assert comp["sector_strength"]["score"] == 0
    assert "unknown" in comp["sector_strength"]["note"]


def test_relative_volume_is_prorated_during_the_session():
    """Today's daily bar holds only the volume traded SO FAR. Comparing it to a
    20-day average of COMPLETE days made every name read ~0.3x at midday."""
    b = _series(260, last_vol=1_000_000)
    b["bars"][-1]["v"] = 350_000                      # ~35% of a normal day
    mid = SC.indicators("X", b, session_fraction=0.35)
    # the partial bar also drags the 20-day average down slightly, so anchor the
    # assertion to the raw figure the module actually computed
    assert 0.34 < mid["rel_volume_raw"] < 0.38
    assert mid["rel_volume"] == pytest.approx(mid["rel_volume_raw"] / 0.35, abs=0.01)
    assert mid["rel_volume"] > 0.95            # reads as a roughly normal-volume day
    assert "pro-rated" in mid["rel_volume_basis"]
    closed = SC.indicators("X", b, session_fraction=None)
    assert closed["rel_volume"] == closed["rel_volume_raw"]
    assert closed["rel_volume_basis"] == "full session vs 20-day average"


def test_session_fraction_is_none_outside_the_session():
    assert SC.session_fraction_elapsed(_et("2026-08-06T08:00")) is None   # pre-market
    assert SC.session_fraction_elapsed(_et("2026-08-06T17:00")) is None   # after hours
    assert SC.session_fraction_elapsed(_et("2026-08-08T12:00")) is None   # weekend
    assert SC.session_fraction_elapsed(_et("2026-12-25T11:00")) is None   # holiday
    half = SC.session_fraction_elapsed(_et("2026-08-06T12:45"))
    assert 0.49 < half < 0.51
    # an early-close day uses the SHORT session as the denominator
    early = SC.session_fraction_elapsed(_et("2026-11-27T11:15"))
    assert 0.49 < early < 0.51


def test_levels_reward_risk_is_measured_not_defined():
    """Targets come from the range; R:R must vary between charts rather than being a
    constant produced by defining targets as multiples of the stop."""
    tight = SC.indicators("T", _series(260, drift=0.4, vol=0.2))
    wide = SC.indicators("W", _series(260, drift=0.4, vol=6.0))
    lt, lw = SC._levels(tight, CFG), SC._levels(wide, CFG)
    assert lt["state"] == "ok" and lw["state"] == "ok"
    assert lt["rr_target_1"] != lw["rr_target_1"]
    for lv in (lt, lw):
        assert lv["target_1"] > lv["reference_price"] > lv["invalidation"]
        assert lv["target_1_basis"] and lv["target_2_basis"]
        expected = round((lv["target_1"] - lv["reference_price"]) / lv["risk_per_share"], 2)
        assert abs(lv["rr_target_1"] - expected) < 0.02


def test_levels_unavailable_without_atr():
    assert SC._levels({"price": 10.0, "atr14": None}, CFG)["state"] == "unavailable"


def test_sector_ranking_orders_and_keeps_unavailable_tiles():
    tiles = {"sectors": [
        {"key": "a", "name": "A", "state": "ok", "rs_vs_spy_1m": 3.0, "perf_5d": 4.0,
         "perf_1d": 1.0, "momentum_pct": 2.0, "rel_volume": 1.2,
         "breadth": {"advancers": 9, "decliners": 1, "counted": 10}},
        {"key": "b", "name": "B", "state": "ok", "rs_vs_spy_1m": -4.0, "perf_5d": -2.0,
         "perf_1d": -1.0, "momentum_pct": -1.0, "rel_volume": 0.8,
         "breadth": {"advancers": 1, "decliners": 9, "counted": 10}},
        {"key": "c", "name": "C", "state": "throttled"}]}
    ranked = SC.rank_sectors(tiles)
    assert [r["key"] for r in ranked] == ["a", "b", "c"]
    assert ranked[0]["rank"] == 1 and ranked[1]["rank"] == 2
    assert ranked[2]["rank"] is None and ranked[2]["rank_state"] == "throttled"
    assert ranked[0]["rank_score"] > ranked[1]["rank_score"]


# ══════════════════════ Options desk: contract maths ══════════════════════

def _contract(**over):
    c = {"contract_id": "id1", "symbol": "BAC", "expiry": (date.today() + timedelta(days=15)).isoformat(),
         "strike": 63.0, "side": "CALL", "multiplier": 100.0,
         "min_ticks": {"above_tick": "0.05", "below_tick": "0.01", "cutoff_price": "3.00"},
         "bid": 1.21, "ask": 1.31, "mark": 1.26, "last": 1.25,
         "volume": 83, "open_interest": 4826, "implied_volatility": 0.208,
         "delta": 0.5607, "gamma": 0.09, "theta": -0.0382, "vega": 0.05,
         "chance_of_profit_long": 0.3617, "high_fill_rate_buy_price": 1.287,
         "quote_timestamp": datetime.now(timezone.utc).isoformat(), "provider": "robinhood"}
    c.update(over)
    return c


OCFG = OD.config()


def test_midpoint_spread_and_breakeven():
    a = OD.analyse_contract(_contract(), spot=63.25, cfg=OCFG)
    assert a["mid"] == 1.26
    assert a["spread_dollars"] == 0.10
    assert a["spread_pct"] == pytest.approx(7.94, abs=0.01)
    # break-even = strike + the price actually paid (not the mid)
    assert a["break_even"] == pytest.approx(a["strike"] + a["limit_price"], abs=0.001)
    assert a["pct_move_to_break_even"] == pytest.approx((a["break_even"] - 63.25) / 63.25 * 100, abs=0.01)
    assert a["break_even_provenance"] == "calculated"


def test_limit_price_is_rounded_up_to_the_contract_tick():
    a = OD.analyse_contract(_contract(), spot=63.25, cfg=OCFG)
    assert a["tick_size"] == 0.01
    assert a["limit_price"] == 1.29           # 1.287 rounded UP to the penny you pay
    b = OD.analyse_contract(_contract(bid=19.0, ask=21.0, high_fill_rate_buy_price=19.977,
                                      strike=472.5), spot=487.0, cfg=OCFG)
    assert b["tick_size"] == 0.05 and b["limit_price"] == 20.0


def test_max_loss_is_the_whole_premium_plus_fees():
    a = OD.analyse_contract(_contract(), spot=63.25, cfg=OCFG, contracts=2)
    assert a["entry_debit"] == pytest.approx(a["limit_price"] * 100 * 2)
    assert a["max_loss"] == pytest.approx(a["entry_debit"] + a["fees_estimated"])
    assert "entire premium" in a["max_loss_basis"]


def test_required_underlying_move_and_expected_move():
    a = OD.analyse_contract(_contract(), spot=63.25, cfg=OCFG, atr_pct=1.66)
    assert a["implied_move_pct"] > 0
    assert a["underlying_expected_move_pct"] > 0
    assert a["break_even_within_expected_move"] is (
        abs(a["pct_move_to_break_even"]) <= a["underlying_expected_move_pct"])
    assert "model" in a["implied_move_provenance"]


def test_theta_cost_per_day_and_delta_exposure():
    a = OD.analyse_contract(_contract(), spot=63.25, cfg=OCFG, contracts=1)
    assert a["theta_cost_per_day"] == pytest.approx(abs(-0.0382) * 100, abs=0.01)
    assert a["delta_adjusted_exposure"] == pytest.approx(0.5607 * 100 * 63.25, abs=1)


def test_zero_bid_contract_is_rejected():
    a = OD.analyse_contract(_contract(bid=0.0, ask=0.10, high_fill_rate_buy_price=None,
                                      mark=None), spot=63.25, cfg=OCFG)
    assert a["tradeable"] is False
    assert any("bid" in f for f in a["liquidity_failures"])


def test_missing_greeks_are_modelled_and_labelled_never_invented():
    c = _contract(delta=None, gamma=None, theta=None, vega=None, provider="yahoo")
    a = OD.analyse_contract(c, spot=63.25, cfg=OCFG)
    assert a["greeks_provenance"] == "model"
    assert a["greeks_model"]["model"] == "black_scholes"
    assert a["greeks_model"]["inputs"]["iv"] == 0.208
    assert a["delta"] is not None and 0 < a["delta"] < 1
    # with no IV either, nothing is fabricated and the contract is rejected
    b = OD.analyse_contract(_contract(delta=None, theta=None, implied_volatility=None),
                            spot=63.25, cfg=OCFG)
    assert b["greeks_provenance"] is None
    assert "greeks" in b["missing_fields"] and b["tradeable"] is False


def test_model_greeks_can_be_disabled():
    c = _contract(delta=None, gamma=None, theta=None, vega=None)
    a = OD.analyse_contract(c, spot=63.25, cfg={**OCFG, "allow_model_greeks": False})
    assert a["greeks_provenance"] is None and "greeks" in a["missing_fields"]


def test_spread_dollar_cap_scales_with_contract_price():
    """A flat $0.60 cap rejected every near-the-money contract on an expensive name;
    a $3.20 spread on a $74 contract is 4%, which is tight."""
    a = OD.analyse_contract(_contract(bid=72.4, ask=75.6, strike=415.0,
                                      high_fill_rate_buy_price=74.2,
                                      min_ticks={"above_tick": "0.05", "below_tick": "0.01",
                                                 "cutoff_price": "3.00"}),
                            spot=487.0, cfg=OCFG)
    assert a["spread_dollars"] == pytest.approx(3.2, abs=0.01)
    assert a["spread_dollar_cap_applied"] > 0.60
    assert not any("exceeds the $" in f for f in a["liquidity_failures"])


def test_volume_gate_is_waived_outside_the_session():
    """Today's option volume is 0 before the bell — enforcing a minimum then would
    reject the entire chain for a reason unrelated to the contract."""
    c = _contract(volume=0)
    closed = OD.analyse_contract(c, spot=63.25, cfg=OCFG, session_open=False)
    assert closed["tradeable"] is True
    assert "waived" in closed["volume_gate"]
    opened = OD.analyse_contract(c, spot=63.25, cfg=OCFG, session_open=True)
    assert opened["tradeable"] is False
    assert any("volume" in f for f in opened["liquidity_failures"])


def test_contract_without_a_quote_timestamp_is_rejected():
    a = OD.analyse_contract(_contract(quote_timestamp=None), spot=63.25, cfg=OCFG)
    assert a["tradeable"] is False
    assert any("timestamp" in f for f in a["liquidity_failures"])


def test_stale_contract_quote_is_rejected():
    old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    a = OD.analyse_contract(_contract(quote_timestamp=old), spot=63.25, cfg=OCFG)
    assert a["tradeable"] is False and any("old" in f for f in a["liquidity_failures"])


def test_far_otm_and_thin_open_interest_are_rejected():
    a = OD.analyse_contract(_contract(strike=80.0, open_interest=5), spot=63.25, cfg=OCFG)
    assert a["tradeable"] is False
    joined = " ".join(a["liquidity_failures"])
    assert "out of the money" in joined and "open interest" in joined


def test_expected_value_is_suppressed_when_the_target_is_out_of_reach():
    """Pairing a break-even probability with a swing-target payoff the contract cannot
    plausibly reach produced a confident +$1,818 on an 8-day MSFT call."""
    a = OD.analyse_contract(_contract(), spot=63.25, cfg=OCFG, atr_pct=1.66,
                            target_price=63.25 * 1.30)     # a 30% move
    assert a["expected_value"] is None
    assert "false precision" in a["expected_value_unavailable_reason"]
    assert a["probability_above_break_even"] == 0.3617     # still reported as an observation
    assert a["target_within_contract_life"] is False


def test_expected_value_is_published_when_the_target_is_reachable():
    a = OD.analyse_contract(_contract(), spot=63.25, cfg=OCFG, atr_pct=1.66,
                            target_price=63.25 * 1.02)
    assert a["expected_value"] is not None
    inp = a["expected_value_inputs"]
    assert inp["probability"] == 0.3617
    assert "Robinhood" in inp["probability_source"]
    assert "OPTIMISTIC" in inp["note"]


def test_no_probability_means_scenarios_instead_of_a_number():
    a = OD.analyse_contract(_contract(chance_of_profit_long=None), spot=63.25, cfg=OCFG,
                            atr_pct=1.66, target_price=64.5)
    assert a["expected_value"] is None
    assert "invented win rate" in a["expected_value_unavailable_reason"]
    assert len(a["scenarios"]) >= 2


def test_scenarios_are_intrinsic_at_expiry_and_labelled():
    a = OD.analyse_contract(_contract(), spot=63.25, cfg=OCFG, target_price=66.15,
                            stop_price=61.68)
    by = {s["scenario"]: s for s in a["scenarios"]}
    t1 = by["target_1"]
    assert t1["value_at_expiry"] == pytest.approx((66.15 - 63.0) * 100, abs=0.01)
    assert "intrinsic" in t1["provenance"]
    assert by["invalidation"]["pl_at_expiry"] < 0
    assert "delta-linear" in t1["before_expiry_provenance"]


def test_earnings_before_expiry_is_flagged():
    soon = (date.today() + timedelta(days=5)).isoformat()
    cs = [_contract(expiry=(date.today() + timedelta(days=15)).isoformat()),
          _contract(expiry=(date.today() + timedelta(days=3)).isoformat())]
    OD.apply_event_risk(cs, {"state": "ok", "date": soon, "days_away": 5})
    assert cs[0]["event_before_expiry"] is True and "earnings" in cs[0]["event_note"]
    assert cs[1]["event_before_expiry"] is False


def test_unknown_earnings_date_is_not_treated_as_no_risk():
    cs = [_contract()]
    OD.apply_event_risk(cs, {"state": "unknown", "reason": "none scheduled"})
    assert cs[0]["event_before_expiry"] is None
    assert "cannot verify" in cs[0]["event_note"]


def test_black_scholes_refuses_unusable_inputs():
    assert OD.bs_greeks(0, 100, 0.3, 30, "CALL") is None
    assert OD.bs_greeks(100, 100, 0, 30, "CALL") is None
    assert OD.bs_greeks(100, 100, 0.3, 0, "CALL") is None
    g = OD.bs_greeks(100, 100, 0.3, 30, "CALL")
    assert 0.4 < g["delta"] < 0.65 and g["theta"] < 0
    p = OD.bs_greeks(100, 100, 0.3, 30, "PUT")
    assert -0.65 < p["delta"] < -0.35


# ══════════════════════ Position sizing ══════════════════════

def test_sizing_respects_the_binding_constraint_and_never_uses_the_whole_account():
    s = OD.size_position(entry=63.25, stop=61.68, buying_power=447.94, cfg=OCFG)
    assert s["state"] == "ok"
    sh = s["shares"]
    assert 0 < sh["notional"] < 447.94
    assert sh["max_loss"] <= 447.94 * OCFG["max_account_risk_pct"] / 100 + 0.01
    assert sh["binding_constraint"] in ("risk budget", "position cap", "total exposure cap")
    assert sh["remaining_buying_power"] == pytest.approx(447.94 - sh["notional"], abs=0.01)


def test_sizing_reports_an_unaffordable_contract_rather_than_rounding_to_one():
    s = OD.size_position(entry=63.25, stop=61.68, buying_power=447.94, cfg=OCFG,
                         contract={"limit_price": 12.9, "multiplier": 100.0})
    assert s["option"]["contracts"] == 0
    assert s["option"]["affordable"] is False
    assert s["option"]["status"] == "NO CONTRACT FITS CURRENT RISK POLICY"


def test_sizing_unavailable_without_buying_power():
    s = OD.size_position(entry=10, stop=9, buying_power=None, cfg=OCFG)
    assert s["state"] == "unavailable" and "buying power" in s["reason"]


def test_sizing_rejects_a_stop_that_is_not_below_entry():
    s = OD.size_position(entry=10, stop=10, buying_power=1000, cfg=OCFG)
    assert s["state"] == "unavailable"


def test_sizing_blocks_at_position_and_sector_limits():
    s = OD.size_position(entry=63.25, stop=61.68, buying_power=447.94, cfg=OCFG,
                         open_positions=99, sector_positions=99)
    assert any("position limit" in b for b in s["blocked"])
    assert any("per-sector" in b for b in s["blocked"])


# ══════════════════════ Shares vs option verdict ══════════════════════

def _candidate(score=70.0, rr=2.0):
    return {"symbol": "BAC", "score": {"total": score},
            "indicators": {"price": 63.25, "atr_pct": 1.66, "realized_vol_20d": 16.5},
            "levels": {"state": "ok", "reference_price": 63.25, "invalidation": 61.68,
                       "entry_zone": [62.99, 63.62], "invalidation_basis": "1.5 x ATR(14)",
                       "target_1": 66.15, "target_2": 68.73, "rr_target_1": rr,
                       "rr_target_2": rr + 1, "target_1_basis": "20-day high",
                       "risk_pct": 2.5},
            "setups": [{"type": "sector_rotation", "horizon": "days to weeks"}]}


ACCT = {"state": "ok", "buying_power": 447.94, "positions": [], "spreads_available": False}


def test_verdict_is_one_of_the_defined_values():
    d = OD.decide(candidate=_candidate(), best_contract=None, account_info=ACCT, cfg=OCFG,
                  contracts_analysed=60)
    assert d["verdict"] in OD.VERDICTS


def test_prefer_stock_always_reports_the_option_it_evaluated():
    best = OD.analyse_contract(_contract(), spot=63.25, cfg=OCFG, atr_pct=1.66,
                               target_price=66.15, stop_price=61.68)
    sizing = OD.size_position(entry=63.25, stop=61.68, buying_power=447.94, cfg=OCFG,
                              contract=best)
    d = OD.decide(candidate=_candidate(), best_contract=best, account_info=ACCT, cfg=OCFG,
                  sizing=sizing, contracts_analysed=60)
    assert d["verdict"] == "prefer-stock"
    assert d["evaluated_option"] is not None
    assert d["evaluated_option"]["strike"] == 63.0
    assert all(c["pass"] for c in d["verification"])
    assert len(d["verification"]) == 3          # triple-checked


def test_prefer_stock_survives_a_chain_where_nothing_passed():
    """A chain in which every contract was rejected is the STRONGEST evidence for
    shares — it must not downgrade the verdict to watch-only."""
    sizing = OD.size_position(entry=63.25, stop=61.68, buying_power=447.94, cfg=OCFG)
    d = OD.decide(candidate=_candidate(), best_contract=None, account_info=ACCT, cfg=OCFG,
                  sizing=sizing, contracts_analysed=120,
                  rejected_sample=[{"strike": 415.0, "reason": "spread too wide"}])
    assert d["verdict"] == "prefer-stock"
    assert all(c["pass"] for c in d["verification"])


def test_prefer_stock_is_downgraded_when_no_chain_was_read():
    sizing = OD.size_position(entry=63.25, stop=61.68, buying_power=447.94, cfg=OCFG)
    d = OD.decide(candidate=_candidate(), best_contract=None, account_info=ACCT, cfg=OCFG,
                  sizing=sizing, contracts_analysed=0)
    assert d["verdict"] == "watch-only"
    assert any("verification" in b for b in d["blockers"])


def test_poor_reward_risk_is_rejected():
    d = OD.decide(candidate=_candidate(rr=0.6), best_contract=None, account_info=ACCT,
                  cfg=OCFG, contracts_analysed=10)
    assert d["verdict"] == "reject"


def test_low_score_is_watch_only():
    d = OD.decide(candidate=_candidate(score=40.0), best_contract=None, account_info=ACCT,
                  cfg=OCFG, contracts_analysed=10)
    assert d["verdict"] == "watch-only"


def test_no_levels_is_rejected_outright():
    c = _candidate()
    c["levels"] = {"state": "unavailable", "reason": "no ATR"}
    d = OD.decide(candidate=c, best_contract=None, account_info=ACCT, cfg=OCFG)
    assert d["verdict"] == "reject"


def test_spreads_are_not_recommended_without_the_account_level():
    d = OD.decide(candidate=_candidate(), best_contract=None, account_info=ACCT, cfg=OCFG,
                  contracts_analysed=10)
    assert d["verdict"] != "prefer-defined-risk-spread"
    assert "Level 3" in d["spread_note"]


def _strong_but_unaffordable_contract():
    """A contract that wins on EVERY quality axis the verdict counts — tight spread,
    deep OI, low theta, break-even inside the expected move, IV not inflated — but
    whose premium the account cannot cover. Modelled on the real PLTR 2026-08-28 $170
    call ($870.06/contract against $447.94 buying power and a $4.48 risk budget), with
    the strike moved near _candidate()'s own $63.25 price (Canonical Option
    Architecture v1.1, Step 8): decide() now runs this contract through canonical
    Layer A (contract_quality) using `spot = candidate.indicators.price`, so a
    strike as far from that spot as the original PLTR fixture would fail canonical's
    OTM gate for a reason unrelated to what these tests are isolating — affordability,
    not quality. bid/ask and a fresh quote_timestamp are added for the same reason:
    canonical A hard-fails on a missing quote/timestamp, and these tests need quality
    to genuinely PASS so affordability is the only thing left to disagree on."""
    return {"tradeable": True, "bid": 8.60, "ask": 8.80, "spread_pct": 2.3,
            "open_interest": 1549, "theta_pct_of_premium_per_day": 0.9,
            "break_even_within_expected_move": True, "pct_move_to_break_even": 5.7,
            "underlying_expected_move_pct": 12.3, "implied_volatility": 0.51,
            "dte": 21, "strike": 65.0, "side": "CALL", "limit_price": 8.70,
            "multiplier": 100.0, "expiry": (date.today() + timedelta(days=21)).isoformat(),
            "quote_timestamp": datetime.now(timezone.utc).isoformat(),
            "greeks_provenance": "provider"}


def test_unaffordable_contract_never_wins_prefer_option():
    """Affordability is a HARD constraint, not one vote among many. This contract
    collects more reasons_for_option than reasons_for_stock; on reason count alone it
    would win. It must not, because the account cannot buy it."""
    best = _strong_but_unaffordable_contract()
    sizing = OD.size_position(entry=169.06, stop=155.26, buying_power=447.94,
                              cfg=OCFG, contract=best, equity=447.94, spot=169.06,
                              atr_pct=3.0, setup_score=90.0)
    assert sizing["option"]["affordable"] is False       # premise of the test
    d = OD.decide(candidate=_candidate(), best_contract=best, account_info=ACCT,
                  cfg=OCFG, sizing=sizing, contracts_analysed=83)
    assert len(d["reasons_for_option"]) > len(d["reasons_for_stock"]) - 2, \
        "premise: the contract should look good on quality"
    assert d["verdict"] != "prefer-option"
    assert d["option_eligible"] is False
    assert d["instrument"] != "OPTION PREFERRED"
    assert "NO CONTRACT FITS CURRENT RISK POLICY" in d["option_ineligible_reason"]
    # quality and suitability must disagree here — that is the whole point
    assert d["contract_quality"]["gates_passed"] is True
    assert d["portfolio_suitability"]["pass"] is False


def test_unaffordable_contract_is_still_reported_not_hidden():
    """Ineligible for this account is not the same as not worth seeing — the best
    contract found must still be returned, marked research-only."""
    best = _strong_but_unaffordable_contract()
    sizing = OD.size_position(entry=169.06, stop=155.26, buying_power=447.94,
                              cfg=OCFG, contract=best)
    d = OD.decide(candidate=_candidate(), best_contract=best, account_info=ACCT,
                  cfg=OCFG, sizing=sizing, contracts_analysed=83)
    assert d["evaluated_option"] is not None
    assert d["evaluated_option"]["strike"] == 65.0


def test_premium_no_longer_measured_against_the_stock_risk_budget():
    """REGRESSION on the old policy. It set planned_risk = the whole premium and then
    checked it against a stock-style 1%-of-equity budget ($4.48 here), so an
    'affordable' contract had to cost $0.04/share and the option route was closed for
    every name, always. Premium is now judged against the premium cap, and planned risk
    is modelled separately — so a real contract can clear."""
    s = OD.size_position(entry=169.06, stop=155.26, buying_power=447.94, cfg=OCFG,
                         contract={**_strong_but_unaffordable_contract(),
                                   "limit_price": 0.35},
                         equity=447.94, spot=169.06, atr_pct=3.0, setup_score=90.0)
    stock_style_budget = 447.94 * 0.01
    assert s["option"]["capital_committed"] > stock_style_budget    # would have failed before
    assert s["option"]["binding_constraint"] != "risk budget"
    # the four quantities are genuinely distinct, not three aliases of the premium
    o = s["option"]
    assert o["absolute_max_loss"] == o["capital_committed"]
    assert o["planned_risk"] <= o["absolute_max_loss"]
    assert o["stress_risk"] <= o["absolute_max_loss"]


def test_unscored_setup_cannot_unlock_an_option():
    """A missing quality score must fail closed — otherwise the strictest gate is
    bypassed by omission rather than by judgement."""
    s = OD.size_position(entry=169.06, stop=155.26, buying_power=447.94, cfg=OCFG,
                         contract={**_strong_but_unaffordable_contract(),
                                   "limit_price": 0.35},
                         equity=447.94, spot=169.06, atr_pct=3.0)   # no setup_score
    assert s["option"]["affordable"] is False
    assert s["option"]["binding_constraint"] == "setup quality"
    assert "no setup score" in s["option"]["eligibility"]["reason"]


def test_affordable_contract_can_still_win_prefer_option():
    """The veto must bite ONLY on affordability — an affordable, high-quality contract
    must still win, or the fix has simply disabled the option route altogether."""
    best = _strong_but_unaffordable_contract()
    best["limit_price"] = 1.50                     # $150.06/contract
    best["implied_volatility"] = 0.20              # not inflated vs the candidate's RV
    acct = {**ACCT, "buying_power": 50_000.0}      # an account that CAN carry a contract
    sizing = OD.size_position(entry=169.06, stop=155.26, buying_power=50_000.0,
                              cfg=OCFG, contract=best, equity=50_000.0, spot=169.06,
                              atr_pct=3.0, setup_score=90.0)
    assert sizing["option"]["affordable"] is True   # premise of the test
    assert sizing["option"]["status"] == "1 CONTRACT ELIGIBLE"
    assert sizing["option"]["contracts"] == 1      # never fractional
    d = OD.decide(candidate=_candidate(), best_contract=best, account_info=acct,
                  cfg=OCFG, sizing=sizing, contracts_analysed=83)
    assert d["option_eligible"] is True
    assert d["option_ineligible_reason"] is None
    assert d["verdict"] == "prefer-option"
    assert d["instrument"] == "OPTION PREFERRED"
    assert d["decision"] == "TRADEABLE"
    assert d["portfolio_suitability"]["pass"] is True


def test_prefer_option_requires_affordability_to_have_been_checked():
    """No sizing means affordability was never established. Silence is not a pass."""
    best = _strong_but_unaffordable_contract()
    d = OD.decide(candidate=_candidate(), best_contract=best, account_info=ACCT,
                  cfg=OCFG, sizing=None, contracts_analysed=83)
    assert d["verdict"] != "prefer-option"
    assert d["option_eligible"] is False


def test_verdict_never_claims_an_order_was_placed():
    d = OD.decide(candidate=_candidate(), best_contract=None, account_info=ACCT, cfg=OCFG,
                  contracts_analysed=10)
    assert "No order is placed" in d["disclaimer"]


# ══════════════════════ Robinhood failure handling ══════════════════════

def test_account_reports_auth_failure_without_crashing_or_leaking(monkeypatch):
    import robinhood_view as rv
    monkeypatch.setattr(rv, "accounts", lambda force=False: (_ for _ in ()).throw(
        RuntimeError("401 Unauthorized token=SECRETVALUE")))
    a = OD.account()
    assert a["state"] == "unavailable"
    assert a["buying_power"] is None and a["positions"] == []
    assert a["read_only"] is True


def test_account_handles_a_disconnected_view(monkeypatch):
    import robinhood_view as rv
    monkeypatch.setattr(rv, "accounts", lambda force=False: {
        "status": {"connected": False}, "reason": "auth required", "cash": {}, "agentic": {}})
    a = OD.account()
    assert a["state"] == "unavailable" and a["read_only"] is True


def test_rh_chain_surfaces_a_provider_error(monkeypatch):
    import robinhood_mcp as rh
    monkeypatch.setattr(rh, "call_tool", lambda n, a=None: (_ for _ in ()).throw(
        rh.RobinhoodMCPError("forbidden", "not on the read-only allowlist")))
    R.invalidate("rhexp:")
    out = OD.rh_expirations("ZZZZ")
    assert out["state"] == "error"


def test_yahoo_chain_declares_its_missing_capabilities(monkeypatch):
    import tradingview_mcp.core.services.options_service as osvc
    monkeypatch.setattr(osvc, "get_options_chain", lambda s, e=None: {
        "symbol": s, "underlying_price": 63.0, "requested_expiry": "2026-08-21",
        "available_expiries": ["2026-08-21"],
        "calls": [{"contract_symbol": "X", "strike": 63.0, "bid": 1.2, "ask": 1.3,
                   "last_price": 1.25, "volume": 10, "open_interest": 500,
                   "implied_volatility": 0.2, "expiration": "2026-08-21"}], "puts": []})
    R.invalidate("yopt:")
    ch = OD.yahoo_chain("BAC", "2026-08-21", "call")
    assert ch["state"] == "ok"
    assert set(ch["missing_capabilities"]) == {"greeks", "quote_timestamp", "mark"}
    assert ch["contracts"][0]["delta"] is None
    assert ch["contracts"][0]["quote_timestamp"] is None


# ══════════════════════ Tracker ══════════════════════

@pytest.fixture()
def tracker(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKER_DB", str(tmp_path / "t.db"))
    import importlib
    import tracker as T
    importlib.reload(T)
    T.init_db()
    return T


def _tracked_candidate(sym="BAC", price=63.25):
    return {"symbol": sym, "name": sym, "primary_setup": "sector_rotation",
            "sector": "financials", "sector_name": "Financials",
            "indicators": {"price": price, "source_timestamp": "2026-08-05T20:00:00+00:00"},
            "levels": {"state": "ok", "entry_zone": [62.99, 63.62], "invalidation": 61.68,
                       "target_1": 66.15, "target_2": 68.73, "risk_per_share": 1.57},
            "score": {"total": 76.4},
            "setups": [{"type": "sector_rotation", "horizon": "days to weeks"}],
            "quote": {"source_timestamp": "2026-08-05T20:00:00+00:00"}}


def test_add_setup_is_idempotent_per_symbol_and_day(tracker):
    a = tracker.add_setup(_tracked_candidate())
    assert a["state"] == "added"
    b = tracker.add_setup(_tracked_candidate())
    assert b["state"] == "exists" and b["setup_id"] == a["setup_id"]
    assert tracker.list_setups()["count"] == 1


def test_add_setup_refuses_a_candidate_without_levels(tracker):
    c = _tracked_candidate()
    c["levels"] = {"state": "unavailable"}
    assert tracker.add_setup(c)["state"] == "rejected"


def test_status_transitions_record_a_reason(tracker, monkeypatch):
    tracker.add_setup(_tracked_candidate())
    monkeypatch.setattr(tracker._R, "quotes", lambda syms, **kw: {
        "BAC": {"state": "ok", "price": 63.25,
                "source_timestamp": datetime.now(timezone.utc).isoformat()}})
    out = tracker.update_all()
    assert out["updated"] == 1
    s = tracker.list_setups()["setups"][0]
    assert s["status"] == "entry_triggered"
    change = [e for e in s["timeline"] if e["kind"] == "status_change"][-1]
    assert change["from_status"] == "watch" and change["to_status"] == "entry_triggered"
    assert "entry zone" in change["reason"]


def test_invalidation_and_targets_transition(tracker, monkeypatch):
    tracker.add_setup(_tracked_candidate())
    monkeypatch.setattr(tracker._R, "quotes", lambda syms, **kw: {
        "BAC": {"state": "ok", "price": 60.0,
                "source_timestamp": datetime.now(timezone.utc).isoformat()}})
    tracker.update_all()
    assert tracker.list_setups(include_closed=True)["setups"][0]["status"] == "invalidated"

    tracker.add_setup(_tracked_candidate(sym="XYZ"))
    monkeypatch.setattr(tracker._R, "quotes", lambda syms, **kw: {
        "XYZ": {"state": "ok", "price": 70.0,
                "source_timestamp": datetime.now(timezone.utc).isoformat()},
        "BAC": {"state": "ok", "price": 60.0,
                "source_timestamp": datetime.now(timezone.utc).isoformat()}})
    tracker.update_all()
    xyz = [s for s in tracker.list_setups(include_closed=True)["setups"] if s["symbol"] == "XYZ"][0]
    assert xyz["status"] == "target_2_reached"


def test_duplicate_alerts_are_suppressed_within_the_cooldown(tracker, monkeypatch):
    tracker.add_setup(_tracked_candidate())
    monkeypatch.setattr(tracker._R, "quotes", lambda syms, **kw: {
        "BAC": {"state": "ok", "price": 63.25,
                "source_timestamp": datetime.now(timezone.utc).isoformat()}})
    first = tracker.update_all()
    second = tracker.update_all()
    assert first["alerts"] == 1
    assert second["alerts"] == 0
    assert len(tracker.recent_alerts()["alerts"]) == 1


def test_cooldown_expiry_allows_the_alert_again(tracker, monkeypatch):
    monkeypatch.setenv("TRACK_ALERT_COOLDOWN_S", "0")
    tracker.add_setup(_tracked_candidate())
    monkeypatch.setattr(tracker._R, "quotes", lambda syms, **kw: {
        "BAC": {"state": "ok", "price": 63.25,
                "source_timestamp": datetime.now(timezone.utc).isoformat()}})
    tracker.update_all()
    tracker.close_setup(tracker.list_setups(include_closed=True)["setups"][0]["setup_id"])
    # a fresh setup with a zero cooldown alerts again
    tracker.add_setup(_tracked_candidate(sym="AAA"))
    monkeypatch.setattr(tracker._R, "quotes", lambda syms, **kw: {
        "AAA": {"state": "ok", "price": 63.25,
                "source_timestamp": datetime.now(timezone.utc).isoformat()}})
    assert tracker.update_all()["alerts"] == 1


def test_stale_and_failed_quotes_pause_tracking(tracker, monkeypatch):
    tracker.add_setup(_tracked_candidate())
    monkeypatch.setattr(tracker._R, "quotes", lambda syms, **kw: {
        "BAC": {"state": "error", "price": None, "reason": "provider timeout"}})
    out = tracker.update_all()
    assert tracker.list_setups()["setups"][0]["status"] == "data_unavailable"
    assert out["alerts"] == 1
    assert tracker.recent_alerts()["alerts"][0]["alert_type"] == "data_source_failure"


def test_stale_quote_does_not_drive_a_status_change(tracker, monkeypatch):
    tracker.add_setup(_tracked_candidate())
    old = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
    monkeypatch.setattr(tracker._R, "quotes", lambda syms, **kw: {
        "BAC": {"state": "ok", "price": 40.0, "source_timestamp": old}})
    tracker.update_all()
    s = tracker.list_setups()["setups"][0]
    assert s["status"] == "data_unavailable"      # NOT 'invalidated' off a 4-day-old print


def test_tracker_has_no_order_path(tracker):
    src = open(os.path.join(_ROOT, "dashboard", "tracker.py"), encoding="utf-8").read().lower()
    for forbidden in ("place_order", "submit_order", "buy(", "sell(", "execute_trade"):
        assert forbidden not in src
    assert tracker.stats()["order_placement"] == "disabled"
    assert tracker.update_all()["order_placement"].startswith("disabled")


def test_close_setup_is_terminal(tracker):
    sid = tracker.add_setup(_tracked_candidate())["setup_id"]
    assert tracker.close_setup(sid)["status"] == "manually_closed"
    assert tracker.list_setups()["count"] == 0
    assert tracker.close_setup("nope")["state"] == "not_found"


def test_schedule_is_market_calendar_aware(tracker):
    # a Saturday: the day itself is reported as skipped with the reason
    sat = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    nr = tracker.next_runs(sat)
    assert any(u.get("skipped") and "weekend" in u["reason"] for u in nr["upcoming"])
    assert all(u["run"] in dict((n, d) for n, _t, d in tracker.CADENCE)
               for u in nr["upcoming"] if u.get("run"))
    # Christmas: the holiday name is given
    xmas = datetime(2026, 12, 25, 12, 0, tzinfo=timezone.utc)
    nr2 = tracker.next_runs(xmas)
    assert any(u.get("skipped") and "Christmas" in u["reason"] for u in nr2["upcoming"])
    assert nr2["order_placement"].startswith("disabled")


def test_all_statuses_and_alert_types_are_declared(tracker):
    assert set(tracker.TERMINAL) <= set(tracker.STATUSES)
    for s in ("scanning", "watch", "approaching_entry", "entry_triggered", "invalidated",
              "target_1_reached", "target_2_reached", "expired", "data_unavailable",
              "manually_closed"):
        assert s in tracker.STATUSES


# ══════════════════════ Dashboard API responses ══════════════════════

@pytest.fixture()
def client():
    sys.path.insert(0, os.path.join(_ROOT, "dashboard"))
    import app as A
    A.app.config["TESTING"] = True
    return A.app.test_client()


def test_session_endpoint_is_always_available(client):
    r = client.get("/api/session")
    assert r.status_code == 200
    d = r.get_json()
    assert d["state"] in ("open", "premarket", "afterhours", "closed", "holiday")
    assert d["now_et"] and d["now_ist"] and d["next_session"]["date"]


def test_regime_endpoint_returns_session_even_while_loading(client):
    r = client.get("/api/regime")
    assert r.status_code == 200
    assert r.get_json()["session"]["state"]


def test_scan_endpoint_is_non_blocking_by_default(client):
    r = client.get("/api/scan?preset=liquid")
    assert r.status_code == 200
    d = r.get_json()
    assert d["state"] in ("ok", "loading")


def test_scan_presets_report_the_scanner_limit(client):
    d = client.get("/api/scan/presets").get_json()
    assert d["presets"]
    lim = SC.config()["universe_limit"]
    assert any(p.get("limit") == lim for p in d["presets"] if "limit" in p)


def test_options_desk_requires_a_symbol(client):
    assert client.get("/api/options/desk").status_code == 400
    r = client.get("/api/options/desk?symbol=ZZZZNOTREAL")
    assert r.status_code in (404, 200)


def test_tracker_endpoints_respond(client):
    assert client.get("/api/tracker").status_code == 200
    assert client.get("/api/tracker/alerts").status_code == 200
    sch = client.get("/api/tracker/schedule").get_json()
    assert sch["order_placement"].startswith("disabled")


def test_rejected_endpoint_groups_by_stage(client):
    d = client.get("/api/scan/rejected?preset=liquid").get_json()
    assert "by_stage" in d and "rejected" in d


def test_tracker_close_requires_a_setup_id(client):
    assert client.post("/api/tracker/close", json={}).status_code == 400


# ══════════════════════ Safety ══════════════════════

def test_no_module_can_reach_a_live_order_endpoint():
    """The whole research terminal must be incapable of placing an order."""
    for name in ("scanner.py", "options_desk.py", "tracker.py", "market_regime.py"):
        src = open(os.path.join(_ROOT, "dashboard", name), encoding="utf-8").read()
        for forbidden in ("place_order", "submit_order", "create_order", "exercise_option",
                          "cancel_order", "ROBINHOOD_TRADING_ENABLED=true"):
            assert forbidden not in src, f"{name} references {forbidden}"


def test_robinhood_write_tools_stay_blocked_while_trading_is_disabled():
    import robinhood_mcp as rh
    assert rh.trading_enabled() is False
    for tool in ("place_option_order", "place_equity_order", "cancel_order", "exercise_option"):
        assert tool not in rh._READ_ALLOWLIST
        with pytest.raises(rh.RobinhoodMCPError):
            rh.call_tool(tool, {})


def test_options_desk_only_calls_allowlisted_robinhood_tools():
    src = open(os.path.join(_ROOT, "dashboard", "options_desk.py"), encoding="utf-8").read()
    import re
    import robinhood_mcp as rh
    called = set(re.findall(r'call_tool\(\s*"([a-z_]+)"', src))
    assert called, "expected the desk to call Robinhood read tools"
    assert called <= set(rh._READ_ALLOWLIST)
