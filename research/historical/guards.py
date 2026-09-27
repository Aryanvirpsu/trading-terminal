"""H0 — production isolation. Every historical entry point (account.py, runner.py, the CLI) calls
`guard_all()` before it does anything else. This module has NO dependency on `lab.paper` so it can never
be defeated by a coincidence in that package's own state.

Two independent guards:
  * `assert_not_production_path` — a path (account DB, output dir, ...) may never resolve inside a known
    production data location (the Ubuntu runtime's `/data/case1/paper`, the local dev machine's
    `~/.tradingview_mcp_data`, or any file that looks like the real ledger/shadow DB).
  * `assert_not_production_host` — refuses to run at all on a machine that shows the Ubuntu production
    runtime's own fingerprints (its data directory, its `avdi-runtime` deploy tree, or its container env).

Both fail CLOSED: an error resolving a path (permissions, a dangling symlink, ...) is treated as "cannot
prove this is safe" and raises, it never silently treats the unknown as safe.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Union

# Substrings that identify a production LOCATION (case-insensitive, matched against the resolved,
# normalized absolute path). Kept as plain substrings, not regexes, on purpose: simple enough to audit.
_PRODUCTION_PATH_MARKERS = (
    "case1", "case2",                          # docker/compose.c0.yml, compose.ubuntu.yml profile dirs
    ".tradingview_mcp_data",                    # every real DATA_DIR default across the whole codebase
    "avdi_runtime_ledger",                      # the Ubuntu runtime's named Docker volume
    "avdi-runtime",                             # the Ubuntu host's ~/avdi-runtime deploy tree
)
_PRODUCTION_FILE_MARKERS = (
    "robinhood_500_baseline.db",                # the real ledger filename (lab/paper/db.py DEFAULT_LEDGER)
    "demo_10k.db",                               # the real DEMO_LEDGER filename — also never touched
    "shadow_candidates.db",                      # lab/paper/shadow_log.py SHADOW_DB
)
# Host fingerprints unique to the Ubuntu production runtime (never present on a dev machine or CI runner).
_PRODUCTION_HOST_DIRS = ("/data/case1", "/data/case2", os.path.expanduser("~/avdi-runtime"))
_PRODUCTION_HOST_ENV = ("C0_PROFILE",)           # set ONLY by docker/env/case*.env inside the runtime container


class ProductionIsolationError(RuntimeError):
    """Historical Lab refused to run because it could not prove it was isolated from production."""


def _normalized(path: Union[str, "os.PathLike[str]"]) -> str:
    try:
        return str(Path(path).expanduser().resolve()).replace("\\", "/").lower()
    except OSError as e:                        # a broken symlink etc. — fail closed, not open
        raise ProductionIsolationError(f"cannot resolve path {path!r} to check production isolation: {e}") from e


def assert_not_production_path(path: Union[str, "os.PathLike[str]"]) -> None:
    """Raise if `path` resolves anywhere under a known production data location, or IS a known
    production filename. Historical code must call this on every path it is about to read or write."""
    p = _normalized(path)
    for marker in _PRODUCTION_PATH_MARKERS:
        if marker in p:
            raise ProductionIsolationError(
                f"refusing to touch {path!r}: resolved path {p!r} contains the production marker {marker!r}")
    name = Path(p).name
    for marker in _PRODUCTION_FILE_MARKERS:
        if name == marker:
            raise ProductionIsolationError(
                f"refusing to touch {path!r}: filename matches the production ledger/shadow DB {marker!r}")


def assert_not_production_host() -> None:
    """Raise if this process is running on (what looks like) the Ubuntu production runtime host —
    regardless of what path was asked for. A historical batch job must never even be able to start there."""
    for d in _PRODUCTION_HOST_DIRS:
        if os.path.isdir(d):
            raise ProductionIsolationError(
                f"refusing to start Historical Lab: production host marker directory exists: {d!r}")
    for var in _PRODUCTION_HOST_ENV:
        if os.environ.get(var):
            raise ProductionIsolationError(
                f"refusing to start Historical Lab: production host env var {var} is set")
    # The runtime container always sets these two together; a historical *unit test* must never see both.
    if os.environ.get("BROKER_PROVIDER") not in (None, "", "none") or \
       str(os.environ.get("ROBINHOOD_TRADING_ENABLED", "false")).strip().lower() in ("1", "true", "yes", "on"):
        raise ProductionIsolationError("refusing to start Historical Lab: a live-execution env var is set")


def guard_all(*paths: Union[str, "os.PathLike[str]"]) -> None:
    """Convenience: check the host, then every path supplied. Call this first, in every constructor
    that will touch disk (HistoricalAccount, dataset writers, the runner, the CLI entry point)."""
    assert_not_production_host()
    for p in paths:
        assert_not_production_path(p)


def historical_data_root() -> Path:
    """The ONLY directory Historical Lab code may write under. Never a function of PAPER_DATA_DIR or any
    other production env var — always relative to the repo (or AVDI_HISTORICAL_DATA_DIR if set), so a
    stray production env var in the environment can never redirect historical output into it."""
    root = Path(os.environ.get("AVDI_HISTORICAL_DATA_DIR") or (Path(__file__).resolve().parents[2] / "data" / "historical"))
    assert_not_production_path(root)
    return root
