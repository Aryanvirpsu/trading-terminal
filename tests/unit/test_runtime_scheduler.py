"""Runtime scheduler: NYSE calendar, ET schedule, singleton, overlap, no-backfill, paper-only guard, health."""
import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for folder in ("src", "dashboard", "lab"):
    sys.path.insert(0, str(ROOT / folder))

from paper import db, runtime as rt
from paper import market_calendar as cal

UTC = dt.timezone.utc


# ── calendar ─────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("d,closed", [
    ("2026-09-07", True),    # Labor Day
    ("2026-11-26", True),    # Thanksgiving
    ("2026-12-25", True),    # Christmas (Friday)
    ("2026-07-03", True),    # July 4 is Saturday -> observed Friday July 3
    ("2027-01-01", True),    # New Year's Day (Friday)
    ("2026-04-03", True),    # Good Friday
    ("2026-06-19", True),    # Juneteenth (Friday)
    ("2026-09-26", True),    # Saturday
    ("2026-09-25", False),   # ordinary Friday
    ("2026-11-27", False),   # day after Thanksgiving (open, early close)
    ("2026-12-24", False),   # Christmas Eve (open, early close)
    ("2027-12-31", False),   # New Year's Day 2028 is a Saturday: NOT observed by NYSE
])
def test_holidays(d, closed):
    assert (not cal.is_trading_day(dt.date.fromisoformat(d))) is closed


def test_early_closes_and_session_times():
    assert cal.session(dt.date(2026, 9, 25)) == (dt.datetime(2026, 9, 25, 9, 30, tzinfo=cal.ET),
                                                 dt.datetime(2026, 9, 25, 16, 0, tzinfo=cal.ET))
    assert cal.is_early_close(dt.date(2026, 11, 27)) and cal.is_early_close(dt.date(2026, 12, 24))
    assert cal.session(dt.date(2026, 11, 27))[1].hour == 13
    assert cal.session(dt.date(2026, 11, 26)) is None
    assert cal.next_trading_day(dt.date(2026, 9, 4)) == dt.date(2026, 9, 8)      # Fri -> skips Labor Day


def test_extra_closed_dates_env(monkeypatch):
    monkeypatch.setenv("AVDI_EXTRA_CLOSED_DATES", "2026-09-25")
    assert not cal.is_trading_day(dt.date(2026, 9, 25))


# ── schedule ─────────────────────────────────────────────────────────────────────────────────────────

def test_regular_day_schedule_et():
    slots = rt.build_slots(dt.date(2026, 9, 25))
    by = {s.id: s for s in slots}
    assert by["prep_health@0830"].kind == "prep_health" and not by["prep_health@0830"].allow_entries
    assert by["prep_scan@0915"].session_type == "premarket_prep" and not by["prep_scan@0915"].allow_entries
    disc = [s for s in slots if s.kind == "discovery"]
    assert disc[0].id == "discovery@0935" and disc[-1].id == "discovery@1550"
    assert [s.when.minute for s in disc[:4]] == [35, 50, 5, 20]                  # every 15 minutes
    assert by["discovery@1450"].allow_entries and not by["discovery@1505"].allow_entries   # entry cutoff 15:00 ET
    assert by["discovery@1505"].session_type == "post_cutoff"
    assert slots[-1].kind == "close" and slots[-1].id == "close@1610"


def test_early_close_and_closed_day_schedule():
    early = rt.build_slots(dt.date(2026, 11, 27))
    disc = [s for s in early if s.kind == "discovery"]
    assert disc[-1].id == "discovery@1250"                                       # close 13:00 - 10 min
    assert [s for s in disc if s.allow_entries][-1].id == "discovery@1150"       # cutoff 12:00
    assert early[-1].id == "close@1310"
    assert rt.build_slots(dt.date(2026, 11, 26)) == [] and rt.build_slots(dt.date(2026, 9, 26)) == []


# ── fake actions + clock ─────────────────────────────────────────────────────────────────────────────

class FakeActions:
    def __init__(self):
        self.calls = []

    def prep_health(self, d):
        self.calls.append(("prep_health", d))
        return {"ok": True}

    def discovery(self, d, cycle_id, allow_entries, session_type):
        self.calls.append(("discovery", cycle_id, allow_entries, session_type))
        return {"state": "ok", "finalists": [1, 2], "evaluated": [{"action": "TRADEABLE"}], "orders_placed": []}

    def tracker(self, d, scope):
        self.calls.append(("tracker", scope))
        return {"symbols_watched": ["AAA"], "exits": 0, "quote_age_s_max": 3.0, "quotes_missing": []}

    def close(self, d):
        self.calls.append(("close", d))
        return {"backup": {"ok": True, "path": "x"}, "ch001": {}}


def et(h, m, s=0, day=25):
    return dt.datetime(2026, 9, day, h, m, s, tzinfo=cal.ET).astimezone(UTC)


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("PAPER_DATA_DIR", str(tmp_path))
    db.reset_for_tests(str(tmp_path))
    yield tmp_path
    db.close()


def make(clock, actions=None):
    return rt.Runtime(actions or FakeActions(), clock=clock, state_path=str(Path(db._DATA_DIR) / "rs.json"))


def test_tick_runs_due_slots_once_and_tracker_cadence(env):
    clk = Clock(et(9, 35, 30))
    r = make(clk)
    ev = r.tick()
    kinds = [c[0] for c in r.actions.calls]
    assert "discovery" in kinds and ("tracker", "all") in r.actions.calls          # first tick: full tracker
    disc = [c for c in r.actions.calls if c[0] == "discovery"]
    assert disc[0][1] == "2026-09-25T0935" and disc[0][2] is True                # entries allowed in session
    # 30 seconds later: nothing new (tracker fast is every 60 s; discovery slot already done)
    clk.t = et(9, 36, 0)
    n = len(r.actions.calls)
    r.tick()
    assert len(r.actions.calls) == n
    clk.t = et(9, 36, 45)
    r.tick()
    assert ("tracker", "positions") in r.actions.calls[n:]                        # fast tracker after 60 s
    assert sum(1 for c in r.actions.calls if c[0] == "discovery") == 1           # never re-run


def test_missed_slots_are_not_backfilled(env):
    clk = Clock(et(10, 0, 0))                    # runtime (re)started at 10:00 ET
    r = make(clk)
    r.tick()
    assert not any(c[0] == "discovery" for c in r.actions.calls)                 # 09:35/09:50 are too late
    missed = [k for k in r.state["missed"]]
    assert "2026-09-25:discovery@0935" in missed and "2026-09-25:discovery@0950" in missed
    clk.t = et(10, 5, 10)
    r.tick()
    disc = [c for c in r.actions.calls if c[0] == "discovery"]
    assert [c[1] for c in disc] == ["2026-09-25T1005"]                           # only the current slot, once


def test_post_cutoff_discovery_is_observation_only(env):
    clk = Clock(et(15, 20, 5))
    r = make(clk)
    r.tick()
    disc = [c for c in r.actions.calls if c[0] == "discovery"]
    assert disc and disc[0][2] is False and disc[0][3] == "post_cutoff"


def test_closed_day_does_nothing(env):
    r = make(Clock(et(9, 40, day=26)))           # Saturday
    r.tick()
    assert r.actions.calls == []


def test_close_slot_runs_backup_and_state_persists_across_restart(env):
    clk = Clock(et(16, 10, 30))
    r = make(clk)
    r.tick()
    assert ("close", dt.date(2026, 9, 25)) in r.actions.calls
    assert r.state["last_backup"]["ok"] is True
    r2 = make(clk)                                # "process restart" reloads scheduling state from disk
    n = len(r2.actions.calls)
    r2.startup()
    r2.tick()
    assert not [c for c in r2.actions.calls if c[0] == "close"]                  # close is not repeated


def test_interrupted_slot_is_never_rerun(env):
    clk = Clock(et(9, 35, 20))

    class Boom(FakeActions):
        def discovery(self, *a, **k):
            raise KeyboardInterrupt if False else RuntimeError("crash mid-scan")

    r = make(clk, Boom())
    r.tick()                                                                     # slot fails, recorded
    assert r.state["done"]["2026-09-25:discovery@0935"]["status"] == "failed"
    assert r.state["last_exception"]["where"].startswith("discovery")
    r2 = make(clk)
    r2.tick()
    assert not [c for c in r2.actions.calls if c[0] == "discovery"]              # at-most-once


def test_overlapping_discovery_is_prevented(env):
    clk = Clock(et(9, 35, 10))
    r = make(clk)
    guard = rt.discovery_lock()
    assert guard.acquire()                                                       # a scan is "still running"
    try:
        r.tick()
    finally:
        guard.release()
    assert not [c for c in r.actions.calls if c[0] == "discovery"]
    assert r.state["done"]["2026-09-25:discovery@0935"]["status"] == "overlap_skipped"


# ── singleton / safety ───────────────────────────────────────────────────────────────────────────────

def test_singleton_lock_blocks_second_runtime(env):
    a, b = rt.runtime_lock(), rt.runtime_lock()
    assert a.acquire()
    assert not b.acquire()                                                       # second instance refused
    a.release()
    assert b.acquire()                                                           # lock freed -> restart OK
    b.release()


def _cli():
    spec = importlib.util.spec_from_file_location("avdi_runtime_cli", ROOT / "automation" / "avdi_runtime.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_cli_run_refuses_when_another_runtime_holds_the_lock(env, monkeypatch):
    monkeypatch.setenv("AVDI_FATAL_EXIT_DELAY_S", "0")
    held = rt.runtime_lock()
    assert held.acquire()
    try:
        with pytest.raises(SystemExit) as e:
            _cli().main(["run"])
        assert e.value.code == 3
        assert rt.load_state().get("duplicate_attempts") == 1                    # visible to health/status
    finally:
        held.release()


@pytest.mark.parametrize("key,val", [("ROBINHOOD_TRADING_ENABLED", "true"), ("BROKER_PROVIDER", "robinhood")])
def test_live_execution_is_refused(monkeypatch, key, val):
    monkeypatch.setenv(key, val)
    monkeypatch.setenv("AVDI_FATAL_EXIT_DELAY_S", "0")
    with pytest.raises(rt.LiveExecutionRefused):
        rt.assert_paper_only()
    with pytest.raises(SystemExit) as e:                # the CLI refuses to start: exit 4, no runtime created
        _cli().main(["run"])
    assert e.value.code == 4


def test_paper_defaults_are_allowed(monkeypatch):
    monkeypatch.delenv("ROBINHOOD_TRADING_ENABLED", raising=False)
    monkeypatch.setenv("BROKER_PROVIDER", "none")
    rt.assert_paper_only()


def test_runtime_never_imports_a_broker_client():
    import subprocess
    code = ("import sys; sys.path[:0]=['lab','dashboard','src']; import paper.runtime, paper.workflow; "
            "bad=[m for m in sys.modules if 'robinhood' in m.lower() or m.endswith('core.broker') or 'axisdirect' in m]; "
            "print(bad)")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    assert out.endswith("[]"), out


# ── health ───────────────────────────────────────────────────────────────────────────────────────────

def test_health_flags_stale_scan_and_stopped_tracker_in_session(env):
    lock = rt.runtime_lock()
    assert lock.acquire()
    try:
        st = {"heartbeat": et(11, 0).isoformat(), "restore_ok": True, "counters": {"provider_failures": 0},
              "last_discovery": {"at": et(10, 0).isoformat()}, "last_tracker": {"at": et(10, 59).isoformat()}}
        h = rt.health(now=et(11, 0, 30), state=st)
        assert "no_discovery_scan_recent" in h["unhealthy"] and not h["healthy"]
        st["last_discovery"]["at"] = et(10, 50).isoformat()
        st["last_tracker"]["at"] = et(9, 0).isoformat()
        h = rt.health(now=et(11, 0, 30), state=st)
        assert "tracker_stopped" in h["unhealthy"]
        st["last_tracker"] = {"at": et(10, 59).isoformat(), "quote_age_s_max": 4000}
        h = rt.health(now=et(11, 0, 30), state=st)
        assert h["healthy"] and "stale_market_data" in h["degraded"]             # degraded, not unhealthy
    finally:
        lock.release()


def test_health_not_running_and_persistence_failure(env):
    h = rt.health(now=et(11, 0), state={"heartbeat": et(11, 0).isoformat(), "restore_ok": False})
    assert "runtime_not_running" in h["unhealthy"] and "persistence_restore_failed" in h["unhealthy"]


def test_backup_creates_verified_snapshot(env):
    from paper import shadow_log
    db.query("SELECT 1")
    shadow_log._connect().close()
    out = rt.backup_now("test")
    assert out["ok"] and out["files"] >= 2
    import json
    man = json.load(open(Path(out["path"]) / "MANIFEST.json"))
    assert all(f["integrity_ok"] for f in man["files"].values())
