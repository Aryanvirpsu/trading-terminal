"""HIST-001 structural fix: the required daily-bar warm-up is DISCOVERED by probing the real function
historical replay calls, not hardcoded."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.historical.hist001.warmup import required_warmup_daily_bars


def test_current_champion_requires_exactly_55_daily_bars():
    assert required_warmup_daily_bars() == 55


def test_probe_is_monotonic_one_below_the_floor_still_fails():
    from research.historical.avdi_adapter import _bars_dict
    from research.historical.hist001.warmup import _synthetic_points

    n = required_warmup_daily_bars()
    assert _bars_dict(_synthetic_points(n - 1)) is None
    assert _bars_dict(_synthetic_points(n)) is not None
