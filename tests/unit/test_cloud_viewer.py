"""dashboard/cloud_viewer.py — the read-only cloud-mirror viewer, run as the real separate process it is in use.

A mirror is built from a REAL paper ledger (created by lab/paper/db.py in a throw-away dir) plus a shadow DB with the
same tables the cloud runtime writes, installed through scripts/sync_cloud_data.py exactly as a sync would. The viewer
is then started on a free port and queried over HTTP. Checked: it answers from the mirror with the real accounting
code, follows a newer mirror without a restart, answers 503 `no_mirror` when none exists, and never creates anything in
the (fake) local ledger directory.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHADOW_SCHEMA = """
CREATE TABLE shadow_evidence_boundary (version TEXT PRIMARY KEY, at TEXT NOT NULL, cycle_id TEXT, session_date TEXT, equity REAL, cash REAL,
  open_positions INTEGER, realized_pnl REAL, entries_today INTEGER, ledger_signals INTEGER, ledger_orders INTEGER, ledger_positions INTEGER, note TEXT);
CREATE TABLE shadow_cycles (cycle_id TEXT PRIMARY KEY, session_date TEXT NOT NULL, scan_ts TEXT NOT NULL, session_type TEXT, created_at TEXT NOT NULL,
  code_version TEXT, engine_version TEXT, config_version TEXT, finalists INTEGER, eligible INTEGER, is_choice_event INTEGER, capacity_json TEXT, funnel_json TEXT, note TEXT);
CREATE TABLE shadow_candidates (cand_id TEXT PRIMARY KEY, cycle_id TEXT NOT NULL, event_id TEXT, session_date TEXT NOT NULL, scan_ts TEXT, session_type TEXT,
  symbol TEXT NOT NULL, sector TEXT, strategy TEXT, scanner_rank INTEGER, signal_id TEXT, decision TEXT, failed_gates TEXT, quality REAL, expected_r REAL,
  ev_per_share REAL, rr REAL, exec_conf REAL, composite REAL, entry REAL, stop REAL, target REAL, quote_bid REAL, quote_ask REAL, quote_last REAL,
  quote_source_ts REAL, quote_provider TEXT, spread_pct REAL, eligible INTEGER, champion_executed INTEGER, champion_reason TEXT, capacity_reason TEXT, features_json TEXT);
CREATE TABLE shadow_bars_5m (symbol TEXT NOT NULL, ts TEXT NOT NULL, o REAL, h REAL, l REAL, c REAL, v REAL, fetched_at TEXT, PRIMARY KEY (symbol, ts));
CREATE TABLE shadow_bars_d (symbol TEXT NOT NULL, day TEXT NOT NULL, o REAL, h REAL, l REAL, c REAL, v REAL, fetched_at TEXT, PRIMARY KEY (symbol, day));
CREATE TABLE shadow_ch001_results (position_id TEXT PRIMARY KEY, event_id TEXT, symbol TEXT, signal_id TEXT, avg_entry REAL, stop REAL, target REAL,
  risk_per_share REAL, opened_at TEXT, closed_at TEXT, original_exit_reason TEXT, original_exit_price REAL, original_R REAL, plus1r_occurred INTEGER,
  plus1r_ts TEXT, be_exit_ts TEXT, be_exit_price REAL, be_R REAL, delta_R REAL, ambiguous INTEGER, ambiguity_note TEXT, bars_used INTEGER, computed_at TEXT);
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _load_sync(mirror: Path, home: Path):
    os.environ["AVDI_MIRROR_DIR"], os.environ["TVMCP_DATA_DIR"] = str(mirror), str(home)
    spec = importlib.util.spec_from_file_location("sync_cloud_data_v", ROOT / "scripts" / "sync_cloud_data.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _real_ledger(dest: Path) -> None:
    code = "import sys; sys.path.insert(0, r'%s'); from paper import db; db.connect(); db.close()" % (ROOT / "lab")
    subprocess.run([sys.executable, "-c", code], check=True, env={**os.environ, "PAPER_DATA_DIR": str(dest.parent),
                   "PAPER_LEDGER": dest.stem, "TVMCP_DATA_DIR": str(dest.parent / "_x"), "HOME": str(dest.parent), "USERPROFILE": str(dest.parent)})


def _backup(tmp: Path, name: str, boundary_equity=500.0, open_position=False) -> Path:
    d = tmp / name / name
    d.mkdir(parents=True)
    _real_ledger(d / "robinhood_500_baseline.db")
    if open_position:   # 2 shares of ZZZ bought at 100 — marks must come from the backup's bars, never a live quote
        con = sqlite3.connect(d / "robinhood_500_baseline.db")
        con.execute("INSERT INTO positions (position_id, symbol, opened_at, quantity, avg_entry, status) VALUES ('p1','ZZZ','2026-10-12T14:00:00Z',2,100,'open')")
        con.commit(); con.close()
    con = sqlite3.connect(d / "shadow__shadow_candidates.db")
    con.executescript(SHADOW_SCHEMA)
    con.execute("INSERT INTO shadow_evidence_boundary (version, at, equity, cash, open_positions) VALUES ('v1.1','2026-09-25T13:35:00Z',?,?,0)", (boundary_equity, boundary_equity))
    if open_position:   # the last bar BEFORE the backup (20:25Z = 16:25 ET) is 130; a later bar (140) must be ignored
        con.executemany("INSERT INTO shadow_bars_5m (symbol, ts, c) VALUES ('ZZZ',?,?)",
                        [("2026-10-12T15:55:00-04:00", 130.0), ("2026-10-13T09:35:00-04:00", 140.0)])
    con.execute("INSERT INTO shadow_cycles (cycle_id, session_date, scan_ts, session_type, created_at, finalists, eligible, funnel_json) "
                "VALUES ('c1','2026-09-25','2026-09-25T13:35:00Z','regular','2026-09-25T13:36:00Z',3,2,'{\"universe\":632}')")
    con.execute("INSERT INTO shadow_candidates (cand_id, cycle_id, session_date, symbol, scanner_rank, decision, eligible, champion_executed) "
                "VALUES ('k1','c1','2026-09-25','DELL',1,'TRADEABLE',1,1)")
    con.commit(); con.close()
    files = {p.name: {"sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in d.iterdir() if p.suffix == ".db"}
    (d / "MANIFEST.json").write_text(json.dumps({"ok": True, "files": files, "created_at": "2026-10-12T20:25:00Z"}), encoding="utf-8")
    tgz = tmp / f"{name}.tgz"
    with tarfile.open(tgz, "w:gz") as tf:
        tf.add(d, arcname=name)
    return tgz


def _get(port: int, path: str):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@pytest.fixture()
def viewer(tmp_path):
    mirror, home = tmp_path / "mirror" / "cloud", tmp_path / "home"
    (home / "paper").mkdir(parents=True)
    port = _free_port()
    proc = subprocess.Popen([sys.executable, str(ROOT / "dashboard" / "cloud_viewer.py")], cwd=ROOT,
                            env={**os.environ, "AVDI_MIRROR_DIR": str(mirror), "TVMCP_DATA_DIR": str(home), "CLOUD_VIEWER_PORT": str(port)},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            _get(port, "/api/cloud/health")
            break
        except OSError:
            time.sleep(0.2)
    else:
        proc.kill(); pytest.fail("viewer did not start")
    yield port, mirror, home, tmp_path
    proc.kill(); proc.wait(timeout=10)


def test_no_mirror_is_a_clear_503_not_a_crash(viewer):
    port, *_ = viewer
    code, body = _get(port, "/api/cloud/paper/account")
    assert code == 503 and body["state"] == "no_mirror"
    assert _get(port, "/api/cloud/meta")[1]["state"] == "no_mirror"


def test_answers_from_the_mirror_with_the_real_accounting_and_follows_a_newer_mirror(viewer):
    port, mirror, home, tmp = viewer
    sync = _load_sync(mirror, home)
    sync.install(_backup(tmp, "20261012T202500Z", boundary_equity=500.0), {"name": "avdi-ubuntu-backup-1"})

    meta = _get(port, "/api/cloud/meta")[1]
    assert meta["state"] == "ok" and meta["name"] == "20261012T202500Z" and meta["read_only"] and meta["verified"]

    code, acct = _get(port, "/api/cloud/paper/account")
    assert code == 200 and acct["is_real_money"] is False and acct["is_simulated"] is True
    assert acct["equity"] == pytest.approx(500.0) and acct["open_positions"] == 0

    code, rt = _get(port, "/api/cloud/runtime")
    assert code == 200 and rt["evidence_boundary"][0]["version"] == "v1.1"
    assert rt["v11"]["v11_pnl"] == pytest.approx(0.0)                     # equity now − boundary equity, from the same ledger
    assert rt["latest_candidates"][0]["symbol"] == "DELL" and rt["counts"]["shadow_cycles"] == 1
    assert rt["recent_cycles"][0]["funnel_json"] == {"universe": 632}      # JSON columns arrive parsed

    # a newer mirror is picked up on the next request — no restart
    sync.install(_backup(tmp, "20261013T202500Z", boundary_equity=490.0), {"name": "avdi-ubuntu-backup-2"})
    assert _get(port, "/api/cloud/meta")[1]["name"] == "20261013T202500Z"
    assert _get(port, "/api/cloud/runtime")[1]["v11"]["v11_pnl"] == pytest.approx(10.0)

    # isolation: the (fake) local ledger directory was never written to
    assert list((home / "paper").iterdir()) == []


def test_open_positions_are_marked_at_the_backup_not_at_live_prices(viewer):
    port, mirror, home, tmp = viewer
    _load_sync(mirror, home).install(_backup(tmp, "20261012T202500Z", open_position=True), {"name": "avdi-ubuntu-backup-3"})
    code, acct = _get(port, "/api/cloud/paper/account")
    assert code == 200 and acct["open_positions"] == 1
    pos = acct["open"][0]
    assert pos["current_price"] == 130.0 and pos["mark_source"] == "backup_5m_bar"   # not 140 (later), not a live quote
    assert pos["unrealized_pnl"] == pytest.approx(60.0)
    assert "backup snapshot" in acct["mark_basis"] and acct["as_of"].startswith("2026-10-12T20:25")
