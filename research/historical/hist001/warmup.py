"""HIST-001 structural fix #3 (post-Smoke): determine the Champion's required daily-bar warm-up
EMPIRICALLY, by probing the actual function historical replay calls, rather than hardcoding a duplicate
"55" anywhere in Historical Lab. Tracks the real Champion's own requirement automatically if it ever
changes (e.g. a future SMA period change) without anyone needing to remember to update a second copy.
"""
from __future__ import annotations

from typing import Any, Dict, List


def _synthetic_points(n: int) -> List[Dict[str, Any]]:
    return [{"c": 100.0 + i * 0.1, "h": 101.0 + i * 0.1, "l": 99.0 + i * 0.1, "v": 1_000_000.0,
            "t": f"2024-{1 + (i // 28) % 12:02d}-{1 + i % 28:02d}T21:00:00+00:00"} for i in range(n)]


def required_warmup_daily_bars(*, max_probe: int = 500) -> int:
    """The smallest number of daily bars for which `avdi_adapter._bars_dict()` -- the exact function
    HistoricalAVDIContext's `_patched_bars` calls, itself built to mirror `strategies._bars()`'s own floor
    -- stops returning None. Probes 1, 2, 3, ... rather than asserting a value, so this is a measurement of
    the real Champion's current requirement, not an assumption planted here."""
    from ..avdi_adapter import _bars_dict

    for n in range(1, max_probe + 1):
        if _bars_dict(_synthetic_points(n)) is not None:
            return n
    raise RuntimeError(f"avdi_adapter._bars_dict() never returned non-None within {max_probe} synthetic "
                       f"daily bars -- the Champion's warm-up requirement could not be determined; "
                       f"investigate before running any HIST-001 stage")
