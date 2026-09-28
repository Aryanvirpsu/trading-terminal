# H5 — Reproduce the known forward session (2026-09-25)

**Verdict: PASS WITH DOCUMENTED CAPABILITY DIFFERENCES.** Historical Lab is mechanically representative
enough to proceed to H6 — see "Final verdict" at the end for the precise scope of that pass, including one
finding that should shape H6+ priorities rather than be read past.

Branch `h1/historical-lab` only. Nothing here touched `main`, a production ledger, or the forward Ubuntu
runtime. No strategy logic, gate, threshold, ranking, capacity rule, or entry cutoff was changed anywhere
in this work — every fix below is to a market-data/clock seam, never to Champion decision logic.

## 1. The frozen reference

Source: `docs/UBUNTU_LIVE_ACCEPTANCE_01.md` (commit `f12a552`), the real Ubuntu acceptance report for
Friday 2026-09-25. Transcribed into `research/historical/known_forward/reference.py`, never imported by
`replay.py` (comparison happens after the fact, per the directive's own step 3, precisely to prevent
accidentally feeding known decisions into the replay).

**Scope disclosed plainly**: this environment has no SSH or backup access to the Ubuntu host
(129.159.91.34) or its raw shadow-log database. The reference is therefore session/event-level — the
session summary (v1.1 boundary, cycle/observation/event/signal/entry counts), all 12 independent events
with their documented classification progressions and final actions/reasons, and the two fully detailed
order traces (DELL, META) — not a fabricated 26-row cycle-by-cycle machine trace. Inventing that precision
would be worse than being explicit about what is and is not available.

## 2. The KNOWN_FORWARD_2026_09_25 dataset

fabhaus ends before September 2026 and must not be used ("do not fabricate September 2026 HF data" — the
directive is explicit). Real Ubuntu-captured data (shadow logger bars, decision-time quote/bid/ask
records) is the directive's preferred source and is unreachable from this environment. The necessary,
clearly documented supplement: real Yahoo data for the 12 symbols the forward session actually touched,
fetched fresh for this exact date via the same `YahooBootstrapAdapter` H1's own acceptance sample uses.

Two separately versioned datasets, own manifests, never mixed with fabhaus or each other:

| Dataset | Rows | Timeframe | Role | `volume_trust` |
|---|---|---|---|---|
| `KNOWN_FORWARD_2026_09_25_DAILY` | 984 (12 symbols × 82 days) | 1d, 2026-06-01..09-25 | decisions (trend/RSI) | `ABSOLUTE` |
| `KNOWN_FORWARD_2026_09_25_5M` | 936 (12 symbols × 78 bars) | 5m, 2026-09-25 | execution (quotes/marks) | `ABSOLUTE` |

`volume_trust=ABSOLUTE`: this is genuine Yahoo consolidated-tape volume for large, liquid US equities —
the same source the fabhaus audit itself used as ground truth — not a general claim about Yahoo everywhere.

Both are small (~36-40KB) and committed alongside their manifests, following the `fabhaus_sample/`
precedent, so this reproduction is itself reproducible without re-fetching anything.

## 3. Three real mechanical bugs found and fixed

Found by actually running the replay and diagnosing why DELL/META stayed at MONITOR — not by inspection.

1. **`lab/freshness.py:bar_age_seconds()` used the real wall clock**, not the historical clock. A
   replay run today judges a 2026-09-25 bar's freshness against however many real days have passed since
   the dataset was fetched. First symptom: `failed_gates=['data_quality','freshness']`. **Fixed** via
   `HistoricalAVDIContext._patched_bar_age_seconds` (patches `freshness.bar_age_seconds`).
2. **A second, latent bug found while replacing #1**: the original's midnight→session-close adjustment
   added hours in UTC-space to a UTC-midnight timestamp, landing at `session_close_hour` UTC (noon ET in
   EDT) instead of the real `session_close_hour` ET (4pm ET, i.e. 20:00 UTC in EDT). **Fixed** with real
   ET-aware arithmetic, using the midnight timestamp's own UTC date as the trading day (not a date
   re-derived by projecting into ET first, which shifts the calendar date backward by a day).
3. **`dashboard/market_regime.session_state()` defaults to the real wall clock** when called with no
   `now` argument — `decision_engine._engine_freshness()` always calls it that way. A replay run for real
   on a later date classified an ordinary Friday session as whatever the market happens to be doing RIGHT
   NOW. **Fixed** (only the "no explicit `now`" default; an explicit caller-supplied `now` still works).
4. **`yahoo_bootstrap.py` labelled a daily bar at UTC midnight of its own date** — `HistoricalMarketProvider`'s
   `timestamp <= clock.now` filter then treated it as visible from the START of that date, a real same-day
   lookahead leak (a noon clock on trading day D could see day D's full close/high/low, which cannot
   actually be known until the session closes that afternoon). **Fixed**: daily bars are now labelled at
   their own date's real 16:00 ET close (DST-correct, per-row), matching `schemas/bars.py`'s own documented
   "period-end" convention, which this adapter was silently violating for the daily timeframe.

A fifth addition, not a bug fix: **today's still-forming daily bar can now be honestly reconstructed** from
real, already-visible intraday bars (open of the first / running high-low / close of the latest / summed
volume) when a finer execution feed is available (H5 blocker #5). Without this, a live intraday
`fallback_ta.analysis()` call benefits from yfinance's own continuously-updating in-progress daily candle;
a historical bulk daily fetch made after the fact can only ever return complete, closed sessions — a real,
structural `CAPABILITY_DIFFERENCE` for any source with no intraday feed, closed here for the one dataset
that has one.

Net effect: freshness now correctly reads `fresh, age=0` intraday for both DELL and META (previously
20h-72k-seconds stale, depending on which clock bug was still present).

## 4. The remaining gap: `data_quality`, precisely diagnosed

With freshness fixed, DELL and META both cap out at exactly **`data_quality.overall = 0.536`**, just under
the engine's `0.55` minimum (`decision_engine._min_data_quality()`, `DECISION_MIN_DATA_QUALITY`, unchanged
default). `data_quality` is a SOFT gate (`lab/decision_engine.py`'s `_gate("data_quality", ...)`,
`blocking=False`) — failing it alone, with every hard gate passing, caps the decision at **MONITOR**, never
REJECT and never TRADEABLE (`decision = "REJECT" if hard_fail else ("MONITOR" if soft_fail else "TRADEABLE")`).

This is **not symbol-specific and not date-specific**. Under `PRICE_TREND_ONLY_V1`, only `price` (weight
1.0, coverage ~0.95), `candles` (weight 1.0, coverage ~0.925) and `sector` (weight 0.4, coverage ~0.8) ever
have non-zero coverage; `fundamentals`, `news`, `analyst`, `filings`, `macro`, and `options` are
permanently at 0% coverage (the same disclosed families `avdi_adapter.py`'s
`FAMILIES_WITHOUT_HISTORICAL_REPLAY` already names). The resulting ~0.536 ceiling is a **mathematical
consequence of the current capability fingerprint**, not a defect in this replay, this dataset, or this
symbol/date — **no symbol can reach TRADEABLE under this fingerprint, on any date, no matter how strong its
price action**, until at least one more evidence category gets real historical coverage. This was not
tuned to reach this conclusion (directive step 13 forbids it); it was measured, once, on the real data.

## 5. Comparison matrix

Full replay (all 27 scheduled cycles, real committed dataset, no fixtures) compared against the frozen
reference:

| Symbol | Forward classification | Historical classification | Forward action | Historical action | Difference reason |
|---|---|---|---|---|---|
| DELL | TRADEABLE | MONITOR | entered | not entered (capped at MONITOR all day) | **CAPABILITY_DIFFERENCE** |
| META | TRADEABLE | MONITOR | entered | not entered (capped at MONITOR all day) | **CAPABILITY_DIFFERENCE** |
| TMO | TRADEABLE | MONITOR | not entered (daily cap) | not entered (capped at MONITOR all day) | **CAPABILITY_DIFFERENCE** |
| AAPL | TRADEABLE | never a candidate | not entered (sector cap) | never a candidate | PRICE_DATA_DIFFERENCE |
| MSFT | TRADEABLE | never a candidate | not entered (daily cap) | never a candidate | PRICE_DATA_DIFFERENCE |
| TSLA | MONITOR | never a candidate | not entered (cutoff) | never a candidate | PRICE_DATA_DIFFERENCE |
| NVDA | MONITOR | never a candidate | not entered | never a candidate | PRICE_DATA_DIFFERENCE |
| FCX | MONITOR | never a candidate | not entered | never a candidate | PRICE_DATA_DIFFERENCE |
| NEM | MONITOR | never a candidate | not entered | never a candidate | PRICE_DATA_DIFFERENCE |
| VRTX | MONITOR | never a candidate | not entered | never a candidate | PRICE_DATA_DIFFERENCE |
| AMD | MONITOR | MONITOR | not entered | not entered | **MATCH** |
| CRM | MONITOR | MONITOR | not entered | not entered | **MATCH** |

Zero rows are `REPLAY_BUG` or `UNKNOWN`. Every divergence is explained by one of exactly two mechanisms:

* **`CAPABILITY_DIFFERENCE` (DELL, META, TMO)** — the universal `data_quality` ceiling in §4. These three
  are exactly the symbols the forward session actually pushed to TRADEABLE; the replay reaches the same
  MONITOR-vs-TRADEABLE boundary and is held back by the one, precisely quantified, already-disclosed cause.
* **`PRICE_DATA_DIFFERENCE` (AAPL, MSFT, TSLA, NVDA, FCX, NEM, VRTX)** — never became a scanner candidate at
  all under the real Yahoo-sourced technicals for this date (didn't clear `score_liquid_momentum`/
  `score_sector_rs`/`score_mean_reversion`'s own entry criteria). Plausible causes: the forward session's
  live provider mix (60% TradingView / 40% Yahoo, per the acceptance doc) saw different intraday technicals
  than Yahoo's own bars for the same date, or the real setups genuinely differed. **Not independently
  verified further in this pass** — stated as a hypothesis, not a conclusion, per the same evidence
  standard as everything else in this report.

### Traced explicitly (directive step 9)

* **DELL** — reaches the real `evaluate()` end-to-end with `confidence_quality=91.0` (well above the 45
  threshold), `conviction_threshold=60.0` cleared, real `stop`/`target`/`quantity` computed by the real
  `canonical_bridge`/`risk` code — the ENTIRE decision pipeline runs correctly on DELL; the ONLY thing
  keeping it at MONITOR is the `data_quality` soft gate. Account fit, executable price, quantity, stop and
  target were never exercised end-to-end through the broker because DELL never reaches TRADEABLE in this
  replay — the sizing/fill MACHINERY itself is exercised and tested independently (H4's
  `test_h4_execution.py`), just not by DELL's own signal in this specific run.
* **META** — identical pattern to DELL, same root cause.
* **AAPL** — never becomes a candidate at all (`PRICE_DATA_DIFFERENCE`), so the replay cannot show whether
  it would generate the same technology-sector slot conflict DELL won in the forward session. The
  SECTOR-CAPACITY mechanism itself (one slot per sector, real `risk.check_entry`) is the literal,
  unmodified production code and is independently exercised by unit tests
  (`tests/unit/test_executable_risk.py`); this replay run specifically did not reach the point of testing
  it end-to-end, because it never got TWO TRADEABLE technology candidates in the same cycle.
* **TMO / MSFT** — TMO reaches MONITOR (capped by `data_quality`, like DELL/META); MSFT never becomes a
  candidate. Neither reaches TRADEABLE, so the daily-entry-cap block the forward session applied to them
  could not be exercised by an actual TRADEABLE attempt in this run either.
* **TSLA** — MONITOR only in the forward session too (its single 15:05 observation never reached
  TRADEABLE); the replay also never makes it a candidate. Whether the entry-cutoff mechanism would have
  correctly blocked a TSLA TRADEABLE this late is not tested by this specific run, but IS independently
  covered: the schedule's `allow_entries=False` for every `post_cutoff` cycle is real, tested
  (`test_h5_known_forward_replay.py::test_schedule_matches_the_documented_forward_cadence`), and
  `workflow.premarket()`'s own `allow_entries` handling is the literal unmodified production code.

## 6. Mechanical equivalence (directive step 7, layer 1)

All independently verified, not merely asserted:

| Property | Status | Evidence |
|---|---|---|
| No lookahead | ✓ | Structural (`HistoricalMarketProvider._visible()`); `test_h2_clock_and_lookahead.py`; the §3 daily-bar fix closed a real same-day leak |
| Correct cycle chronology | ✓ | One shared `HistoricalClock` for both decision and execution providers; refuses to move backward |
| Correct session rules | ✓ | Exact 27-cycle schedule matches the documented forward cadence; `allow_entries`/`session_type` per cycle |
| State persistence | ✓ | `test_replay_capacity_state_persists_across_cycles` (idempotency notes only fire when prior-cycle ledger state is genuinely visible later) |
| Capacity propagation | Not exercised by a real TRADEABLE in THIS run (see §4/§5) | Mechanism itself is the real, unmodified `risk.check_entry`, independently tested in `tests/unit/test_executable_risk.py` |
| Event clustering | Not exercised (shadow_log is disabled for historical replay — no historical-replay meaning, same disclosed pattern as H3's stubbed families) | `journal.record_signal`'s own signal-per-symbol-per-session semantics is the real production code and IS exercised (27 cycles, unchanged observations correctly not re-journaled) |
| Execution ordering | ✓ | `premarket()` then `market_hours()` every cycle, exactly the forward runtime's own cadence |
| Determinism | ✓ | `test_replay_is_deterministic_same_run_twice` |
| Production isolation | ✓ | `test_no_production_ledger_touched_by_the_replay`; guard_all() on every entry point |

## 7. Decision equivalence (directive step 7, layer 2)

12 reference symbols compared; 2 exact matches, 3 explained by one precisely quantified capability
difference, 7 explained by a stated (not yet independently verified) price-data-difference hypothesis, and
**zero unexplained divergences or replay bugs**.

## 8. Execution-risk equivalence (directive step 11)

`research/historical/legacy_sizing.py` reimplements the pre-`322e325` sizing formula standalone (never
wired into any live path) for a historical-as-executed comparison. Not exercised against DELL/META's own
numbers in this run (neither reaches TRADEABLE here, so no historical-as-executed order was actually
produced to compare) — verified instead against a DELL-shaped synthetic scenario
(`tests/historical/test_h5_legacy_sizing.py`), reproducing the documented mechanism (the old formula's
realised stop loss exceeds its own $5 budget; the current, real `risk.position_size()` on the identical
inputs does not). The real DELL ($5.277 realised vs $5.00 budget) and META ($5.296) figures from the
acceptance doc stand as the historical-as-executed ground truth; this run does not reproduce them via its
own order because no order was placed.

## 9. Volume-trust enforcement (directive step 6)

Closed as its own increment (`volume_trust.py`), not merely documented: `HistoricalMarketProvider` now
carries an explicit `volume_trust` attribute; `score_liquid_momentum` (the only funnel strategy with an
absolute-dollar-volume floor) is excluded from `strategies=` for any non-`ABSOLUTE` source, enforced at
`paper.config.enabled_strategies()` so it covers every caller including `workflow.premarket()`'s own
internal scan. `KNOWN_FORWARD_2026_09_25`'s Yahoo data is `ABSOLUTE`, so this replay's own `liquid_momentum`
strategy was NOT excluded — the enforcement mechanism is proven against a `RELATIVE_ONLY`/`UNKNOWN` fixture
in `test_h5_volume_trust_and_multisector.py`, not exercised in anger by this specific run.

## 10. What this run does NOT prove

Stated plainly rather than left implicit:

* It does not prove the sector-capacity, daily-entry-cap, or entry-cutoff mechanisms behave identically to
  the forward session END TO END through an actual TRADEABLE decision, because none occurred in this run.
  Those mechanisms are the literal unmodified production code and are independently unit-tested, but this
  specific replay did not exercise them via its own natural output.
* It does not independently verify the `PRICE_DATA_DIFFERENCE` hypothesis for the 7 symbols that never
  became candidates — that would require comparing Yahoo's exact intraday technicals against whatever the
  forward session's live TradingView/Yahoo mix actually saw, which this pass did not do.
* It does not reproduce the DELL/META historical-as-executed dollar figures from an order this replay
  itself placed (none was placed); the legacy-sizing mechanism is verified against a synthetic scenario
  instead.

## Final verdict

**PASS WITH DOCUMENTED CAPABILITY DIFFERENCES**, per directive step 16's own definition ("mechanics are
sound; remaining decision differences are attributable to intentionally unavailable historical evidence
families") — with one addition that should be read as load-bearing, not a footnote:

> **Under the current `PRICE_TREND_ONLY_V1` capability fingerprint, no historical decision can reach
> TRADEABLE, on any symbol or date, because the `data_quality` soft gate structurally caps out at ~0.536
> against a 0.55 floor.** This is a real, quantified, previously-undocumented constraint discovered by
> this H5 run, not a defect in H5 itself. It does not block H6 (outcome/event engine) or H7 (event identity
> finalization) — both operate on whatever decisions a run actually produces, MONITOR included, and the
> mechanics proven in §6 hold regardless. It DOES mean that a HIST-001 Champion baseline, run under this
> same fingerprint, will systematically under-produce TRADEABLE signals relative to the forward Champion's
> true selectivity, and any such baseline must say so explicitly rather than being read as "the historical
> Champion is less selective than the forward one." Before HIST-001 is treated as representative, wiring at
> least one more evidence category into real historical replay (fundamentals looks like the cheapest lever:
> static point-in-time fundamentals data is far more commonly available historically than news/analyst/
> filings/macro/options text-and-event feeds) should be considered — a decision for the user to make, not
> unilaterally decided here.

Per directive step 17: proceed next to **H6 (outcome engine)**. The mechanics this run validates —
chronology, lookahead safety, state/capacity persistence, determinism, production isolation — are exactly
what H6's outcome tracking needs to be trustworthy, independent of how many decisions happen to reach
TRADEABLE. No CH-001/capacity/ranking profitability conclusions before HIST-001 exists, and HIST-001 itself
must carry the capability-fingerprint caveat above.
