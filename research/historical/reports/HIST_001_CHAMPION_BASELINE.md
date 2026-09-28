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
