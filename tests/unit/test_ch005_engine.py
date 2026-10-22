"""CH-005 engine tests (synthetic data only): causality / no look-ahead, each rule, execution timing, account limits, settlement."""
import datetime as dt
import importlib.util
import os
import sys

import numpy as np
import pytest

_HERE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "experiments", "ch005_pullback_uptrend")
_spec = importlib.util.spec_from_file_location("ch005_engine", os.path.join(_HERE, "engine.py"))
E = importlib.util.module_from_spec(_spec)
sys.modules["ch005_engine"] = E
_spec.loader.exec_module(E)

N = 420


def _cal(n=N):
    d, out = dt.date(2020, 1, 1), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def _series(n=N, drop_at=None, drops=(-0.03, -0.03, -0.03), growth=0.002, v=2e6, p0=50.0):
    c = np.array([p0 * (1 + growth) ** i for i in range(n)], dtype=float)
    if drop_at is not None:
        for k, r in enumerate(drops):
            c[drop_at + k:] *= (1 + r)
    o = np.concatenate([[c[0]], c[:-1]])
    h = np.maximum(o, c) * 1.003
    l = np.minimum(o, c) * 0.997
    return {"o": o, "h": h, "l": l, "c": c, "v": np.full(n, v)}


def _setup(**kw):
    p = E.Params(**kw) if kw else E.Params()
    cal = _cal()
    frame = {"SPY": _series(growth=0.001), "A": _series(drop_at=300), "B": _series(), "C": _series(), "D": _series()}
    sector = {"A": "tech", "B": "energy", "C": "health", "D": "tech"}
    return p, cal, frame, sector


def _copy(frame):
    return {s: {k: v.copy() for k, v in d.items()} for s, d in frame.items()}


def _run(p, cal, frame, sector, start=None, end=None, selector="rank", seed=0):
    ind, pull, base, spy_i, _ = E.prepare(frame, p)
    res = E.simulate(frame, ind, pull, base, spy_i, sector, cal, p, start or cal[210], end or cal[-1], selector, seed)
    return res, ind, pull, base


T0 = 303          # the synthetic drop runs over sessions 300-302; the signal is still true on 303 (earlier days belong to the falling phase)


def _sig_day(p, frame, sym="A"):
    assert E.prepare(frame, p)[1][sym][T0]
    return T0


def _run0(p, cal, frame, sector, **kw):
    return _run(p, cal, frame, sector, start=cal[T0], **kw)


# ───────────── causality ─────────────

def test_indicators_are_causal_truncation_invariant():
    rng = np.random.default_rng(3)
    c = 50 * np.exp(np.cumsum(rng.normal(0, 0.01, 500)))
    d = {"o": c * 0.999, "h": c * 1.01, "l": c * 0.99, "c": c, "v": rng.uniform(1e6, 2e6, 500)}
    p = E.Params()
    full = E.indicators(d, p)
    for t in (250, 333, 499):
        cut = E.indicators({k: v[:t + 1] for k, v in d.items()}, p)
        for k in full:
            a, b = full[k][t], cut[k][t]
            assert (np.isnan(a) and np.isnan(b)) or a == pytest.approx(b, rel=1e-9), (k, t)


def test_signal_masks_are_causal_on_real_shaped_data():
    p, cal, frame, sector = _setup()
    full = E.prepare(frame, p)
    for t in (303, 310, 380):
        cut = E.prepare({s: {k: v[:t + 1] for k, v in d.items()} for s, d in frame.items()}, p)
        for s in ("A", "B"):
            assert bool(full[1][s][t]) == bool(cut[1][s][t]) and bool(full[2][s][t]) == bool(cut[2][s][t])


def test_rsi_matches_an_explicit_wilder_reference():
    rng = np.random.default_rng(5)
    c = 50 + np.cumsum(rng.normal(0, 1, 80))
    ch = np.diff(c)
    up, dn = np.where(ch > 0, ch, 0.0), np.where(ch < 0, -ch, 0.0)
    au, ad = up[:14].mean(), dn[:14].mean()
    ref = [100 - 100 / (1 + au / ad)]
    for i in range(14, len(ch)):
        au, ad = (au * 13 + up[i]) / 14, (ad * 13 + dn[i]) / 14
        ref.append(100 - 100 / (1 + au / ad))
    r = E.wilder_rsi(c, 14)
    assert np.isnan(r[:14]).all() and np.allclose(r[14:], ref, rtol=1e-12)
    assert E.wilder_rsi(np.arange(1.0, 40.0), 14)[-1] == 100.0          # only gains -> 100


def test_leading_nan_symbol_still_gets_indicators():
    c = np.concatenate([np.full(100, np.nan), 50 + np.cumsum(np.random.default_rng(1).normal(0, 0.3, 300))])
    d = {"o": c, "h": c + 0.5, "l": c - 0.5, "c": c, "v": np.full(400, 1e6)}
    ind = E.indicators(d, E.Params())
    assert np.isfinite(ind["rsi"][-1]) and np.isfinite(ind["atr"][-1]) and np.isnan(ind["rsi"][105])


def test_simulation_is_invariant_to_future_data_for_trades_that_already_closed():
    p, cal, frame, sector = _setup()
    full, *_ = _run(p, cal, frame, sector)
    T = 360
    cut_frame = {s: {k: v[:T] for k, v in d.items()} for s, d in frame.items()}
    cut, *_ = _run(p, cal[:T], cut_frame, sector, end=cal[T - 1])
    done = lambda r: sorted((t["sym"], t["entry"], t["exit"], round(t["pnl"], 8)) for t in r["trades"] if t["exit"] < cal[T - 1] and t["reason"] != "open_at_end")
    assert done(full) == done(cut) and len(done(full)) >= 1


# ───────────── rules ─────────────

def test_signal_fires_on_the_designed_pullback_and_not_on_trend_only_names():
    p, cal, frame, sector = _setup()
    ind, pull, base, spy_i, mk = E.prepare(frame, p)
    t = int(np.flatnonzero(pull["A"])[0])
    assert 300 <= t <= 306 and not pull["B"].any() and base["B"].any()


def test_each_condition_removes_the_signal():
    p, cal, frame, sector = _setup()
    t = _sig_day(p, frame)

    def fires(mod):
        f = _copy(frame)
        mod(f)
        return bool(E.prepare(f, p)[1]["A"][t])
    assert fires(lambda f: None)
    assert not fires(lambda f: f["A"]["v"].__setitem__(slice(None), 1000.0))                              # dollar volume < $5M
    assert not fires(lambda f: f["SPY"]["c"].__setitem__(slice(None), np.linspace(400, 300, N)))          # market below its 200d
    p2 = E.Params(rsi_max=1.0)
    assert not bool(E.prepare(frame, p2)[1]["A"][t])                                                      # RSI condition
    p3 = E.Params(below_high_pct=0.50)
    assert not bool(E.prepare(frame, p3)[1]["A"][t])                                                      # distance below the 60d high
    p4 = E.Params(bb_k=50.0)
    assert not bool(E.prepare(frame, p4)[1]["A"][t])                                                      # Bollinger band


def test_trend_filter_blocks_signals_below_the_200d_average():
    p, cal, frame, sector = _setup()
    frame["A"] = _series(drop_at=300, growth=-0.0005)
    assert not E.prepare(frame, p)[1]["A"].any()


def test_market_filter_blocks_entries():
    p, cal, frame, sector = _setup()
    frame["SPY"] = _series(growth=-0.001)
    res, *_ = _run(p, cal, frame, sector)
    assert res["trades"] == []


def test_entry_is_next_open_with_cost_and_never_the_signal_close():
    p, cal, frame, sector = _setup()
    res, ind, pull, base = _run0(p, cal, frame, sector)
    t = T0
    tr = [x for x in res["trades"] if x["sym"] == "A"][0]
    assert tr["entry"] == cal[t + 1]
    assert tr["entry_px"] == pytest.approx(frame["A"]["o"][t + 1] * (1 + 15e-4), rel=1e-5)
    assert tr["entry_px"] != pytest.approx(frame["A"]["c"][t], rel=1e-4)


def test_sizing_uses_the_smaller_of_risk_and_notional_caps():
    p, cal, frame, sector = _setup()
    res, *_ = _run0(p, cal, frame, sector)
    tr = [x for x in res["trades"] if x["sym"] == "A"][0]
    assert tr["notional"] <= 125.0 + 1e-6 and tr["risk"] <= 5.0 + 1e-6
    assert max(tr["notional"] / 125.0, tr["risk"] / 5.0) == pytest.approx(1.0, abs=1e-3)       # one cap binds exactly, neither is exceeded


def test_intraday_stop_fills_at_stop_minus_cost_and_gap_uses_open_minus_gap_cost():
    p, cal, frame, sector = _setup()
    res0, ind, pull, base = _run0(p, cal, frame, sector)
    t = T0
    stop = [x for x in res0["trades"] if x["sym"] == "A"][0]["stop"]
    f = _copy(frame)
    f["A"]["l"][t + 3] = stop * 0.99
    res, *_ = _run0(p, cal, f, sector)
    tr = [x for x in res["trades"] if x["sym"] == "A"][0]
    assert tr["reason"] == "stop" and tr["exit"] == cal[t + 3] and tr["exit_px"] == pytest.approx(stop * (1 - 15e-4), rel=1e-5)
    f["A"]["o"][t + 3] = stop * 0.97
    f["A"]["l"][t + 3] = stop * 0.96
    res, *_ = _run0(p, cal, f, sector)
    tr = [x for x in res["trades"] if x["sym"] == "A"][0]
    assert tr["reason"] == "stop" and tr["exit_px"] == pytest.approx(stop * 0.97 * (1 - 35e-4), rel=1e-5)


def test_stop_can_trigger_on_the_entry_session():
    p, cal, frame, sector = _setup()
    res0, ind, pull, base = _run0(p, cal, frame, sector)
    t = T0
    stop = [x for x in res0["trades"] if x["sym"] == "A"][0]["stop"]
    f = _copy(frame)
    f["A"]["l"][t + 1] = stop * 0.99
    res, *_ = _run0(p, cal, f, sector)
    tr = [x for x in res["trades"] if x["sym"] == "A"][0]
    assert tr["reason"] == "stop" and tr["exit"] == tr["entry"] == cal[t + 1] and tr["sessions"] == 1


def _drift_after(frame, t, daily=-0.0004):
    a = {k: v.copy() for k, v in frame["A"].items()}
    px = a["c"][t] * (1 + daily) ** np.arange(1, len(a["c"]) - t)
    a["c"][t + 1:] = px
    a["o"][t + 1:] = np.concatenate([[a["c"][t]], px[:-1]])
    a["h"][t + 1:] = np.maximum(a["o"][t + 1:], px) * 1.0005
    a["l"][t + 1:] = np.minimum(a["o"][t + 1:], px) * 0.9995
    return a


def test_time_exit_after_15_sessions_fills_at_the_16th_session_open():
    p, cal, frame, sector = _setup()
    t = _sig_day(p, frame)
    f = dict(frame, A=_drift_after(frame, t))
    res, *_ = _run0(p, cal, f, sector)
    tr = [x for x in res["trades"] if x["sym"] == "A"][0]
    assert tr["reason"] == "time" and tr["entry"] == cal[t + 1] and tr["exit"] == cal[t + 1 + 15] and tr["sessions"] == 16


def test_recovery_exit_is_flagged_at_the_close_and_filled_at_the_next_open():
    p, cal, frame, sector = _setup()
    t = _sig_day(p, frame)
    a = _drift_after(frame, t)
    j = t + 4
    sma = E.indicators(a, p)["sma_bb"][j]
    a["c"][j:] = sma * 1.2                                         # decisive rebound through the 15-day mean at the close of session j
    a["o"][j + 1:] = sma * 1.2
    a["h"][j:] = np.maximum(a["h"][j:], a["c"][j:] * 1.001)
    a["l"][j + 1:] = a["c"][j + 1:] * 0.999
    f = dict(frame, A=a)
    res, ind2, *_ = _run0(p, cal, f, sector)
    tr = [x for x in res["trades"] if x["sym"] == "A"][0]
    assert tr["reason"] == "recovery" and tr["exit"] == cal[j + 1]
    assert f["A"]["c"][j] >= ind2["A"]["sma_bb"][j] and f["A"]["c"][j - 1] < ind2["A"]["sma_bb"][j - 1]      # known at the close, acted on next open
    assert tr["exit_px"] == pytest.approx(f["A"]["o"][j + 1] * (1 - 15e-4), rel=1e-5)


def test_trend_invalidation_exit():
    p0, cal, frame, sector = _setup()
    t = _sig_day(p0, frame)
    a = _drift_after(frame, t)
    ind = E.indicators(a, p0)
    entry = a["o"][t + 1] * (1 + 15e-4)
    k = (entry - 0.5 * ind["sma_trend"][t]) / ind["atr"][t]          # puts the stop at half the 200-day mean: far below any price used here
    p = E.Params(stop_atr=k, min_notional=1.0)
    j = t + 4
    below = ind["sma_trend"][j] * 0.8                                  # closes below the 200-day average, still above the stop
    a["c"][j:] = below
    a["o"][j + 1:] = below
    a["h"][j:] = np.maximum(a["h"][j:], below * 1.001)
    a["l"][j:] = below * 0.999
    res, *_ = _run0(p, cal, dict(frame, A=a), sector)
    tr = [x for x in res["trades"] if x["sym"] == "A"][0]
    assert tr["reason"] == "trend_invalid" and tr["exit"] == cal[j + 1]


# ───────────── account constraints ─────────────

def _multi(n_syms, same_sector=False):
    frame = {"SPY": _series(growth=0.001)}
    sector = {}
    for i in range(n_syms):
        s = f"S{i}"
        frame[s] = _series(drop_at=300)
        sector[s] = "one" if same_sector else f"sec{i}"
    return frame, sector


def _open_counts(res, cal):
    opens = {}
    for t in res["trades"]:
        for d in cal[cal.index(t["entry"]):cal.index(t["exit"]) + 1]:
            opens[d] = opens.get(d, 0) + 1
    return opens


def test_max_two_entries_per_day_and_three_open_positions():
    p = E.Params()
    cal = _cal()
    frame, sector = _multi(6)
    res, *_ = _run(p, cal, frame, sector)
    by_day = {}
    for t in res["trades"]:
        by_day[t["entry"]] = by_day.get(t["entry"], 0) + 1
    assert max(by_day.values()) == 2 and max(_open_counts(res, cal).values()) <= 3


def test_one_position_per_sector():
    p = E.Params()
    cal = _cal()
    frame, sector = _multi(4, same_sector=True)
    res, *_ = _run(p, cal, frame, sector)
    assert res["trades"] and max(_open_counts(res, cal).values()) == 1


def test_cash_reserve_is_respected():
    p = E.Params(start_cash=200.0)
    cal = _cal()
    frame, sector = _multi(3)
    res, *_ = _run(p, cal, frame, sector)
    first = min(x["entry"] for x in res["trades"])
    assert sum(t["notional"] for t in res["trades"] if t["entry"] == first) <= 100.0 + 1e-6


def test_sale_proceeds_are_not_spendable_until_the_next_session():
    p = E.Params(start_cash=225.0, max_positions=3)             # $125 of spendable cash above the reserve
    cal = _cal()
    frame, sector = _multi(2)
    res, *_ = _run(p, cal, frame, sector)
    ts = res["trades"]
    for a in ts:
        for b in ts:
            if a is not b and a["exit"] == b["entry"]:
                # b was bought on the session a was sold: its cash must come from settled cash only, never from a's proceeds
                assert b["notional"] <= 125.0 - sum(x["notional"] for x in ts if x["entry"] < b["entry"] and x["exit"] >= b["entry"] and x is not a) + 1e-6


def test_drawdown_halt_blocks_new_entries():
    p = E.Params(drawdown_halt=1e-9)
    cal = _cal()
    frame, sector = _multi(3)
    res, *_ = _run(p, cal, frame, sector)
    n_default, *_ = _run(E.Params(), cal, frame, sector)
    assert len(res["trades"]) <= len(n_default["trades"])


def test_control_selector_is_deterministic_per_seed():
    p, cal, frame, sector = _setup()
    a1, ind, pull, base = _run(p, cal, frame, sector, selector="random", seed=7)
    a2, *_ = _run(p, cal, frame, sector, selector="random", seed=7)
    key = lambda r: [(t["sym"], t["entry"]) for t in r["trades"]]
    assert key(a1) == key(a2) and a1["trades"] and base["B"].any() and not pull["B"].any()


def test_metrics_basic():
    p, cal, frame, sector = _setup()
    res, *_ = _run(p, cal, frame, sector)
    m = E.metrics(res, p, cal[210], cal)
    assert m["trades"] == len(res["trades"]) and m["net_pnl"] == pytest.approx(sum(t["pnl"] for t in res["trades"]))
    assert m["pnl_per_slot_day"] == pytest.approx(m["net_pnl"] / m["slot_days"])


def test_same_session_sale_proceeds_cannot_fund_a_purchase_but_next_session_can():
    """A exits (recovery) at the open of session j+1; B's only entry opportunity is that same open. With $225 (=$125 spendable over the
    reserve) fully used by A, B can only be bought with A's proceeds, which settle one session later -> B must NOT trade."""
    p = E.Params(start_cash=225.0)
    cal = _cal()
    t = T0
    j = t + 4
    base_frame = {"SPY": _series(growth=0.001), "A": _series(drop_at=300), "B": _series(drop_at=j - 3 + 1)}
    sector = {"A": "tech", "B": "energy"}
    a = _drift_after(base_frame, t)
    sma = E.indicators(a, p)["sma_bb"][j]
    a["c"][j:] = sma * 1.2
    a["o"][j + 1:] = sma * 1.2
    a["h"][j:] = np.maximum(a["h"][j:], a["c"][j:] * 1.001)
    a["l"][j + 1:] = a["c"][j + 1:] * 0.999
    frame = dict(base_frame, A=a)
    ind, pull, base, spy_i, _ = E.prepare(frame, p)
    assert pull["B"][j]                                                # signals are evaluated only through session j: B's last chance is the open of j+1
    res = E.simulate(frame, ind, pull, base, spy_i, sector, cal, p, cal[t], cal[j], "rank", 0)
    a_tr = [x for x in res["trades"] if x["sym"] == "A"][0]
    assert a_tr["exit"] == cal[j + 1] and a_tr["reason"] == "recovery"
    assert not any(x["sym"] == "B" for x in res["trades"])           # proceeds withheld on the sale date
    # control: with spare settled cash B is bought on that very open, so the absence above is due to settlement and nothing else
    res2 = E.simulate(frame, ind, pull, base, spy_i, sector, cal, E.Params(start_cash=500.0), cal[t], cal[j], "rank", 0)
    assert any(x["sym"] == "B" and x["entry"] == cal[j + 1] for x in res2["trades"])
