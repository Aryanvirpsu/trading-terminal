"""CH-003 protocol integrity: the pre-registration and the frozen replay code are hash-locked, and the runner refuses to run on tampering.

If one of these fails, a locked file was edited AFTER the lock: the experiment is no longer the one that was pre-registered. Either revert
the edit, or (for the code) re-run Stage A, re-freeze and log why in RESULTS.md — never just update the hash to make the test pass.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments" / "ch003_time_stop"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_preregistration_is_unchanged_since_it_was_locked():
    lock = json.loads((EXP / "PREREG.lock.json").read_text(encoding="utf-8"))
    assert lock["results_computed_before_lock"] is False
    assert sha(EXP / "PREREG.md") == lock["sha256"]


def test_replay_code_is_unchanged_since_the_freeze():
    frozen = json.loads((EXP / "CODE.lock.json").read_text(encoding="utf-8"))["code_sha256"]
    assert frozen == {"engine.py": sha(EXP / "engine.py"), "ch003.py": sha(EXP / "ch003.py")}


def test_the_runner_refuses_an_edited_preregistration(tmp_path):
    copy = tmp_path / "ch003"
    shutil.copytree(EXP, copy, ignore=shutil.ignore_patterns("__pycache__"))
    (copy / "PREREG.md").write_text((copy / "PREREG.md").read_text(encoding="utf-8") + "\nA quietly added criterion.\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(copy / "ch003.py"), "--stage-a"], capture_output=True, text=True)
    assert r.returncode == 2 and "REFUSED" in r.stderr and "PREREG.md differs" in r.stderr


def test_the_results_file_declares_that_nothing_is_promoted_automatically():
    res = json.loads((EXP / "ch003_results.json").read_text(encoding="utf-8"))
    assert res["promotion_gate"]["auto_promote"] is False and res["promotion_gate"]["eligible"] is False
    assert res["validity_gate"]["passed"] is True and res["kind"] == "arm_comparison"
    assert not (EXP / "PROMOTION_LOG.md").exists()      # a promotion is a human act; none is recorded for an in-sample result
