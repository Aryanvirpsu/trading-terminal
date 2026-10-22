"""The ops workflow may only run read-only ops-gate commands, and its backup must not depend on the runtime being healthy.

`ubuntu-ops.yml` holds the restricted `ops` SSH key.  This pins (1) the exact set of gate commands it may call, so a later edit
cannot quietly add a deploy / backup-now / acceptance-status step, and (2) the 2026-10-16 lesson: a failed health probe must not
suppress the off-host backup (the job still ends red because the probe job fails).
"""
import os
import re

import pytest

yaml = pytest.importorskip("yaml")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WF = os.path.join(ROOT, ".github", "workflows", "ubuntu-ops.yml")
READ_ONLY_COMMANDS = {"status", "health", "evidence", "health-log", "verify-backup", "backup-pull", "offhost-ack"}


def _load():
    with open(WF, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _gate_commands(doc):
    cmds = set()
    for job in doc["jobs"].values():
        for step in job["steps"]:
            for m in re.finditer(r"\$SSH\s+\"?([a-z-]+)", step.get("run", "") or ""):
                cmds.add(m.group(1))
    return cmds


def test_workflow_only_calls_read_only_gate_commands():
    cmds = _gate_commands(_load())
    assert cmds, "expected the workflow to call the ops gate"
    assert cmds <= READ_ONLY_COMMANDS, f"unexpected gate commands: {sorted(cmds - READ_ONLY_COMMANDS)}"
    for forbidden in ("deploy", "backup-now", "backup-live", "acceptance-status", "run"):
        assert forbidden not in cmds


def test_backup_runs_after_a_failed_probe_but_not_when_the_probe_was_skipped():
    doc = _load()
    backup = doc["jobs"]["backup"]
    assert backup["needs"] == "probe" or backup["needs"] == ["probe"]
    cond = backup["if"]
    assert "always()" in cond and "needs.probe.result == 'failure'" in cond and "needs.probe.result == 'success'" in cond
    assert "AVDI_OPS_ENABLED" in cond, "the kill switch must still disable the whole workflow"
    assert "skipped" not in cond and "cancelled" not in cond


def test_health_step_still_fails_the_probe_job_so_the_run_stays_red():
    steps = _load()["jobs"]["probe"]["steps"]
    health = next(s for s in steps if (s.get("run") or "").strip() == "$SSH health")
    assert not health.get("continue-on-error")
    log = next(s for s in steps if (s.get("run") or "").strip() == "$SSH health-log")
    assert log.get("if") == "failure()" and log.get("continue-on-error") is True
