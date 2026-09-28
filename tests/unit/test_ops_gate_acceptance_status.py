"""ops-gate.sh's new `acceptance-status` operation: a fixed, read-only, argument-locked-down forced
command for the restricted SSH gate -- proves exactly what the PR requires:

  * the ops key CAN call acceptance-status (with and without a date)
  * the ops key still CANNOT execute an arbitrary docker command
  * the deploy key CANNOT use this read path (ops-only, unless a future change explicitly adds it)
  * the operation can never mutate ledger/shadow/runtime state (it only ever invokes the one, already
    read-only extractor -- verified by inspecting the EXACT command the stub `docker` receives)

Runs the real `deploy/ubuntu/ops-gate.sh` under `sh` (dash, matching the Ubuntu host's actual /bin/sh) with
a stub `docker`/`tail` on PATH that records its own invocation instead of touching anything real -- no
network, no container, no host access.
"""
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "deploy" / "ubuntu" / "ops-gate.sh"

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="requires a POSIX sh (dash/bash) on PATH")


def _stub_bin(tmp_path: Path) -> Path:
    """A directory on PATH ahead of the real one, with fake `docker` and `tail` executables that just
    record argv (one line per invocation, space-joined) to $STUB_LOG and exit 0 -- so ops-gate.sh's own
    `exec docker ...` / `exec tail ...` calls are captured without touching anything real."""
    bin_dir = tmp_path / "stubbin"
    bin_dir.mkdir()
    docker_stub = bin_dir / "docker"
    docker_stub.write_text('#!/bin/sh\necho "docker $*" >> "$STUB_LOG"\nexit 0\n')
    tail_stub = bin_dir / "tail"
    tail_stub.write_text('#!/bin/sh\necho "tail $*" >> "$STUB_LOG"\nexit 0\n')
    for f in (docker_stub, tail_stub):
        f.chmod(f.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


_CALL_COUNTER = {"n": 0}


def _run_gate(tmp_path: Path, role: str, original_command: str):
    _CALL_COUNTER["n"] += 1
    call_dir = tmp_path / f"call{_CALL_COUNTER['n']}"
    call_dir.mkdir()
    home = call_dir / "home"
    home.mkdir()
    stub_log = call_dir / "stub.log"
    stub_bin = _stub_bin(call_dir)
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["SSH_ORIGINAL_COMMAND"] = original_command
    env["STUB_LOG"] = str(stub_log)
    env["PATH"] = f"{stub_bin}{os.pathsep}{env.get('PATH', '')}"
    proc = subprocess.run(["sh", str(GATE), role], env=env, capture_output=True, text=True, timeout=15)
    logged = stub_log.read_text().strip().splitlines() if stub_log.exists() else []
    return proc, logged


def test_ops_key_can_call_acceptance_status_with_no_date(tmp_path):
    proc, logged = _run_gate(tmp_path, "ops", "acceptance-status")
    assert proc.returncode == 0, proc.stderr
    assert logged == ["docker exec avdi-runtime python automation/avdi_acceptance.py"]


def test_ops_key_can_call_acceptance_status_with_a_valid_date(tmp_path):
    proc, logged = _run_gate(tmp_path, "ops", "acceptance-status 2026-09-28")
    assert proc.returncode == 0, proc.stderr
    assert logged == ["docker exec avdi-runtime python automation/avdi_acceptance.py 2026-09-28"]


@pytest.mark.parametrize("bad_date", [
    "2026-9-28",            # not zero-padded
    "not-a-date",
    "2026-09-28; rm -rf /", # a shell-metacharacter payload -- must be REJECTED outright, never passed through
    "2026-09-28 --extra",
    "../../etc/passwd",
])
def test_acceptance_status_rejects_anything_that_is_not_a_plain_date(tmp_path, bad_date):
    proc, logged = _run_gate(tmp_path, "ops", f"acceptance-status {bad_date}")
    assert proc.returncode == 2
    assert logged == []                          # docker was never invoked at all


def test_ops_key_still_cannot_execute_an_arbitrary_docker_command(tmp_path):
    """The gate must not become a generic docker-exec proxy just because acceptance-status exists."""
    for arbitrary in ("exec avdi-runtime rm -rf /", "run --rm -v /:/host alpine sh",
                      "ps", "docker-compose down"):
        proc, logged = _run_gate(tmp_path, "ops", arbitrary)
        assert proc.returncode == 126, f"{arbitrary!r} should have been denied, got rc={proc.returncode}"
        assert logged == []


def test_deploy_key_cannot_use_the_acceptance_status_read_path(tmp_path):
    proc, logged = _run_gate(tmp_path, "deploy", "acceptance-status")
    assert proc.returncode == 126
    assert "denied" in proc.stderr
    assert logged == []


def test_deploy_key_retains_only_its_own_documented_operations(tmp_path):
    for op, expect_rc in (("status", 0), ("health", 0), ("acceptance-status", 126), ("backup-now", 126)):
        proc, _ = _run_gate(tmp_path, "deploy", op)
        assert proc.returncode == expect_rc, f"deploy:{op} expected rc={expect_rc}, got {proc.returncode}"


def test_acceptance_status_never_invokes_a_mutating_operation():
    """Static guard on the gate's own source: acceptance-status's case arm must contain exactly one
    `docker exec ... avdi_acceptance.py` invocation and nothing else (no avdi_runtime.py calls, which are
    what backup-now/offhost-ack/etc. use and which CAN mutate runtime state)."""
    src = GATE.read_text()
    # Find the CASE-ARM ("  acceptance-status)" at the start of a line), not the allow-listing in
    # allowed()'s "ops:acceptance-status)" pattern, which is also a substring match for a bare search.
    marker = "\n  acceptance-status)"
    start = src.index(marker) + 1
    end = src.index("\n  backup-list)", start)    # the next case arm -- avoids the nested date-validation esac
    block = src[start:end]
    assert "avdi_acceptance.py" in block
    assert "avdi_runtime.py" not in block
    assert "docker run" not in block            # no bind-mounted container spin-up path either


def test_acceptance_status_is_ops_only_in_the_allowed_table():
    src = GATE.read_text()
    allowed_start = src.index("allowed() {")
    allowed_end = src.index("\n}", allowed_start)
    allowed_block = src[allowed_start:allowed_end]
    assert "ops:acceptance-status" in allowed_block
    assert "deploy:acceptance-status" not in allowed_block
