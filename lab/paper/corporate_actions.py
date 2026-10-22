"""Live corporate-action (stock-split) detection and open-position adjustment for the real paper-trading
runtime.

Background: `broker.manage_open_positions()` compares a position's stored `stop`/`target` against the live
quote with zero split awareness. Confirmed in the Historical Lab (AVGO's real 2024-07-15 10-for-1 split
produced a ~17R artificial stop-loss that then tripped the account's own `max_drawdown` circuit breaker —
`research/historical/hist001/split_aware_diagnostic.py`) and, on a 2026-09-30 audit, confirmed identical in
this live path: same unmodified `manage_open_positions()`, no corporate-actions feed anywhere in
`automation/`, `apply_split()` defined in `fills.py` but never called from any live path. This module closes
that gap for the live runtime.

Deliberately standalone, not imported from `research/historical/corporate_actions.py` (a backtesting-only
package that reads a full historical dataframe) — production carries no dependency on the research code.
The core ratio/volume-confirmation ALGORITHM mirrors that module's (day-over-day close ratio outside a
normal band, rounded to a common split factor, corroborated by volume moving inversely), because it is the
same real-world phenomenon; this file reimplements it against a live, two-point (yesterday's close vs
today's quote) comparison instead of a batch dataframe scan.

FAIL CLOSED. Nothing here liquidates a position based on a guess:
  1. CONFIRMED split (ratio cleanly matches a common split/reverse-split factor AND volume corroborates it
     moving inversely by a comparable factor) — adjust the position's own stored state
     (quantity/avg_entry/stop/target/mfe/mae) via the real, already-existing `lab.paper.fills.apply_split()`,
     mirroring exactly what a real broker does to a resting position and stop order across a real split.
     This changes no realized P&L and books no fill of its own — economic exposure and risk dollars are
     invariant under the adjustment (see `apply_split_to_position`'s own tests).
  2. SUSPECTED-BUT-UNCONFIRMED (ratio cleanly matches a plausible split factor, but volume does not
     corroborate it, or volume data is unavailable) — do NOT adjust, and do NOT let
     `manage_open_positions()`'s raw stop/target comparison run against this position on this cycle. Logged
     loudly via `db.audit` for operator review. Self-healing, not an indefinite mask: this check re-runs
     every cycle against the SAME yesterday-close reference, so if better volume data becomes available
     later the same day, it can confirm (and adjust) as soon as that happens; if it never confirms, the
     pause lasts at most until the next trading day's own check, which compares against a fresh reference
     and lets normal stop/target management resume with the real, current price.
  3. NOT A SUSPECTED SPLIT (price moved outside the normal band but the ratio doesn't cleanly match any
     common split factor) — this is an ordinary (if extreme) price move, e.g. a real crash or gap on news,
     not a corporate action. Stop/target management proceeds completely normally and is NEVER paused for
     this case — pausing risk management on a genuine adverse move would be actively harmful, the opposite
     of what this module exists to prevent.

2:1 / reverse 1:2 coverage (resolved before this PR could be considered for merge): the original
[0.4, 2.5] SUSPECT band did not even consider a clean 2-for-1 split (ratio 0.5) or 1-for-2 reverse split
(ratio 2.0) as suspicious at all -- both fell comfortably inside the "ordinary" range. Narrowed to
[0.6, 1/0.6] (see SUSPECT_RATIO_LOW/HIGH's own comment for the exact reasoning and the false-positive
analysis) to bracket both with real margin while still never flagging ordinary moves under ~40%.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Dict, Optional

from .fills import apply_split

# Day-over-day RAW close ratio bounds outside which a split is SUSPECTED at all. Deliberately wide, since a
# false positive costs one flagged, paused cycle-group, and a false negative costs a real artificial
# liquidation, which is much worse.
#
# NARROWED from the historical detector's original [0.4, 2.5] (research/historical/corporate_actions.py's
# own bounds, kept unchanged there -- changing it retroactively would alter already-accepted HIST-001
# results, a separate review this PR does not touch). A clean 2-for-1 split has ratio 0.5 and a clean
# 1-for-2 reverse split has ratio 2.0 -- BOTH fell inside [0.4, 2.5] and were therefore never even
# considered for split detection at all, a real, disclosed blocker flagged on review before this PR could be
# considered for merge. [0.6, 1/0.6] brackets both with real margin (2:1's ratio of 0.5 is comfortably
# inside 0.6; 1:2's ratio of 2.0 is comfortably inside 1.667) while still treating any ordinary single-day
# move up to 40% in either direction as never-suspected at all -- this bound alone does not pause anything;
# a suspected ratio still has to cleanly round to a common factor (_round_to_common_factor's existing 8%
# tolerance, unchanged) AND fail volume corroboration before SplitGuard pauses a position (see
# is_plausible_but_unconfirmed's own docstring) -- an ordinary ~45-55% single-day move that does NOT land
# within ~8% of a clean 2x ratio still falls through to case 3 (ordinary move, never paused), exactly as
# before this change.
SUSPECT_RATIO_LOW = 0.6
SUSPECT_RATIO_HIGH = 1.0 / 0.6   # ~1.667, the exact reciprocal -- keeps the band symmetric in ratio-space

# Common split/reverse-split factors -- identical set to research/historical/corporate_actions.py, so a
# ratio is classified the same way in both places.
_COMMON_FACTORS = (1.5, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 15.0, 20.0)

_ADJUSTABLE_FIELDS = ("quantity", "avg_entry", "stop", "target", "mfe", "mae")


def _round_to_common_factor(x: float) -> tuple:
    """Return (rounded_factor, confidence) for a raw ratio magnitude >= 1.

    Tolerance TIGHTENED from the historical detector's original 8% to 3%, alongside the SUSPECT band
    narrowing above: a real corporate split lands at an essentially EXACT ratio (2.0000, 10.0000--
    defined by the split terms, not organic price action, with at most a fraction of a percent of
    after-hours/pre-market noise around the ex-date), so a tight tolerance costs real splits nothing. An
    organic, unrelated price move landing within 8% of a clean factor is a real coincidence risk once the
    SUSPECT band itself was narrowed to catch 2:1/1:2 -- a genuine ~47% single-day crash (ratio ~0.53,
    magnitude ~1.89) sits only 5.7% from a clean 2.0, which the OLD 8% tolerance would have wrongly let
    through as "plausibly a split," pausing real risk management on a real crash. At 3%, that same crash
    (5.7% error) correctly falls through to case 3 (ordinary move, never paused) -- see
    test_split_guard_never_pauses_an_ordinary_large_move and test_a_45_percent_crash_that_is_not_a_clean_
    split_ratio_is_never_paused."""
    best, best_err = None, None
    for f in _COMMON_FACTORS:
        err = abs(x - f) / f
        if best_err is None or err < best_err:
            best, best_err = f, err
    if best_err is not None and best_err <= 0.03:
        return best, "high"
    return round(x, 4), "low"


@dataclasses.dataclass(frozen=True)
class SuspectedSplit:
    symbol: str
    prior_close: float
    current_price: float
    close_ratio: float             # current_price / prior_close
    inferred_split_ratio: float    # apply_split()'s convention: >1 forward split, <1 reverse split
    prior_volume: Optional[float]
    current_volume: Optional[float]
    volume_confirms: bool
    confidence: str                # "high" | "low"

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


def detect_suspected_split(symbol: str, prior_close: float, current_price: float,
                           prior_volume: Optional[float] = None,
                           current_volume: Optional[float] = None) -> Optional[SuspectedSplit]:
    """Point-in-time, two-value version of `research/historical/corporate_actions.py`'s `detect_splits()`
    inner loop: compares yesterday's real close to today's live price. Returns None for an ordinary
    day-over-day move (inside [SUSPECT_RATIO_LOW, SUSPECT_RATIO_HIGH]) -- most calls return None."""
    if prior_close is None or current_price is None or prior_close <= 0 or current_price <= 0:
        return None
    ratio = current_price / prior_close
    if SUSPECT_RATIO_LOW <= ratio <= SUSPECT_RATIO_HIGH:
        return None
    if ratio < 1:                       # forward split: price fell -> shares should have risen
        mag = 1.0 / ratio
        inferred_ratio, confidence = _round_to_common_factor(mag)
        expected_vol_ratio = inferred_ratio
    else:                                # reverse split: price rose -> shares should have fallen
        rounded, confidence = _round_to_common_factor(ratio)
        inferred_ratio = 1.0 / rounded
        expected_vol_ratio = rounded
    volume_confirms = False
    if prior_volume and current_volume and prior_volume > 0 and current_volume > 0:
        actual_vol_ratio = current_volume / prior_volume if ratio < 1 else prior_volume / current_volume
        # "comparable factor" = within 3x of the price-implied factor either way, same soft-corroboration
        # tolerance as the historical detector (live volume is noisier still: a same-session cumulative
        # volume-so-far compared against a prior FULL trading day's volume, not an apples-to-apples window).
        if expected_vol_ratio > 0 and (expected_vol_ratio / 3.0) <= actual_vol_ratio <= (expected_vol_ratio * 3.0):
            volume_confirms = True
    return SuspectedSplit(symbol=symbol, prior_close=round(prior_close, 4), current_price=round(current_price, 4),
                          close_ratio=round(ratio, 6), inferred_split_ratio=inferred_ratio,
                          prior_volume=prior_volume, current_volume=current_volume,
                          volume_confirms=volume_confirms, confidence=confidence if volume_confirms else "low")


def is_confirmed(ev: SuspectedSplit) -> bool:
    """Identical acceptance rule to the historical detector: high-confidence ratio match AND volume
    corroboration. Anything else is reported but NOT auto-applied (fail closed)."""
    return ev.confidence == "high" and ev.volume_confirms


def is_plausible_but_unconfirmed(ev: SuspectedSplit) -> bool:
    """The genuinely ambiguous case this module's fail-closed pause exists for: the ratio cleanly matches a
    common split factor (so it PLAUSIBLY is a real split), but volume does not corroborate it (or volume
    data is unavailable). `confidence` collapses to "low" in this case too (see `detect_suspected_split`'s
    own confidence-downgrade rule) -- this function re-derives "would have been high on ratio alone" to
    distinguish this case from a ratio that never looked like a split shape in the first place (case 3 in
    this module's docstring), which must NEVER pause real stop/target management."""
    _, ratio_only_confidence = _round_to_common_factor(
        (1.0 / ev.close_ratio) if ev.close_ratio < 1 else ev.close_ratio)
    return ratio_only_confidence == "high" and not ev.volume_confirms


def apply_split_to_position(position: Dict[str, Any], ratio: float) -> Dict[str, Any]:
    """Return a NEW dict (never mutates `position`) with quantity/avg_entry/stop/target/mfe/mae adjusted by
    `apply_split()`'s real, already-existing convention. `realized_pnl` and every other field are passed
    through unchanged -- this operation books no fill, no trade, and no P&L of its own; it only restates the
    SAME economic position (same dollar exposure, same dollar risk) in post-split share terms, exactly as a
    real broker restates a resting position and its stop order across a real split."""
    out = dict(position)
    for field in _ADJUSTABLE_FIELDS:
        val = position.get(field)
        if val is None:
            continue
        # apply_split(quantity, price, ratio) -> (new_quantity, new_price); every one of these fields is a
        # PRICE except quantity itself, so quantity uses the quantity slot and every price field reuses the
        # price slot of a quantity=1.0 call, exactly like split_aware_diagnostic.py's own historical pattern.
        if field == "quantity":
            new_qty, _ = apply_split(val, position.get("avg_entry") or 1.0, ratio)
            out["quantity"] = new_qty
        else:
            _, new_val = apply_split(1.0, val, ratio)
            out[field] = new_val
    return out


class SplitGuard:
    """Per-cycle hook for `workflow.market_hours()`, called BEFORE `broker.manage_open_positions()`.
    Usage:

        guard = SplitGuard()
        paused_symbols = guard.check_and_adjust(open_positions, quotes, prior_close_and_volume)
        managed = broker.manage_open_positions(
            {sym: q for sym, q in quotes.items() if sym not in paused_symbols}, session_date)

    `open_positions`: real rows from `SELECT * FROM positions WHERE status='open'`.
    `quotes`: `{symbol: Quote}`, the same dict `market_hours()` already builds.
    `prior_close_and_volume`: `{symbol: (prior_close, prior_volume_or_None)}` -- yesterday's real daily
    close/volume, e.g. from `research.price_history(symbol, "1M")`'s second-to-last point. Callers that
    cannot supply volume pass `None` for it; `is_confirmed()` then requires the ratio-only classification
    to still corroborate on its own, since `volume_confirms` is always False without volume data -- i.e. a
    plausible-looking ratio with no volume data available is PAUSED, never silently auto-adjusted.

    Returns the set of symbols paused this cycle (never liquidated this cycle) and, as a side effect,
    updates any CONFIRMED position's stored state in the DB via `db.execute` before returning."""

    def __init__(self):
        self.confirmed_this_cycle: list = []
        self.paused_this_cycle: list = []

    def check_and_adjust(self, open_positions, quotes: Dict[str, Any],
                         prior_close_and_volume: Dict[str, tuple]) -> set:
        from . import db

        paused: set = set()
        for pos in open_positions:
            sym = pos["symbol"]
            quote = quotes.get(sym)
            ref = prior_close_and_volume.get(sym)
            if quote is None or ref is None:
                continue
            prior_close, prior_volume = ref
            current_price = quote.last
            current_volume = getattr(quote, "volume", None)
            ev = detect_suspected_split(sym, prior_close, current_price, prior_volume, current_volume)
            if ev is None:
                continue                           # ordinary day -- case 3, never pauses, nothing to do
            if is_confirmed(ev):
                adjusted = apply_split_to_position(pos, ev.inferred_split_ratio)
                db.execute(
                    "UPDATE positions SET quantity=?, avg_entry=?, stop=?, target=?, mfe=?, mae=? "
                    "WHERE position_id=?",
                    (adjusted.get("quantity"), adjusted.get("avg_entry"), adjusted.get("stop"),
                     adjusted.get("target"), adjusted.get("mfe"), adjusted.get("mae"), pos["position_id"]))
                db.audit("position", pos["position_id"], "split_adjusted_live", {
                    "symbol": sym, "ratio": ev.inferred_split_ratio, "prior_close": ev.prior_close,
                    "current_price": ev.current_price, "volume_confirms": True})
                self.confirmed_this_cycle.append(ev.to_dict())
            elif is_plausible_but_unconfirmed(ev):
                paused.add(sym)
                db.audit("position", pos["position_id"], "split_suspected_unconfirmed_paused", {
                    "symbol": sym, "close_ratio": ev.close_ratio, "inferred_split_ratio": ev.inferred_split_ratio,
                    "prior_close": ev.prior_close, "current_price": ev.current_price,
                    "prior_volume": ev.prior_volume, "current_volume": ev.current_volume,
                    "note": "FAIL CLOSED: stop/target management paused for this position this cycle -- "
                            "ratio looks like a plausible split but volume did not corroborate it. Requires "
                            "operator review; see lab/paper/corporate_actions.py."})
                self.paused_this_cycle.append(ev.to_dict())
            # else: ev.confidence == "low" on ratio alone -- case 3, an ordinary (if extreme) price move,
            # never paused.
        return paused
