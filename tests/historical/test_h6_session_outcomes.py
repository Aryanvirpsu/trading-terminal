"""H6 integration acceptance: outcomes resolve end-to-end for the real known-forward session's reference
symbols, using the real decision stack for entry/stop/target and the real intraday feed for the walk."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.historical.known_forward import reference as ref
from research.historical.known_forward.session_outcomes import resolve_all_reference_symbols


@pytest.fixture(scope="module")
def outcomes():
    return resolve_all_reference_symbols(run_id="h6_test_session_outcomes")


def test_every_reference_symbol_gets_an_outcome_or_an_explicit_none(outcomes):
    assert set(outcomes.keys()) == set(ref.all_symbols())


def test_dell_and_meta_resolve_with_sane_hypothetical_levels(outcomes):
    for sym in ("DELL", "META"):
        o = outcomes[sym]
        assert o is not None
        assert o.hypothetical is True
        assert o.stop < o.entry_price < o.target        # LONG, correctly ordered
        assert o.r_per_share > 0
        assert o.bars_used > 0                            # real intraday bars were actually walked
        assert o.exit_reason in ("target", "stop", "ambiguous", "still_open", "no_data")


def test_no_outcome_ever_uses_a_bar_at_or_before_its_own_entry_time(outcomes):
    import datetime as dt

    for sym, o in outcomes.items():
        if o is None or o.last_bar_time is None:
            continue
        assert dt.datetime.fromisoformat(o.last_bar_time) > dt.datetime.fromisoformat(o.entry_time)
