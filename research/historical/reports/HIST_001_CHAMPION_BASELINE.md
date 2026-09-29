# HIST-001 — Current Champion Historical Baseline

**Stage completed: SMOKE (stage 1 of 3). Medium (~50-100 symbols × ~3 months) and Full (full universe ×
2024-2026) are NOT started.** Per the pre-registration's own explicit staged rollout, each stage begins
only once the previous is deterministic and resource-safe; that gate is what this report establishes for
Smoke, nothing more. **This is not yet the BASELINE VALID/INVALID verdict** — that verdict is reserved for
when the Full stage (or, at minimum, Medium) has run; see the decision gate at the end.

Pre-registration: `research/historical/reports/HIST_001_PREREGISTRATION.md`, committed before this run.
This report does not alter that pre-registration; every parameter below matches it exactly.

## 1. What actually ran

* **Universe**: the pre-registered 10-symbol `technology` sector smoke set — AAPL, ADBE, AMD, AVGO, CRM,
  CSCO, DELL, MSFT, NVDA, ORCL.
* **Period**: 2024-01-01 .. 2024-01-31 (21 real trading days per `lab.paper.market_calendar`, correctly
  excluding New Year's Day and the observed MLK Day holiday — not a naive weekday count).
* **Source**: `fabhaus/equities_5m_stockprices` @ pinned revision `f17c0b0c3cf6a455994f93d6a85e76274172ab03`,
  shard `2024-01.jsonl`. **Not the whole 478GB corpus**: this one ~15.6GB monthly shard was streamed
  (chunked HTTP GET, 8MB at a time) and filtered to the 10 wanted symbols on the fly — the unfiltered shard
  was never held in memory or written to disk. 9,199,183 rows were read; 28,139 were kept (10 symbols).
  Upstream `Content-Length` (15,633,792,596 bytes) and `ETag` recorded; the downloaded bytes' own SHA-256
  was computed during the stream and matches the committed manifest.
* **Daily decision bars**: fabhaus has no separate daily table — daily bars (242 rows, ~24/symbol) were
  **aggregated from the same 5-minute source** (open of the session's first bar, running high/low, close of
  the last bar, summed volume), per the pre-registration's disclosed methodology, not a post-hoc excuse.
* **Corporate actions**: `detect_splits()` ran against the full daily dataset — **zero suspected events**.
  Raw and `split_adjusted` views are therefore identical for this batch; nothing was quarantined (no
  ticker-change/merger/delisting metadata applies to this window/universe).
* **Volume trust**: `RELATIVE_ONLY` throughout (fabhaus verdict, unchanged by daily aggregation).
* **Macro**: `PRICE_TREND_MACRO_V1` capability fingerprint was used for the run, but **no macro dataset
  covering January 2024 has been fetched yet** — see §6's capability disclosure and the limitations below;
  `_fam_macro` behaved as `PRICE_TREND_ONLY_V1` in practice for this specific run because no
  `macro_history` was supplied. This is a **known gap in this Smoke run**, corrected in Medium/Full (build
  a macro dataset for 2024-01 onward before those stages run).
* **Account**: fresh `$500.00` cash/equity (not the known-forward session's special $504.66 boundary — this
  is a new baseline, not a reproduction of a specific forward day).
* **Champion code**: real, unmodified — same scanner/A-B-C-D-E/decision-engine/data-quality/risk/sizing/
  fill-simulator/entry-cutoff/exit code as every prior H3-H5.5 increment. No threshold changed.

## 2. The central Smoke-stage finding

**Zero raw observations, zero independent events, zero TRADEABLE decisions, zero orders — for the entire
month, across all 567 scheduled cycles (21 days × 27 cycles), on all 10 symbols.**

This is **not a bug** — it is a direct, verified consequence of one thing: `strategies._bars()`'s own
55-daily-bar trend/RSI floor. A single calendar month provides at most ~21-25 daily bars per symbol (24 for
this exact window, confirmed directly: `test_no_symbol_has_55_daily_bars_in_a_single_month`), so `scan()`'s
own `_bars(sym)` call returns `None` for every symbol on every single cycle, and the scanner never produces
a single candidate (`candidates_total_raw: 0`, confirmed in the funnel below) — the real Champion code is
running correctly; it simply has no runway to form an opinion within a 1-month window, by construction, not
by defect. **This is a property of window length, not of this specific month or universe**, and it
generalizes: the Medium batch (~3 months ≈ 63 trading days) is expected to cross the 55-bar floor partway
through its own window and start producing real candidates in its later portion; the Full baseline
(2024-2026) will have full decision capability from its first ~11 weeks onward. **No warm-up period before
2024-01-01 was fetched for this Smoke run** (a deliberate time/resource tradeoff, disclosed here rather than
silently worked around) — fetching ~3 additional prior months would very likely produce real decisions
within this exact January window; that increment is not done and is recommended before Medium, not
required for it (Medium's own 3-month span self-resolves the warm-up problem for its own later weeks).

Determinism was verified directly, not assumed: two independent runs over 2024-01-01..2024-01-10 produced
byte-identical cycle-state sequences, signal counts, and account equity
(`test_smoke_replay_is_deterministic_across_two_independent_runs`).

## 3. Funnel (§10 of the directive)

| Stage | Count |
|---|---|
| Universe | 10 symbols |
| Cycles completed OK | 567 / 567 |
| Scanner candidates (raw) | **0** |
| Finalists (raw) | **0** |
| Raw observations | **0** |
| Independent events | **0** |
| TRADEABLE (observations / events) | 0 / 0 |
| Orders placed | 0 |
| Fills | 0 |
| Exits | 0 |
| Major rejection reasons | none recorded — nothing was ever journaled to reject |

The funnel bottleneck for this Smoke stage is unambiguous and singular: **the scanner's own daily-bar
data-sufficiency gate**, before any decision-engine gate (`data_quality`, `conviction`, capacity, etc.) is
even reached. This is itself a useful, honest funnel finding — the bottleneck named for Medium/Full to
watch is whether it *remains* the dominant one once enough warm-up exists, or whether `data_quality`
(H5/H5.5's own finding) becomes dominant instead once real candidates start flowing.

## 4. Event identity (§9)

`event_identity.py` (H7) was wired live into the replay loop (stamping `event_id`/`observation_id`/
`decision_id` on every evaluated observation as it happened, not reconstructed after the fact) — with zero
observations this stage, it stamped nothing, which is itself the correct, expected behavior for a
zero-observation run, verified directly (`event_count == 0`).

## 5. Capacity opportunity cost, choice events, CH-001 shadow (§13-§15)

All three are **trivially empty for this Smoke stage** — there were no TRADEABLE observations (blocked or
otherwise), no multi-candidate competitions, and no positions to reach +1R. The analysis functions
(`capacity_opportunity_cost`, `choice_events`, `ch001_shadow`) were exercised directly against this real
(trivial) output and confirmed to handle it cleanly rather than erroring
(`test_capacity_choice_ch001_portfolio_all_handle_the_trivial_case_cleanly`) — this is a genuine test of the
ANALYSIS CODE's correctness on an edge case, not a finding about the Champion.

**Scope limit, disclosed**: `capacity_opportunity_cost()` as built in this pass can identify a blocked
TRADEABLE observation by its journalled note, but does not yet capture that decision's own
price/stop/target at replay time (needed to call `outcomes.resolve_hypothetical`) — a real gap to close
before Medium/Full, where blocked TRADEABLEs are actually expected to occur. Not silently worked around:
stated here as unresolved.

## 6. Historical capability disclosure (mandatory, per the pre-registration §6)

> `PRICE_TREND_MACRO_V1` is not yet feature-identical to the full forward AVDI evidence stack.

Replayed (real code, real data): price/trend/momentum, RSI/ATR, the three funnel strategies, real
executable-risk sizing, real capacity/sector caps, real fill simulation. **Macro was NOT actually exercised
in this specific Smoke run** (no January-2024 macro dataset was built) — this run's own decisions, had any
occurred, would have been produced under `PRICE_TREND_ONLY_V1`'s real behavior despite being tagged
`PRICE_TREND_MACRO_V1`; since zero decisions occurred, this had no effect on the result, but it is a real
process gap to fix before Medium/Full (build and pass a real `macro_history` for the batch's own date
range). Neutralized: catalyst, short interest, filings, options flow, social, analyst, regime, sector
rotation, halts. This is a historical baseline of the current Champion decision code under (nominally)
`PRICE_TREND_MACRO_V1` — never a backtest of the full forward Champion.

## 7. Survivorship bias audit (§17, mandatory)

All 10 requested symbols have data in this batch (`symbols_missing_from_source: []`) — the universe is
`dashboard/sector_map.py`'s **current** technology-sector membership, not point-in-time 2024 membership.
**Survivorship bias is present by construction**: any technology-sector symbol that existed in January 2024
but has since been delisted, acquired, or renamed is invisible to this universe, not detected and excluded
after the fact — this project has no reliable point-in-time constituent history to do otherwise. This
caveat scales unchanged into Medium/Full using the same 90-symbol current universe.

## 8. Performance metrics (§11)

Trivial for this stage: starting equity $500.00, ending equity $500.00, net P&L $0, 0 resolved trades.
Sharpe/Sortino and other distributional statistics are correctly NOT reported (per the pre-registration:
only reported once the independent-event count is large enough to be meaningful — 0 is not).

## Answers to the 14 HIST-001 report questions (Smoke-stage scope)

1. **Positive historical net expectancy under `PRICE_TREND_MACRO_V1`?** Not determinable — zero trades.
2. **Across how many independent events/trades?** 0 events, 0 trades.
3. **Stability across time?** Not determinable at this sample size.
4. **Dominated by a few symbols/events?** Not applicable — no events occurred.
5. **Max drawdown?** $0 / 0R (no positions were ever opened).
6. **Biggest funnel bottleneck?** The scanner's own 55-daily-bar data-sufficiency floor, ahead of every
   decision-engine gate — a property of this stage's 1-month window length, not of the Champion's logic.
7. **Historical opportunity cost of the 2-entry daily cap?** Not determinable — the cap was never binding
   (no entries were ever attempted).
8. **How often does sector capacity block a better outcome?** Not determinable — no candidates ever
   reached the capacity check.
9. **Does scanner ranking select better/worse outcomes in real choice events?** Not determinable — zero
   choice events occurred.
10. **What does CH-001 look like historically?** Not determinable — zero positions reached +1R (zero
    positions existed at all).
11. **Which regimes appear strongest/weakest?** Not determinable at this sample size.
12. **Survivorship/data limitations?** Current-universe survivorship bias (§7, present); no macro data was
    actually exercised in this run despite the intended fingerprint (§6); daily-bar warm-up was
    insufficient for any decision to occur (§2) — the dominant limitation of this specific stage.
13. **Any correctness anomalies?** None found. Determinism verified directly (two independent runs, byte-
    identical). No sizing/risk-invariant violations occurred (there were no trades to violate anything).
    No lookahead is structurally possible (H2's guard, unmodified). Zero exceptions across 567 cycles.
14. **Is the baseline trustworthy enough to begin Challenger experiments?** Not yet answerable from Smoke
    alone — see the decision gate below.

## HIST-001 decision gate

**PIPELINE: VALIDATED for this stage.** Real, pinned-revision fabhaus data flows end to end through the
real, unmodified Champion decision and execution stack, corporate-action detection runs and reports
honestly (zero found, not silently assumed), the real trading calendar is used (not a naive weekday count),
event identity is stamped live, the analysis layer (funnel/survivorship/capacity/choice/CH-001/portfolio)
runs cleanly against real (if trivial) output, determinism is proven by an actual repeated run, and the run
is tracked in MLflow with its dataset manifest hashes.

**BASELINE VERDICT: NOT YET RENDERED.** Per the pre-registration's own three-way scale, rendering
BASELINE VALID / VALID WITH LIMITATIONS / INVALID on zero trades and zero events would be meaningless —
there is no Champion behavior here to judge yet, only pipeline mechanics. That verdict is deferred to
Medium (where real decisions are expected in the later weeks of its 3-month window) and finalized at Full.

**Recommended next increment, in order**: (1) fetch a real 2024 macro dataset (starting several months
before 2024-01, so both Medium's warm-up and its own window are covered) and thread `macro_history` through
`baseline.run_baseline()` — currently a manual step, not yet wired as a default; (2) close the
`capacity_opportunity_cost()` scope limit (capture decision-time price/stop/target for non-executed
TRADEABLE observations); (3) run the Medium batch (~50-100 symbols × ~3 months) once (1) and (2) are done,
since Medium is exactly where this Smoke stage's central limitation (insufficient warm-up) is expected to
resolve on its own.

---

# MEDIUM STAGE (stage 2 of 3)

**Status: COMPLETE.** Pre-registration: `HIST_001_PREREGISTRATION.md` Amendment 1 (2026-09-29), committed
*before* any Medium result was computed. Universe: the full pre-registered 90-symbol
`dashboard/sector_map.py` universe (89 with real fabhaus data — `BRK-B` has zero rows in the source, most
likely a ticker-format mismatch, disclosed not dropped). Warm-up: 2024-01-01..2024-03-31 (61 real trading
days). Evaluation: 2024-04-01..2024-06-30 (63 real trading days). This section reports on
**`CORRECTED_MEDIUM_3`** only — the two earlier Medium attempts are quarantined and documented separately in
*Invalid Medium Attempts* below, and contribute **zero** to any number in this section.

## Invalid Medium Attempts (kept in the audit trail, excluded from every statistic below)

### `INVALID_REPLAY_CLOCK_LEAK_1`

* **Affected commit**: `7aeabc0` (pre-registration amendment commit; no clock fixes yet).
* **Symptom**: 0 orders, 0 fills across the entire 6-month replay despite 2,353 evaluation-phase TRADEABLE
  observations (29 independent events) — every single entry attempt was refused by `check_preconditions()`
  with the audit reason `"price data is display-only (source age 80740713s > limit 900s)"`.
* **Root cause**: `lab.paper.broker._decision_valid()` calls `cache_policy.classify("price",
  quote.source_ts)` with no `now=` argument, so `classify()`'s real-wall-clock default (`time.time()`)
  compared a genuinely historical 2024 quote timestamp against the real 2026 replay run-time — a ~2.5-year
  apparent staleness, always past the 900-second freshness limit.
* **Correction**: `HistoricalExecutionContext` now also patches `cache_policy.classify`, injecting the
  replay's own `HistoricalClock` as `now` whenever a caller doesn't supply one — commit `fcd931b`.
* **Regression test**: `tests/historical/test_h4_execution.py::test_decision_valid_uses_the_historical_clock_not_the_real_wall_clock`.
* **Why excluded**: every reported metric in this section (funnel, execution, blocked-opportunity, choice,
  CH-001, performance) depends on real orders/fills existing at all — a run with zero of either has nothing
  usable to measure Champion behavior from.

### `INVALID_REPLAY_CLOCK_LEAK_2`

* **Affected commit**: `fcd931b` (cache_policy fix applied; the two bugs below not yet found).
* **Symptom**: none observed directly — this run was **killed mid-flight** (before completion) once a
  deeper audit, prompted by the directive's own request to search for remaining wall-clock leaks, found two
  more reachable, decision/analysis-affecting bugs that would have silently corrupted this run's results
  exactly as `INVALID_REPLAY_CLOCK_LEAK_1` had, just less visibly (no executions were refused outright — the
  data would have looked plausible while being wrong).
* **Root causes** (both found by static audit before this run finished, not by observing bad output from
  it):
  1. `paper.db.utcnow()` (no override parameter at all) stamps every position's `opened_at`/`closed_at` —
     and every signal/order/fill `created_at`/`outcome_at`/`filled_at` — with the real wall-clock run-time,
     not the historical trade date. This project's own `analysis.py` filters positions by `opened_at` date
     against the evaluation window; left unpatched, every real historical trade would have been invisible
     to that filter (reporting zero trades despite real fills existing in the ledger).
  2. `risk.cooldown_state()` calls `dt.date.today()` inline, with no override parameter at all. Even with
     (1) fixed, comparing a historical `until` date against the real wall-clock date means the
     consecutive-loss cooldown gate could never trigger during any historical replay.
* **Correction**: `HistoricalExecutionContext` now also patches `paper.db.utcnow` (replaced outright — it
  takes no parameters to call through) and `risk.cooldown_state` (call-through: real streak/threshold
  computation unchanged, only the final wall-clock comparison corrected) — commit `fa35d55`.
* **Regression tests**: `test_db_utcnow_uses_the_historical_clock_not_the_real_wall_clock`,
  `test_position_opened_at_is_the_historical_date_not_the_real_run_date`,
  `test_cooldown_state_compares_against_the_historical_clock` (all in `tests/historical/test_h4_execution.py`).
* **Why excluded**: never completed; even if it had, its `opened_at`/`closed_at` and cooldown-gate behavior
  were confirmed wrong before the run finished, so nothing from it would have been usable regardless.

### `CORRECTED_MEDIUM_3`

* **Commit**: `fa35d55d91cf66f009fba0e27d3eab85708a4356` — all three clock fixes applied, nothing further
  changed in `baseline.py`/`execution.py` before or during this run.
* **Result**: completed cleanly in 2,462.5s (~41 minutes), 3,348 cycles, all `premarket` states `"ok"`
  (zero exceptions), **19 orders, 19 fills (100% conversion)**, 11 positions opened, 8 closed, 3 open at
  window end. This is the run every number below is computed from.
* One further, unrelated bug was found and fixed *after* this run completed, in this project's own
  post-hoc analysis code (not in the replay itself, so it needed no rerun): `capacity_opportunity_cost()`'s
  phrase filter looked for `"daily entry cap"` (the wording used only in a separate audit-log string) where
  the real `ev["note"]` text is `"daily order cap reached"` — fixed in commit `f265dc5`, re-run against the
  same immutable `CORRECTED_MEDIUM_3` pickle (no replay needed for an analysis-only fix).

## A. Replay correctness — wall-clock audit

Every `datetime.now()`/`date.today()`/`time.time()`/wall-clock-freshness call reachable from
`workflow.premarket()`/`workflow.market_hours()`'s real call graph (the only two functions HIST-001 drives)
was traced and classified. **Zero unresolved `REPLAY_CLOCK_REQUIRED` paths remain.**

| Location | Call | Reachable? | Classification | Resolution |
|---|---|---|---|---|
| `broker._decision_valid()` → `cache_policy.classify` | no `now=` | Yes, every entry attempt | was `REPLAY_CLOCK_REQUIRED` | **Fixed** (`fcd931b`) |
| `paper.db.utcnow()` | `datetime.now(UTC)`, no params | Yes, every position/order/signal timestamp | was `REPLAY_CLOCK_REQUIRED` | **Fixed** (`fa35d55`) |
| `risk.cooldown_state()` | `dt.date.today()` inline | Yes, every `check_entry()` call | was `REPLAY_CLOCK_REQUIRED` | **Fixed** (`fa35d55`) |
| `broker.py` ×5, `journal.py`, `options_shadow.py`, `workflow._today()` — `session_date or dt.date.today()` | fallback only | Dead — `baseline.py` threads an explicit historical `session_date` down every call in the replay path (verified, not assumed) | `SAFE` | none needed |
| `journal.expire_stale_tracking()` — `dt.date.today() - max_age_days` | only called from `workflow.postmarket()` | `postmarket()` is never called during replay (only `premarket`/`market_hours`) | `NON_DECISIONAL` | none needed |
| `canonical/contract_quality.py` — `now or datetime.now(UTC)` (×2) | option-quality grading, guarded by `if opt:` | `options_shadow` table is empty across the entire run — `strategy_service._pick_option_idea()` never returns a usable candidate historically, confirmed empirically, not assumed | `NON_DECISIONAL` | none needed |
| `dashboard/sector_map.py` — `datetime.now()` in `sector_map()` | only reached via `strategies.rank_sectors()` | `rank_sectors()` itself is entirely replaced by H3's `_patched_rank_sectors` | `NON_DECISIONAL` | none needed |
| `lab/paper/market_calendar.py` — `et_now()` | `now or datetime.now(UTC)` | Zero callers anywhere in the codebase | `NON_DECISIONAL` | none needed |
| `lab/paper/shadow_log.py`, `options_shadow.resolve_outcomes()` (multiple `db.utcnow()`/`now or datetime.now()`) | shadow-evidence bookkeeping | `capacity_snapshot`/`record_cycle`/`update_bars` no-op patched (H4); `candidate_row()` has no wall-clock call and its output is discarded; `resolve_outcomes()`'s `db.utcnow()` is now covered by the `db.utcnow` fix anyway | `SAFE` (2 no-op'd) / covered by fix (1) | none needed |
| `lab/paper/runtime.py` (live scheduler daemon) and every live-data-source module (`alphavantage`, `edgar`, `finnhub_data`, `fred.py`'s own live-call path, `halts`, `market_data`, `model_registry`, `news_feeds`, `providers`, `robinhood_mcp`, `robinhood_view`, `snaptrade_data`, `social_sentiment`, `calibration`, `catalyst_scan`, `credit_spreads`, `debit_spreads`, `risk_engine`, `run_lab`, `strategy_lab`, `auto_paper`) | none of these modules are imported or called anywhere in `workflow.premarket()`/`market_hours()`'s call graph (H3 stubs every `_fam_*` family except `_fam_trend`/`_fam_macro`; `robinhood_mcp`/`snaptrade_data` are live-broker-only) | `NON_DECISIONAL` | none needed |

**Other §A checks**: zero unexpected exceptions (all 3,348 `premarket` states `"ok"`; zero `quotes_missing`
across every `market_hours` cycle). Zero lookahead violations (H2's `HistoricalMarketProvider` timestamp
filter is structural and unmodified; its own test suite — part of the 218 passing — still covers it). Zero
production-state access (`guard_all()`/`assert_not_production_host`/`assert_not_production_path` ran on
every guarded path; the isolated ledger lives under `data/historical/ledgers/hist001_medium_2024h1/`, a
historical-only directory). Zero live-provider fallback (`quotes_missing` empty everywhere; `provider_health`
patched to a constant historical-replay stub). Zero stale-quote rejections caused by present-day wall time in
the corrected run (the `cache_policy` fix specifically eliminates this failure mode — confirmed by its own
regression test and by the fact that `check_preconditions`/`_decision_valid` produced **zero**
`entry_refused`/`risk_blocked`/`unaffordable` audit rows in `CORRECTED_MEDIUM_3`, versus 2,672 in the first
invalid run).

## B. Macro correctness

* **Macro store**: `HIST001_MEDIUM_2024_H1_MACRO`, real ALFRED vintage-aware fetch (`DGS10`, `DGS2`,
  `VIXCLS`, `FEDFUNDS`), 424 observations, `local_sha256=ae9c08970f26a16b7290f9ec81a1b9d6f10486d577187fcbda212d8036d39e44`.
* **Coverage**: `assert_macro_coverage()` verified clean for the full `[2024-01-01, 2024-06-30]`
  warm-up+evaluation range before `run_baseline()` was ever invoked for Medium — the run structurally could
  not have started otherwise.
* **Point-in-time lookup**: `historical_macro_signal()` uses `MacroHistory.latest_value_as_of()`'s vintage
  window semantics throughout (same, already-tested H5.5 machinery — no new code path for Medium).
* **No live FRED calls during replay**: the macro dataset is loaded once from local Parquet
  (`load_macro_history`) before the replay starts; nothing in `avdi_adapter.py`'s `_patched_fam_macro` touches
  the network.
* **No silent fallback to `PRICE_TREND_ONLY_V1`**: `capability_fingerprint=PRICE_TREND_MACRO_V1` on every
  single one of the **10,530** `decision_capture` entries this run produced — zero exceptions, zero missing.
* **Genuinely exercised, not just tagged**: a 2,000-signal sample of the real `signals` table's
  `data_quality_json` shows `macro.coverage=0.75` on **every** sampled row (never 0.0, which would mean
  "no macro data"), and `overall=0.575` on every sampled row — exactly the H5.5-established value that
  crosses the 0.55 data-quality floor via macro alone.
* **Missing observations / fallback count**: 0.

## C. Medium dataset facts

| Field | Value |
|---|---|
| Git commit (replay) | `fa35d55d91cf66f009fba0e27d3eab85708a4356` |
| Champion decision-engine version | `decision_engine/gates-v1.1` |
| Execution/risk version | post-`322e325` executable-risk sizing (unchanged) |
| Event-identity version | `research/historical/event_identity.py` (H7), unchanged since Smoke |
| Capability fingerprint | `PRICE_TREND_MACRO_V1` (100% of decisions, verified above) |
| Symbol count (requested / with data) | 90 / 89 (`BRK-B` has zero fabhaus rows) |
| Symbol list source | `dashboard/sector_map.py` `SECTORS`, all 11 sectors, at the HIST-001 pre-registration commit |
| Warm-up | 2024-01-01 .. 2024-03-31 (61 real trading days) |
| Evaluation | 2024-04-01 .. 2024-06-30 (63 real trading days) |
| Total cycles | 3,348 (124 trading days × 27 cycles/day) |
| Total 5-minute rows | 1,164,068 |
| Daily bars generated | 11,491 |
| Corporate-action findings | 3 suspected (NVDA 10-for-1 split 2024-06-10, **confirmed**, high confidence; CMG ~50-for-1 split 2024-06-26, suspected, low confidence; WMT 3-for-1 split 2024-02-26, suspected, volume didn't confirm) — all three are REAL historical splits; only NVDA cleared the detector's own confirmation bar; none of the three affects the 11 executed positions in this run (checked: none of UBER/NFLX/NVDA/CAT/FCX/RTX/NEE/AMGN/SO/WMT/AVGO's entry/exit windows overlap a suspected split date except NVDA itself, whose position closed 2024-04-19, well before its own 2024-06-10 split) |
| Volume trust | `RELATIVE_ONLY` throughout |
| Equity dataset manifests | `HIST001_MEDIUM_2024_H1_5M` (`parquet_sha256=f2a3306fb6f243a2a4706b71538e8eb8dd125362a0e3f0e3ba54a21f33a901f6`), `HIST001_MEDIUM_2024_H1_DAILY` (`parquet_sha256=541bcd39a1cd0de63e2c5207b994e23a0088bdf09814c2c2d8563ff0b083840d`) |
| Survivorship bias | Present, disclosed (today's 90-symbol universe replayed backward — unchanged from Smoke's disclosure) |

## D. Funnel (evaluation phase only)

| Stage | Raw observations | Independent events | % of upstream |
|---|---|---|---|
| Finalists (raw, from scanner) | 8,505 | 47 | — |
| REJECT | 5,812 | 45 | 68.3% obs / 95.7% of events *ever* showed REJECT |
| MONITOR | 340 | 21 | 4.0% obs / 44.7% of events |
| TRADEABLE | 2,353 | 29 | 27.7% obs / 61.7% of events |
| → orders placed | 19 | 19 | 65.5% of TRADEABLE independent events |
| → fills | 19 | 19 | 100% of orders |
| → exits (closed by window end) | 8 | 8 | 42.1% of fills (3 still open) |

**An event's decision/independent-event counts are not mutually exclusive**: 45+29+21=95 exceeds the 47
total independent events because a single persisting event can legitimately be observed as REJECT on one
cycle and TRADEABLE on a later one as price/indicators move — this is expected, not a duplication bug (the
same property Smoke's own funnel design already documents).

**Major rejection/block reasons** (all-phases, audit-sourced — repeated scans of the same setup are NOT
counted as independent evidence here, only as raw observation volume):
* `"stock leg not canonically executable"` — 2,177 raw observations. This is the generic label
  `lab.paper.workflow.py` attaches whenever a finalist's decision-engine result was REJECT/MONITOR (i.e.
  `setup_tradeable=False`) — not a genuine "TRADEABLE but blocked" case; confirmed by cross-referencing a
  sample against the `signals` table (`action='REJECT'`).
  Zero of the 29 real TRADEABLE independent events ever hit this reason.
* `"daily entry cap reached (2 per day, incl. persisted entries)"` — 17 raw observations, **all on
  2024-03-08** (warm-up phase, before `evaluation_start`) — zero occurrences during the evaluation window
  itself in this particular run.

## E. Execution invariants

For every one of the **11 entry orders**, modeled risk (`quantity × |entry_price − stop|`, using the exact
decision-time price/stop `baseline.run_baseline()` captured) was checked against `max_loss_per_trade=$5.00`
(`cfg.risk()`, unchanged Champion config):

| Order | Symbol | Qty | Price | Stop | Modeled risk | ≤ $5.00? |
|---|---|---|---|---|---|---|
| ord_96e0a844 | UBER | 1.556949 | 79.76 | 77.14 | $4.08 | ✓ |
| ord_138694b5 | NFLX | 0.205063 | 610.25 | 591.98 | $3.75 | ✓ |
| ord_54b1dc54 | NVDA | 0.090904 | 870.00 | 817.41 | $4.78 | ✓ |
| ord_14b092f6 | CAT | 0.355858 | 351.00 | 342.55 | $3.01 | ✓ |
| ord_9922d4a2 | FCX | 2.320260 | 48.76 | 46.66 | $4.87 | ✓ |
| ord_3b742551 | RTX | 1.227219 | 102.16 | 99.08 | $3.78 | ✓ |
| ord_b85999b7 | NEE | 1.853278 | 65.40 | 63.06 | $4.34 | ✓ |
| ord_903c5b47 | AMGN | 0.318340 | 300.93 | 285.82 | $4.81 | ✓ |
| ord_62d7599c | SO | 1.593599 | 78.38 | 76.14 | $3.57 | ✓ |
| ord_9e211401 | WMT | 1.893623 | 65.96 | 64.47 | $2.82 | ✓ |
| ord_d266f5f9 | AVGO | 0.039368 | 1643.88 | 1522.53 | $4.78 | ✓ |

**Entries checked: 11. Maximum modeled risk: $4.87. Configured budget: $5.00. Maximum excess: $0.00.
Violations: 0.** (Two of these entries show `planned_risk=0.0` in the `signals` table itself — a journal-
bookkeeping quirk from `prev_sid` reuse across cycles, not a risk violation; the *actual* executed risk,
computed directly from decision-time price/stop above, was correctly within budget for both.)

Also verified: zero negative or zero-quantity fills (all 19 fills > 0); zero entries after cutoff (every
position's real `opened_at` timestamp matches, to the second, a cycle with `allow_entries=True` — checked
against all 11 positions); zero sector-cap bypasses (no two open positions ever shared a sector
simultaneously — verified by inspecting each position's open/close interval); zero daily-cap bypasses
(maximum 2 entries on any single calendar day, matching `max_entries_per_day=2` exactly, on 2024-03-08); zero
capital overspend (`available_cash`/`buying_power` never negative in the equity table); zero
`quotes_missing` (no fill outside the available historical bar range).

## F. Blocked TRADEABLE opportunity cost

Every blocked-TRADEABLE decision-time snapshot in this run carries: `event_id`, `decision_id`, timestamp,
symbol, direction, entry/stop/target, bid/ask, hypothetical quantity, risk budget, sector, exact blocking
reason, account equity, and capability fingerprint — the directive's own required minimum — confirmed by
direct inspection of `decision_capture` records, not assumed.

| Blocking category | Raw obs | Independent events | Resolved | Unresolved | Hypothetical net R | Avg R | Target hits | Stop hits | Ambiguous |
|---|---|---|---|---|---|---|---|---|---|
| Cutoff (`entries disabled (post_cutoff)`) | 365 | 26 | 21 | 5 | +3.00R | +0.14R | 8 | 13 | 0 |
| Daily entry cap | 0 | 0 | — | — | — | — | — | — | — |
| Sector cap | 0 | 0 | — | — | — | — | — | — | — |
| Existing position / already entered (incl. an earlier occurrence of a symbol later re-entered) | 497 | 6 | 5 | 1 | +4.00R | +0.80R | 3 | 2 | 0 |

Daily-entry-cap and sector-cap were **never independently binding on a TRADEABLE candidate during the
evaluation window** in this specific run (the only daily-cap hits occurred during warm-up, §D) — this is a
real, disclosed property of this particular 3-month sample, not a code gap; the capture mechanism itself is
proven functional by the cutoff and existing-position categories, which did produce real, resolved
counterfactuals. Per the directive: **no constraint is recommended for change based on this** — this is
measurement only, and the cutoff category shows a small positive edge (+0.14R average) in the setups that
were seen too late in the day to enter, which is exactly the kind of finding HIST-004 exists to investigate
formally, not something to act on from a Medium-stage side observation.

## G. Choice events

Collapsing raw cycle-level "≥2 simultaneous TRADEABLE finalists" snapshots (765 raw, most of them the same
underlying competition re-observed every ~15 minutes intraday) into contiguous distinct-candidate-set
episodes (by `event_id` set, evaluation phase, `allow_entries=True` only) yields **58 distinct competition
episodes**. Of these, exactly **1** coincided with a moment the account actually had a free slot: on
2024-05-14, FCX closed (freeing one of the 3 open-position slots) in the same cycle that both AMGN
(already open since 2024-05-07 — not a genuine new candidate, just re-observed as TRADEABLE while already
held) and SO (a genuine new candidate) appeared; SO was entered.

**Re-examining this one case closely: it is not a genuine ranking competition between two newly-eligible
candidates either.** AMGN already held its own slot and was structurally ineligible for re-entry
(`"existing position/open order - no duplicate entry"`); SO simply took the slot FCX's exit freed. **Genuine
choice events — two or more newly-eligible (not-already-held) candidates competing for one truly scarce,
available slot — occurred zero times in this Medium run.**

This is itself a real, useful finding for HIST-004 (the future ranking-policy study), not a defect: at this
strategy's real entry cadence (~1 new position per 1-2 weeks) against `max_open_positions=3` and
`max_positions_per_sector=1`, a genuinely scarce-slot, multiple-simultaneously-eligible-candidate decision is
rare enough that Medium's 3-month/63-day evaluation window produced none. **HIST-004 will need the Full
2024-2026 window's much larger trade count to gather real choice-event data** — this is disclosed here as a
limitation of Medium's sample size, not of the event-capture mechanism (which is proven correct by
`tests/historical/test_hist001_capacity_and_choice.py`'s synthetic two-candidate fixture). No ranking policy
change is made or recommended.

## H. CH-001 shadow analysis (SHADOW ONLY — never affects a real position, never promotes CH-001)

**6 of the 7 evaluation-phase positions reached +1R** per their own real ledger MFE (FCX +1.97R, RTX +2.04R,
NEE +1.97R, AMGN +1.94R [open], SO +1.04R [open], WMT +1.92R). Only AVGO (opened 2024-06-24, 4 trading days
before window end) had not yet reached +1R by the evaluation window's close.

**Full CH-001 delta-R (Champion's real exit vs. a hypothetical breakeven-after-+1R exit) is NOT computed in
this pass** — this requires a bar-by-bar `outcomes.py` resolution forward from each position's own +1R
timestamp, which the current `ch001_shadow()` implementation explicitly documents as out of scope
(`scope_limit` field, unchanged from before this run). Extending it now, under this acceptance sequence's own
time pressure, would risk exactly the kind of rushed implementation this project's correctness discipline
exists to avoid — and per the directive's own instruction, **HIST-002 remains the formal Challenger
experiment** for this question. Reporting "6 of 7 positions reached +1R" honestly, without a fabricated
delta-R number, is the correct scope for a Medium-stage measurement pass.

## I. Champion performance

**Evaluation-phase only** (the officially reported HIST-001 window — 4 resolved trades, 3 still open):

| Metric | Value |
|---|---|
| Starting equity | $500.00 |
| Ending equity | $521.74 |
| Net P&L | +$32.04 (realized, resolved trades only) |
| Net R (resolved trades) | +7.724R |
| Resolved trades | 4 |
| Win rate | 100% (4/4) — **see the small-sample warning below** |
| Average winner R | +1.931R |
| Average loser R | n/a (zero losers in this sample) |
| Expectancy | +1.931R / trade |
| Median R | +1.934R |
| Profit factor | undefined (no losses to divide by) |
| Max drawdown ($ / %) | $29.94 / 5.83% (peak 2024-04-11, trough 2024-04-23 — this drawdown falls entirely inside the evaluation window) |
| Drawdown duration | 21 calendar days (trough to full recovery, 2024-04-23 → 2024-05-14) |
| Longest losing streak | 0 (no losing trade occurred within the evaluation window) |
| Average holding time | 28.0 days |
| Open positions at window end | 3 (AMGN, SO, AVGO — unresolved, not counted above) |
| Capital utilization at window end | 54.8% |
| Execution/slippage cost | $0.00 commissions (Champion's real $0-commission model); slippage is priced into fill prices via `slippage_bps=5` (normal) / `gap_slippage_bps=25` (gap) — not a separate ledger line item |
| Sharpe / Sortino | **Not reported** — n=4 is far below any meaningful sample size, per the pre-registration's own rule |

**⚠ The 100% win rate and undefined profit factor are small-sample artifacts, not evidence of Champion
skill.** All 4 warm-up-phase resolved trades (UBER −1.01R, NFLX −2.24R, NVDA −1.01R, CAT +1.91R — excluded
from the table above by the warm-up/evaluation split's own design, not cherry-picked) show that real losses
absolutely occur under this exact Champion configuration; they simply didn't happen to close during this
specific 3-month evaluation window. **A statistically meaningful read requires Full's much larger trade
count — this Medium result must not be read as "the Champion wins 100% of the time."**

**Full-window supplementary context** (warm-up + evaluation combined, 8 resolved trades — informational
only, never the official HIST-001 metric, shown here because the directive explicitly asked whether a small
number of events dominate and this context is necessary to answer that honestly): net P&L +$19.96, net R
+5.37R, 5 winners / 3 losers (62.5% win rate), profit factor 2.11, expectancy +0.67R/trade, average holding
time 26.4 days. NFLX's −2.24R (versus every other loser's ≈−1.0R) is a genuine gap-fill: its stop was set at
$590.52 but the actual exit filled at $566.87 on a real overnight gap, a `fill_gap` audit event with
$23.65/share of modeled slippage (`gap_slippage_bps=25`) — correct, disclosed fill-simulator behavior, not a
defect.

## J. Concentration and stability

**By symbol / sector (evaluation-phase, n=4)**: no repeated symbols, no repeated sectors — FCX (materials),
RTX (industrials), NEE (utilities), WMT (consumer staples) are four different sectors. Zero
single-symbol/sector concentration structurally possible at this composition.

**Contribution to total** (evaluation-phase, n=4, net P&L $32.04): best trade (FCX, +$9.67) = 30.2% of
total; top 2 (FCX+NEE) = 59.3%. **Diagnostic excluding the single best trade**: remaining 3 trades still
100% win rate, net P&L $22.37 — the qualitative finding (all winners) does not depend on any one trade.

**By month (close date, evaluation-phase)**: May $18.99 (NEE, FCX — 2 trades), June $13.05 (RTX, WMT — 2
trades), April $0 (0 closes, though 3 positions opened). No losing month occurred in the evaluation window
(consistent with, and subject to the same caveat as, the 100% win rate above).

**Full-window (n=8) contribution is NOT a meaningful statistic at this sample size** — with 5 winners and 3
losers, "top 5 trades" is simply "every winning trade that exists," and any per-trade % of the small net
total ($19.96) is mechanically inflated (FCX alone = 48% of full-window net). This is flagged, not hidden:
**at n=4 (eval) or n=8 (full), no concentration statistic should be read as a finding about the Champion's
real diversification** — it is a direct, arithmetic consequence of having very few trades, fully resolved by
scaling to Full.

**Worst symbol/sector (full-window)**: NFLX / communication (−$8.76, the gap-fill loss, §I). **Best
symbol/sector (full-window)**: FCX / materials (+$9.67) or NEE / utilities (+$9.32), effectively tied.

## K. Correctness sanity audit

| Check | Result |
|---|---|
| Implausibly high win rate | **Flagged and explained** (§I) — n=4 small-sample artifact, not hidden or dismissed |
| Huge Sharpe / almost no losing trades | Sharpe not computed (too few trades); "almost no losing trades" is explained by the warm-up/evaluation split moving all 3 evaluation-phase-window losses into the disclosed warm-up bucket (real losses exist, just not inside this window) |
| Impossible fill behavior | None found — one real gap fill (NFLX), correctly modeled with disclosed slippage, not an anomaly |
| Equity discontinuities | None — full 124-point daily equity curve inspected directly, smooth day-to-day transitions throughout |
| Giant corporate-action P&L | None — none of the 3 suspected splits (NVDA/CMG/WMT) overlaps any executed position's open/close window except NVDA itself, whose own position closed before its own split date |
| One symbol dominating returns | Not present in the evaluation-phase 4-trade set (4 different symbols); present only in the full-window 8-trade set, and explained as a sample-size artifact (§J), not investigated further as an anomaly |
| Duplicated event IDs counted as independent trades | Checked — `event_identity.py`'s stamping is unique per real new setup; the "45+29+21 > 47" apparent excess in §D is explained (same event observed under different decisions over time), not a duplication bug |
| Repeated observations treated as independent trades | Explicitly guarded against throughout this report — all "independent event" counts are deduplicated by `event_id`, distinct from the much larger raw-observation counts |
| Unrealistically low drawdown | Not present — 5.83%/$29.94 max drawdown is a real, non-trivial, disclosed figure |
| Impossible account usage | None — cash/buying power never negative, capital utilization stayed in a sane 0-70% band throughout the equity curve |
| Suspiciously perfect exit ordering | The 5 winning trades' R-multiples (1.90–1.96R) cluster near, but are not identical to, a plausible fixed ~2R target — consistent with genuine price/slippage variance around a real target formula, not fabricated or duplicated data |

**No unresolved anomaly remains.**

## L. Determinism

**Representative-subset determinism** (already reported by the user, reproduced here for the record): a
5-day slice (2024-04-01..2024-04-05, with March 2024 as warm-up) run twice independently from the same
frozen commit and datasets produced **byte-identical** results: 20 events, 7 orders, 7 fills, ending equity
exactly $509.82, and identical order tuples (symbol/side/quantity) in identical sequence.

**Full-run determinism**: the full 6-month `CORRECTED_MEDIUM_3` replay was **not** independently re-run in
full a second time (a full rerun costs ~40-70 minutes of compute; given the representative-subset proof
above already exercises the complete pipeline — real macro, real decision-time capture, real orders/fills,
real capacity gates — against the same frozen commit and manifests, a second full-scale run was judged not
to add proportionate evidence for this acceptance decision). This is disclosed as a scope choice, not
skipped silently. Internal self-consistency was checked instead and found clean: audit-table tallies
(`not_executed`=2,194, `entry_allowed`+`opened`=11, `created`=19 orders) reconcile exactly against the
funnel and position counts reported in §D/§E with no discrepancy. **A full independent re-run of Medium is
recommended, but not required, before Full begins** — Full's own walk-forward design (development/
validation/holdout splits) will itself exercise determinism at a larger scale regardless.

## Answers to the 14 HIST-001 report questions (Medium-stage scope)

1. **Positive historical net expectancy under `PRICE_TREND_MACRO_V1`?** Evaluation-phase: +1.931R/trade
   (n=4, not statistically meaningful). Full-window: +0.67R/trade (n=8, still not meaningful). Directionally
   positive in this one 3-month sample; **not yet a reliable estimate**.
2. **Across how many independent events/trades?** 29 independent TRADEABLE events (eval phase); 19 real
   orders/fills; 4 resolved trades (eval phase), 8 resolved (full window).
3. **Stability across time?** By month: May and June both positive in the eval window (§J); too few months
   to assess stability meaningfully.
4. **Dominated by a few symbols/events?** Yes, unavoidably at this N (§J) — explained as a sample-size
   artifact, not investigated further as a red flag; resolved by Full's larger trade count.
5. **Max drawdown?** $29.94 / 5.83%, 21-day recovery (§I).
6. **Biggest funnel bottleneck?** `"stock leg not canonically executable"` (REJECT/MONITOR finalists, not a
   real bottleneck on genuine TRADEABLE candidates — those converted to orders 65.5% of the time); among
   genuine TRADEABLE-blocking reasons, cutoff (end-of-day timing) is the largest single category.
7. **Historical opportunity cost of the 2-entry daily cap?** Not binding during the evaluation window in
   this run (0 independent events blocked by it) — all 17 daily-cap hits fell in warm-up.
8. **How often does sector capacity block a better outcome?** Zero independent events blocked by sector cap
   in this run's evaluation window.
9. **Does scanner ranking select better/worse outcomes in real choice events?** Not answerable from this
   run — zero genuine choice events occurred (§G); deferred to Full/HIST-004.
10. **What does CH-001 look like historically?** 6/7 evaluation-phase positions reached +1R; full delta-R
    not computed this pass (§H, scope-limited by design, deferred to HIST-002).
11. **Which regimes appear strongest/weakest?** Not assessed — no new regime definitions invented, per the
    standing rule, and too few months for the existing simple descriptive breakdown to be meaningful.
12. **Survivorship/data limitations?** Current-universe survivorship bias persists (§C); `BRK-B` has no
    fabhaus data (disclosed, not dropped from the requested universe); 2 of 3 suspected corporate actions
    remain unconfirmed by the detector's own volume heuristic (disclosed, fail-closed, not silently
    adjusted).
13. **Any correctness anomalies?** Three real ones were found and fixed during this stage (cache_policy
    clock leak, `db.utcnow()` clock leak, `cooldown_state()` clock leak) plus one analysis-only bug
    (`capacity_opportunity_cost()`'s phrase filter) — all documented above with root cause, fix, and
    regression test. **Zero unresolved anomalies remain** in the corrected run.
14. **Is the baseline trustworthy enough to scale to Full?** See the decision gate below.

## Medium decision gate

**`MEDIUM_VALID_WITH_LIMITATIONS`**

Real Champion behavior occurred (89-symbol universe, real macro replay genuinely exercised and verified,
real executions with zero risk-invariant violations, real blocked-TRADEABLE counterfactuals resolved, real
event identity, a clean wall-clock audit with zero unresolved `REPLAY_CLOCK_REQUIRED` paths, and
representative-subset determinism proven byte-identical). This is squarely mechanical/research validity —
**not** a profitability verdict; the positive result in this one sample is explicitly flagged as not yet
statistically meaningful.

**Named limitations, carried into Full**: (1) sample size — n=4/n=8 resolved trades make every performance
statistic in §I/§J a preliminary read, not a reliable estimate; (2) survivorship bias — today's universe
replayed backward, unchanged since Smoke; (3) zero genuine choice events occurred, so HIST-004's ranking
study has no Medium-stage data to draw on and must wait for Full; (4) CH-001's full delta-R remains
unmeasured (deferred to HIST-002 by design); (5) 2 of 3 suspected corporate actions are unconfirmed by the
detector's own heuristic (disclosed, not silently resolved either way); (6) full-run determinism was
checked via internal self-consistency and a representative subset, not an independent full second run.
**None of these limitations reflect a correctness defect** — every one is a disclosed scope/sample-size
boundary, and none blocks proceeding to Full, which by design produces a much larger, more statistically
meaningful sample and will itself exercise cross-validation via its own development/validation/holdout
structure.
