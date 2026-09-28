# AVDI Historical Lab

A thin research harness around the **existing** AVDI decision stack (scanner, canonical A/B/C/D/E,
Champion/Challenger sizing, risk, the paper broker's fill simulator). It supplies its own clock, market
data provider, isolated account/ledger, and walk-forward/outcome/event-identity machinery around that
stack — it does not duplicate any of AVDI's decision logic. See `CLAUDE.md` for the standing rule this
work is held to.

Install (separate from the production dependency set — never installed on the Ubuntu runtime):
```
pip install -r research/historical/requirements.txt
```
Run its tests: `python -m pytest tests/historical -q` (add `-m "not network"` to skip the one live-data test).

## Status (2026-09-28)

| Phase | What | Status |
|---|---|---|
| **H0** | Production isolation guard (`guards.py`) — refuses any path resolving into `/data/case1(2)`, `.tradingview_mcp_data`, the real ledger/shadow filenames, or a host fingerprinted as the Ubuntu runtime | **Done.** `tests/historical/test_production_isolation.py` (26 tests) |
| **H1** | Canonical bar schema + validation (`schemas/bars.py`), dataset manifest with SHA-256 provenance (`manifest.py`), source-agnostic adapter interface (`datasets/base.py`), a generic (config-driven) Hugging Face adapter, and a Parquet+DuckDB store | **Done.** `tests/historical/test_h1_dataset_foundation.py` (14 tests incl. one real 5-symbol pull) |
| **H2** | `HistoricalClock` + `HistoricalMarketProvider`, with a hard lookahead guard | **Done.** `tests/historical/test_h2_clock_and_lookahead.py` (18 tests) |
| **H3** | Wire the historical provider into the real scanner/decision-engine data-fetch points (adapters only, no duplicated logic) | **Done.** See below |
| **H4** | Corporate-action layer, capability fingerprint, reproducibility check, thin execution reuse (real risk/broker/fills/journal against an isolated ledger) | **Done.** See below |
| **H5** | Reproduce the known 2026-09-25 Ubuntu forward session | **Done — PASS WITH DOCUMENTED CAPABILITY DIFFERENCES.** `research/historical/reports/H5_FORWARD_REPRODUCTION.md` |
| **H5.5** | Unlock one more historical evidence family to cross the data_quality ceiling | **Done. Real macro replay crosses the floor in real replay behavior; DELL/META/TMO reach TRADEABLE under `PRICE_TREND_MACRO_V1`, unchanged thresholds.** `research/historical/reports/H55_MACRO_REPLAY.md` |
| **H6** | Causal outcome engine (target/stop/ambiguous, MFE/MAE, hypothetical candidates) | **Done.** `outcomes.py`, 13 tests + 3 integration tests |
| **H7** | One canonical event/observation/decision/trade identity system | **Done.** `event_identity.py`, 7 tests |
| **H8** | Walk-forward orchestration with a holdout that cannot be casually spent | **Done.** `walkforward.py`, 12 tests |
| **H9** | MLflow experiment tracking | **Done.** `experiment_tracking.py`, 9 tests |
| H10-H15 | Funnel attribution, HIST-001..004, regime attribution, options, Nautilus cross-check, Optuna | Not started |

### The Hugging Face dataset gap (disclosed, not papered over)

Searched the Hugging Face datasets API directly (`ohlcv`, `us equities minute bars`, `nasdaq intraday`,
`stock market ohlc`, `yahoo finance stock`, `stooq`, `polygon.io`, `daily stock prices csv`, and more) —
**no freely-licensed dataset providing intraday US-equity OHLCV bars for arbitrary tickers exists on the
Hub today.** `datasets/huggingface_equities.py` is built and tested as a generic, config-driven adapter
(point it at a real `dataset_id` + column map once one exists, or once this project's own data is
uploaded to the Hub) but is **not wired to any specific dataset id** — doing so would have meant either
fabricating an id that might not exist or silently mis-mapping a dataset's actual columns.

The H1 acceptance sample (AAPL/MSFT/META/NVDA/DELL, 5-minute bars, a few days) instead uses
`datasets/yahoo_bootstrap.py`, clearly labelled `source="yahoo-bootstrap"` in its manifest so it can never
be mistaken for a Hugging Face import. Yahoo's public intraday history is short and rate-limited — fine
for this acceptance sample, not a basis for years of walk-forward research. **A real bulk data source
(vendor, a specific verified HF dataset, or your own uploaded parquet) is needed before H4 onward can do
anything beyond replaying the last few days.**

## Guarantees (tested)

* No historical path can resolve into the production ledger/shadow directory or filenames.
* No historical process can start on a host fingerprinted as the Ubuntu production runtime.
* Every dataset is schema-validated (OHLC ordering, non-negative volume, monotonic per-symbol timestamps,
  no duplicate symbol/timestamp rows) before it is stored, and every stored file is content-hashed and
  re-verifiable against its manifest.
* The market data provider cannot return, or be asked for, information timestamped after its bound
  clock's current instant — an explicit request for the future raises, it is never silently clamped.

## H3 (done)

`avdi_adapter.py` (`HistoricalAVDIContext`) patches AVDI's own existing seams — `strategies._bars`,
`strategies.rank_sectors`, `decision_engine._load_analysis`, `_safe_regime`, the 7 `_fam_*` families with
no historical replay (routed to the production `_fam_stub` — the SAME zero-confidence contract the live
system already uses when a source is down), `ss._pick_option_idea`, `halts.is_halted` — then runs
`strategies.scan()` and `decision_engine.evaluate()` completely unchanged. `_load_analysis`'s replacement
reuses `lab.fallback_ta`'s own `_rsi`/`_atr` pure helpers (only the data FETCH is substituted, since
`fallback_ta.analysis()` calls yfinance directly with no injection seam and is production code that must
not be touched).

Verified (`tests/historical/test_h3_avdi_wiring.py`, 4 tests):
* a deterministic, no-network fixture proves the real `decision_engine.evaluate()`'s own `price` field and
  the real `_fam_trend()` never reflect information later than the clock, ARE identical when queried twice
  at the same instant, and DO change correctly once the clock genuinely advances past a planted price move;
* an explicit request for the future is refused THROUGH the adapter (not just at the raw provider);
* patches restore exactly on context exit;
* the real-data acceptance smoke test (network) runs the literal 8 acceptance steps against a real 5-symbol
  Yahoo daily sample: real scanner, real decision path, captured finalists/labels, a second independent
  clock/context at an earlier instant, no exception, no lookahead.

Disclosed limitation: sector breadth/rotation and 7 of the decision engine's 9 evidence families (catalyst,
short interest, filings, options flow, social, analyst, macro) have no historical replay yet and are
neutralised to zero confidence for every historical run — only the price-derived trend/momentum family and
the risk/regime family (itself neutral when no regime is supplied) carry real signal today. A historical
decision label should be read with that in mind until more evidence families are wired.

## Dataset audit (done, corrected): `fabhaus/equities_5m_stockprices`

Full report: `audits/FABHAUS_AUDIT_REPORT.md`. **Verdict: ACCEPTED for H4**, conditional on the
corporate-action detector/quarantine logic actually running (it now does — see H4 below) and volume being
treated as `RELATIVE_ONLY` (also enforced below, not just documented).

The audit's own first pass concluded the `datetime` field was mislabelled (America/New_York wall-clock with
an incorrect "Z" suffix), based on a 260 MB sample window that happened to truncate before the real market
close. **That conclusion was wrong and has been corrected in the report**, after re-testing across 6 dates
spanning both seasons and both 2024 DST transition boundaries, for an ETF and an equity: the timestamps are
genuinely, correctly UTC. `source_tz` remains a generic `HuggingFaceEquitiesAdapter` feature (for a source
that genuinely has this problem) but must NOT be used for fabhaus. Volume is characterized (not fixed) as a
consistent 20-46% of Yahoo's consolidated daily volume across every symbol checked — marked `RELATIVE_ONLY`;
no absolute-volume rule may run against this source. The dataset's lack of pre-applied corporate-action
adjustment is confirmed (NVDA's 2024-06-07 10:1 split shows up exactly as expected) and is now handled by
`corporate_actions.py` (raw/split_adjusted, detection, lookahead-safe application, quarantine).

Secondary candidate `GGLabYale/MTBench_finance_stock` (2013-2023 coverage) is recorded, not integrated.

## H4 (done)

The H4 directive's five blockers, all done:

1. **Timezone re-audit** — corrected in the audit report (see above); `research/historical/audits/fabhaus_tz_reaudit.py` + `fabhaus_tz_sample/` are the reproducible evidence.
2. **Volume characterization** — `RELATIVE_ONLY` verdict in the audit report §6; enforced at runtime by `volume_trust.py` (H5 closed the "not yet a runtime assertion" gap noted here originally — see H5 below): `HistoricalMarketProvider.volume_trust` + `enabled_strategies_for()` drop `score_liquid_momentum` (the only strategy with an absolute-dollar-volume floor) from a non-`ABSOLUTE` source's enabled strategies, enforced at `paper.config.enabled_strategies()` so every caller is covered.
3. **Corporate actions, implemented** — `corporate_actions.py`: `detect_splits()` (day-over-day RAW close ratio outside [0.4, 2.5], corroborated by an inverse volume move where available), `is_confirmed()` (fail-closed default), `split_adjusted_view(as_of_date=...)` (lookahead-safe: a split only affects the view from its own date onward, never retroactively into an earlier as-of instant), `detect_quarantine_candidates()` and `apply_ticker_mapping()` (quarantine, never silent remap). Tested in `tests/historical/test_h4_corporate_actions.py`.
4. **Capability fingerprint** — `capability.py`'s `PRICE_TREND_ONLY_V1`, naming exactly which decision-engine families a historical run replays vs. stubs, plus the volume status. Added to `DatasetManifest.capability_fingerprint`. A historical result must always carry this fingerprint and must never be described as a backtest of the full forward Champion.
5. **Repeatable ingestion check** — `tests/historical/test_h4_capability_and_reproducibility.py` proves `import_and_store()` is byte-identical (same rows, same content hash, same manifest) for identical inputs, and that the check actually detects a real change (tested both directions). The live-source version of this property (same HF revision + byte range → byte-identical bytes) was demonstrated by hand in the audit report §8 via the `git-lfs` ETag and a repeated extraction.

**Execution, thin reuse (`execution.py`)**: `isolate_paper_ledger()` points `paper.db`'s module-level
`_DATA_DIR` at a historical-only SQLite directory (the same seam `tests/conftest.py` already uses) — this
isolates `journal`, `risk`, `broker`, `options_shadow`, and `shadow_log` all at once, since every one of
them reads `db._DATA_DIR` rather than caching its own copy (confirmed by reading each module).
`HistoricalExecutionContext` (extends H3's `HistoricalAVDIContext`) additionally patches `workflow.quote_for`,
`workflow.provider_health`, and `risk._live_mark_src` to route through historical data. `run_session()` then
calls the REAL, unmodified `workflow.premarket()`/`workflow.market_hours()` — i.e. the real
capacity/sector/correlation/cooldown/drawdown checks, the real executable-price sizing, the real fill
simulator, the real order lifecycle and journal — against the isolated ledger. Tested in
`tests/historical/test_h4_execution.py` (5 tests), including a test that forces any real network call to
raise, proving every live path really is intercepted rather than coincidentally unreached.

Two real defects were found and fixed while wiring this, beyond what H3 already covers:

* **`risk._live_mark_src()`** (used by both `risk.account_state()` and `broker.account()` to mark OPEN
  positions) calls a live Yahoo/Finnhub quote directly — unpatched, a historical replay's reported
  equity/drawdown would have been silently contaminated by TODAY's real price for any symbol with an open
  historical position. This is a correctness defect distinct from H3's disclosed missing-evidence-families
  limitation, now patched.
* **`workflow.provider_health()`** makes live provider checks and `premarket()` aborts entirely
  ("no orders planned") if it reports unhealthy — now patched to report synthetic health for historical runs.
* **Module identity**: `avdi_adapter.py` imports the decision stack as the flat `paper.*` package (`lab/`
  on `sys.path`), because that is how the AVDI package's own internal relative imports resolve. Importing
  the same files as `lab.paper.*` instead creates a SECOND, independent module object per name — a patch
  on one has zero effect on the other, with no error, just a silently-unpatched live call. `execution.py`
  and its tests therefore import everything through the flat `paper.*` form; this is documented prominently
  in `execution.py`'s module docstring so it isn't re-discovered the hard way later.

**Remaining, disclosed limit**: `tests/historical/` mutates `sys.path` process-wide on import (via
`avdi_adapter.py`'s `lab`/`dashboard`/`src` insertion) and must be run in its OWN pytest process, never in
the same session as `tests/unit/` — confirmed pre-existing since H3, not introduced by H4/H5. CI's separate
`historical-lab` job already does this correctly; running `python -m pytest tests/` (no path filter)
locally will show unrelated failures in `tests/unit/` for this reason — always target `tests/historical`
and `tests/unit` in separate invocations.

## H5 (done — PASS WITH DOCUMENTED CAPABILITY DIFFERENCES)

Full report: `research/historical/reports/H5_FORWARD_REPRODUCTION.md`; machine-readable artifacts at
`research/historical/reports/h5_artifacts.json`. Reproduced the real 2026-09-25 Ubuntu forward session
(`docs/UBUNTU_LIVE_ACCEPTANCE_01.md`) against a real, committed Yahoo-sourced dataset
(`research/historical/known_forward/`, `KNOWN_FORWARD_2026_09_25_{DAILY,5M}` — separately versioned, never
mixed with the fabhaus corpus), driving the exact 27-cycle discovery schedule through the real, unmodified
`workflow.premarket()`/`market_hours()`.

Closed H4's remaining "documented, not enforced" gap: `execution.py`'s `HistoricalExecutionContext` now
accepts a separate `execution_provider` (finer timeframe for quotes/marks, independent of the
decision-making daily feed — H4's old single-feed limitation).

**Found and fixed three real mechanical bugs** while diagnosing why the replay wasn't reproducing DELL/
META's TRADEABLE transitions: `lab/freshness.py:bar_age_seconds()` and
`dashboard/market_regime.session_state()` both read the real wall clock instead of the historical clock
(now patched in `avdi_adapter.py`), and `yahoo_bootstrap.py` labelled daily bars at UTC midnight instead of
their own session close — a real same-day lookahead leak, now fixed (DST-correct, per-row). Also added:
today's still-forming daily bar can be honestly reconstructed from real, already-visible intraday bars when
a finer feed is available, closing a real freshness gap a pure single-daily-feed replay can't otherwise
close.

**Central finding**: with those fixed, DELL and META both reach the real decision pipeline with strong
technicals but cap out at exactly `data_quality.overall=0.536`, just under the unchanged 0.55 minimum — a
**structural ceiling under `PRICE_TREND_ONLY_V1`** (fundamentals/news/analyst/filings/macro/options
permanently at 0% coverage) meaning **no historical decision can reach TRADEABLE under the current
capability fingerprint, on any symbol or date**. This is a quantified constraint for H6+ to carry forward,
not a defect in H5.

**H5 passed.** Per the project's own ordering: H4 execution → H5 (done) → H6-H9 (done, see below) →
H5.5 (done) → **HIST-001 Champion baseline is now unblocked** → CH-001/capacity/ranking experiments.
HIST-001 must still carry the AMD/`PRICE_DATA_DIFFERENCE` caveats H5.5 disclosed (see below).

## H5.5 (done — real macro replay crosses the data_quality floor in real replay behavior)

Full report: `research/historical/reports/H55_MACRO_REPLAY.md`. Investigated the H5 ceiling before
implementing anything, per the user's own instruction.

**Correction to the original framing**: `data_quality`'s `"fundamentals"` category (weight 0.35) is never
populated by `decision_engine.py` at all — not historically, not in live production either; there is no
`_fam_fundamentals`. What actually maps onto the user's "fundamentals/filings" and "macro" instincts:
`_fam_filings` (SEC EDGAR) and `_fam_macro` (FRED) — both real, live-wired families already in
`FAMILIES_WITHOUT_HISTORICAL_REPLAY`, simply stubbed for historical replay. Macro was implemented first
(cheaper: one shared time series per date vs. filings' per-symbol SEC history, for the identical structural
gain).

`macro.py`: real ALFRED vintage semantics (`MacroHistory.latest_value_as_of()`, proven against the
directive's own revision example — X before a revision, Y at/after, never Y early), `historical_macro_signal()`
reproducing `lab/fred.py`'s exact arithmetic. A real `FRED_API_KEY` was supplied and used only as an
in-process env var (never written to disk/logged/committed); fetching real data corrected two
assumptions the mechanism-only build had made — the real `output_type=2` vintage-column date format needed
normalizing to ISO, and FEDFUNDS turned out to be monthly, not daily, as originally guessed (irrelevant to
the signal either way, since FEDFUNDS is dead weight in the real formula — but the record is now the
verified fact, not the guess). Zero revisions found in the real data for DGS10/DGS2/VIXCLS, consistent with
the `SERIES_INTEGRITY` verdict.

**The rerun, in real replay behavior (not static arithmetic)**: under `PRICE_TREND_MACRO_V1`, DELL, META,
and TMO — the exact three symbols H5 found capped by the ceiling — now reach **TRADEABLE**, through the
real, unmodified `evaluate()`/`data_quality.py`/`decision_engine.py` code, with the 0.55 floor untouched.
DELL's own `data_quality` crosses `0.536 → 0.575` in the same process, same instant, with only the dataset
switched in. One new divergence surfaced and was investigated rather than hidden: **AMD**, which stayed
MONITOR-only in the forward session, now also reaches TRADEABLE — because macro evidence is symbol-agnostic
(it lifts every symbol whose own technicals are otherwise strong enough that day, not only the ones the
forward session happened to trade); classified `PRICE_DATA_DIFFERENCE`, most likely explained by AMD's real
Yahoo-sourced technicals differing from the forward session's own live provider mix, not independently
confirmed. Zero `UNKNOWN`/`REPLAY_BUG` rows either before or after.

`KNOWN_FORWARD_2026_09_25_MACRO` (116 rows, ~12KB total) is committed alongside its manifest, grepped
directly for the API key text (zero occurrences in either file) — this rerun is reproducible without a key.

## H6-H9 (done — built in parallel with H5.5, since none require the data_quality ceiling to be closed first)

* **H6 — causal outcome engine** (`outcomes.py`): `resolve_outcome()` walks bars strictly after an entry/
  fill and resolves target/stop/still-open/no-data, tracking MFE, MAE, and the first timestamp each of
  +1R/-1R/target/stop was touched. A stop and target both falling inside the same bar with no way to order
  them from OHLC alone resolves `AMBIGUOUS`, always using the stop price (never the favorable target) for
  any net-R figure. `resolve_hypothetical()` answers "what happened to the TRADEABLE blocked by capacity"
  or "the MONITOR just below the gate" by resolving the same target/stop AVDI's own `evaluate()` proposed,
  without touching the ledger — exercised end-to-end against all 12 `KNOWN_FORWARD_2026_09_25` reference
  symbols in `known_forward/session_outcomes.py`.
* **H7 — one canonical event/observation/decision/trade identity** (`event_identity.py`): consolidates the
  forward runtime's own `shadow_log._assign_event()` clustering concept into a small, DB-free module.
  Repeated observations of the same symbol+direction share one `event_id` until `close_event()` is called;
  an event carries at most one open `trade_id`. Directly reproduces the acceptance doc's own headline
  number (133 observations → 12 events) from the real per-symbol observation counts.
* **H8 — walk-forward with a holdout that cannot be casually spent** (`walkforward.py`): a `WalkForwardPlan`
  enforces exactly one holdout period, last. Every period access is recorded, tagged by purpose;
  `inspect()` refuses the holdout for anything but a final report, and the only other way in,
  `inspect_holdout(..., i_understand_this_ends_the_holdout=True)`, permanently and irreversibly marks it
  contaminated the instant it succeeds.
* **H9 — MLflow experiment tracking** (`experiment_tracking.py`): every run binds git commit, dataset
  manifest hashes, capability fingerprint, strategy version, parameters, date range, walk-forward split, and
  the outcome metric set (independent events, trades, net R, expectancy, profit factor, max drawdown,
  MFE/MAE, funnel losses, capacity blocks) onto one MLflow run, tracked in a SQLite store under
  `historical_data_root()/mlruns` (isolated, never a production path).

All four are usable today independent of the data_quality ceiling — H6 already resolves real outcomes for
every MONITOR-capped candidate H5 produced; H7-H9 are infrastructure the eventual HIST-001 baseline needs
regardless of which capability fingerprint it runs under.

## HIST-001 (Smoke stage done; Medium/Full not started)

Pre-registration (committed before any result): `research/historical/reports/HIST_001_PREREGISTRATION.md`.
Result: `research/historical/reports/HIST_001_CHAMPION_BASELINE.md`. Code: `research/historical/hist001/`.

Smoke stage (10 `technology`-sector symbols, January 2024, real pinned-revision fabhaus data streamed and
filtered — never the full 478GB corpus, one ~15.6GB monthly shard processed on the fly) ran the real,
unmodified Champion stack across all 567 real-trading-calendar cycles with zero exceptions and proven
determinism (two independent runs, byte-identical). **Central finding**: zero decisions occurred — confirmed
directly as a consequence of `strategies._bars()`'s own 55-daily-bar floor (a single month never
accumulates more than ~24 daily bars), a property of window length, not a defect; Medium's 3-month window
is expected to self-resolve this in its later weeks. Two disclosed gaps to close before Medium/Full: build
a real macro dataset for the batch's own date range (this run's `PRICE_TREND_MACRO_V1` tag was nominal,
not exercised, since no decisions occurred to exercise it), and capture decision-time price/stop/target for
non-executed TRADEABLE observations so `capacity_opportunity_cost()` can actually resolve them.

**Decision gate: PIPELINE VALIDATED. BASELINE VALID/INVALID verdict explicitly deferred** to Medium/Full,
where real Champion decisions are expected — rendering that verdict on zero trades would be meaningless.
