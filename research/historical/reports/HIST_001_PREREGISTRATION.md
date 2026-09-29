# HIST-001 — pre-registration (committed BEFORE any result is computed)

Per the directive: this file is written and committed before HIST-001's baseline is run. It is not altered
after looking at results except through an explicitly versioned amendment (a new dated section appended
below, never an edit to what is already committed here).

**HIST-001 is descriptive, not optimization.** It answers: *how does the current, unmodified Champion
decision code behave historically, under the validated `PRICE_TREND_MACRO_V1` capability fingerprint,
before anything is changed?* It is **not** "a backtest of full AVDI" — see §6.

## 1. Identity

| Field | Value |
|---|---|
| Experiment ID | `HIST-001` |
| Git commit (pre-registration) | `ed6df56a12a4de4437c19ffb091897809cdd6e5e` (branch `h1/historical-lab`) |
| Champion decision-engine version | `decision_engine/gates-v1.1` (`lab/paper/journal.py:ENGINE_VERSION`) |
| Champion sizing/execution version | post-`322e325` (the executable-risk fix; current `lab/paper/risk.py`/`canonical/account_fit.py`, unchanged since) |
| Account policy | `canonical.risk_policy.STRATEGY_500_POLICY` (via `canonical_bridge.py`) |
| Capability fingerprint | `PRICE_TREND_MACRO_V1` (`research/historical/capability.py`) — real price/TA + real historical macro; every other evidence family stubbed (§6) |
| Event-clustering version | `research/historical/event_identity.py` (H7), as committed at the pre-registration commit above |
| Walk-forward machinery | `research/historical/walkforward.py` (H8), as committed at the pre-registration commit above |
| Outcome engine | `research/historical/outcomes.py` (H6), as committed at the pre-registration commit above |

## 2. Dataset (per-batch manifests are the authoritative record; summarized here)

* **Price/volume source**: `fabhaus/equities_5m_stockprices`, pinned revision
  `f17c0b0c3cf6a455994f93d6a85e76274172ab03` (never `main`/`latest`) — the source accepted in
  `FABHAUS_AUDIT_REPORT.md` (timestamps ACCEPTED, volume `RELATIVE_ONLY`, corporate actions
  `ACCEPTED_WITH_POLICY`).
* **Macro source**: FRED (`DGS10`, `DGS2`, `VIXCLS`, `FEDFUNDS`), real ALFRED vintage-aware fetch
  (`research/historical/macro.py`), a NEW dataset per batch covering that batch's date range (never the
  `KNOWN_FORWARD_2026_09_25_MACRO` dataset, which only covers Sept 2026).
* **Daily decision bars**: fabhaus provides 5-minute resolution only — no separate daily table exists in
  the source. Daily bars for decision-making (`strategies._bars()`'s 55-bar trend/RSI floor) are
  **aggregated from fabhaus's own native 5-minute bars** (open of the session's first bar, running high/low,
  close of the last bar, summed volume) — the SAME technique already built and tested for "today's
  still-forming daily bar" synthesis in `avdi_adapter.py`. This is a **deliberate, disclosed methodology,
  not a limitation discovered after running**: it makes one real intraday source serve both decision and
  execution consistently, and volume produced this way inherits fabhaus's own `RELATIVE_ONLY` verdict
  unchanged (summing a `RELATIVE_ONLY` series does not make it `ABSOLUTE`).
* **Corporate-action policy**: `raw` bars remain immutable and are what the paper broker actually trades
  against (per `corporate_actions.py`'s own design and this project's standing rule). The canonical
  REPORTING/continuity view is `split_adjusted` (`corporate_actions.split_adjusted_view`, lookahead-safe,
  fail-closed on an ambiguous/unconfirmed split). Ticker changes, mergers, and delistings are quarantined
  (`detect_quarantine_candidates`), never silently remapped or dropped; the count of quarantined
  observations/events is reported per batch, never silently absorbed.
* **Volume trust**: `RELATIVE_ONLY` for every fabhaus-sourced dataset in HIST-001 (enforced at runtime by
  `volume_trust.py`: `score_liquid_momentum` is excluded from `strategies=` for any non-`ABSOLUTE` source).
* **Universe**: the REAL, current `dashboard/sector_map.py` membership AVDI's own forward scanner draws
  from — 90 unique symbols across all 11 sectors at the pre-registration commit above. This is **today's**
  membership, not point-in-time historical membership for 2024-2026 (fabhaus/this project has no reliable
  point-in-time constituent history) — **this is a disclosed survivorship-bias source, quantified per batch
  in each report's survivorship-bias section (§17 of the directive), never called unbiased.**
* **Timezone/session policy**: fabhaus timestamps are genuine UTC (`FABHAUS_AUDIT_REPORT.md` §2, corrected
  finding); daily-bar aggregation and the 5-minute execution feed both use this directly, ET-aware only
  where the Champion's own session-state/cutoff logic requires it (`market_regime.py`, patched for
  clock-awareness per H5).

## 3. Initial time range and staged rollout

Per the directive's own explicit staging (**mandatory**: each stage begins only once the previous is
deterministic and resource-safe):

| Stage | Universe | Period | Status as of this pre-registration |
|---|---|---|---|
| **Smoke** | 10 symbols (the `technology` sector: AAPL, ADBE, AMD, AVGO, CRM, CSCO, DELL, MSFT, NVDA, ORCL) | 2024-01 (one month) | To be run immediately after this commit |
| **Medium** | ~50-100 symbols (target: the full 90-symbol universe, or the largest fabhaus-available subset of it) | ~3 months | Not started — gated on Smoke being deterministic |
| **Full baseline** | Full research universe (§2) | 2024-01 through 2026-03 (fabhaus's practical coverage) | Not started — gated on Medium |

**This pre-registration covers the Smoke stage's exact parameters now; Medium and Full are pre-registered
at the universe/period level above, with their own exact dataset manifests/hashes recorded and committed
before THEIR results are computed, per the same rule.** A later stage's report will link back to this file,
never restate or revise it.

## 4. Walk-forward structure (H8) — intended for the FULL baseline; Smoke exercises a slice of Development-1 only

| Period | Kind | Range |
|---|---|---|
| `development_1` | DEVELOPMENT | 2024-01-01 .. 2024-07-01 |
| `validation_1` | VALIDATION | 2024-07-01 .. 2024-10-01 |
| `development_2` | DEVELOPMENT | 2024-10-01 .. 2025-04-01 |
| `validation_2` | VALIDATION | 2025-04-01 .. 2025-07-01 |
| `holdout` | HOLDOUT | 2025-07-01 .. 2026-03-01 |

No optimization occurs on any period in HIST-001 (directive §4/§18) — this split exists only to create
clean reporting boundaries and a genuinely locked final period for future Challenger work. HIST-001 may
report the unchanged Champion's behavior on every predefined section (development, validation, AND holdout)
as part of this single pre-registered baseline — reporting descriptive results on the holdout here does
**not** spend it for tuning purposes; what would spend it is using its results to CHANGE anything about the
Champion or a Challenger afterward, which HIST-001 explicitly does not do (§18). The Smoke stage
(2024-01) falls inside `development_1` and is a pipeline-validation run, not a walk-forward-scale
conclusion.

## 5. Champion logic reused (unchanged, no new seams)

Real scanner (`strategies.scan()`), the real canonical A/B/C/D/E pipeline (`canonical_bridge.py`,
`canonical/account_fit.py`), the real decision engine (`decision_engine.evaluate()`, real `data_quality.py`
coverage/gate logic, real `PRICE_TREND_MACRO_V1` family wiring), real ranking (whatever `scan()`'s own
candidate sort already does), real account fit/capacity/sector limits (`risk.check_entry`/`account_state`),
the real executable-risk sizing (`risk.position_size`/`stock_account_fit`, post-`322e325`), the real fill
simulator (`lab/paper/fills.py`), the real entry cutoff (session-schedule `allow_entries`, generalized from
`known_forward/schedule.py`'s single-day design to an arbitrary date range), and the real exit logic
(`broker.manage_open_positions`). No historical-only threshold, no manual exclusion based on observed
profitability, no lowering of the `0.55` data_quality floor.

## 6. Historical capability disclosure (mandatory on every HIST-001 report, verbatim or by direct reference)

> `PRICE_TREND_MACRO_V1` is not yet feature-identical to the full forward AVDI evidence stack.

* **Replayed** (real, live decision-engine code, real historical data): price/trend/momentum
  (`_fam_trend`), RSI/ATR, `liquid_momentum`/`sector_relative_strength`/`mean_reversion` scoring, real
  executable-risk sizing, real capacity/sector caps, real fill simulation, macro-rates (`_fam_macro`, via
  `macro.py`'s vintage-aware FRED replay).
* **Neutralized to a fixed/zero-confidence stand-in** (the same contract the live system itself falls back
  to when a source is unavailable): catalyst headlines (`_fam_catalyst`), short interest (`_fam_short`),
  SEC filings (`_fam_filings`), options flow (`_fam_options_flow`), social sentiment (`_fam_social`),
  analyst ratings (`_fam_analyst`), regime (`_safe_regime`), sector breadth/rotation (real MEMBERSHIP is
  used — H5's multi-sector extension — but real RELATIVE RANKING/rotation is not), trading halts.
* **Unavailable at any historical resolution today**: live bid/ask microstructure beyond the
  close-plus-synthetic-spread model; true point-in-time universe membership (§2's survivorship-bias
  disclosure).

HIST-001 is: **a historical baseline of the current Champion decision code under the `PRICE_TREND_MACRO_V1`
capability set** — never described as a backtest of the full forward Champion.

## 7. AMD / price-data caveat carried forward

H5.5 found AMD reaching TRADEABLE under `PRICE_TREND_MACRO_V1` where the real forward session did not,
most likely because of a real price-data difference between fabhaus/Yahoo-sourced technicals and the
forward session's own live provider mix — not confirmed, not resolved, AMD is **not** excluded or
hand-edited out of HIST-001. Per-event source-attribution fields (historical OHLC source, volume
source/trust, macro source, capability fingerprint) are recorded on every event/trade record so this
remains an open, traceable data-equivalence question rather than a silently absorbed one.

## 8. Metrics to be reported (directive §11-§14, §19; recorded here so no metric is added or dropped after
seeing results)

Portfolio: starting/ending equity, net P&L, net R, return %, max drawdown (and in R), profit factor,
Sharpe/Sortino (only if the independent-event count is large enough to be statistically meaningful — stated
explicitly per batch, not computed reflexively), exposure, capital utilization. Trades: total entries,
resolved trades, independent trade events, win rate, average win/loss, expectancy, median R, best/worst
trade, longest losing streak, average holding time. Path behavior (via `outcomes.py`): MFE, MAE, +1R
frequency, target/stop/ambiguous/unresolved counts. Execution: spread/slippage cost, execution leakage,
% rejected by account/risk constraints, sizing/risk-invariant violations (expected zero — any nonzero count
is a correctness anomaly per §19, not a "finding"). Funnel: universe → scanner detections → raw setups →
validation → A/B/C/D/E → finalists → TRADEABLE → capacity-eligible → orders → fills → exits, each stage's
observation count, independent-event count, conversion %, and major rejection reasons. Attribution: symbol,
sector, long/short, month/quarter/year, time-of-day bucket, quality bucket, conviction bucket, setup/
strategy, entry/exit reason — only already-defined or simple descriptive partitions, no novel regime
definitions invented for this purpose. Capacity opportunity cost (via `outcomes.resolve_hypothetical`) for
every TRADEABLE blocked by the daily cap/sector cap/open-position cap/capital/cutoff. Choice-event records
(candidate set, Champion selection, later real outcome, later hypothetical outcome of the blocked
candidates) for every genuine multi-candidate competition. CH-001 shadow result (Champion exit vs.
breakeven-after-+1R), computed but never allowed to affect a real position, reported as `SHADOW ANALYSIS`
only.

## 9. Success/failure interpretation (fixed now, applied after the fact, never the reverse)

* **BASELINE VALID** — mechanics/data are trustworthy enough to use for Challenger comparisons.
* **BASELINE VALID WITH LIMITATIONS** — usable, but named data/universe/capability constraints (§2/§6/§7)
  must accompany every conclusion drawn from it.
* **BASELINE INVALID** — a correctness/data problem makes the results unsuitable for strategy research.

This verdict is about research validity, never about whether the Champion made money — a losing but
trustworthy baseline is a completely acceptable outcome and must not be reported as if it were a defect.

## 10. No parameter search (directive §18) — binding for every stage of HIST-001

No Optuna, no nearby-threshold testing, no alteration of conviction/data_quality/daily capacity/stops/
targets/ranking/macro weights/signal weights, at any stage. HIST-001 is one strategy/config: the Champion as
committed at §1's git commit.

---

*Amendments, if any become necessary, are appended below as new dated sections and never edit the text
above.*

## Amendment 1 — 2026-09-29 — Medium stage exact parameters (committed BEFORE Medium results are computed)

Per the post-Smoke directive: Smoke is accepted as PIPELINE VALIDATED (not a Champion profitability verdict).
Before Medium, three structural gaps were closed (committed separately, ahead of this amendment, on
`h1/historical-lab`): (1) an explicit `warmup_start`/`evaluation_start`/`evaluation_end` split, with every
cycle/observation/event tagged by phase and only `evaluation`-phase observations counted in reported metrics;
(2) real macro data threaded into the replay, with `assert_macro_coverage()` refusing to start a
`PRICE_TREND_MACRO_V1` run whose macro store doesn't cover the full range; (3) full decision-time capture
(price/stop/target/quantity/sector/quote/account-state) for every evaluated symbol, not just executed ones,
so a blocked TRADEABLE's counterfactual is actually resolvable. This amendment fixes Medium's exact
parameters — universe, dates, dataset IDs and hashes — **before** `run_baseline()` is invoked for Medium.

**Git commit at this amendment**: `1a03e17d42bffe54760e8ab602876fb04e737a54` (branch `h1/historical-lab`).

### A1.1 Universe (50-100 symbols, deterministic — directive sec 6)

The **entire** pre-registered 90-symbol `dashboard/sector_map.py` universe (§2 above), across all 11
sectors — not a hand-picked subset. Using the whole pre-registered universe removes any subset-selection
judgment call entirely: nothing here is chosen based on which symbols looked like they'd perform well.

One symbol, **`BRK-B`, has zero rows in the fabhaus source** for this six-month window (0 of 1,164,068 kept
rows) — most likely a ticker-format mismatch (fabhaus may key Berkshire differently, e.g. without the
hyphen) rather than genuine absence, not investigated further at this stage. This is disclosed, not silently
dropped: `BRK-B` remains in the requested universe and dataset manifests; it simply produces no decisions
because it has no data, exactly like any other symbol with a real data gap. **Effective universe with data:
89 symbols.**

### A1.2 Dates — this is the canonical meaning of "3-month Medium" (directive sec 2)

| Field | Value |
|---|---|
| `warmup_start` | `2024-01-01` |
| `evaluation_start` | `2024-04-01` |
| `evaluation_end` | `2024-06-30` |

Warm-up (2024-01-01 .. 2024-03-31) comfortably clears the empirically-confirmed 55-trading-day floor
(`research/historical/hist001/warmup.py:required_warmup_daily_bars()`, probed against the real
`avdi_adapter._bars_dict()` dependency, not hardcoded — confirmed to return exactly 55) well before
2024-04-01: ~62 trading days of real daily-bar depth exist by the evaluation start, per
`lab.paper.market_calendar`. Warm-up cycles run for real (real scan/evaluate/broker/ledger calls) so account
state and daily-bar depth accumulate correctly into the evaluation window; only `evaluation`-phase
observations/events/trades are counted in Medium's reported metrics.

### A1.3 Equity dataset (directive sec 7/8 — exact manifests, batch strategy)

* **Source**: `fabhaus/equities_5m_stockprices` @ pinned revision `f17c0b0c3cf6a455994f93d6a85e76274172ab03`
  (same pinned revision as Smoke) — six monthly shards (`2024-01.jsonl` .. `2024-06.jsonl`), **not** the full
  478GB corpus. Each shard streamed (8MB chunks) and filtered to the 90-symbol universe on the fly, never
  held in full in memory or on disk; upstream `Content-Length`/`ETag` and the downloaded bytes' own SHA-256
  recorded per shard (`research/historical/hist001/medium_2024-0{1..6}_fetch_manifest.json`, committed).
  94.06 GB streamed total; 1,164,068 rows kept.
* **Batch strategy**: one batch per calendar month (fabhaus's own native shard boundary) — a deterministic
  partition, not a chosen one. `build_medium_dataset.py` combines the six independently-loaded monthly
  frames by concatenation followed by a full sort on `(symbol, timestamp)`; a pure sort has no dependency on
  concatenation order, proven directly by
  `tests/historical/test_hist001_medium_batching.py` (all 6 permutations of month order tested, byte-identical
  results, both raw and daily-aggregated).
* **Daily decision bars**: aggregated from the same 5-minute source (open/high/low/close/volume rollup,
  16:00 ET-labelled), same disclosed methodology as Smoke (§2 above).
* **`HIST001_MEDIUM_2024_H1_5M`**: 1,164,068 rows, 89 symbols with data, `2024-01-01`..`2024-07-01`,
  `parquet_sha256=f2a3306fb6f243a2a4706b71538e8eb8dd125362a0e3f0e3ba54a21f33a901f6`.
* **`HIST001_MEDIUM_2024_H1_DAILY`**: 11,491 rows, `parquet_sha256=541bcd39a1cd0de63e2c5207b994e23a0088bdf09814c2c2d8563ff0b083840d`.
* **Corporate actions**: `detect_splits()` found 3 suspected events over the window — **NVDA's real 10-for-1
  split (2024-06-10, high confidence, volume-confirmed)**, and two low-confidence suspected events (**CMG's
  real ~50-for-1 split, 2024-06-26**; **WMT's real 3-for-1 split, 2024-02-26**, whose volume ratio didn't
  clear the detector's own confirmation bar). Only the NVDA event is `is_confirmed()`. Per the standing
  policy, raw bars remain immutable and are what the paper broker trades against; `split_adjusted` treatment
  is available on demand but not applied to raw replay data; the two unconfirmed events are **not**
  silently adjusted or excluded — reported here exactly as detected, fail-closed.
* **Volume trust**: `RELATIVE_ONLY` throughout (unchanged from Smoke).

### A1.4 Macro dataset (directive sec 3)

`HIST001_MEDIUM_2024_H1_MACRO` — real ALFRED vintage-aware fetch (`DGS10`, `DGS2`, `VIXCLS`, `FEDFUNDS`),
fetched `2023-12-11`..`2024-06-30` (a 21-day lookback before `warmup_start` so
`latest_value_as_of(2024-01-01)` resolves across the New Year's Day holiday gap — see commit message for
detail), 424 vintage observations, `local_sha256=ae9c08970f26a16b7290f9ec81a1b9d6f10486d577187fcbda212d8036d39e44`.
`assert_macro_coverage()` verified to pass cleanly for the full `[2024-01-01, 2024-06-30]` warm-up+evaluation
range before this amendment was written. `capability_fingerprint=PRICE_TREND_MACRO_V1` for Medium (not
`PRICE_TREND_ONLY_V1`) — the run structurally cannot start without this coverage, per
`research/historical/hist001/baseline.py`.

### A1.5 Champion config, capability fingerprint, execution assumptions

Unchanged from §1/§5/§6 above — same `decision_engine/gates-v1.1`, same post-`322e325` executable-risk
sizing, same real scanner/canonical/risk/fill/exit code, same `PRICE_TREND_MACRO_V1` capability disclosure
(§6). `universe_sectors` passed to `run_baseline()` is all 11 sector keys (`technology`, `communication`,
`consumer_discretionary`, `consumer_staples`, `financials`, `health_care`, `industrials`, `energy`,
`materials`, `utilities`, `real_estate`) — this neutralizes real sector-rotation ranking uniformly across the
whole universe (§6's disclosed limitation: real membership is used, real relative ranking/rotation is not),
matching Smoke's treatment of its own (single-sector) universe rather than applying it selectively.

### A1.6 Expected scanner warm-up (directive's key Smoke lesson)

The evaluation window begins only after ~62 real trading days of accumulated daily-bar depth — the Champion
enters `evaluation_start` with the same information it would have had forward, not a cold start. This is the
"structural requirement discovered by Smoke" this amendment exists to close.

### A1.7 Metrics and acceptance criteria

Exactly the metrics list in §8 above — no addition, no omission. Acceptance criteria are the directive's own
sec 9 list (candidates produced, macro exercised, TRADEABLEs occur naturally, account persists
chronologically, blocked candidates fully resolvable, event clustering works, no lookahead, no production
touched, deterministic rerun) — evaluated in the Medium report, not assumed here. No parameter search, no
threshold change, at any point in the Medium stage (§10, unchanged, binding).

## Amendment 2 — 2026-09-29 — Full stage exact parameters (committed BEFORE Full results are computed)

Medium was accepted as `MEDIUM_VALID_WITH_LIMITATIONS` and judged mechanically justified for scale (real
Champion behavior, real macro replay, zero risk-invariant violations, zero unresolved wall-clock leaks,
representative-subset determinism proven byte-identical). This amendment freezes the accepted implementation
and locks in Full's exact parameters before `run_baseline()` is invoked for Full at all.

### A2.1 Frozen accepted implementation (directive sec 1)

| Component | Frozen value |
|---|---|
| Git commit (Full pre-registration) | `37b52dd96c97822e6b3ce3976723f279e102d6a5` (branch `h1/historical-lab`) |
| Champion decision-engine version | `decision_engine/gates-v1.1` |
| Capability fingerprint | `PRICE_TREND_MACRO_V1` — real price/TA + real historical macro; every other evidence family stubbed (§6 above, unchanged since Smoke) |
| Execution model / executable-risk sizing | post-`322e325` (unchanged since Smoke) |
| Event-identity version | `research/historical/event_identity.py` (H7), unchanged since Smoke |
| Replay-clock implementation | `HistoricalClock` (`research/historical/clock.py`) + six patched wall-clock seams in `execution.py`: `workflow.quote_for`, `workflow.provider_health`, `risk._live_mark_src`, `cache_policy.classify`, `paper.db.utcnow`, `risk.cooldown_state` — the last three fixed during Medium (see Medium's "Invalid Medium Attempts" section), all six unchanged since `CORRECTED_MEDIUM_3` |
| Macro implementation | `research/historical/macro.py`, vintage-aware ALFRED fetch/point-in-time lookup, unchanged since H5.5/Medium |
| Corporate-action policy | Raw bars immutable; `split_adjusted` view available on demand; quarantine-by-default for ticker-change/merger/delisting (unchanged since Smoke, §2 above) |
| Volume trust policy | `RELATIVE_ONLY` throughout (unchanged) |
| Capacity rules (`cfg.risk()`) | `max_loss_per_trade=$5.00`, `max_position_notional=$125.00`, `min_cash_reserve=$100.00`, `max_daily_loss=$10.00`, `max_drawdown=$50.00`, `max_entries_per_day=2`, `max_open_positions=3`, `max_positions_per_sector=1`, `max_correlated_positions=2`, `risk_per_trade_pct=1%`, `max_sector_exposure_pct=30%`, `cooldown_losses=3`, `cooldown_days=1` (unchanged since Smoke, verified in Medium §E) |
| Ranking | Whatever `scan()`'s own candidate sort already does (unchanged, no code touched) |
| Entry cutoff | Session-schedule `allow_entries`, from `schedule.py` (unchanged) |
| Stop/target logic | Real `broker.manage_open_positions()` / decision-engine stop-target computation (unchanged) |
| Account configuration | $500 seed cash, fractional shares enabled, no margin, no shorting (Robinhood cash-account model, unchanged since `PAPER_500_ACCOUNT.md`) |

**No strategy-affecting behavior changes during Full.** The only code change since `CORRECTED_MEDIUM_3` is
`baseline.py`'s optional `progress_every` parameter (default `None` — off, no behavior change) added purely
for visibility into a multi-hour run, and `analysis.py`'s `capacity_opportunity_cost()` phrase-matching fix
(post-hoc analysis code, not the replay itself). Neither touches a Champion decision.

### A2.2 Full historical period (directive sec 3)

The pinned fabhaus revision (`f17c0b0c3cf6a455994f93d6a85e76274172ab03`) was confirmed via the Hugging Face
tree API to have **exactly 27 monthly shards**: `2024-01.jsonl` through `2026-03.jsonl` — this fixes Full's
range at the real data boundary, not an assumed one. The `2026-03` shard itself is a **partial month**: real
data stops at **2026-03-10** (confirmed directly from the built daily dataset — no bars exist for
2026-03-11 onward at this pinned revision).

| Field | Value |
|---|---|
| `warmup_start` | `2024-01-01` (identical to Medium's warm-up start — Full's warm-up is not a new period, it is the same already-vetted 61-trading-day window) |
| `evaluation_start` | `2024-04-01` (identical to Medium's evaluation start — Full's evaluation window is a strict superset of Medium's, extended forward rather than re-defined) |
| `evaluation_end` | `2026-03-10` (the real data boundary at this pinned revision, not `2026-03-31`) |

Warm-up exists only to satisfy the empirically-confirmed 55-trading-day floor
(`research/historical/hist001/warmup.py`); warm-up cycles run for real (real scan/evaluate/broker/ledger
calls) so account state and daily-bar depth accumulate correctly, but are excluded from official Full
performance statistics — unchanged design from Medium.

### A2.3 Full research universe (directive sec 4)

Identical to Medium's: the full 90-symbol `dashboard/sector_map.py` universe (all 11 sectors), deterministic,
not selected on historical performance. 89 of 90 symbols have real fabhaus data; `BRK-B` has zero rows across
the entire 2024-01..2026-03 range (same finding as Medium, confirmed again at Full scale) — most likely a
ticker-format mismatch, disclosed and kept in the requested universe, not silently dropped. Universe
membership is **not** point-in-time (today's universe replayed backward) — survivorship bias is present and
disclosed, unchanged since Smoke. No ticker changes, mergers, or delistings were supplied as metadata for
this batch, so none are quarantined by that mechanism; corporate-action quarantining (a separate, already-
tested mechanism) is reported in §A2.5 below.

### A2.4 Data acquisition (directive sec 5)

* **Source**: `fabhaus/equities_5m_stockprices` @ pinned revision `f17c0b0c3cf6a455994f93d6a85e76274172ab03`
  — all 27 monthly shards, **not** the ~478GB full corpus. 6 months (`2024-01`..`2024-06`) were reused
  directly from Medium's own committed fetch manifests, never re-downloaded; the other 21 months
  (`2024-07`..`2026-03`) were newly streamed (8MB chunks, filtered to the 90-symbol universe on the fly,
  never held in full in memory or on disk) — 21 new fetch manifests committed alongside this amendment.
* **`HIST001_FULL_2024_2026_5M`**: 5,286,164 rows, 89 symbols with data, `2024-01-01`..`2026-04-01`,
  `parquet_sha256=30f98cbad7b1ee2eafa905089f6cabfcbd91e2b8018775f88cdf79a6e7b550c2`.
* **`HIST001_FULL_2024_2026_DAILY`**: 50,609 rows,
  `parquet_sha256=076c62a9db45e7eeb5c8749b1fb2601c67d3c192844a03230117037350ec0361`.
* **Batch strategy**: identical to Medium's proven, tested design — one batch per calendar month (fabhaus's
  own shard boundary), combined via concatenation followed by a full `(symbol, timestamp)` sort. The
  combination algorithm in `build_full_dataset.py` is byte-identical to `build_medium_dataset.py`'s (already
  proven batch-order invariant by `test_hist001_medium_batching.py`); not re-tested at this larger scale
  since the algorithm itself did not change, only the month list and a file-prefix lookup were extended.
* **Daily decision bars**: aggregated from the same 5-minute source, same disclosed methodology as Smoke/Medium.
* **Volume trust**: `RELATIVE_ONLY` throughout.

### A2.5 Corporate actions (directive sec 4/20)

5 suspected split events detected across the full 27-month range, 2 confirmed by the detector's own volume
heuristic:

| Symbol | Date | Ratio | Confidence | Real event? |
|---|---|---|---|---|
| NVDA | 2024-06-10 | 10-for-1 | High, **confirmed** | Yes (real, well-known) |
| AVGO | 2024-07-15 | 10-for-1 | High, **confirmed** | Yes (real, well-known) |
| CMG | 2024-06-26 | ~50-for-1 | Low, unconfirmed | Yes (real, volume heuristic simply didn't clear its own bar) |
| WMT | 2024-02-26 | 3-for-1 | Low, unconfirmed | Yes (real, same reason) |
| NFLX | 2025-11-17 | 10-for-1 | Low, unconfirmed | Not independently verified by this project; reported exactly as detected, fail-closed, neither assumed real nor dismissed |

Per the standing policy: raw bars remain immutable and are what the paper broker trades against; only the
two `is_confirmed()` events would be eligible for the on-demand `split_adjusted` view; none of the five are
silently adjusted or excluded from replay.

### A2.6 Macro dataset (directive sec 6)

`HIST001_FULL_2024_2026_MACRO` — real ALFRED vintage-aware fetch (`DGS10`, `DGS2`, `VIXCLS`, `FEDFUNDS`),
fetched `2023-12-11`..`2026-03-31` (same 21-day lookback established for Medium), 1,769 vintage observations,
`local_sha256=04fab584010dbb01e4f7bca008b7d91a9e86da588187c5bb86610d721b49b519`. `assert_macro_coverage()`
verified to pass cleanly for the full `[2024-01-01, 2026-03-31]` range (a superset of the actual
`evaluation_end=2026-03-10` needed) before this amendment was written — the run structurally cannot start
without this coverage. `FRED_API_KEY` was used only as an in-process environment variable for this one build
command; never exported persistently, never printed, never written to any file. Working tree swept for the
literal key value before every commit in this stage (zero occurrences, every time).

### A2.7 Champion config, capability fingerprint, execution assumptions

Unchanged from Medium (§A2.1 above) — same `decision_engine/gates-v1.1`, same post-`322e325` executable-risk
sizing, same real scanner/canonical/risk/fill/exit code, same `PRICE_TREND_MACRO_V1` capability disclosure.
`universe_sectors` passed to `run_baseline()` is all 11 sector keys, identical to Medium.

### A2.8 Execution strategy, checkpointing (directive sec 8/9)

**Execution**: Full runs as one uninterrupted background process (the same single-process design already
used for all of Smoke and Medium, including all three Medium attempts) — no batching of the replay itself is
needed beyond the dataset's own monthly-shard batching (§A2.4), which is already proven batch-order
invariant.

**Checkpointing**: a lightweight, purely observational progress heartbeat (`run_baseline(progress_every=...)`,
default off) prints cycle index/session_date/elapsed-time/event-count/orders-so-far to stderr periodically —
added specifically because Full is long enough that zero visibility until completion would otherwise be the
only option. **Genuine interrupt/resume was deliberately scoped OUT of this run**: the same single-process
design has now completed reliably three times at Medium scale (up to ~68 minutes each); Full is expected to
run several hours as one process; and building genuine resumability correctly (persisting
`decision_capture`/event-identity state and *proving* a resumed run reproduces an uninterrupted one exactly)
is a substantial standalone engineering effort whose own correctness risk works against the reason this audit
trail exists. If a future run genuinely needs multi-day resumability, it should be built deliberately then,
not rushed under this run's own time pressure. This is a disclosed scope decision, not a silent gap.

### A2.9 Metrics, funnel, choice-event definition, and acceptance criteria

Exactly the metrics list in §8 above, extended with the granular breakdowns and diagnostics the Full
directive itself specifies (concentration excluding top trades, regime attribution, stability by month/
quarter/year) — no metric invented beyond what was requested. **Choice events use the corrected definition
established during Medium**: a genuine choice event requires two or more truly newly-eligible (not
already-held) candidates competing for a genuinely scarce, available slot at that instant — the naive
raw-episode count (765/58-style) is explicitly rejected as a measure of genuine ranking competition, per
Medium's own finding. No parameter search, no threshold change, at any point in the Full stage — binding,
unchanged from Medium/Smoke.
