"""H4 — historical execution. Runs the REAL `lab.paper` workflow/broker/risk/journal/fills code,
UNCHANGED, against an isolated historical ledger and historical (never live) market data.

Per the standing rule (see `research/historical/__init__.py` and the H4 directive): "reuse the current
account state, capacity rules, sector rules, corrected executable-price sizing, risk budget, broker fill
simulator, stop/target behavior, and entry cutoff. Do not create a simplified 'backtest broker.'" This
module does not reimplement any of that logic -- it patches only the market-data/health seams
`lab.paper.workflow`/`lab.paper.risk` already expose, on top of H3's `HistoricalAVDIContext` (which
patches the decision-stack seams). Everything downstream of a quote -- account_fit, position_size,
check_entry (capacity/sector/correlation/cooldown/drawdown caps), fills.simulate, the order lifecycle,
the journal -- is the literal production code path.

Isolation: `paper.db`'s module-level `_DATA_DIR` is the ONE thing every one of those modules reads from
(`journal`, `risk`, `broker`, `options_shadow`, and `shadow_log`, which derives its own DB path from
`db._DATA_DIR` too -- confirmed by reading each module, not assumed). Pointing `_DATA_DIR` at a
historical-only directory via `db.reset_for_tests()` therefore isolates the ENTIRE production stack in one
place, the same seam `tests/conftest.py` already uses for test isolation -- not a new mechanism.

MODULE IDENTITY, the sharp edge this file exists to get right: `avdi_adapter.py` (H3) puts `lab/` on
`sys.path` and imports everything as the FLAT `paper.*` package (`from paper import strategies`), because
that is how `lab/paper/strategies.py`'s own sibling modules import each other internally (relative imports
resolve against whichever dotted name the importing module was first loaded under). Importing the SAME
files instead as `lab.paper.*` (e.g. `from lab.paper import workflow`) creates a SECOND, independent module
object per name in `sys.modules` -- `lab.paper.strategies` and `paper.strategies` end up as two different objects
holding two different copies of module-level state, so patching one's `rank_sectors` attribute has zero
effect on calls made from the other. Every import in this file therefore uses the flat `paper.*` form, to
stay in the SAME module-identity space H3's patches already live in -- `lab.paper.*` must never be mixed in
here, or a patch silently stops applying with no error, only a live network call or a stale mock.

Two hidden LIVE-data paths were found and are patched here (see docstrings below for why each matters):
  * `workflow.quote_for` -- the obvious one; workflow.py's own market-data entry point.
  * `workflow.provider_health` -- premarket() aborts entirely ("no orders planned") if this reports
    unhealthy, and it calls live Yahoo/Finnhub/TradingView checks that have no historical meaning.
  * `risk._live_mark_src` (used directly by both `risk.account_state()` and `broker.account()` to mark
    OPEN positions) -- if left unpatched, a historical replay's reported equity/drawdown would be
    contaminated by TODAY'S real live price for any symbol with a currently-open historical position, a
    serious lookahead defect that has nothing to do with the deliberately-disclosed missing evidence
    families (news/filings/etc.) H3 already flags -- this one would have silently corrupted P&L.

Shadow evidence collection (`shadow_log`, `options_shadow`'s own resolve path) exists to collect live
Challenger evidence for the forward Ubuntu runtime and has no historical-replay meaning; disabled here the
same way H3 disables `_fam_*` families with no historical replay -- a disclosed limitation, not a shortcut,
and not required for correctness (it already writes through the isolated ledger like everything else).
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Dict, List
from unittest import mock

from .avdi_adapter import HistoricalAVDIContext
from .guards import assert_not_production_host, assert_not_production_path, historical_data_root


def isolate_paper_ledger(run_id: str) -> Path:
    """Point `lab.paper.db` at a historical-only SQLite directory under `historical_data_root()`, under a
    ledger NAME that also can never collide with a production filename marker (`db.ledger_name()` defaults
    to `PAPER_LEDGER` or `"robinhood_500_baseline"` -- the real ledger's name -- regardless of directory;
    the guard checks filenames independent of their directory on purpose, so isolation must pick a
    distinct name too, not rely on the directory alone). WIPES any existing file there first -- this is
    `db.reset_for_tests()`'s own documented behavior, and is the correct semantics here too: a historical
    replay starts from a clean $500 account every run, it does not accumulate across runs the way the
    forward Ubuntu ledger does. Guarded twice: once on the directory we ask for, once on the actual path
    `db` resolves afterward (belt & suspenders against a future change to `db.db_path()`'s own logic)."""
    import os

    from paper import db     # flat namespace -- see module docstring on module identity
    assert_not_production_host()
    root = historical_data_root() / "ledgers" / run_id
    assert_not_production_path(root)
    root.mkdir(parents=True, exist_ok=True)
    os.environ["PAPER_LEDGER"] = f"historical_{run_id}"
    db.reset_for_tests(str(root))
    resolved = Path(db.db_path())
    assert_not_production_path(resolved)
    return resolved


class HistoricalExecutionContext(HistoricalAVDIContext):
    """Extends H3's `HistoricalAVDIContext` with the additional seams `lab.paper.workflow`/`risk` own, so
    the REAL `premarket()`/`market_hours()` cycle functions run unmodified against historical data. Call
    `isolate_paper_ledger()` BEFORE constructing this (it does not isolate the ledger itself -- isolation
    and decision-stack patching are independent concerns, and a caller may want the ledger isolated for
    longer than one context's lifetime, e.g. across an entire multi-day walk-forward)."""

    def _patched_quote_for(self, symbol: str):
        self.calls.append({"fn": "workflow.quote_for", "symbol": symbol, "clock_now": self.clock.now.isoformat()})
        return self.provider.quote(symbol)

    def _patched_provider_health(self):
        self.calls.append({"fn": "workflow.provider_health", "clock_now": self.clock.now.isoformat()})
        return {"checked_at": self.clock.now.isoformat(), "healthy": True, "historical_replay": True,
                "providers": {"note": "historical replay -- live provider health has no meaning here"}}

    def _patched_live_mark_src(self, symbol: str):
        self.calls.append({"fn": "risk._live_mark_src", "symbol": symbol, "clock_now": self.clock.now.isoformat()})
        q = self.provider.quote(symbol)
        if q is not None and q.last is not None:
            return float(q.last), "historical_quote"
        return None, "historical_quote_unavailable"          # caller falls back to last fill / avg_entry

    def __enter__(self) -> "HistoricalExecutionContext":
        super().__enter__()
        from paper import risk as risk_mod       # flat namespace -- see module docstring
        from paper import shadow_log, workflow
        p = self._stack.enter_context
        p(mock.patch.object(workflow, "quote_for", self._patched_quote_for))
        p(mock.patch.object(workflow, "provider_health", self._patched_provider_health))
        p(mock.patch.object(risk_mod, "_live_mark_src", self._patched_live_mark_src))
        # No historical replay for Challenger shadow evidence (see module docstring) -- disabled, not
        # reimplemented, same disclosed-limitation pattern as avdi_adapter.py's FAMILIES_WITHOUT_HISTORICAL_REPLAY.
        p(mock.patch.object(shadow_log, "capacity_snapshot", lambda *a, **k: None))
        p(mock.patch.object(shadow_log, "record_cycle", lambda *a, **k: None))
        p(mock.patch.object(shadow_log, "update_bars", lambda *a, **k: None))
        return self


def run_session(ctx: HistoricalExecutionContext, session_date: str, bar_times: List[dt.datetime]) -> Dict[str, Any]:
    """Drive one trading day over its own historical bar grid: `premarket()` (discovery) at the first bar,
    `market_hours()` (fills/stops/targets -- the REAL broker/fills code) at every bar after that. This
    mirrors the forward Ubuntu runtime's own discovery-then-tracker cadence, just compressed onto whatever
    timeframe the dataset natively provides rather than wall-clock minutes. Must be called with `ctx`
    already entered (`with HistoricalExecutionContext(provider) as ctx:`)."""
    from paper import broker, workflow           # flat namespace -- see module docstring

    if not bar_times:
        raise ValueError("run_session requires at least one bar_time (the discovery bar)")
    cycles: List[Dict[str, Any]] = []
    for i, t in enumerate(bar_times):
        ctx.clock.set(t)
        if i == 0:
            cycles.append({"phase": "premarket", "clock_now": t.isoformat(),
                           "result": workflow.premarket(session_date)})
        else:
            cycles.append({"phase": "market_hours", "clock_now": t.isoformat(),
                           "result": workflow.market_hours(session_date)})
    return {"session_date": session_date, "cycles": cycles, "account": broker.account(session_date)}
