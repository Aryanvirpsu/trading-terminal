"""HIST-002's own resolver: given a real, already-closed Champion position, replay its bar-by-bar path
(5-minute bars, the same `HistoricalMarketProvider`/`provider.bars()` every other Historical Lab resolver
uses) under a SINGLE counterfactual exit rule -- move the stop to entry once price reaches +1R, effective
from the NEXT bar -- and report what would have happened instead. Never touches a real position; this is a
pure, read-only re-walk of already-recorded market data.

Reuses `outcomes.py`'s own R-multiple convention (`_r_multiple`) and ambiguous-bar philosophy (never resolve
in the variant's favor), identical to `experiments/ch001_breakeven_intraday/PREREG.md`'s live pilot:

A trade is classified AMBIGUOUS (conservative, non-favorable resolution) when a single bar contains:
  * the +1R trigger AND the original stop, before the stop has moved -- can't tell from OHLC alone whether
    price touched +1R or the original stop first;
  * the moved (breakeven) stop AND the target, after the stop has moved -- can't tell whether it cut the
    winner or stopped flat first;
  * the original stop AND the target, before the stop has ever moved -- Champion's own stop-first convention
    is kept (matching `outcomes.resolve_outcome()`'s real policy), but the bar is still flagged ambiguous so
    it can be excluded from clean statistics.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any, Dict, List, Optional

from ..provider import HistoricalMarketProvider

EXIT_TARGET = "target"
EXIT_STOP = "stop"
EXIT_BREAKEVEN = "breakeven"
EXIT_AMBIGUOUS = "ambiguous"
EXIT_STILL_OPEN = "still_open"
EXIT_NO_DATA = "no_data"


def _r_multiple(direction: str, entry: float, price: float, r_per_share: float) -> float:
    """Identical formula/convention to `outcomes.py`'s own `_r_multiple` -- duplicated rather than imported
    across a leading-underscore package boundary, kept byte-identical on purpose (see
    `test_breakeven_resolver.py`'s own parity check against `outcomes._r_multiple`)."""
    if r_per_share <= 0:
        return 0.0
    move = (price - entry) if direction == "LONG" else (entry - price)
    return round(move / r_per_share, 4)


@dataclasses.dataclass
class BreakevenResult:
    symbol: str
    direction: str
    entry_time: str
    entry_price: float
    original_stop: float
    target: float
    r_per_share: float
    resolved: bool
    exit_reason: str
    exit_time: Optional[str]
    exit_price: Optional[float]
    variant_net_r: Optional[float]
    plus_1r_time: Optional[str]
    stop_moved_to_entry: bool
    ambiguous: bool
    ambiguous_note: Optional[str]
    bars_used: int

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


def resolve_breakeven_after_plus1r(provider: HistoricalMarketProvider, symbol: str, direction: str,
                                   entry_price: float, original_stop: float, target: float,
                                   entry_time: dt.datetime, timeframe: str = "5m") -> BreakevenResult:
    """`provider` must already be bound to a clock at/after the horizon this should resolve through. Bars
    at/before `entry_time` are excluded on principle (the entry itself is not its own outcome) -- same
    `start=entry_time` bound `outcomes.resolve_outcome()` uses."""
    if direction not in ("LONG", "SHORT"):
        raise ValueError(f"direction must be LONG or SHORT, got {direction!r}")
    r_per_share = abs(entry_price - original_stop)
    base = dict(symbol=symbol.upper(), direction=direction, entry_time=entry_time.isoformat(),
               entry_price=entry_price, original_stop=original_stop, target=target,
               r_per_share=round(r_per_share, 6))

    points = provider.bars(symbol, timeframe=timeframe, start=entry_time)
    if not points:
        return BreakevenResult(**base, resolved=False, exit_reason=EXIT_NO_DATA, exit_time=None,
                               exit_price=None, variant_net_r=None, plus_1r_time=None,
                               stop_moved_to_entry=False, ambiguous=False, ambiguous_note=None, bars_used=0)

    stop_moved = False
    plus_1r_time = None
    bars_used = 0

    for p in points:
        bars_used += 1
        hi, lo = p["h"], p["l"]

        if not stop_moved:
            fav_r = _r_multiple(direction, entry_price, hi if direction == "LONG" else lo, r_per_share)
            trig = fav_r >= 1.0
            stop_touched = (lo <= original_stop) if direction == "LONG" else (hi >= original_stop)
            target_touched = (hi >= target) if direction == "LONG" else (lo <= target)

            if trig and stop_touched:
                return BreakevenResult(**base, resolved=True, exit_reason=EXIT_AMBIGUOUS, exit_time=p["t"],
                                       exit_price=original_stop,
                                       variant_net_r=_r_multiple(direction, entry_price, original_stop, r_per_share),
                                       plus_1r_time=p["t"], stop_moved_to_entry=False, ambiguous=True,
                                       ambiguous_note="trigger and original stop in one bar", bars_used=bars_used)
            if stop_touched and target_touched:
                # Identical gap-open-ordering policy to outcomes.resolve_outcome() -- the prereg explicitly
                # says this specific case "match[es] outcomes.resolve_outcome()'s real policy": a gap open
                # past one level (with the other not touched by the open itself) is real, causal information
                # that orders the bar unambiguously; only a genuine same-bar range straddle (neither level
                # touched by the open) falls back to the conservative stop-assumed ambiguous resolution.
                opened_past_target = (p["o"] >= target) if direction == "LONG" else (p["o"] <= target)
                opened_past_stop = (p["o"] <= original_stop) if direction == "LONG" else (p["o"] >= original_stop)
                if opened_past_stop and not opened_past_target:
                    return BreakevenResult(**base, resolved=True, exit_reason=EXIT_STOP, exit_time=p["t"],
                                           exit_price=original_stop,
                                           variant_net_r=_r_multiple(direction, entry_price, original_stop, r_per_share),
                                           plus_1r_time=None, stop_moved_to_entry=False, ambiguous=False,
                                           ambiguous_note=None, bars_used=bars_used)
                if opened_past_target and not opened_past_stop:
                    return BreakevenResult(**base, resolved=True, exit_reason=EXIT_TARGET, exit_time=p["t"],
                                           exit_price=target,
                                           variant_net_r=_r_multiple(direction, entry_price, target, r_per_share),
                                           plus_1r_time=None, stop_moved_to_entry=False, ambiguous=False,
                                           ambiguous_note=None, bars_used=bars_used)
                return BreakevenResult(**base, resolved=True, exit_reason=EXIT_AMBIGUOUS, exit_time=p["t"],
                                       exit_price=original_stop,
                                       variant_net_r=_r_multiple(direction, entry_price, original_stop, r_per_share),
                                       plus_1r_time=None, stop_moved_to_entry=False, ambiguous=True,
                                       ambiguous_note="original stop and target in one bar (pre-move)",
                                       bars_used=bars_used)
            if stop_touched:
                return BreakevenResult(**base, resolved=True, exit_reason=EXIT_STOP, exit_time=p["t"],
                                       exit_price=original_stop,
                                       variant_net_r=_r_multiple(direction, entry_price, original_stop, r_per_share),
                                       plus_1r_time=None, stop_moved_to_entry=False, ambiguous=False,
                                       ambiguous_note=None, bars_used=bars_used)
            if target_touched:
                return BreakevenResult(**base, resolved=True, exit_reason=EXIT_TARGET, exit_time=p["t"],
                                       exit_price=target,
                                       variant_net_r=_r_multiple(direction, entry_price, target, r_per_share),
                                       plus_1r_time=None, stop_moved_to_entry=False, ambiguous=False,
                                       ambiguous_note=None, bars_used=bars_used)
            if trig:
                stop_moved = True
                plus_1r_time = p["t"]
                continue        # stop moves to entry, EFFECTIVE FROM THE NEXT BAR -- this bar is done
            continue

        # stop already moved to entry (breakeven) -- check breakeven level vs. target in this bar
        be_touched = (lo <= entry_price) if direction == "LONG" else (hi >= entry_price)
        target_touched = (hi >= target) if direction == "LONG" else (lo <= target)
        if be_touched and target_touched:
            return BreakevenResult(**base, resolved=True, exit_reason=EXIT_AMBIGUOUS, exit_time=p["t"],
                                   exit_price=entry_price, variant_net_r=0.0, plus_1r_time=plus_1r_time,
                                   stop_moved_to_entry=True, ambiguous=True,
                                   ambiguous_note="breakeven level and target in one bar", bars_used=bars_used)
        if target_touched:
            return BreakevenResult(**base, resolved=True, exit_reason=EXIT_TARGET, exit_time=p["t"],
                                   exit_price=target,
                                   variant_net_r=_r_multiple(direction, entry_price, target, r_per_share),
                                   plus_1r_time=plus_1r_time, stop_moved_to_entry=True, ambiguous=False,
                                   ambiguous_note=None, bars_used=bars_used)
        if be_touched:
            return BreakevenResult(**base, resolved=True, exit_reason=EXIT_BREAKEVEN, exit_time=p["t"],
                                   exit_price=entry_price, variant_net_r=0.0, plus_1r_time=plus_1r_time,
                                   stop_moved_to_entry=True, ambiguous=False, ambiguous_note=None,
                                   bars_used=bars_used)

    # ran out of visible data with neither level touched
    return BreakevenResult(**base, resolved=False, exit_reason=EXIT_STILL_OPEN, exit_time=None,
                           exit_price=None, variant_net_r=None, plus_1r_time=plus_1r_time,
                           stop_moved_to_entry=stop_moved, ambiguous=False, ambiguous_note=None,
                           bars_used=bars_used)
