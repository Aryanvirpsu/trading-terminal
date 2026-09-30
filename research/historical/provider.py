"""H2 — HistoricalMarketProvider. Mimics the interface AVDI's real market code already expects
(`lab.paper.fills.Quote`; `bars(...)` shaped like `research.price_history()`'s points), bound to a
`HistoricalClock`, so H3's adapter work is "plug this in", not "teach AVDI a new interface".

THE INVARIANT THIS FILE EXISTS TO ENFORCE: no component may read market information timestamped after the
clock's current instant. Every read is filtered to `timestamp <= clock.now`; an explicit request for
information after `clock.now` raises `LookaheadError` rather than silently clamping, because a caller that
asks for the future has a bug worth surfacing, not hiding.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

import pandas as pd

from .clock import HistoricalClock
from .datasets.base import load_parquet
from .volume_trust import VolumeTrust

try:                                   # optional: only used for the return type's shape, never for I/O
    from lab.paper.fills import Quote
except Exception:                      # pragma: no cover - keeps H2 usable even if lab/paper isn't importable
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Quote:                                                       # type: ignore[no-redef]
        symbol: str
        last: Optional[float] = None
        bid: Optional[float] = None
        ask: Optional[float] = None
        open: Optional[float] = None
        high: Optional[float] = None
        low: Optional[float] = None
        volume: Optional[float] = None
        source_ts: Optional[float] = None
        provider: Optional[str] = None


class LookaheadError(RuntimeError):
    """Raised when historical code asked for market information later than the replay clock's `now`."""


class HistoricalMarketProvider:
    """Bound to one `HistoricalClock` and one or more loaded datasets (by dataset_id). All bars for the
    requested symbols are loaded once, at construction, and then every read is filtered by the clock — the
    provider itself never re-touches disk per call, so a walk-forward loop stays fast."""

    provider_name = "historical"

    def __init__(self, clock: HistoricalClock, dataset_ids: List[str], *, synthetic_spread_bps: float = 5.0,
                volume_trust: "VolumeTrust | str" = VolumeTrust.UNKNOWN):
        self.clock = clock
        self.synthetic_spread_bps = synthetic_spread_bps
        # H5 blocker #6: what this provider's volume figures may honestly be used for (see volume_trust.py).
        # Defaults to UNKNOWN, which is treated exactly like RELATIVE_ONLY everywhere -- a provider must be
        # given ABSOLUTE explicitly, it is never assumed.
        self.volume_trust = VolumeTrust(volume_trust)
        frames = [load_parquet(did) for did in dataset_ids]
        df = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
        df = df.sort_values(["symbol", "timestamp"]).drop_duplicates(subset=["symbol", "timestamp"])
        self._by_symbol: Dict[str, pd.DataFrame] = {
            sym: grp.reset_index(drop=True) for sym, grp in df.groupby("symbol", sort=False)}

    # -- the lookahead guard, in one place --------------------------------------------------------------
    def _bound(self, end: Optional[dt.datetime]) -> dt.datetime:
        now = self.clock.now
        if end is None:
            return now
        if end.tzinfo is None:
            raise ValueError("`end` must be timezone-aware")
        end = end.astimezone(dt.timezone.utc)
        if end > now:
            raise LookaheadError(
                f"requested market data as of {end.isoformat()}, but the replay clock is at {now.isoformat()} "
                f"— historical code may never read information from the future")
        return end

    def _visible(self, symbol: str, end: Optional[dt.datetime]) -> pd.DataFrame:
        bound = self._bound(end)
        df = self._by_symbol.get(symbol.upper())
        if df is None or df.empty:
            return pd.DataFrame(columns=["symbol", "timestamp", "open", "high", "low", "close", "volume"])
        # `df` is already sorted by timestamp ascending for this symbol (guaranteed at __init__, by
        # construction, never re-checked per call) -- a boolean mask (`df["timestamp"] <= bound`) scans
        # every row of this symbol's ENTIRE history on every single call, which is O(n) per call and, at
        # Full-scale HIST-001 (multi-year history, thousands of quote()/bars() calls per symbol across a
        # 16k-cycle replay), made the replay run orders of magnitude slower than at Medium scale -- a real
        # performance defect found dry-running Full (a 6-day/108-cycle slice took ~33 minutes; at that rate
        # the full ~26-month replay would have taken days). `searchsorted` finds the same cutoff via binary
        # search (O(log n)) on the already-sorted column -- IDENTICAL result to the boolean mask (proven by
        # a dedicated regression test comparing both on real data), just not re-scanning the whole history
        # on every call. This is a pure performance fix: no row that would have been visible before is
        # excluded now, and no row that would have been invisible is now included -- no decision anywhere
        # in the replay can be affected by which of these two equivalent slicing methods produced its input.
        idx = df["timestamp"].searchsorted(bound, side="right")
        return df.iloc[:idx]

    # -- the two calls the plan asks for -----------------------------------------------------------------
    def quote(self, symbol: str, as_of: Optional[dt.datetime] = None) -> Optional[Quote]:
        """The most recent bar at/before `as_of` (default: clock.now), as a `Quote`. `bid`/`ask` are
        synthesised from `close` with a documented synthetic spread — never a zero spread, and never the
        bar's own high/low, which is where a naive backtest quietly launders lookahead into "the spread"."""
        vis = self._visible(symbol, as_of)
        if vis.empty:
            return None
        row = vis.iloc[-1]
        last = float(row["close"])
        if "bid" in vis.columns and pd.notna(row.get("bid")) and "ask" in vis.columns and pd.notna(row.get("ask")):
            bid, ask = float(row["bid"]), float(row["ask"])
        else:
            half = last * (self.synthetic_spread_bps / 1e4) / 2
            bid, ask = round(last - half, 4), round(last + half, 4)
        return Quote(symbol=symbol.upper(), last=last, bid=bid, ask=ask,
                    open=float(row["open"]), high=float(row["high"]), low=float(row["low"]),
                    volume=float(row["volume"]), source_ts=row["timestamp"].timestamp(),
                    provider=self.provider_name)

    def bars(self, symbol: str, timeframe: str = "5m", end: Optional[dt.datetime] = None,
            lookback: Optional[int] = None, start: Optional[dt.datetime] = None) -> List[Dict[str, Any]]:
        """Points shaped like `research.price_history()["points"]` (`t/o/h/l/c/v`), oldest first, every one
        at/before the bound. `lookback` (if given) keeps only the most recent N of the visible bars —
        it can only shrink the visible window, never extend it past the clock.

        `start`, if given, keeps only bars STRICTLY AFTER `start` (real performance defect found running
        HIST-001's own capacity_opportunity_cost() report at Full scale: `outcomes.resolve_outcome()` called
        `bars(symbol)` with no bound at all, materializing EVERY visible bar since the dataset's start --
        up to ~2 years' worth of 5-minute bars -- for EVERY blocked-TRADEABLE candidate, only to immediately
        Python-filter down to `timestamp > entry_time` and discard the rest. `start` does that same
        filtering via `searchsorted` on the already-sorted column (identical semantics to the `> entry_time`
        Python filter it replaces: `side="right"` on an exact match returns the index of the first row
        STRICTLY greater than `start`, matching `>` exactly, not `>=`) instead of materializing rows that
        get thrown away. Composes with `end`/`lookback` unchanged: `start` only narrows further from the
        near side, exactly like `lookback` narrows from the far side."""
        vis = self._visible(symbol, end)
        if start is not None:
            if start.tzinfo is None:
                raise ValueError("`start` must be timezone-aware")
            start_bound = start.astimezone(dt.timezone.utc)
            idx = vis["timestamp"].searchsorted(start_bound, side="right")
            vis = vis.iloc[idx:]
        if lookback is not None:
            vis = vis.tail(lookback)
        if vis.empty:
            return []
        # `.iterrows()` builds a new pandas Series per row (real, measured overhead: this call profiled as
        # the dominant cost of a single HIST-001 Full premarket() cycle before this fix -- see
        # avdi_adapter.py's _synthesize_todays_daily_bar docstring for the full finding). Extracting each
        # column as a plain numpy array once and zipping them is the same row-by-row output, built without
        # ever constructing a Series -- same points, same order, same values, just not by that path.
        ts = vis["timestamp"].tolist()
        o, h, l, c, v = (vis[col].to_numpy(dtype=float) for col in ("open", "high", "low", "close", "volume"))
        return [{"t": t.isoformat(), "o": float(oo), "h": float(hh), "l": float(ll), "c": float(cc), "v": float(vv)}
               for t, oo, hh, ll, cc, vv in zip(ts, o, h, l, c, v)]

    # -- convenience aggregates that MUST be clock-bound, never "whole day" -----------------------------
    def session_high_so_far(self, symbol: str, session_start: dt.datetime) -> Optional[float]:
        vis = self._visible(symbol, None)
        vis = vis[vis["timestamp"] >= session_start]
        return float(vis["high"].max()) if not vis.empty else None

    def session_low_so_far(self, symbol: str, session_start: dt.datetime) -> Optional[float]:
        vis = self._visible(symbol, None)
        vis = vis[vis["timestamp"] >= session_start]
        return float(vis["low"].min()) if not vis.empty else None
