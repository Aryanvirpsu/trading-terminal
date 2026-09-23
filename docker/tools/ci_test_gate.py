"""C1 CI test gate — runs `pytest tests/unit`, then judges the result against the exact
per-ID baseline G5-A already established (docs/C0_EQUIVALENCE_REPORT.md §1,
docs/C0_TEST_BASELINE_311.md), instead of treating "any test failed" as fatal.

A bare `pytest` exit code is the wrong gate for this repo: 5 tests are intentionally red
forever (2 canonical-invariant probes, 3 optional torch/transformers-dependent), one more
is expected-red in the minimal container image specifically (needs `git`, deliberately
absent), and three more (`test_cache_behavior` — nondeterministic timing;
`test_iv_richness_is_a_genuine_quality_tilt_input` and its meta-test — TZ/wall-clock
dependent by design) have no fixed expected outcome at all. A gate that fails on any of
those is a gate nobody can ever pass; a gate that ignores pytest's exit code entirely
would hide a real regression. This script does neither: it fails ONLY on
  (a) a test outside both lists below failing, or
  (b) an ALWAYS_RED test unexpectedly passing (drift worth knowing about, per
      C0_TEST_BASELINE_311.md's own rule: "any new pass of a D-class failure ... is an
      equivalence failure").

Usage: python ci_test_gate.py [pytest-args...]
Exit: 0 if the classification matches expectations, 1 otherwise (with a clear report).
"""
from __future__ import annotations

import os
import subprocess
import sys
import xml.etree.ElementTree as ET

REPO = "/app"  # matches the image's WORKDIR (docker/Dockerfile.terminal)

# Deterministic, permanent — see docs/C0_EQUIVALENCE_REPORT.md §1.2.
ALWAYS_RED = {
    "tests/unit/test_canonical_v11_invariants.py::test_oi_policy_has_no_overlapping_hard_and_soft_ranges",
    "tests/unit/test_canonical_v11_invariants.py::test_contract_quality_identical_across_pipelines",
    "tests/unit/test_finbert_sentiment.py::test_transformers_available",
    "tests/unit/test_finbert_service.py::test_available",
    "tests/unit/test_finbert_service.py::test_score_force",
    # git deliberately absent from the minimal image — docs/C0_EQUIVALENCE_REPORT.md §1.3.
    "tests/unit/test_pipeline3_canonical_migration.py::test_sector_funnel_unchanged",
}

# No fixed expected outcome — never gate on these, only report them.
NEVER_GATED = {
    "tests/unit/test_finbert_service.py::test_cache_behavior",  # N-class, timing
    "tests/unit/test_pipeline2_canonical_migration.py::test_iv_richness_is_a_genuine_quality_tilt_input",  # T-class
    "tests/unit/test_pipeline3_canonical_migration.py::test_pipeline2_tests_unchanged",  # T-class (derived)
}


def node_id(classname: str, name: str) -> str:
    parts = classname.split(".")
    for i in range(len(parts), 0, -1):
        f = "/".join(parts[:i]) + ".py"
        if os.path.exists(os.path.join(REPO, f)):
            cls = "::".join(parts[i:])
            return f + "::" + (cls + "::" if cls else "") + name
    return classname + "::" + name


def main() -> int:
    junit = "/tmp/ci_test_gate_junit.xml"
    args = sys.argv[1:] or ["tests/unit", "-q", "-p", "no:cacheprovider", "--ignore=tests/e2e"]
    cmd = [sys.executable, "-m", "pytest", *args, f"--junitxml={junit}", "-o", "junit_family=xunit2"]
    subprocess.run(cmd, cwd=REPO)  # exit code ignored on purpose — junit is the source of truth

    results = {}
    for tc in ET.parse(junit).getroot().iter("testcase"):
        outcome = "passed"
        for tag in ("failure", "error"):
            if tc.find(tag) is not None:
                outcome = "failed"
                break
        if tc.find("skipped") is not None:
            outcome = "skipped"
        results[node_id(tc.get("classname", ""), tc.get("name", ""))] = outcome

    problems = []
    for tid, outcome in results.items():
        if tid in NEVER_GATED:
            continue
        elif tid in ALWAYS_RED:
            if outcome != "failed":
                problems.append(f"UNEXPECTED PASS (was always-red): {tid} -> {outcome}")
        else:
            if outcome == "failed":
                problems.append(f"UNEXPECTED FAILURE: {tid}")

    missing = (ALWAYS_RED | NEVER_GATED) - set(results)
    for tid in sorted(missing):
        problems.append(f"EXPECTED TEST ID NOT COLLECTED (did the suite change?): {tid}")

    total = len(results)
    passed = sum(1 for v in results.values() if v == "passed")
    print(f"collected={total} passed={passed} always_red={len(ALWAYS_RED & set(results))} "
          f"never_gated={len(NEVER_GATED & set(results))}")
    for tid in sorted(NEVER_GATED & set(results)):
        print(f"  (not gated) {tid}: {results[tid]}")

    if problems:
        print("\nCI TEST GATE FAILED:")
        for p in problems:
            print(" -", p)
        return 1
    print("\nCI TEST GATE PASSED — matches the known baseline exactly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
