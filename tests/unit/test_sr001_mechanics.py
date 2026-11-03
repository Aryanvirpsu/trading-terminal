"""SR-001 mechanics on synthetic fixtures. Outcome-blind: no market data, no ledger, no network."""
import datetime as dt
import importlib.util
import pathlib
import sys
from zoneinfo import ZoneInfo

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments" / "sr001_slot_rotation"))
import sim  # noqa: E402
from sim import Cand, Cfg, Cycle, Quote, Session, run  # noqa: E402

ET = ZoneInfo("America/New_York")
CFG = Cfg()


def _calendar_module():
    spec = importlib.util.spec_from_file_location("market_calendar", ROOT / "lab" / "paper" / "market_calendar.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def make_sessions(n=40, start=dt.date(2026, 11, 4)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            o = dt.datetime.combine(d, dt.time(9, 30), ET)
            out.append(Session(d, o, o.replace(hour=15, minute=0), o.replace(hour=15, minute=55), o.replace(hour=16, minute=0)))
        d += dt.timedelta(days=1)
    return out


S = make_sessions()


def at(si, hh, mm):
    return S[si].open.replace(hour=hh, minute=mm)


def q(ts, bid, ask=None, ok=True):
    return Quote(bid, ask if ask is not None else bid + 0.1, ts - dt.timedelta(seconds=5), ok)


def cyc(si, hh, mm, cands=(), quotes=None, exits=()):
    ts = at(si, hh, mm)
    return Cycle(ts, si, tuple(cands), {k: q(ts, *v) for k, v in (quotes or {}).items()}, frozenset(exits))


T0 = at(0, 10, 0)
# three incumbents entered in session 0 at $100; cash $136.21 (as in the live book on 2026-11-02)
SEED = [("AAA", 1.0, 100.0, 0, T0), ("BBB", 1.0, 100.0, 0, T0 + dt.timedelta(minutes=1)), ("CCC", 1.0, 100.0, 0, T0 + dt.timedelta(minutes=2))]
INC_Q = {"AAA": (99.0,), "BBB": (101.0,), "CCC": (101.0,)}   # only AAA has bid <= entry cost
NEW = Cand("NEW", 1, True, stop=47.0)
AGE_OK = 11   # session index at which the 10-completed-sessions rule is first met


def day_with_rotation(extra=None):
    quotes = {**INC_Q, "NEW": (50.0, 50.0)}
    return [cyc(AGE_OK, 10, 0, [NEW], quotes), cyc(AGE_OK, 10, 15, [NEW], quotes)]


def run_both(cycles, cash=136.21, seed=SEED):
    return (run(cycles, CFG, S, rotate=False, cash=cash, seed=seed), run(cycles, CFG, S, rotate=True, cash=cash, seed=seed))


# ---- 1. waits for settled cash, then enters -------------------------------------------------------------------
def test_waits_for_settled_cash_then_enters_next_session():
    quotes = {**INC_Q, "NEW": (50.0, 50.0)}
    cycles = day_with_rotation() + [cyc(AGE_OK, 10, 30, [NEW], quotes), cyc(AGE_OK, 14, 0, [NEW], quotes),
                                    cyc(AGE_OK + 1, 9, 35, [NEW], quotes)]
    ctl, ch = run_both(cycles)
    kinds = [e[0] for e in ch.events]
    assert kinds.count("rotation_submitted") == 1 and kinds.count("sell") == 1
    buys = [e for e in ch.events if e[0] == "buy"]
    assert len(buys) == 1
    # the buy happens only after the T+1 open, never in the sale session
    assert dt.datetime.fromisoformat(buys[0][1]) >= S[AGE_OK + 1].open
    waits = [e for e in ch.events if e[0] == "reservation_wait"]
    assert waits and all(dict(w[2])["why"] == "awaiting_settled_cash" for w in waits)
    assert "NEW" in ch.pos and "AAA" not in ch.pos
    assert ch.cash >= CFG.reserve - 1e-9
    assert not [e for e in ctl.events if e[0] != "sell"] and len(ctl.pos) == 3   # control untouched


def test_reserved_entry_never_in_the_cycle_of_the_exit():
    rich = 1000.0   # cash would be enough on its own
    quotes = {**INC_Q, "NEW": (50.0, 50.0)}
    ch = run(day_with_rotation(), CFG, S, rotate=True, cash=rich, seed=SEED)
    sell_ts = [e[1] for e in ch.events if e[0] == "sell"]
    buy_ts = [e[1] for e in ch.events if e[0] == "buy"]
    # with ample settled cash the replacement still needs a cycle strictly after the sale
    assert sell_ts and not buy_ts


# ---- 2. $100 reserve -------------------------------------------------------------------------------------------
def test_no_rotation_when_projection_breaches_reserve():
    # needs 136.21 + 98.85 - 81.4 = 153.6 >= 100 normally; with cash 45 the projection is 62.4 < 100 -> no exit submitted
    ch = run(day_with_rotation(), CFG, S, rotate=True, cash=45.0, seed=SEED)
    kinds = [e[0] for e in ch.events]
    assert "rotation_submitted" not in kinds and "rotation_cash_short" in kinds
    assert len(ch.pos) == 3


def test_reserve_never_breached_after_entry():
    quotes = {**INC_Q, "NEW": (50.0, 50.0)}
    cycles = day_with_rotation() + [cyc(AGE_OK + 1, 9, 35, [NEW], quotes), cyc(AGE_OK + 1, 9, 50, [NEW], quotes)]
    ch = run(cycles, CFG, S, rotate=True, cash=136.21, seed=SEED)
    assert ch.cash >= CFG.reserve - 1e-9


# ---- 3. exit without replacement (orphan) ---------------------------------------------------------------------
def test_candidate_gone_leaves_orphan_exit_and_vacancy_is_not_hand_picked():
    quotes = {**INC_Q, "NEW": (50.0, 50.0), "OTHER": (30.0, 30.0)}
    gone = Cand("NEW", 1, False, stop=47.0)           # fails revalidation next session
    other = Cand("OTHER", 2, True, stop=28.0)
    cycles = day_with_rotation() + [
        cyc(AGE_OK + 1, 9, 35, [gone, other], quotes),    # reservation live: OTHER must NOT take the slot
        cyc(AGE_OK + 1, 14, 0, [gone, other], quotes),
        cyc(AGE_OK + 2, 9, 35, [gone, other], quotes),    # reservation expired -> normal Champion rules resume
    ]
    ch = run(cycles, CFG, S, rotate=True, cash=136.21, seed=SEED)
    ev = ch.events
    assert [e for e in ev if e[0] == "orphan_exit"], "orphan exit must be logged"
    assert [e for e in ev if e[0] == "sell" and dict(e[2])["why"] == "rotation"], "exit stays in the challenger record"
    buys = [(e[1], dict(e[2])["symbol"]) for e in ev if e[0] == "buy"]
    assert all(sym != "NEW" for _, sym in buys)
    assert all(dt.datetime.fromisoformat(t) >= S[AGE_OK + 2].open for t, _ in buys), "no buy into the reserved slot"
    assert ch.attempts.get(AGE_OK) == 1


def test_exit_unfilled_is_cancelled_and_counted():
    quotes = {**INC_Q, "NEW": (50.0, 50.0)}
    stale = {**quotes, "AAA": (99.0, 99.1)}
    cycles = [cyc(AGE_OK, 10, 0, [NEW], quotes),
              Cycle(at(AGE_OK, 10, 30), AGE_OK, (NEW,), {**{k: q(at(AGE_OK, 10, 30), *v) for k, v in quotes.items()},
                    "AAA": Quote(99.0, 99.1, at(AGE_OK, 10, 30) - dt.timedelta(minutes=10))}),   # stale
              cyc(AGE_OK + 1, 9, 35, [], quotes)]          # next session: no candidate, so no new attempt
    ch = run(cycles, CFG, S, rotate=True, cash=136.21, seed=SEED)
    assert ch.exit_failures == 1 and "AAA" in ch.pos and ch.reservation is None
    assert not [e for e in ch.events if e[0] == "sell"]
    assert [e[0] for e in ch.events].count("rotation_exit_cancelled") == 1 and ch.attempts == {AGE_OK: 1}


# ---- 4. identical accounts when no rotation occurs -----------------------------------------------------------
@pytest.mark.parametrize("name,cycles", [
    ("incumbents_too_young", lambda: [cyc(AGE_OK - 1, 10, 0, [NEW], {**INC_Q, "NEW": (50.0, 50.0)})]),
    ("no_eligible_candidate", lambda: [cyc(AGE_OK, 10, 0, [Cand("NEW", 1, False, 47.0)], {**INC_Q, "NEW": (50.0, 50.0)})]),
    ("incumbent_above_entry_cost", lambda: [cyc(AGE_OK, 10, 0, [NEW], {"AAA": (101.0,), "BBB": (101.0,), "CCC": (101.0,), "NEW": (50.0, 50.0)})]),
    ("after_entry_cutoff", lambda: [cyc(AGE_OK, 15, 30, [NEW], {**INC_Q, "NEW": (50.0, 50.0)})]),
])
def test_identical_when_rotation_never_triggers(name, cycles):
    ctl, ch = run_both(cycles())
    assert ctl.events == ch.events and ctl.pos == ch.pos and ctl.cash == ch.cash and ctl.recv == ch.recv


def test_identical_through_normal_entries_and_ordinary_exits():
    sess = S
    quotes = {"X": (20.0, 20.0), "Y": (40.0, 40.0)}
    cands = [Cand("X", 1, True, 18.0), Cand("Y", 2, True, 36.0)]
    cycles = [cyc(1, 10, 0, cands, quotes), cyc(1, 10, 15, cands, quotes), cyc(3, 11, 0, cands, quotes, exits=["X"]),
              cyc(5, 11, 0, cands, quotes)]
    ctl = run(cycles, CFG, sess, rotate=False, cash=500.0)
    ch = run(cycles, CFG, sess, rotate=True, cash=500.0)
    marks = cyc(6, 10, 0, cands, quotes).quotes
    assert ctl.events == ch.events and ctl.equity(marks) == ch.equity(marks) and [e for e in ctl.events if e[0] == "buy"]


# ---- rule boundaries ------------------------------------------------------------------------------------------
def test_age_boundary_is_ten_completed_sessions_after_fill_session():
    quotes = {**INC_Q, "NEW": (50.0, 50.0)}
    young = run([cyc(AGE_OK - 1, 10, 0, [NEW], quotes)], CFG, S, True, 136.21, SEED)     # 9 completed
    ok = run([cyc(AGE_OK, 10, 0, [NEW], quotes)], CFG, S, True, 136.21, SEED)            # 10 completed
    assert not young.pending_exit and ok.pending_exit and ok.pending_exit["symbol"] == "AAA"


def test_incumbent_selection_earliest_entry_then_ticker():
    seed = [("ZZZ", 1.0, 100.0, 0, T0), ("AAA", 1.0, 100.0, 0, T0), ("MMM", 1.0, 100.0, 0, T0 + dt.timedelta(minutes=5))]
    quotes = {"ZZZ": (99.0,), "AAA": (99.0,), "MMM": (99.0,), "NEW": (50.0, 50.0)}
    ch = run([cyc(AGE_OK, 10, 0, [NEW], quotes)], CFG, S, True, 136.21, seed)
    assert ch.pending_exit["symbol"] == "AAA"            # same timestamp -> ticker ascending


def test_candidate_order_and_ticker_tie_break():
    c1, c2 = Cand("BBB2", 1, True, 47.0), Cand("AAA2", 1, True, 47.0)       # true rank tie
    quotes = {**INC_Q, "BBB2": (50.0, 50.0), "AAA2": (50.0, 50.0)}
    ch = run([cyc(AGE_OK, 10, 0, [c1, c2], quotes)], CFG, S, True, 136.21, SEED)
    assert ch.reservation["symbol"] == "AAA2"


def test_only_first_candidate_is_considered():
    first = Cand("F1", 1, True, 47.0)
    second = Cand("S2", 2, True, 47.0)
    # F1 is unaffordable (cash projection fails) even though S2 would be; SR-001 considers only the first
    quotes = {**INC_Q, "F1": (1000.0, 1000.0), "S2": (50.0, 50.0)}
    f1 = Cand("F1", 1, True, 990.0)
    ch = run([cyc(AGE_OK, 10, 0, [f1, second], quotes)], CFG, S, True, 45.0, SEED)
    assert not ch.pending_exit and ch.reservation is None


def test_one_attempt_per_session_and_daily_limit_counts_normal_fills():
    quotes = {"X": (20.0, 20.0), "Y": (40.0, 40.0), "Z": (10.0, 10.0)}
    cands = [Cand("X", 1, True, 18.0), Cand("Y", 2, True, 36.0), Cand("Z", 3, True, 9.0)]
    ctl = run([cyc(1, 10, 0, cands, quotes)], CFG, S, False, 500.0)
    assert sorted(ctl.pos) == ["X", "Y"] and ctl.entries[1] == 2          # third blocked by the 2/day limit
    quotes2 = {**INC_Q, "NEW": (50.0, 50.0)}
    ch = run([cyc(AGE_OK, 10, 0, [NEW], quotes2), cyc(AGE_OK, 10, 15, [NEW], quotes2)], CFG, S, True, 136.21, SEED)
    assert ch.attempts[AGE_OK] == 1 and [e[0] for e in ch.events].count("rotation_submitted") == 1


# ---- calendar: T+1 across weekends/holidays and the 60-session clock ---------------------------------------------
def test_t_plus_one_on_the_real_exchange_calendar_and_clock():
    mc = _calendar_module()
    days, d = [], dt.date(2026, 11, 3)
    while len(days) < 61:
        d += dt.timedelta(days=1)
        if mc.is_trading_day(d):
            days.append(d)
    assert dt.date(2026, 11, 26) not in days and dt.date(2026, 12, 25) not in days
    assert dt.date(2027, 1, 1) not in days and dt.date(2027, 1, 18) not in days
    assert mc.is_early_close(dt.date(2026, 11, 27)) and mc.is_early_close(dt.date(2026, 12, 24))
    nxt = lambda x: next(y for y in days if y > x)
    assert nxt(dt.date(2026, 11, 25)) == dt.date(2026, 11, 27)      # sale before Thanksgiving settles after it
    assert nxt(dt.date(2026, 11, 27)) == dt.date(2026, 11, 30)      # sale on the early-close Friday settles Monday
    assert days[0] == dt.date(2026, 11, 4) and len(days[:60]) == 60
    print("60th session after 2026-11-03:", days[59])
