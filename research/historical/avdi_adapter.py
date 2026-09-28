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
from typing import Any, Dict, List, Optional
from unittest import mock

from .clock import HistoricalClock
from .guards import assert_not_production_host
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


def historical_analysis(provider: HistoricalMarketProvider, symbol: str, as_of: Optional[dt.datetime]):
    """`decision_engine._load_analysis`'s PRIMARY (yfinance-fallback) branch, reimplemented against the
    historical provider instead of a live `yf.Ticker(...).history()` call. Reuses `lab.fallback_ta`'s pure
    `_rsi`/`_atr` helpers (the exact formulas the live path uses) — only the data FETCH is substituted,
    because `fallback_ta.analysis()` has no injection seam of its own (it calls yfinance directly) and must
    not be modified: it is production code the forward Ubuntu runtime depends on."""
    import fallback_ta

    points = provider.bars(symbol, timeframe="1d", end=as_of)
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

    def __init__(self, provider: HistoricalMarketProvider, *, neutral_sector: str = "technology"):
        assert_not_production_host()
        self.provider = provider
        self.clock: HistoricalClock = provider.clock
        self.neutral_sector = neutral_sector
        self._stack: Optional[ExitStack] = None
        self.calls: List[Dict[str, Any]] = []            # every patched call this context served, for audit

    # -- patched replacements (each records what it was asked for + what clock.now was) -----------------
    def _patched_bars(self, symbol: str):
        self.calls.append({"fn": "strategies._bars", "symbol": symbol, "clock_now": self.clock.now.isoformat()})
        points = self.provider.bars(symbol, timeframe="1d")
        return _bars_dict(points)

    def _patched_rank_sectors(self, limit_strong: int = 2, limit_weak: int = 1):
        # Sector breadth/rotation has no historical replay yet (disclosed in README.md) — a single fixed,
        # neutral sector is used so the REAL scan()/score_* code still runs unchanged on real per-symbol
        # bars; only the sector-strength INPUT is stubbed, exactly like decision_engine._fam_stub().
        self.calls.append({"fn": "strategies.rank_sectors", "clock_now": self.clock.now.isoformat()})
        strong = {"key": self.neutral_sector, "name": self.neutral_sector, "sector_score": 0.0, "rs_vs_spy_1m": 0.0}
        return {"state": "ok", "ranked": [strong], "strong": [strong], "weak": [],
                "benchmark": None, "freshness": {"state": "historical-neutral"}}

    def _patched_load_analysis(self, symbol: str, exchange: str):
        self.calls.append({"fn": "decision_engine._load_analysis", "symbol": symbol,
                           "clock_now": self.clock.now.isoformat()})
        return historical_analysis(self.provider, symbol, self.clock.now)

    def _patched_safe_regime(self):
        self.calls.append({"fn": "decision_engine._safe_regime", "clock_now": self.clock.now.isoformat()})
        return None                                       # -> _fam_regime() degrades to zero-confidence, unchanged

    def __enter__(self) -> "HistoricalAVDIContext":
        import decision_engine as de
        import halts
        from paper import strategies

        self._stack = ExitStack()
        p = self._stack.enter_context
        p(mock.patch.object(strategies, "_bars", self._patched_bars))
        p(mock.patch.object(strategies, "rank_sectors", self._patched_rank_sectors))
        p(mock.patch.object(de, "_load_analysis", self._patched_load_analysis))
        p(mock.patch.object(de, "_safe_regime", self._patched_safe_regime))
        for name in FAMILIES_WITHOUT_HISTORICAL_REPLAY:
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
