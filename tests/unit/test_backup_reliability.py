"""Backup creation / verification / retention / off-host ack, and backup-failure isolation."""
import datetime as dt
import json
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for folder in ("src", "dashboard", "lab"):
    sys.path.insert(0, str(ROOT / folder))

from paper import db, journal, runtime as rt, shadow_log

UTC = dt.timezone.utc


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("PAPER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AVDI_CODE_VERSION", "deadbeef")
    db.reset_for_tests(str(tmp_path))
    db.query("SELECT 1")
    shadow_log.ensure_boundary("v1.1", "2026-09-25T0935", "2026-09-25")
    yield tmp_path
    db.close()


def test_backup_manifest_contents_and_read_only_files(env):
    out = rt.backup_now("test")
    assert out["ok"] and out["runtime_version"] == "deadbeef"
    d = Path(out["path"])
    m = json.load(open(d / "MANIFEST.json"))
    assert m["reason"] == "test" and m["runtime_version"] == "deadbeef" and m["engine_version"] == journal.ENGINE_VERSION
    assert m["created_iso"].endswith("+00:00") and m["config_version"].startswith("cfg-")
    assert m["evidence"]["boundary"]["version"] == "v1.1" and m["ledger_summary"]["signals"] == 0
    assert set(m["files"]) == {"robinhood_500_baseline.db", "shadow__shadow_candidates.db"}
    for meta in m["files"].values():
        assert meta["integrity_ok"] and len(meta["sha256"]) == 64 and meta["bytes"] > 0
    for f in d.iterdir():                                         # immutable once written
        assert not (f.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)) or f.is_dir()


def test_verify_backup_passes_and_detects_tampering_and_missing_shadow(env):
    out = rt.backup_now("t")
    v = rt.verify_backup("latest")
    assert v["ok"] and v["backup"] == Path(out["path"]).name
    assert all(f["sha256_match"] and f["integrity"] == "ok" for f in v["files"].values())
    d = Path(out["path"])
    victim = d / "shadow__shadow_candidates.db"
    os.chmod(victim, 0o644)
    with open(victim, "ab") as fh:
        fh.write(b"tamper")                                       # any post-hoc modification is caught by the hash
    v2 = rt.verify_backup(d.name)
    assert not v2["ok"] and any("sha256 mismatch" in p for p in v2["problems"])
    os.remove(victim)
    v3 = rt.verify_backup(d.name)
    assert not v3["ok"] and any("missing" in p for p in v3["problems"])


def test_verify_backup_max_age(env):
    rt.backup_now("t")
    later = dt.datetime.now(UTC) + dt.timedelta(hours=31)
    v = rt.verify_backup("latest", max_age_hours=30, now=later)
    assert not v["ok"] and any("old" in p for p in v["problems"])
    assert rt.verify_backup("latest", max_age_hours=30)["ok"]


def test_no_backup_is_reported_not_crashed(env):
    v = rt.verify_backup("latest")
    assert not v["ok"] and v["problems"] == ["no backup exists"]


def _mk(root, name):
    d = Path(root) / name
    d.mkdir(parents=True)
    (d / "MANIFEST.json").write_text("{}")
    os.chmod(d / "MANIFEST.json", 0o444)
    return d


def test_retention_tiers_never_fill_the_disk(env):
    root = Path(rt.backup_dir())
    now = dt.datetime(2026, 12, 1, 12, tzinfo=UTC)
    names = []
    for days_ago in list(range(0, 10)) + [20, 20, 40, 40, 59, 61, 90, 200]:
        t = now - dt.timedelta(days=days_ago, hours=1 if days_ago == 20 and len(names) % 2 else 0)
        n = t.strftime("%Y%m%dT%H%M%SZ")
        if not (root / n).exists():
            _mk(root, n)
            names.append(n)
    # two backups on the same old day (day 20) at different times
    _mk(root, (now - dt.timedelta(days=20, hours=5)).strftime("%Y%m%dT%H%M%SZ"))
    dropped = rt._prune_backups(now)
    left = sorted(os.listdir(root))
    assert (now - dt.timedelta(days=0)).strftime("%Y%m%dT%H%M%SZ") in left          # newest kept
    assert all(n[:8] >= (now - dt.timedelta(days=6)).strftime("%Y%m%d") for n in left if n in
               [x for x in left if (now - dt.datetime.strptime(x, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)).days < 7])
    assert not any((now - dt.datetime.strptime(n, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)).days >= 60 for n in left)
    day20 = [n for n in left if (now - dt.datetime.strptime(n, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)).days == 20]
    assert len(day20) == 1                                                             # one per old day
    assert dropped                                                                     # read-only files were removable


def test_backup_failure_never_breaks_the_close_job(env, monkeypatch):
    monkeypatch.setattr(rt, "backup_now", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    monkeypatch.setattr("paper.workflow.postmarket", lambda d: {"report": {}})
    monkeypatch.setattr("paper.report.daily", lambda d: {})
    out = rt.PaperActions().close(dt.date(2026, 9, 25))
    assert out["backup"]["ok"] is False and "disk full" in out["backup"]["error"]


def test_offhost_ack_roundtrip_and_health_degradation(env):
    assert rt.offhost_status()["backup"] is None
    with pytest.raises(ValueError):
        rt.record_offhost_ack("../../etc/passwd")
    rec = rt.record_offhost_ack("20260925T232307Z")
    assert rt.offhost_status()["backup"] == "20260925T232307Z"
    lock = rt.runtime_lock()
    assert lock.acquire()
    try:
        now = dt.datetime(2026, 9, 28, 23, tzinfo=UTC)                                # Monday evening, market closed
        st = {"heartbeat": now.isoformat(), "restore_ok": True, "counters": {}}
        stale = rt.health(now=now, state=st)
        assert "offhost_backup_stale" in stale["degraded"] and stale["healthy"] is True   # degraded, never unhealthy
        rt.save_state({"backup": "x", "at": now.isoformat(timespec="seconds")}, rt._ack_path())
        assert "offhost_backup_stale" not in rt.health(now=now, state=st)["degraded"]
    finally:
        lock.release()
