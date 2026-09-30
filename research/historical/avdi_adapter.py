"""H3 — the ONLY historical-aware code that touches the real AVDI decision stack.

Rule (see CLAUDE.md and research/historical/README.md): the existing scanner (`lab.paper.strategies`) and
decision engine (`lab.decision_engine`) run UNCHANGED. This module substitutes their market-data inputs at
the seams the codebase ALREADY exposes for exactly this purpose (the same seams its own unit tests patch:
`decision_engine._load_analysis`, `_safe_regime`, the `_fam_*` family functions, `ss._pick_option_idea`,
and `strategies._bars`) — nothing here is a new "historical mode" branch inside AVDI's own logic.

Evidence families with no historical replay today (SEC filings, catalyst headlines, options flow, social
sentiment, analyst ratings, sector breadth/rotation) are neutralised to the SAME zero-confidence stand-in
(`decision_engine._fam_stub`) the production code already falls back to when a live source is unavailable
— this is a disclosed limitation (see README), not new logic: it reuses the project's own "no data source
yet" contract rather than inventing a historical version of the value.

Two data feeds are used, matching each real caller's own resolution:
  * DAILY bars for `strategies._bars()` (trend filters need 55+ daily closes) and for
    `decision_engine._load_analysis` (reuses `lab.fallback_ta`'s pure RSI/ATR helpers — the same formulas
    the live yfinance-fallback path uses — sourced from the historical provider instead of a live call).
  * Whatever finer timeframe (e.g. 5-minute) the provider was also loaded with, for quotes.
"""
from __future__ import annotations

import datetime as dt
import sys
from contextlib import ExitStack
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
from unittest import mock

from .clock import HistoricalClock
from .guards import assert_not_production_host
from .macro import MacroHistory, historical_macro_signal
from .provider import HistoricalMarketProvider

_ROOT = Path(__file__).resolve().parents[2]
for _p in ("lab", "dashboard", "src"):
    p = str(_ROOT / _p)
    if p not in sys.path:
        sys.path.insert(0, p)

FAMILIES_WITHOUT_HISTORICAL_REPLAY = (
    "_fam_catalyst", "_fam_short", "_fam_filings", "_fam_options_flow", "_fam_social",
    "_fam_analyst", "_fam_macro",
)


def _bars_dict(points: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """`strategies._bars()`'s own return shape, straight from `provider.bars()`'s points."""
    if len(points) < 55:                      # strategies._bars()'s own floor (SMA50 + a margin)
        return None
    return {"closes": [p["c"] for p in points], "vols": [p.get("v") or 0 for p in points],
            "highs": [p["h"] for p in points], "lows": [p["l"] for p in points],
            "as_of": points[-1]["t"]}


def _synthesize_todays_daily_bar(intraday_provider: Optional[HistoricalMarketProvider], symbol: str,
                                 as_of: dt.datetime, last_daily_et_date) -> Optional[Dict[str, Any]]:
    """H5 finding: a LIVE `fallback_ta.analysis()` call, made intraday, benefits from yfinance's own
    behaviour of including TODAY's still-forming daily candle as `h.index[-1]` -- continuously updated as
    the session progresses. A historical bulk daily-bar fetch made AFTER the fact can only ever return
    complete, closed sessions; it has no way to reconstruct "what today's in-progress candle looked like at
    this exact intraday instant" -- a genuine, structural CAPABILITY_DIFFERENCE (not a replay bug), because
    no historical vendor can answer that question after the fact for daily-resolution data.

    Where a genuinely finer intraday feed IS available (H5 blocker #5's execution_provider), this is not a
    capability gap at all: today's in-progress daily bar can be honestly reconstructed by aggregating the
    intraday bars already visible as of `as_of` -- open of the first, running high/low, close of the most
    recent, summed volume -- using ONLY information legally available at the clock's current instant. This
    is real, not fabricated: every input bar is itself a real historical bar filtered through the same
    lookahead-safe `_visible()` the rest of Historical Lab already trusts."""
    if intraday_provider is None:
        return None
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    as_of_et_date = as_of.astimezone(et).date()
    if last_daily_et_date is not None and last_daily_et_date >= as_of_et_date:
        return None                                       # today's daily bar already exists -- no synthesis needed
    # Real performance defect found building HIST-001 Full: calling bars() with no `lookback` fetches EVERY
    # visible 5-minute bar for this symbol since the dataset's start -- tens of thousands of rows once the
    # replay clock is deep into a multi-year dataset -- only to throw away all but one day's worth in the
    # Python filter below. `lookback=300` (25 hours of 5-minute bars -- comfortably more than any single
    # session, including pre/post-market extension, ever needs) bounds the fetch to a small, cheap slice;
    # the date filter below still does the exact same narrowing to `as_of_et_date` either way, so the
    # result is byte-identical to the unbounded fetch -- this changes only how much irrelevant history is
    # fetched and immediately discarded, never which bars end up in `todays`.
    todays = [p for p in intraday_provider.bars(symbol, timeframe="5m", end=as_of, lookback=300)
             if dt.datetime.fromisoformat(p["t"]).astimezone(et).date() == as_of_et_date]
    if not todays:
        return None
    return {"o": todays[0]["o"], "h": max(p["h"] for p in todays), "l": min(p["l"] for p in todays),
           "c": todays[-1]["c"], "v": sum(p.get("v") or 0 for p in todays), "t": todays[-1]["t"]}


def historical_analysis(provider: HistoricalMarketProvider, symbol: str, as_of: Optional[dt.datetime],
                        *, intraday_provider: Optional[HistoricalMarketProvider] = None,
                        daily_points_cache: Optional[Dict[str, Tuple[Any, List[Dict[str, Any]]]]] = None):
    """`decision_engine._load_analysis`'s PRIMARY (yfinance-fallback) branch, reimplemented against the
    historical provider instead of a live `yf.Ticker(...).history()` call. Reuses `lab.fallback_ta`'s pure
    `_rsi`/`_atr` helpers (the exact formulas the live path uses) — only the data FETCH is substituted,
    because `fallback_ta.analysis()` has no injection seam of its own (it calls yfinance directly) and must
    not be modified: it is production code the forward Ubuntu runtime depends on.

    `intraday_provider`, if given, lets today's still-forming daily bar be honestly reconstructed from real
    intraday bars already visible (see `_synthesize_todays_daily_bar`) rather than leaving the analysis
    stuck on yesterday's close (and therefore critically_stale) for the entire length of today's session.

    `daily_points_cache`, if given, memoizes `provider.bars(symbol, "1d", end=as_of)` per symbol, keyed on
    the ET calendar date it was last (re)computed for -- performance only, found building HIST-001 Full: the
    set of COMPLETE prior daily bars visible as of any instant is, by construction, IDENTICAL for every
    cycle within the same calendar day (today's own bar is never in it -- decision_engine only ever sees
    complete, closed sessions), so refetching it ~27 times a day (once per replay cycle) for the same,
    unchanged answer was pure waste at Full's multi-year scale. Keyed by symbol only (not (symbol, date), to
    avoid holding one ever-growing copy of the list PER DATE -- O(days^2) memory across a multi-year run):
    each cache entry is fully OVERWRITTEN, never accumulated, whenever `as_of`'s own date differs from the
    date it was last computed for, so at most one list per symbol is ever held. Only the prior-days fetch is
    cached; `synth` (today's own in-progress bar) is still recomputed fresh every call, since that genuinely
    changes intraday -- the combined `points` list this function computes from is therefore identical to the
    uncached call every time, just without repeating the identical prior-days fetch within a day."""
    import fallback_ta
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    as_of_et_date = as_of.astimezone(et).date() if as_of else None
    cache_entry = daily_points_cache.get(symbol.upper()) if daily_points_cache is not None else None
    if cache_entry is not None and cache_entry[0] == as_of_et_date:
        points = cache_entry[1]
    else:
        points = provider.bars(symbol, timeframe="1d", end=as_of)
        if daily_points_cache is not None:
            daily_points_cache[symbol.upper()] = (as_of_et_date, points)
    last_et_date = None
    if points:
        last_et_date = dt.datetime.fromisoformat(points[-1]["t"]).astimezone(et).date()
    synth = _synthesize_todays_daily_bar(intraday_provider, symbol, as_of, last_et_date)
    if synth is not None:
        points = points + [synth]
    if len(points) < 55:
        return None, "unavailable", "unavailable"
    closes = [p["c"] for p in points]
    highs = [p["h"] for p in points]
    lows = [p["l"] for p in points]
    price = round(closes[-1], 2)
    sma20, sma50 = mean(closes[-20:]), mean(closes[-50:])
    rsi = fallback_ta._rsi(closes)
    atr = fallback_ta._atr(highs, lows, closes)
    chg = round((closes[-1] - closes[-2]) / closes[-2] * 100, 2) if closes[-2] else 0.0
    if price > sma20 > sma50:
        trend = "Strong Uptrend"
    elif price > sma50:
        trend = "Uptrend"
    elif price < sma20 < sma50:
        trend = "Strong Downtrend"
    elif price < sma50:
        trend = "Downtrend"
    else:
        trend = "Sideways"
    mom = "Bullish" if (price > sma20 and rsi > 50) else ("Bearish" if (price < sma20 and rsi < 50) else "Neutral")
    if "Uptrend" in trend and 50 <= rsi <= 72:
        sig = "BUY"
    elif "Downtrend" in trend and 28 <= rsi <= 50:
        sig = "SELL"
    else:
        sig = "NEUTRAL"
    analysis = {
        "symbol": symbol.upper(), "price_data": {"current_price": price, "change_percent": chg},
        "rsi": {"value": rsi}, "atr": {"value": atr, "percent_of_price": round(atr / price * 100, 2) if price else 0.0},
        "trend_state": trend, "market_sentiment": {"momentum": mom, "buy_sell_signal": sig},
        "_source": "historical-replay", "as_of": points[-1]["t"],
    }
    return analysis, "yahoo", "fresh"


class HistoricalAVDIContext:
    """`with HistoricalAVDIContext(provider) as ctx: ctx.scan(); ctx.evaluate("AAPL")` — patches AVDI's
    own market-data seams for the duration of the block, then restores them exactly. Safe to nest a fresh
    instance per clock tick; cheap (no disk I/O of its own beyond what `provider` already loaded)."""

    def __init__(self, provider: HistoricalMarketProvider, *, neutral_sector: Union[str, Sequence[str]] = "technology",
                execution_provider: Optional[HistoricalMarketProvider] = None,
                macro_history: Optional["MacroHistory"] = None):
        assert_not_production_host()
        self.provider = provider
        self.clock: HistoricalClock = provider.clock
        # H5.5: when given, decision_engine._fam_macro is replayed for real (see macro.py) instead of
        # stubbed -- the one FAMILIES_WITHOUT_HISTORICAL_REPLAY entry this closes. None (the default)
        # keeps H3/H4/H5's original behavior (macro stubbed) for every existing caller.
        self.macro_history = macro_history
        # H5: one or more sector keys to treat as "strong" (equal, unranked membership -- see
        # _patched_rank_sectors). A single string is normalized to a one-element list for backward
        # compatibility with H3's original single-sector design.
        self.neutral_sectors: List[str] = [neutral_sector] if isinstance(neutral_sector, str) else list(neutral_sector)
        # H5 blocker #5: decisions (scan/_bars/_load_analysis) read `provider`; execution (quote_for,
        # _live_mark_src -- added by HistoricalExecutionContext) reads `execution_provider` if given,
        # else falls back to `provider` (H3/H4's original single-provider behavior, unchanged for anyone
        # not passing this). Kept as an attribute on the base class, not just the execution subclass, so a
        # caller can inspect which providers are in play from either.
        self.execution_provider: HistoricalMarketProvider = execution_provider or provider
        self._stack: Optional[ExitStack] = None
        self.calls: List[Dict[str, Any]] = []            # every patched call this context served, for audit
        # performance only (see historical_analysis()'s own docstring): memoizes the prior-complete-days
        # daily-bar fetch per symbol for the life of this context -- one real provider.bars() call per
        # symbol per day instead of once per replay cycle (~27/day). Value is (last ET date computed for,
        # points list); overwritten (never accumulated) on a date change, so memory stays O(symbols), not
        # O(symbols x days).
        self._daily_points_cache: Dict[str, Tuple[Any, List[Dict[str, Any]]]] = {}

    # -- patched replacements (each records what it was asked for + what clock.now was) -----------------
    def _patched_bars(self, symbol: str):
        self.calls.append({"fn": "strategies._bars", "symbol": symbol, "clock_now": self.clock.now.isoformat()})
        points = self.provider.bars(symbol, timeframe="1d")
        return _bars_dict(points)

    def _patched_rank_sectors(self, limit_strong: int = 2, limit_weak: int = 1):
        # Sector breadth/ROTATION has no historical replay yet (disclosed in README.md) — every sector in
        # self.neutral_sectors is fed in as equally "strong" (sector_score=0.0, no relative ranking) so the
        # REAL scan()/score_* code still runs unchanged on real per-symbol bars across all of them; only the
        # sector-strength INPUT is stubbed, exactly like decision_engine._fam_stub(). This gives a
        # historical run real MEMBERSHIP coverage for symbols outside a single sector (H5: DELL/AAPL/AMD/
        # MSFT/CRM/NVDA are technology, but META is communication, TSLA is consumer_discretionary, TMO/VRTX
        # are health_care, FCX/NEM are materials) without claiming to replay actual sector rotation/relative
        # strength — a symbol's sector RANK relative to others is still a disclosed CAPABILITY_DIFFERENCE.
        self.calls.append({"fn": "strategies.rank_sectors", "clock_now": self.clock.now.isoformat()})
        strong = [{"key": s, "name": s, "sector_score": 0.0, "rs_vs_spy_1m": 0.0} for s in self.neutral_sectors]
        return {"state": "ok", "ranked": strong, "strong": strong, "weak": [],
                "benchmark": None, "freshness": {"state": "historical-neutral"}}

    def _patched_load_analysis(self, symbol: str, exchange: str):
        self.calls.append({"fn": "decision_engine._load_analysis", "symbol": symbol,
                           "clock_now": self.clock.now.isoformat()})
        # execution_provider defaults to provider itself (H3/H4 single-feed callers) -- in that degenerate
        # case today's bar (if any) is almost always already present in the daily data, so synthesis is a
        # harmless no-op; a genuinely finer execution_provider (H5) lets today's still-forming session
        # contribute real, already-visible information instead of leaving the analysis on yesterday's close.
        return historical_analysis(self.provider, symbol, self.clock.now, intraday_provider=self.execution_provider,
                                   daily_points_cache=self._daily_points_cache)

    def _patched_bar_age_seconds(self, as_of, session_close_hour: int = 16):
        # H5 finding #1: lab/freshness.py's REAL bar_age_seconds() ages a bar against datetime.now(UTC) --
        # the actual wall clock, correct for the live forward runtime, but with no historical-clock
        # awareness at all. Left unpatched, a historical replay judges every bar's freshness against
        # however many real days have passed since THIS SESSION was run (not the replay's own simulated
        # instant), so a bar dated exactly the replay's own "today" reads as critically_stale/"market
        # closed" purely because real wall-clock time has moved on -- found via H5's DELL/META
        # reproduction: `failed_gates=['data_quality','freshness']` was the smoking gun.
        #
        # H5 finding #2, found fixing #1: the ORIGINAL's own "midnight -> session close" adjustment
        # (`dt + timedelta(hours=session_close_hour)`) adds hours in UTC-space to a UTC-midnight
        # timestamp, landing at `session_close_hour` UTC (16:00 UTC = 12:00 ET in EDT), NOT
        # `session_close_hour` ET (16:00 ET = 20:00 UTC in EDT) -- it silently ages a bar from noon
        # instead of the actual 4pm ET close whenever the stored timestamp is genuine UTC midnight (which
        # is exactly what this adapter's daily bars are). This looks like a latent defect in the SAME
        # production function this replaces, not something introduced by patching it -- worth its own
        # look outside Historical Lab; fixed HERE, correctly, using real ET-aware arithmetic, because this
        # function already needs full replacement for clock-awareness regardless.
        self.calls.append({"fn": "freshness.bar_age_seconds", "as_of": as_of, "clock_now": self.clock.now.isoformat()})
        if not as_of:
            return None
        try:
            from zoneinfo import ZoneInfo
            dt_val = dt.datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
            if dt_val.tzinfo is None:
                dt_val = dt_val.replace(tzinfo=dt.timezone.utc)
            dt_utc = dt_val.astimezone(dt.timezone.utc)
            if (dt_utc.hour, dt_utc.minute, dt_utc.second) == (0, 0, 0):
                # The midnight stamp's UTC DATE component is the trading day being labelled (this matches
                # how the bar was written: yahoo_bootstrap.py's OWN daily-bar convention takes yfinance's
                # naive index date directly, not a date re-derived by projecting into ET first -- doing
                # that projection here instead would shift the date backward by one calendar day, since ET
                # is behind UTC, and land on the WRONG day's close entirely.
                trading_day = dt_utc.date()
                dt_val = dt.datetime(trading_day.year, trading_day.month, trading_day.day, session_close_hour,
                                     0, 0, tzinfo=ZoneInfo("America/New_York"))
            age = (self.clock.now - dt_val.astimezone(dt.timezone.utc)).total_seconds()
            return max(0.0, age)
        except Exception:
            return None

    def _patched_session_state(self, now=None):
        # H5 finding, same family as _patched_bar_age_seconds: market_regime.session_state() is a pure,
        # already-parameterized calendar function ("what is the market doing at `now`") -- it already
        # accepts an explicit `now`, but decision_engine._engine_freshness() always calls it with none,
        # so it silently defaults to datetime.now(UTC), the REAL wall clock. A historical replay run for
        # real on a real later date would then classify a perfectly ordinary Friday regular-session
        # instant as whatever the market happens to be doing RIGHT NOW (a weekend, after-hours, etc.),
        # degrading freshness for no causal reason. Only the "no explicit now given" default is
        # overridden; an explicit `now=` from any other caller is passed through unchanged.
        import market_regime as _mr
        self.calls.append({"fn": "market_regime.session_state", "clock_now": self.clock.now.isoformat(),
                           "explicit_now_given": now is not None})
        return self._real_session_state(now if now is not None else self.clock.now)

    def _patched_safe_regime(self):
        self.calls.append({"fn": "decision_engine._safe_regime", "clock_now": self.clock.now.isoformat()})
        return None                                       # -> _fam_regime() degrades to zero-confidence, unchanged

    def _patched_fam_macro(self, direction: str):
        # H5.5: real historical macro replay (see macro.py's module docstring for the point-in-time
        # rationale) -- only installed when self.macro_history is given; see __enter__.
        self.calls.append({"fn": "decision_engine._fam_macro", "direction": direction,
                           "clock_now": self.clock.now.isoformat()})
        return historical_macro_signal(self.macro_history, self.clock.now.date(), direction)

    def _patched_enabled_strategies(self):
        # H5 blocker #6 (volume_trust.py): whichever strategies the REAL config would enable, minus any
        # that need absolute dollar-volume this provider's source can't honestly supply. Patched here (not
        # only in scan()) so EVERY caller that falls back to cfg.enabled_strategies() is covered, including
        # workflow.premarket()'s own internal strategies.scan() call, not just a direct ctx.scan().
        from .volume_trust import enabled_strategies_for
        real = self._real_enabled_strategies()
        chosen = enabled_strategies_for(self.provider.volume_trust, requested=real)
        self.calls.append({"fn": "config.enabled_strategies", "volume_trust": self.provider.volume_trust.value,
                           "requested": real, "enabled": chosen, "clock_now": self.clock.now.isoformat()})
        return chosen

    def __enter__(self) -> "HistoricalAVDIContext":
        import decision_engine as de
        import freshness
        import halts
        import market_regime
        from paper import config as cfg
        from paper import strategies

        self._stack = ExitStack()
        p = self._stack.enter_context
        self._real_enabled_strategies = cfg.enabled_strategies
        self._real_session_state = market_regime.session_state
        p(mock.patch.object(strategies, "_bars", self._patched_bars))
        p(mock.patch.object(strategies, "rank_sectors", self._patched_rank_sectors))
        p(mock.patch.object(cfg, "enabled_strategies", self._patched_enabled_strategies))
        p(mock.patch.object(de, "_load_analysis", self._patched_load_analysis))
        p(mock.patch.object(de, "_safe_regime", self._patched_safe_regime))
        p(mock.patch.object(freshness, "bar_age_seconds", self._patched_bar_age_seconds))
        p(mock.patch.object(market_regime, "session_state", self._patched_session_state))
        for name in FAMILIES_WITHOUT_HISTORICAL_REPLAY:
            if name == "_fam_macro" and self.macro_history is not None:
                p(mock.patch.object(de, name, self._patched_fam_macro))    # H5.5: real replay, not a stub
                continue
            p(mock.patch.object(de, name, (lambda n: lambda *a, **k: de._fam_stub(n))(name)))
        p(mock.patch.object(de.ss, "_pick_option_idea", lambda *a, **k: None))
        p(mock.patch.object(halts, "is_halted", lambda s: False))       # no historical halt feed yet (disclosed)
        return self

    def __exit__(self, *exc):
        self._stack.close()
        self._stack = None

    # -- convenience wrappers over the REAL functions, unchanged ------------------------------------------
    def scan(self, **kw):
        from paper import strategies
        return strategies.scan(**kw)

    def evaluate(self, symbol: str, exchange: str = "NASDAQ", direction: str = "LONG",
                balance: float = 500.0, **kw):
        import decision_engine as de
        kw.setdefault("evaluate_option", False)      # matches the real forward route (canonical bridge Step 10)
        kw.setdefault("portfolio_check", False)       # real portfolio checks run downstream, not here (unchanged)
        kw.setdefault("profile", "momentum")
        return de.evaluate(symbol, exchange, direction, balance=balance, **kw)
