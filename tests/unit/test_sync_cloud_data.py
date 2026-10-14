"""scripts/sync_cloud_data.py — verification, isolation and install logic (no network, no `gh`).

A synthetic backup shaped like the one `ubuntu-ops.yml` pulls (MANIFEST.json + ledger DB + shadow DB) is
installed into a temp mirror. What matters: a tampered or unsafe archive is rejected, the pristine copy stays
byte-identical, the viewer gets a separate working copy, and nothing can ever land in the local ledger folder.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import sqlite3
import tarfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _load(mirror: Path, data_home: Path):
    os.environ["AVDI_MIRROR_DIR"] = str(mirror)
    os.environ["TVMCP_DATA_DIR"] = str(data_home)
    spec = importlib.util.spec_from_file_location("sync_cloud_data_t", _ROOT / "scripts" / "sync_cloud_data.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_backup(tmp: Path, name="20261012T202500Z", tamper=False, evil=False, ok=True) -> Path:
    d = tmp / "src" / name
    d.mkdir(parents=True)
    led = d / "robinhood_500_baseline.db"
    con = sqlite3.connect(led)
    con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO meta VALUES ('k','v')")
    con.commit(); con.close()
    sh = d / "shadow__shadow_candidates.db"
    con = sqlite3.connect(sh)
    con.execute("CREATE TABLE shadow_cycles (cycle_id TEXT)")
    con.commit(); con.close()
    files = {p.name: {"sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in (led, sh)}
    (d / "MANIFEST.json").write_text(json.dumps({"ok": ok, "files": files, "created_at": "2026-10-12T20:25:00Z"}), encoding="utf-8")
    if tamper:
        led.write_bytes(led.read_bytes() + b"x")
    tgz = tmp / "backup.tgz"
    with tarfile.open(tgz, "w:gz") as tf:
        tf.add(d, arcname=name)
        if evil:
            data = b"owned"
            ti = tarfile.TarInfo("../escaped.txt"); ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return tgz


@pytest.fixture()
def env(tmp_path):
    mirror, home = tmp_path / "mirror" / "cloud", tmp_path / "home"
    (home / "paper").mkdir(parents=True)
    return _load(mirror, home), tmp_path, mirror, home


def test_install_verifies_and_separates_pristine_from_working_copy(env):
    sync, tmp, mirror, home = env
    ptr = sync.install(_make_backup(tmp), {"name": "avdi-ubuntu-backup-9", "run_id": 1, "size": 10, "created_at": "x"})
    final = mirror / "20261012T202500Z"
    assert (final / "robinhood_500_baseline.db").exists() and (final / "shadow__shadow_candidates.db").exists()
    assert (final / "work" / "robinhood_500_baseline.db").exists()          # viewer's own copy
    assert ptr["ledger_name"] == "robinhood_500_baseline" and ptr["shadow_file"] == "shadow__shadow_candidates.db"
    assert json.loads((mirror / "current.json").read_text())["name"] == "20261012T202500Z"
    # the pristine original is still exactly what the manifest hashed, and not writable
    m = json.loads((final / "MANIFEST.json").read_text())
    assert hashlib.sha256((final / "robinhood_500_baseline.db").read_bytes()).hexdigest() == m["files"]["robinhood_500_baseline.db"]["sha256"]
    assert not os.access(final / "robinhood_500_baseline.db", os.W_OK)
    assert list((home / "paper").iterdir()) == []                              # local ledger dir untouched


def test_tampered_file_is_rejected_and_nothing_is_installed(env):
    sync, tmp, mirror, _ = env
    with pytest.raises(SystemExit):
        sync.install(_make_backup(tmp, tamper=True), {"name": "n"})
    assert not (mirror / "current.json").exists()


def test_not_ok_manifest_is_rejected(env):
    sync, tmp, mirror, _ = env
    with pytest.raises(SystemExit):
        sync.install(_make_backup(tmp, ok=False), {"name": "n"})
    assert not (mirror / "current.json").exists()


def test_path_traversal_in_archive_is_rejected(env):
    sync, tmp, mirror, _ = env
    with pytest.raises(SystemExit):
        sync.install(_make_backup(tmp, evil=True), {"name": "n"})
    assert not (tmp / "escaped.txt").exists() and not (mirror.parent / "escaped.txt").exists()


def test_mirror_can_never_point_into_the_local_ledger_dir(tmp_path):
    home = tmp_path / "home"
    (home / "paper").mkdir(parents=True)
    sync = _load(home / "paper" / "cloud", home)         # a misconfigured mirror INSIDE the local ledger folder
    with pytest.raises(SystemExit):
        sync.install(_make_backup(tmp_path), {"name": "n"})
    assert not any((home / "paper").rglob("*.db"))


def test_old_mirrors_are_pruned_but_the_current_one_stays(env):
    sync, tmp, mirror, _ = env
    for i in range(5):
        workdir = tmp / f"b{i}"
        workdir.mkdir()
        sync.install(_make_backup(workdir, name=f"2026101{i}T202500Z"), {"name": f"a{i}"})
    names = sorted(p.name for p in mirror.iterdir() if p.is_dir())
    assert len(names) == sync.KEEP and "20261014T202500Z" in names
    assert json.loads((mirror / "current.json").read_text())["name"] == "20261014T202500Z"


def test_resync_of_the_same_backup_keeps_the_working_copy_the_viewer_may_hold_open(env):
    sync, tmp, mirror, _ = env
    (tmp / "a").mkdir()
    sync.install(_make_backup(tmp / "a"), {"name": "n1"})
    work = mirror / "20261012T202500Z" / "work" / "robinhood_500_baseline.db"
    marker = work.stat().st_mtime_ns
    handle = open(work, "rb")                        # on Windows an open file blocks deletion — this is the running viewer
    try:
        (tmp / "b").mkdir()
        ptr = sync.install(_make_backup(tmp / "b"), {"name": "n2"})
    finally:
        handle.close()
    assert work.stat().st_mtime_ns == marker         # untouched, not re-copied or deleted
    assert ptr["source"]["name"] == "n2" and ptr["backup_created_at"] == "2026-10-12T20:25:00Z"


def test_backup_time_comes_from_the_hosts_manifest_or_the_folder_name(env):
    sync, *_ = env
    assert sync._from_folder("20261012T202501Z") == "2026-10-12T20:25:01+00:00"
    assert sync._from_folder("not-a-timestamp") is None
