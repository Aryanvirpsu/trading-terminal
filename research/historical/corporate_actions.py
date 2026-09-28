"""H4 blocker #3 — corporate-action policy, implemented (not just documented; see
`audits/FABHAUS_AUDIT_REPORT.md` §7 for the design rationale this module implements).

Two explicit, never-blended views of price:
  * `raw`            — exactly as delivered by the adapter, immutable. The historical runner trades
                        against THIS view only, by default — matching the forward Ubuntu runtime, which
                        never trades an "adjusted" price; a real fill happens at a real, unadjusted price.
  * `split_adjusted`  — a separate, derived, READ-ONLY projection for cross-period analysis/reporting only
                        (e.g. plotting a multi-year series without a visual discontinuity). Never used for
                        sizing, fills, or gates.

THE INVARIANT THIS FILE EXISTS TO ENFORCE: split metadata must never create lookahead. A split is only
"confirmed" (and only affects `split_adjusted`) once the bar dated on the split day itself exists in the
data — the same day the price actually jumps. Requesting `split_adjusted` `as_of` some instant returns
exactly the adjustment a piece of historical code running AT that instant could have known, never one
based on a split that (from that instant's point of view) hasn't happened yet.

Reuses `lab.paper.fills.apply_split`'s own ratio convention (ratio > 1 = forward split, e.g. 2.0 for a
2-for-1: shares multiply by ratio, price divides by ratio) rather than inventing new split-adjustment math.

Ticker changes, mergers, and delistings are QUARANTINED, not resolved: this module has no reliable
metadata source for them today, and silently remapping or dropping a symbol would be exactly the kind of
survivorship-bias defect the project's own evidence-integrity standard forbids. See `QuarantineEvent`.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Dict, List, Optional

import pandas as pd

try:
    from lab.paper.fills import apply_split
except Exception:                          # pragma: no cover - keeps this module usable standalone
    def apply_split(quantity: float, avg_entry: float, ratio: float) -> tuple:
        if not ratio or ratio <= 0:
            return quantity, avg_entry
        return round(quantity * ratio, 6), round(avg_entry / ratio, 6)

# Day-over-day RAW close ratio bounds outside which a split is SUSPECTED (see FABHAUS_AUDIT_REPORT.md §7).
# Deliberately wide: a false positive costs one flagged-and-quarantined day; a false negative costs a
# silently wrong fill or a silently wrong split-adjusted chart, which is much worse.
SUSPECT_RATIO_LOW = 0.4
SUSPECT_RATIO_HIGH = 2.5

# Common split/reverse-split factors, used only to round a noisy day-over-day ratio to a plausible,
# human-checkable value. A ratio that doesn't round cleanly to one of these is still flagged, just with
# confidence="low" -- it is never silently discarded.
_COMMON_FACTORS = (1.5, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 15.0, 20.0)


def _round_to_common_factor(x: float) -> tuple:
    """Return (rounded_factor, confidence) for a raw ratio magnitude >= 1."""
    best, best_err = None, None
    for f in _COMMON_FACTORS:
        err = abs(x - f) / f
        if best_err is None or err < best_err:
            best, best_err = f, err
    if best_err is not None and best_err <= 0.08:
        return best, "high"
    return round(x, 4), "low"


@dataclasses.dataclass(frozen=True)
class SplitEvent:
    symbol: str
    split_date: str                # the first RAW trading date at the new (post-split) price
    close_before: float
    close_after: float
    close_ratio: float             # close_after / close_before
    inferred_split_ratio: float    # apply_split()'s convention: >1 forward split, <1 reverse split
    volume_before: float
    volume_after: float
    volume_confirms: bool          # did volume move inversely by a comparable factor?
    confidence: str                # "high" | "low"

    # A split is only knowable once the split-date bar itself exists -- i.e. as of its own date, not
    # before. This IS the lookahead boundary; kept as an explicit field (not re-derived ad hoc) so every
    # caller uses the exact same rule.
    @property
    def confirmed_asof_date(self) -> str:
        return self.split_date

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class QuarantineEvent:
    symbol: str
    reason: str                    # "no_further_data" | "unmapped_ticker_change" | "unmapped_merger"
    as_of_date: str
    last_price: Optional[float]
    last_date: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


def _daily_bars(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Collapse intraday RAW bars into one row per calendar date: close = last bar of the date, volume =
    sum of the date's bars. Used only for split DETECTION (a daily-close jump), never for trading."""
    sub = df[df["symbol"] == symbol].sort_values("timestamp")
    if sub.empty:
        return sub
    dates = sub["timestamp"].dt.tz_convert("UTC").dt.date.astype(str)
    g = sub.assign(_date=dates).groupby("_date", sort=True)
    out = g.agg(close=("close", "last"), volume=("volume", "sum")).reset_index()
    out = out.rename(columns={"_date": "date"})
    return out


def detect_splits(df: pd.DataFrame, symbols: Optional[List[str]] = None) -> List[SplitEvent]:
    """Scan RAW canonical bars for day-over-day close jumps outside [SUSPECT_RATIO_LOW, SUSPECT_RATIO_HIGH],
    per symbol. Returns every SUSPECTED split found, sorted by (symbol, date) -- callers decide what to do
    with a low-confidence event (the default runner policy is FAIL CLOSED: see `is_confirmed`)."""
    events: List[SplitEvent] = []
    syms = symbols or sorted(df["symbol"].unique().tolist())
    for sym in syms:
        daily = _daily_bars(df, sym)
        if len(daily) < 2:
            continue
        closes = daily["close"].tolist()
        vols = daily["volume"].tolist()
        dates = daily["date"].tolist()
        for i in range(1, len(closes)):
            c0, c1 = closes[i - 1], closes[i]
            if c0 <= 0 or c1 <= 0:
                continue
            ratio = c1 / c0
            if SUSPECT_RATIO_LOW <= ratio <= SUSPECT_RATIO_HIGH:
                continue                       # ordinary day, not a suspected split
            v0, v1 = vols[i - 1], vols[i]
            if ratio < 1:                       # forward split: price fell -> shares should have risen
                mag = 1.0 / ratio
                inferred_ratio, confidence = _round_to_common_factor(mag)
                expected_vol_ratio = inferred_ratio
            else:                                # reverse split: price rose -> shares should have fallen
                mag = ratio
                rounded, confidence = _round_to_common_factor(mag)
                inferred_ratio = 1.0 / rounded
                expected_vol_ratio = rounded
            volume_confirms = False
            if v0 > 0 and v1 > 0:
                actual_vol_ratio = v1 / v0 if ratio < 1 else v0 / v1
                # "comparable factor" = within 3x of the price-implied factor either way -- volume is noisy
                # (and, per the audit's §6 finding, fabhaus's OWN volume is only RELATIVE_ONLY / partial
                # coverage), so this is a soft corroboration signal, never a hard requirement.
                if expected_vol_ratio > 0 and (expected_vol_ratio / 3.0) <= actual_vol_ratio <= (expected_vol_ratio * 3.0):
                    volume_confirms = True
            events.append(SplitEvent(
                symbol=sym, split_date=dates[i], close_before=round(c0, 4), close_after=round(c1, 4),
                close_ratio=round(ratio, 6), inferred_split_ratio=inferred_ratio,
                volume_before=v0, volume_after=v1, volume_confirms=volume_confirms,
                confidence=confidence if volume_confirms else "low"))
    return events


def is_confirmed(ev: SplitEvent) -> bool:
    """The runner's default acceptance rule for a detected split: high-confidence ratio match AND
    volume corroboration. Anything else is reported but NOT auto-applied (fail closed, per §7)."""
    return ev.confidence == "high" and ev.volume_confirms


def known_as_of(events: List[SplitEvent], as_of_date: str, *, only_confirmed: bool = True) -> List[SplitEvent]:
    """Splits knowable from the vantage point of `as_of_date` (inclusive) -- the lookahead boundary. A
    split dated AFTER `as_of_date` must never affect a `split_adjusted` projection computed as of an
    earlier instant, because nobody at that earlier instant could have known it was coming."""
    out = [e for e in events if e.confirmed_asof_date <= as_of_date]
    if only_confirmed:
        out = [e for e in out if is_confirmed(e)]
    return out


def split_adjusted_view(df: pd.DataFrame, events: List[SplitEvent], *, as_of_date: Optional[str] = None,
                        only_confirmed: bool = True) -> pd.DataFrame:
    """Return a NEW dataframe (never mutates `df`) with `open/high/low/close` divided and `volume`
    multiplied by each applicable split's ratio, for every raw bar strictly BEFORE that split's date.
    `as_of_date`, if given, is the lookahead boundary: only splits already knowable as of that date are
    applied (see `known_as_of`). Omitting `as_of_date` applies every confirmed split unconditionally --
    that mode is for END-OF-STUDY reporting/plotting ONLY, never for anything a historical runner uses to
    make a trading decision at a point in time."""
    out = df.copy()
    applicable = known_as_of(events, as_of_date, only_confirmed=only_confirmed) if as_of_date is not None \
        else [e for e in events if (not only_confirmed) or is_confirmed(e)]
    # Apply most-recent-split-first, so an EARLIER split's adjustment is computed on top of a LATER
    # split's already-adjusted values -- the standard cumulative-adjustment order.
    by_symbol: Dict[str, List[SplitEvent]] = {}
    for e in applicable:
        by_symbol.setdefault(e.symbol, []).append(e)
    for sym, evs in by_symbol.items():
        evs_sorted = sorted(evs, key=lambda e: e.split_date, reverse=True)
        mask_sym = out["symbol"] == sym
        for e in evs_sorted:
            before = mask_sym & (out["timestamp"] < pd.Timestamp(e.split_date, tz="UTC"))
            if not before.any():
                continue
            ratio = e.inferred_split_ratio
            for col in ("open", "high", "low", "close"):
                out.loc[before, col] = out.loc[before, col].apply(
                    lambda px, r=ratio: apply_split(1.0, px, r)[1])
            out.loc[before, "volume"] = out.loc[before, "volume"] * ratio
    return out


def detect_quarantine_candidates(df: pd.DataFrame, universe_symbols: List[str], as_of_date: str,
                                 *, stale_after_days: int = 10) -> List[QuarantineEvent]:
    """A symbol in the point-in-time universe with no RAW bar in the `stale_after_days` calendar days
    before `as_of_date` is quarantined as `no_further_data` -- NOT silently dropped (survivorship bias)
    and NOT assumed delisted (no reliable metadata says so); a caller closes any open historical position
    in it at its last available raw price with this event as the stated reason, reported separately from
    ordinary exits."""
    as_of = pd.Timestamp(as_of_date, tz="UTC")
    cutoff = as_of - pd.Timedelta(days=int(stale_after_days))
    events: List[QuarantineEvent] = []
    for sym in universe_symbols:
        sub = df[(df["symbol"] == sym) & (df["timestamp"] <= as_of)]
        if sub.empty:
            events.append(QuarantineEvent(symbol=sym, reason="no_further_data", as_of_date=as_of_date,
                                          last_price=None, last_date=None))
            continue
        last_row = sub.sort_values("timestamp").iloc[-1]
        if last_row["timestamp"] < cutoff:
            events.append(QuarantineEvent(
                symbol=sym, reason="no_further_data", as_of_date=as_of_date,
                last_price=float(last_row["close"]), last_date=str(last_row["timestamp"].date())))
    return events


def apply_ticker_mapping(df: pd.DataFrame, mapping: Dict[str, str],
                         confirmed_dates: Dict[str, str]) -> pd.DataFrame:
    """Remap an old ticker to a new one -- ONLY when the caller supplies an explicit, dated mapping
    (`mapping = {"OLD": "NEW"}`, `confirmed_dates = {"OLD": "2024-05-01"}`, the date the change took
    effect). With no mapping supplied (the default everywhere in this project today), old-ticker rows are
    left exactly as-is and `detect_quarantine_candidates` will naturally flag "OLD" as `no_further_data`
    once its bars stop appearing -- the safe default. Never called implicitly; a manifest that used this
    must record `mapping` and `confirmed_dates` in its `notes` for provenance."""
    if not mapping:
        return df
    out = df.copy()
    for old, new in mapping.items():
        eff = confirmed_dates.get(old)
        if not eff:
            continue
        mask = (out["symbol"] == old) & (out["timestamp"] < pd.Timestamp(eff, tz="UTC"))
        out.loc[mask, "symbol"] = new
    return out
