"""v1.1 evidence boundary (account continuity without mixing evidence), rollback hook, ops-gate denials."""
import datetime as dt
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for folder in ("src", "dashboard", "lab"):
    sys.path.insert(0, str(ROOT / folder))

from paper import db, journal, risk, runtime as rt, shadow_log
from paper import market_calendar as cal

UTC = dt.timezone.utc


def et(h, m, s=0, day=25):
    return dt.datetime(2026, 9, day, h, m, s, tzinfo=cal.ET).astimezone(UTC)


class FakeActions:
    def __init__(self):
        self.calls = []

    def prep_health(self, d):
        return {"ok": True}

    def discovery(self, d, cycle_id, allow_entries, session_type):
        self.calls.append((cycle_id, allow_entries, session_type))
        return {"state": "ok", "finalists": [], "evaluated": [], "orders_placed": []}

    def tracker(self, d, scope):
        return {"symbols_watched": [], "exits": 0}

    def close(self, d):
        return {}


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("PAPER_DATA_DIR", str(tmp_path))
    db.reset_for_tests(str(tmp_path))
    yield tmp_path
    db.close()


def runtime(clock):
    return rt.Runtime(FakeActions(), clock=clock, state_path=str(Path(db._DATA_DIR) / "rs.json"))


def test_boundary_is_taken_once_before_the_first_real_in_session_cycle(env):
    now = [et(9, 15, 20)]                                    # prep observation: NOT a real cycle
    r = runtime(lambda: now[0])
    r.tick()
    assert shadow_log.evidence_summary()["boundary"] is None
    now[0] = et(9, 35, 20)                                   # first regular cycle
    r.tick()
    b = shadow_log.evidence_summary()["boundary"]
    assert b["version"] == "v1.1" and b["cycle_id"] == "2026-09-25T0935" and b["equity"] == 500.0
    assert b["open_positions"] == 0 and b["entries_today"] == 0
    first_at = b["at"]
    now[0] = et(9, 50, 20)
    r.tick()
    assert shadow_log.evidence_summary()["boundary"]["at"] == first_at      # never re-taken / never moved


def test_post_cutoff_and_manual_cycles_never_create_the_boundary(env):
    r = runtime(lambda: et(15, 20, 10))                      # observation-only slot
    r.tick()
    assert shadow_log.evidence_summary()["boundary"] is None


def test_v11_pnl_ignores_v10_history_and_versions_stay_separately_queryable(env, monkeypatch):
    # v1.0-era row
    old = {"symbol": "OLD", "decision": "REJECT", "price": 10, "entry_range": [10, 10.1], "stop": 9, "target": 12,
           "decision_gates": [], "failed_gates": [], "engine_version": "decision_engine/gates-v1"}
    journal.record_signal(old, strategy="s", session_date="2026-09-24")
    real = risk.account_state
    b = shadow_log.ensure_boundary("v1.1", "2026-09-25T0935", "2026-09-25")
    assert b["created_now"] and b["equity"] == 500.0
    new = dict(old, symbol="NEW")
    new.pop("engine_version")
    journal.record_signal(new, strategy="s", session_date="2026-09-25")
    monkeypatch.setattr(risk, "account_state", lambda d=None: {**real(d), "equity": 507.25})
    s = shadow_log.evidence_summary()
    assert s["v11_pnl"] == 7.25                                # current equity - v1.1 starting equity
    assert s["ledger_signals_by_engine_version"] == {"decision_engine/gates-v1": 1, "decision_engine/gates-v1.1": 1}


def test_boundary_is_append_only(env):
    shadow_log.ensure_boundary("v1.1", "c", "2026-09-25")
    import sqlite3
    c = sqlite3.connect(shadow_log.path())
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        c.execute("UPDATE shadow_evidence_boundary SET equity=1")


def test_force_unhealthy_hook_supports_the_rollback_proof(env, monkeypatch):
    lock = rt.runtime_lock()
    assert lock.acquire()
    try:
        st = {"heartbeat": dt.datetime.now(UTC).isoformat(), "restore_ok": True, "counters": {}}
        assert rt.health(state=st)["healthy"] is True
        monkeypatch.setenv("AVDI_FORCE_UNHEALTHY", "1")
        h = rt.health(state=st)
        assert not h["healthy"] and "forced_unhealthy_test" in h["unhealthy"]
    finally:
        lock.release()


GATE = ROOT / "deploy" / "ubuntu" / "ops-gate.sh"


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")
@pytest.mark.parametrize("role,cmd,code", [
    ("ops", "deploy " + "a" * 40, 126),          # ops key can never deploy
    ("ops", "bash -c id", 126),                  # no shell
    ("ops", "rm -rf /", 126),
    ("deploy", "backup-pull", 126),              # deploy key cannot read backups
    ("deploy", "evidence", 126),
    ("deploy", "deploy notasha", 2),             # malformed sha rejected before anything runs
    ("deploy", "deploy " + "g" * 40, 2),
    ("deploy", "deploy abc123", 2),              # sha must be 40 hex chars
    ("nobody", "status", 126),                   # unknown role
    ("ops", "", 126),                            # no command
    ("ops", "offhost-ack notaname", 2),          # backup name must match the timestamp pattern
    ("ops", "offhost-ack ../../etc", 2),
    ("deploy", "offhost-ack 20260925T232307Z", 126),   # the deploy key cannot acknowledge backups
    ("deploy", "verify-backup", 126),
    ("deploy", "health-log", 126),
])
def test_ops_gate_denies_everything_outside_the_role(tmp_path, role, cmd, code):
    env = dict(os.environ, HOME=str(tmp_path), SSH_ORIGINAL_COMMAND=cmd)
    p = subprocess.run(["sh", str(GATE), role], env=env, capture_output=True, text=True, input="")
    assert p.returncode == code, (p.returncode, p.stderr)
