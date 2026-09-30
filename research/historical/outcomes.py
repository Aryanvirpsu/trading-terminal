"""H6 — the causal outcome engine.

For a real Champion entry (or a HYPOTHETICAL one -- a TRADEABLE blocked by capacity, or a MONITOR sitting
just below the gate; see `hypothetical=True`), resolve what actually happened next: MFE, MAE, the first
timestamp each of +1R / -1R / target / stop was touched, the Champion's own exit, and net R.

THE INVARIANT THIS FILE EXISTS TO ENFORCE: only bars STRICTLY AFTER the entry/fill may be read (the entry
bar itself is the decision, not an outcome), and if a stop and a target both fall inside the SAME bar with
no way to order them from OHLC alone, the result is `AMBIGUOUS` -- never resolved in AVDI's favor. An
ambiguous bar's reported exit uses the STOP price (the conservative, non-favorable assumption) for any net-R
figure that still needs one, but the row is flagged `ambiguous=True` so it can be excluded from clean
statistics rather than silently counted as a clean win or loss.

Reuses `HistoricalMarketProvider`'s own lookahead guard (H2) for what bars are legally visible -- this
module does not re-implement or relax that guard; it just reads bars AFTER the entry timestamp instead of
at/before the decision timestamp, which is the resolver's own job, not the provider's.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any, Dict, List, Optional

from .provider import HistoricalMarketProvider

EXIT_TARGET = "target"
EXIT_STOP = "stop"
EXIT_AMBIGUOUS = "ambiguous"
EXIT_STILL_OPEN = "still_open"
EXIT_NO_DATA = "no_data"


@dataclasses.dataclass
class OutcomeResult:
    symbol: str
    direction: str                      # "LONG" | "SHORT"
    entry_time: str
    entry_price: float
    stop: float
    target: float
    r_per_share: float
    hypothetical: bool                  # True: this position was never actually entered (capacity/monitor)
    resolved: bool                      # True once exit_reason in (target, stop, ambiguous)
    exit_reason: str
    exit_time: Optional[str]
    exit_price: Optional[float]
    net_r: Optional[float]
    ambiguous: bool
    mfe_r: float
    mae_r: float
    plus_1r_time: Optional[str]
    minus_1r_time: Optional[str]
    target_time: Optional[str]
    stop_time: Optional[str]
    bars_used: int
    last_bar_time: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


def _r_multiple(direction: str, entry: float, price: float, r_per_share: float) -> float:
    if r_per_share <= 0:
        return 0.0
    move = (price - entry) if direction == "LONG" else (entry - price)
    return round(move / r_per_share, 4)


def resolve_outcome(provider: HistoricalMarketProvider, symbol: str, entry_time: dt.datetime,
                    entry_price: float, stop: float, target: float, *, direction: str = "LONG",
                    timeframe: str = "5m", hypothetical: bool = False,
                    max_bars: Optional[int] = None) -> OutcomeResult:
    """`provider` must already be bound to a clock at/after the horizon this outcome should be resolved
    through (e.g. end of session, or end of the dataset) -- this function reads whatever is visible to
    that clock and does not advance it. Bars at/before `entry_time` are excluded on principle: the entry
    itself is the decision, never its own outcome."""
    if direction not in ("LONG", "SHORT"):
        raise ValueError(f"direction must be LONG or SHORT, got {direction!r}")
    r_per_share = abs(entry_price - stop)
    base = dict(symbol=symbol.upper(), direction=direction, entry_time=entry_time.isoformat(),
               entry_price=entry_price, stop=stop, target=target, r_per_share=round(r_per_share, 6),
               hypothetical=hypothetical)

    # Real performance defect found running HIST-001's own capacity_opportunity_cost() report at Full scale:
    # calling bars() with no `start` fetches and materializes EVERY visible bar since the dataset's start
    # (up to ~2 years of 5-minute bars) for EVERY hypothetical/blocked candidate resolved, only to
    # immediately discard everything at/before entry_time. provider.bars(..., start=entry_time) does the
    # identical `> entry_time` filtering via searchsorted on the already-sorted column instead of
    # materializing and then discarding rows -- see provider.py's own docstring for the exact equivalence
    # (`side="right"` matches `>`, not `>=`). Byte-identical result, just without the wasted work.
    points = provider.bars(symbol, timeframe=timeframe, start=entry_time)
    if max_bars is not None:
        points = points[:max_bars]

    if not points:
        return OutcomeResult(**base, resolved=False, exit_reason=EXIT_NO_DATA, exit_time=None, exit_price=None,
                             net_r=None, ambiguous=False, mfe_r=0.0, mae_r=0.0, plus_1r_time=None,
                             minus_1r_time=None, target_time=None, stop_time=None, bars_used=0, last_bar_time=None)

    mfe_r = 0.0
    mae_r = 0.0
    plus_1r_time = minus_1r_time = target_time = stop_time = None
    bars_used = 0
    last_bar_time = None

    for p in points:
        bars_used += 1
        last_bar_time = p["t"]
        hi, lo = p["h"], p["l"]

        if direction == "LONG":
            fav_r = _r_multiple(direction, entry_price, hi, r_per_share)
            adv_r = _r_multiple(direction, entry_price, lo, r_per_share)
            target_touched = hi >= target
            stop_touched = lo <= stop
        else:
            fav_r = _r_multiple(direction, entry_price, lo, r_per_share)
            adv_r = _r_multiple(direction, entry_price, hi, r_per_share)
            target_touched = lo <= target
            stop_touched = hi >= stop

        mfe_r = max(mfe_r, fav_r)
        mae_r = min(mae_r, adv_r)          # adverse R is negative by convention (a loss)

        if plus_1r_time is None and fav_r >= 1.0:
            plus_1r_time = p["t"]
        if minus_1r_time is None and adv_r <= -1.0:
            minus_1r_time = p["t"]

        if target_touched and target_time is None:
            target_time = p["t"]
        if stop_touched and stop_time is None:
            stop_time = p["t"]

        if target_touched and stop_touched:
            # Both levels fall inside the SAME bar. A gap-open past one level, with the other never
            # touched by the open itself, is still resolvable (the open IS ordered information); anything
            # else -- both levels genuinely straddled within one bar's range -- cannot be ordered from
            # OHLC alone. Fail toward the UNFAVORABLE assumption, always.
            opened_past_target = (p["o"] >= target) if direction == "LONG" else (p["o"] <= target)
            opened_past_stop = (p["o"] <= stop) if direction == "LONG" else (p["o"] >= stop)
            if opened_past_stop and not opened_past_target:
                exit_reason, exit_price, ambiguous = EXIT_STOP, stop, False
            elif opened_past_target and not opened_past_stop:
                exit_reason, exit_price, ambiguous = EXIT_TARGET, target, False
            else:
                exit_reason, exit_price, ambiguous = EXIT_AMBIGUOUS, stop, True   # conservative: assume stop
            net_r = _r_multiple(direction, entry_price, exit_price, r_per_share)
            return OutcomeResult(**base, resolved=True, exit_reason=exit_reason, exit_time=p["t"],
                                 exit_price=exit_price, net_r=net_r, ambiguous=ambiguous,
                                 mfe_r=round(mfe_r, 4), mae_r=round(mae_r, 4), plus_1r_time=plus_1r_time,
                                 minus_1r_time=minus_1r_time, target_time=target_time, stop_time=stop_time,
                                 bars_used=bars_used, last_bar_time=last_bar_time)
        if target_touched:
            net_r = _r_multiple(direction, entry_price, target, r_per_share)
            return OutcomeResult(**base, resolved=True, exit_reason=EXIT_TARGET, exit_time=p["t"],
                                 exit_price=target, net_r=net_r, ambiguous=False, mfe_r=round(mfe_r, 4),
                                 mae_r=round(mae_r, 4), plus_1r_time=plus_1r_time, minus_1r_time=minus_1r_time,
                                 target_time=target_time, stop_time=stop_time, bars_used=bars_used,
                                 last_bar_time=last_bar_time)
        if stop_touched:
            net_r = _r_multiple(direction, entry_price, stop, r_per_share)
            return OutcomeResult(**base, resolved=True, exit_reason=EXIT_STOP, exit_time=p["t"],
                                 exit_price=stop, net_r=net_r, ambiguous=False, mfe_r=round(mfe_r, 4),
                                 mae_r=round(mae_r, 4), plus_1r_time=plus_1r_time, minus_1r_time=minus_1r_time,
                                 target_time=target_time, stop_time=stop_time, bars_used=bars_used,
                                 last_bar_time=last_bar_time)

    # Ran out of visible data (or max_bars) with neither level touched -- genuinely still open, not a loss
    # or a win. `net_r` is left None (not "0") because a still-open position has no realised R yet; MFE/MAE
    # are the real, honest excursions observed so far.
    return OutcomeResult(**base, resolved=False, exit_reason=EXIT_STILL_OPEN, exit_time=None, exit_price=None,
                         net_r=None, ambiguous=False, mfe_r=round(mfe_r, 4), mae_r=round(mae_r, 4),
                         plus_1r_time=plus_1r_time, minus_1r_time=minus_1r_time, target_time=target_time,
                         stop_time=stop_time, bars_used=bars_used, last_bar_time=last_bar_time)


def resolve_hypothetical(provider: HistoricalMarketProvider, symbol: str, evaluate_result: Dict[str, Any],
                         decision_time: dt.datetime, *, timeframe: str = "5m") -> Optional[OutcomeResult]:
    """Convenience: resolve the outcome AVDI's own `evaluate()` result implies (its `price`/`stop`/`target`
    fields), whether or not the signal was ever actually entered -- this is what answers "what happened to
    the TRADEABLE blocked by capacity" or "the MONITOR just below the gate" (H6 directive requirement).
    Returns None if the result doesn't carry a valid entry/stop/target (e.g. an early REJECT with no
    price)."""
    entry = evaluate_result.get("price")
    stop = evaluate_result.get("stop")
    target = evaluate_result.get("target")
    if entry is None or stop is None or target is None or stop == entry:
        return None
    direction = evaluate_result.get("direction", "LONG")
    return resolve_outcome(provider, symbol, decision_time, float(entry), float(stop), float(target),
                           direction=direction, timeframe=timeframe, hypothetical=True)
