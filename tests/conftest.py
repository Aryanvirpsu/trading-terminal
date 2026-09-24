"""Test-suite isolation: unit tests must NEVER touch the real data directory.

Many modules resolve `~/.tradingview_mcp_data` at import time (calibration, journal_lab,
dashboard stores, the paper DB, ...). Before this guard, running the suite appended fixture rows
(`TEST` / `BAC TRADEABLE q75.5`) to the real `predictions.jsonl` — contaminating the evidence
files. So, before any test module is imported, the whole process is pointed at a throwaway
home, and any change to the real directory fails the run loudly.

Escape hatch for a deliberate real-state run: AVDI_TESTS_USE_REAL_STATE=1 (never in CI).
"""
import atexit
import os
import shutil
import tempfile

import pytest

_DATA_DIRNAME = ".tradingview_mcp_data"
_REAL_HOME = os.path.expanduser("~")
_REAL_DIR = os.environ.get("AVDI_TESTS_REAL_DIR") or os.path.join(_REAL_HOME, _DATA_DIRNAME)  # override: guard self-test only
_ISOLATE = os.environ.get("AVDI_TESTS_USE_REAL_STATE") != "1"

_TMP_HOME = None
if _ISOLATE:
    _TMP_HOME = tempfile.mkdtemp(prefix="avdi-test-home-")
    atexit.register(shutil.rmtree, _TMP_HOME, ignore_errors=True)
    os.environ["HOME"] = _TMP_HOME
    os.environ["USERPROFILE"] = _TMP_HOME          # Windows expanduser()
    os.environ["TVMCP_DATA_DIR"] = os.path.join(_TMP_HOME, _DATA_DIRNAME)
    os.environ["PAPER_DATA_DIR"] = os.path.join(_TMP_HOME, _DATA_DIRNAME, "paper")


def _snapshot(root):
    """{relative path: (size, mtime_ns)} for every file under the REAL data dir."""
    snap = {}
    if not os.path.isdir(root):
        return snap
    for base, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(base, f)
            try:
                st = os.stat(p)
                snap[os.path.relpath(p, root)] = (st.st_size, st.st_mtime_ns)
            except OSError:
                pass
    return snap


_BEFORE = _snapshot(_REAL_DIR) if _ISOLATE else {}


def pytest_sessionstart(session):
    if _ISOLATE:
        got = os.path.expanduser("~")
        assert os.path.normcase(got) == os.path.normcase(_TMP_HOME), (
            f"test isolation failed: expanduser('~') is {got!r}, expected the temp home")


@pytest.fixture(autouse=True)
def _real_state_untouched(request):
    yield
    if not _ISOLATE:
        return
    changed = {k for k, v in _snapshot(_REAL_DIR).items() if _BEFORE.get(k) != v}
    changed |= {k for k in _BEFORE if k not in _snapshot(_REAL_DIR)}
    if changed:
        pytest.fail(
            f"{request.node.nodeid} modified the REAL data directory {_REAL_DIR}: "
            f"{sorted(changed)}. Unit tests must use temporary state.", pytrace=False)
