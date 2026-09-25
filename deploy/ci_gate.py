"""CI test gate: run the unit suite and fail on ANY failure outside the known baseline.

The repository carries a small, documented set of permanently-red tests (the "D-class" baseline:
canonical OI-policy / contract-quality invariants and optional FinBERT/transformers availability).
A bare `pytest` exit code is therefore always non-zero, so this gate judges per test id instead.
Run with TZ=UTC (the wall-clock-dependent "T-class" tests only pass under UTC, which is also the
Case 1 / Ubuntu runtime timezone).
"""
import re
import subprocess
import sys

KNOWN_FAILURES = {
    "tests/unit/test_canonical_v11_invariants.py::test_oi_policy_has_no_overlapping_hard_and_soft_ranges",
    "tests/unit/test_canonical_v11_invariants.py::test_contract_quality_identical_across_pipelines",
    "tests/unit/test_finbert_sentiment.py::test_transformers_available",
    "tests/unit/test_finbert_service.py::test_available",
    "tests/unit/test_finbert_service.py::test_score_force",
}


def main() -> int:
    extra = sys.argv[1:] or ["tests/unit"]
    proc = subprocess.run([sys.executable, "-m", "pytest", *extra, "-q", "-p", "no:cacheprovider", "-rf"],
                          capture_output=True, text=True)
    out = proc.stdout + proc.stderr
    print(out[-6000:])
    failed = set(re.findall(r"^FAILED (\S+)", out, flags=re.M))
    unexpected = sorted(failed - KNOWN_FAILURES)
    fixed = sorted(KNOWN_FAILURES - failed)
    summary = re.findall(r"(\d+ passed.*)", out)
    print(f"\nsummary: {summary[-1] if summary else '?'}")
    print(f"known baseline failures still failing: {len(failed & KNOWN_FAILURES)}/{len(KNOWN_FAILURES)}")
    if fixed:
        print("note: baseline failures that now pass (update KNOWN_FAILURES):", fixed)
    if unexpected:
        print("UNEXPECTED FAILURES:")
        for t in unexpected:
            print("  ", t)
        return 1
    if proc.returncode not in (0, 1):
        print("pytest did not run cleanly (exit", proc.returncode, ")")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
