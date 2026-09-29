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

Six hidden LIVE-data/wall-clock paths were found and are patched here (see docstrings below for why each
matters):
  * `workflow.quote_for` -- the obvious one; workflow.py's own market-data entry point.
  * `workflow.provider_health` -- premarket() aborts entirely ("no orders planned") if this reports
    unhealthy, and it calls live Yahoo/Finnhub/TradingView checks that have no historical meaning.
  * `risk._live_mark_src` (used directly by both `risk.account_state()` and `broker.account()` to mark
    OPEN positions) -- if left unpatched, a historical replay's reported equity/drawdown would be
    contaminated by TODAY'S real live price for any symbol with a currently-open historical position, a
    serious lookahead defect that has nothing to do with the deliberately-disclosed missing evidence
    families (news/filings/etc.) H3 already flags -- this one would have silently corrupted P&L.
  * `cache_policy.classify` (found via HIST-001's Medium stage, once real TRADEABLE candidates first reached
    the execution path) -- `broker._decision_valid()` calls it with no `now=`, so its real-wall-clock default
    compared a genuinely historical quote timestamp against the REPLAY's real run-time, refusing every single
    entry as artificially "stale" (a ~2.5-year apparent age) regardless of how good the setup was.
  * `db.utcnow()` (found auditing every remaining wall-clock call once the above was fixed) -- has no
    override parameter at all, so every `opened_at`/`closed_at`/`created_at`/`outcome_at`/`filled_at`
    timestamp written anywhere in the ledger during a historical replay was the REAL run-time, not the
    historical trade time. This one is doubly dangerous: it doesn't just misclassify one check, it corrupts
    stored data that later analysis (including this project's own `analysis.py`, which buckets positions by
    their own `opened_at` date) reads back as if it were ground truth.
  * `risk.cooldown_state()` -- exposes no seam at all (`dt.date.today()` inline, no parameter), and even
    after `db.utcnow()` is fixed so `closed_at` is genuinely historical, comparing that historical `until`
    date against the REAL wall-clock date means the consecutive-loss cooldown gate can never trigger during
    a replay (real "today" is always past any historical "until"). Patched via call-through: the real
    function's streak/threshold/`until` computation runs completely unchanged; only the final `today <=
    until` comparison is corrected to use the replay clock.
  Unlike the first three (which would either call out to a live network or serve a stale-but-labeled mock),
  the last two silently produced WRONG RESULTS with no error and no live call -- the more dangerous failure
  mode, since nothing about them looked broken from the outside.

Shadow evidence collection (`shadow_log`, `options_shadow`'s own resolve path) exists to collect live
Challenger evidence for the forward Ubuntu runtime and has no historical-replay meaning; disabled here the
same way H3 disables `_fam_*` families with no historical replay -- a disclosed limitation, not a shortcut,
and not required for correctness (it already writes through the isolated ledger like everything else).

H5 blocker #5 -- decision vs. executable-price vs. outcome granularity: `HistoricalAVDIContext.provider`
supplies DECISION data (whatever `strategies._bars`/`decision_engine._load_analysis` need -- normally
daily bars for trend/RSI). `HistoricalExecutionContext` additionally accepts `execution_provider` (passed
through the base class): `quote_for`/`_live_mark_src` read EXECUTABLE-PRICE data from it instead, so a
finer intraday feed can back real fills/marks without ever leaking a finer bar's information into the
daily trend calculation, and vice versa. Passing no `execution_provider` falls back to `provider` for both
(H3/H4's original single-feed behavior, unchanged for any existing caller). OUTCOME/tracking data (stop/
target checks in `market_hours()`) reads through the same execution provider, which is why this alone is
enough to satisfy "subsequent complete bars only": `HistoricalMarketProvider` already filters every read to
`timestamp <= clock.now` (H2's lookahead guard) and the clock only ever advances, so a stop/target check at
cycle N can only see bars up to and including cycle N's own instant, never a later one. The provider's
`quote()` fill price is the bar's CLOSE with a symmetric synthetic spread around it (never the bar's own
high or low) -- the conservative, non-favorable-side choice this blocker calls for, already true of H2's
original design and simply documented here rather than reinvented.
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
        # H5 blocker #5: execution reads self.execution_provider (finer timeframe when the caller supplied
        # one), never self.provider (the decision-making/trend feed) -- see the module docstring's
        # "decision vs executable-price vs outcome granularity" section.
        self.calls.append({"fn": "workflow.quote_for", "symbol": symbol, "clock_now": self.clock.now.isoformat()})
        return self.execution_provider.quote(symbol)

    def _patched_provider_health(self):
        self.calls.append({"fn": "workflow.provider_health", "clock_now": self.clock.now.isoformat()})
        return {"checked_at": self.clock.now.isoformat(), "healthy": True, "historical_replay": True,
                "providers": {"note": "historical replay -- live provider health has no meaning here"}}

    def _patched_live_mark_src(self, symbol: str):
        self.calls.append({"fn": "risk._live_mark_src", "symbol": symbol, "clock_now": self.clock.now.isoformat()})
        q = self.execution_provider.quote(symbol)
        if q is not None and q.last is not None:
            return float(q.last), "historical_quote"
        return None, "historical_quote_unavailable"          # caller falls back to last fill / avg_entry

    def _patched_cache_policy_classify(self, category, source_ts, now=None):
        # A THIRD hidden live-data path, found only once HIST-001's Medium stage produced real TRADEABLE
        # candidates to execute (Smoke never reached this code -- its zero decisions never got past the
        # scanner): `lab.paper.broker._decision_valid()` calls `cache_policy.classify("price", quote.source_ts)`
        # with no `now=` argument, so `classify()`'s own default (`time.time()`, real wall-clock "now")
        # compares a genuinely historical quote timestamp (e.g. 2024-03-08) against the REAL replay-time
        # clock (e.g. 2026-09) -- a ~2.5-year apparent staleness that refused EVERY single entry attempt as
        # "display-only", with the specific, misleading reason text "price data is display-only (source age
        # 80740713s > limit 900s)". This is a real timestamp/freshness-semantics correctness bug in Historical
        # Lab's clock-patching coverage (the standing correctness-freeze's own explicit carve-out), not a
        # Champion strategy defect -- `cache_policy.classify()` already accepts an explicit `now`, so this
        # patches ONLY that default, the same call-through pattern as every other seam in this file.
        if now is None:
            now = self.clock.now.timestamp()
        return self._real_cache_policy_classify(category, source_ts, now=now)

    def _patched_db_utcnow(self) -> str:
        # A FOURTH hidden wall-clock path, found investigating the cache_policy fix further: `paper.db.utcnow()`
        # (real `datetime.now(timezone.utc).isoformat()`, no override parameter at all) is what
        # `broker._apply_fill_to_position()` stamps every position's `opened_at`/`closed_at` with, and what
        # `journal`/`options_shadow`/`shadow_log` stamp every `created_at`/`outcome_at`/`filled_at` with. Left
        # unpatched, every position in a historical replay carries the REAL run-time as its open/close
        # timestamp (e.g. "2026-09-29..." for a trade that actually happened on "2024-03-08") -- silently
        # wrong in two compounding ways: (1) any reporting code that filters/buckets positions by their own
        # opened_at date (this project's own analysis.py, e.g.) would filter out every real historical trade,
        # since no real position's opened_at date could ever fall inside a historical evaluation window; (2)
        # it feeds `risk.cooldown_state()`'s `until` calculation with a real-run-time `closed_at`, which
        # `_patched_cooldown_state` below corrects for separately. `db.utcnow()` has no strategy logic
        # whatsoever -- it is pure timestamp generation -- so replacing it outright (not a call-through
        # override of an optional param, since it takes none) changes no Champion decision, only what "now"
        # means during replay, exactly like every other patch in this file.
        self.calls.append({"fn": "db.utcnow", "clock_now": self.clock.now.isoformat()})
        return self.clock.now.isoformat()

    def _patched_cooldown_state(self):
        # Even after `db.utcnow()` is corrected (so `closed_at` is genuinely historical), `risk.cooldown_state()`
        # independently calls `dt.date.today()` with NO override parameter to decide whether a cooldown
        # triggered by a historical loss streak is still `active` -- comparing a real historical `until` date
        # (e.g. "2024-03-18") against the REAL wall-clock date (e.g. "2026-09-29") always reads `today > until`,
        # so the cooldown gate would silently NEVER trigger during any historical replay, regardless of how
        # many consecutive historical losses occurred. `cooldown_state()` exposes no seam to inject a
        # historical "today" through, so this calls the REAL function through unchanged (same streak-counting,
        # same `cooldown_losses`/`cooldown_days` config, same `until` computation) and corrects ONLY the final
        # wall-clock-dependent comparison using the replay clock's own date -- no cooldown THRESHOLD or
        # strategy parameter is touched, only which "today" the existing threshold is compared against.
        self.calls.append({"fn": "risk.cooldown_state", "clock_now": self.clock.now.isoformat()})
        rec = self._real_cooldown_state()
        if rec.get("until"):
            rec = dict(rec)
            rec["active"] = self.clock.now.date().isoformat() <= rec["until"]
        return rec

    def __enter__(self) -> "HistoricalExecutionContext":
        super().__enter__()
        from paper import db, risk as risk_mod       # flat namespace -- see module docstring
        from paper import shadow_log, workflow
        import cache_policy                       # flat namespace -- lab/ on sys.path, same as every sibling
        p = self._stack.enter_context
        p(mock.patch.object(workflow, "quote_for", self._patched_quote_for))
        p(mock.patch.object(workflow, "provider_health", self._patched_provider_health))
        p(mock.patch.object(risk_mod, "_live_mark_src", self._patched_live_mark_src))
        self._real_cache_policy_classify = cache_policy.classify
        p(mock.patch.object(cache_policy, "classify", self._patched_cache_policy_classify))
        p(mock.patch.object(db, "utcnow", self._patched_db_utcnow))
        self._real_cooldown_state = risk_mod.cooldown_state
        p(mock.patch.object(risk_mod, "cooldown_state", self._patched_cooldown_state))
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
