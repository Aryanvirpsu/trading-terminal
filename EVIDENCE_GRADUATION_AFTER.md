# EVIDENCE_GRADUATION_AFTER.md — Canonical Trading Architecture v1.2: Evidence & Graduation

Companion to `Canonical_Option_Architecture_v1.1.pdf`/`DECISION_LOGIC_AFTER.md`. v1.1
built the A/B/D/E/executable layers and closed the sizing-authority gap. This pass
(2026-09) closes a different gap: the word **EV** was doing five jobs at once —
model opinion, contract quality, execution economics, empirical evidence, and risk
authorization — and nothing forced them apart. This doc records what was found, what
changed, and what's still an open policy question.

## Why this pass happened

The cloud paper ledger (Case 1) went live and started producing real shadow-option
records. Reading them surfaced a real number: `COP  ev_after_costs: +$14.20`. That
number is correctly computed, and correctly means *the model currently favors this
contract*. It does **not** mean the contract has a demonstrated $14.20 expectation —
its probability input has never been checked against a realized outcome. Nothing in
the code, the DB column name, or any doc said so. That's the defect this pass fixes.

## Architecture findings — every EV/scoring implementation traced

| Implementation | File:line | Live/dead | Consumes | Produces | Unit |
|---|---|---|---|---|---|
| `_expected_value(p_win, profit, loss, cost)` | `lab/decision_engine.py:161` | **Live** — the one shared formula | any (p, profit, loss, cost) | `p×profit − (1−p)×loss − cost` | $ |
| `p_direction` | `lab/decision_engine.py:484` | **Live** | indicator agreement/alignment | probability, clamped [0.15, 0.85] | probability-shaped, **not calibrated** |
| `p_trade`/`p_trade_profitable` | `lab/decision_engine.py:499`, `canonical_bridge.py:167` | **Live**, computed independently in both places from the same inputs | `p_direction`, spread%, theta_drag | probability | probability-shaped, **not calibrated** |
| Stock `ev_breakdown` | `lab/decision_engine.py:568` | **Live** | `p_direction`, reward/risk/cost | EV/share, expected R | $ / share |
| **Option `ev_per_contract` / `model_ev_per_contract`** | `canonical_bridge.py:159-191` | **Live — the authoritative option EV** | `p_direction` (via `p_trade`), premium, stock's own reward:risk ratio × 0.5 haircut, spread | EV | $ / contract |
| `options_shadow.evaluate_contract()`'s `ev_after`/`edge` | `lab/paper/options_shadow.py:126` (pre-existing, now clarified) | **Dead in production** — only its own 3 unit tests call it (`test_paper_trading.py`) | expected-move (IV-based) vs. break-even geometry, spread cost | a *different* EV, geometry-based | $ / contract |
| `options_desk.py` EV | `dashboard/options_desk.py:486-528` | **Live, separate pipeline** | Robinhood's `chance_of_profit_long` (external, broker-computed) | EV | $ |
| `scan_cohort.setup_type_performance()` | `dashboard/scan_cohort.py:565-616` | **Live, separate pipeline** | real forward stock returns, grouped | hit rate, avg return | %, **an empirical historical hit-rate — not a calibrated probability, not validated out-of-sample, not regime-stratified, stock-return-only (not option P&L), sample-caveated below ~12 by its own code**. Corrected wording: an earlier version of this doc called this "genuinely empirical" in a way that read as more statistically rigorous than it is — it reports what happened, it does not check a stated probability against reality, which is what "calibrated" actually means. |
| `decision_score` | `dashboard/decision_score.py` | **Live, separate pipeline** | 7 weighted components incl. a partially-empirical history term | -20..82 | normalized score, **not EV** |
| `lab/calibration.py` | whole file | **Live**, real Brier-score loop | logged predictions vs. closed trades | quality-only shrinkage factor | multiplier — **never touches `p_direction`/EV** |

Four genuinely different numbers have all been reachable under the word "EV" at
different points in the pipeline. This pass does not unify them (that's a bigger,
riskier change than relabeling — see open questions) — it makes the one that
determines option shadow records honest about what it is, and documents the rest so
the next person doesn't rediscover this from scratch.

## Root problem

`canonical_bridge.py` computed a real, correctly-formula'd expected value and called
it `ev_per_contract` — a name that reads as a measured fact. Its probability input,
`p_direction`, is an indicator-agreement heuristic with zero calibration evidence
(confirmed: `lab/calibration.py` only ever recalibrates the `quality` score, never
`p_direction`, and needs 20 matched closed-trade pairs it doesn't have — 0 today).
`options_shadow.record()` persisted that number into a column literally named
`ev_after_costs`, and `OPTIONS_SHADOW_REPORT.md` described it with a *different*
formula ("edge minus the cost of crossing the spread") that actually belongs to a
dead function, `evaluate_contract()`. Three independent points — the live code, the
persisted data, and the doc — each told a different, incomplete story about the same
number. That's the conflation.

## Changes made

| File | Change |
|---|---|
| `lab/paper/canonical_bridge.py` | Added `model_ev_per_contract` (alias of `ev_per_contract`, same value), `calibration_status: "UNCALIBRATED"`, `direction_model: "HEURISTIC"` to `option_display`. `ev_per_contract` kept unchanged — other code/tests key off that exact name. Docstring note added. |
| `lab/paper/options_shadow.py` | `evaluate_contract()`: docstring rewritten to state plainly it's not called by production, cite its real callers (its own tests), and explain what unique information it holds (IV-based expected-move/breakeven geometry) vs. what it is not (not a rival "the" EV). Added `execution_gate_state()` (the 6-gate machine), `model_ev_calibration_buckets()` (bucket plumbing, `INSUFFICIENT_SAMPLE`-safe), `format_shadow_disclosure()` (presentation helper). No DB schema change — `ev_after_costs` stays the persisted column name; the new fields are computed at read time or exist only as Python-level dict keys, per the "keep the persisted field, add a presentation alias" instruction. |
| `automation/paper_scheduler.py` | **Not changed.** An earlier version of this commit added `gates`/`calibration` to the `options` CLI branch; an adversarial pre-push review found this touched a frozen file unnecessarily (the same data is fully reachable via direct import — `python3 -c "from paper import options_shadow; print(options_shadow.execution_gate_state())"`) for something that could live outside the frozen file, so it was reverted before push. |
| `PAPER_GRADUATION_CHECKLIST.md` | §A item 6 corrected (wrong function, wrong numbers — was citing dead code). New §A.1: the six gates, explicit about what each does and does not authorize. "Current status" block de-hardcoded (was already stale — two ledgers now exist). |
| `OPTIONS_SHADOW_REPORT.md` | EV field description corrected to the real formula + calibration caveat. Liquidity-floor and affordability sections corrected to describe the live canonical path, with the dead `evaluate_contract()` path called out separately rather than conflated. Flagged (not fixed) that `expected_move`/`iv_context` are persisted as `NULL` unconditionally — a real, separate gap. |
| `CLAUDE.md` | Added a row pointing to this doc. |
| `tests/unit/test_evidence_graduation.py` | New — see Work Package H below. |

## User-facing behavior — before / after

**Before** (nothing distinguished a model opinion from a measured fact):
```
COP  EV after costs: +$14.20
```

**After** (`options_shadow.format_shadow_disclosure()`):
```
Model EV              +$14.20 / contract
Calibration           UNCALIBRATED
Direction model       HEURISTIC
Executable            NO — SHADOW ONLY
```

The underlying number is **identical** — this is a labeling and disclosure fix, not
a retune. The six-gate state and calibration buckets are reachable via
`options_shadow.execution_gate_state()` / `options_shadow.model_ev_calibration_buckets()`
directly (not wired into any CLI command in this pass — see the table above).

## Graduation state machine — final gates

See `PAPER_GRADUATION_CHECKLIST.md` §A.1 for the full table. Summary: Gates 1–2 are
data-sufficiency counts (today: 10 shadow records / need 50; 0 resolved / need 20).
**Gate 2 cannot currently be reached by waiting** — `options_shadow` has no production
mechanism that ever transitions a record's `outcome` away from `open` (confirmed: zero
`UPDATE options_shadow` statements anywhere in the repo; no equivalent of
`journal.update_excursions()` exists for this table). `resolved_outcomes` reads 0 today
and will keep reading 0 indefinitely until that resolver is built — this is a missing
mechanism, not a small sample. Gate 3 (predictive validity) never returns a verdict
below 20 resolved outcomes — `INSUFFICIENT_EVIDENCE`, not a forced pass/fail, and per
the above it cannot leave that state on its own either. Gate 4 (economic eligibility) is
answered per-contract by the existing canonical `account_fit` machinery, independent
of model quality. **Gate 5 (risk authorization) is `NOT_AUTHORIZED` today by explicit
policy — `STRATEGY_500_POLICY` has no option-specific fields, and that is a closed
decision, not a missing default.** Gate 6 (execution) is `SHADOW_ONLY`, structurally
enforced by `shadow_only=True` regardless of every other gate. No number of shadow
records moves Gate 5 or 6 — only an explicit policy decision does.

## Phase 2 (2026-09) — persisting evidence and building the missing resolver

Phase 1 (above) fixed the *label*. It left two structural gaps disclosed but
unfixed: `p_direction`/`theta_drag`/`p_trade`/the stock setup's own entry/stop/
target were computed and then discarded on every single evaluation, and
`options_shadow.outcome` had no production mechanism to ever leave `'open'`.
Both are fixed now.

**What's persisted going forward** (additive migration, schema v2 — 18 new
nullable columns on `options_shadow`, `lab/paper/db.py`'s `_MIGRATIONS[2]`):
`direction`, `p_direction`, `theta_drag`, `p_trade`, `dte_used_in_model`,
`underlying_price`, `stock_stop`, `stock_target`, `expected_move_pct`,
`move_to_be_pct`, `break_even_within_expected_move`, `contract_quality_score`,
`contract_quality_grade`, `quality_eligible`, `quality_rejection_reason`,
`risk_rejection_reason`, `strategy`, `option_outcome`. Every one is read
straight off `canonical_bridge.py`'s `option_display`/`canon_quality`/
`canon_option_fit` — the exact objects already used to compute `ev_opt` —
never recomputed. `strategy` is the one exception: it's a `workflow.py`-loop
concept, passed through the existing `options_shadow.record()` call with one
added keyword argument, not threaded into `canonical_bridge.py`.

**The outcome resolver** (`options_shadow.resolve_outcomes()`, wired into
`workflow.market_hours()` — the same lifecycle stage that already resolves
stock excursions via `journal.update_excursions()`) grades the **underlying
stock thesis only** — did the same stop/target the stock setup itself used
get hit? It deliberately does **not** grade the option contract's own P&L:
this repo has no historical option-chain pricing source anywhere (the chain
is only ever fetched live, for the current instant, via
`strategy_service._pick_option_idea()`), so estimating an option exit price
from the underlying's move would contaminate the exact evidence this exists
to collect honestly. Every resolved row gets `option_outcome = 'unavailable'`
— stated explicitly, never silently omitted. Same-bar stop/target collisions
reuse the **exact** policy `journal.update_excursions()`/`broker.
manage_open_positions()` already use — stop wins, "never flatter the record"
— not an invented "ambiguous" state; the project already had a real, tested
answer to this question. The resolver issues zero broker/order calls, touches
only `options_shadow`, and is idempotent (a resolved row is never re-graded).

**Gates 2/3 updated**: Gate 2 (outcome evidence) is now genuinely reachable —
it was not in Phase 1. Gate 3 (predictive validity) now has three descriptive
states — `INSUFFICIENT_EVIDENCE` (&lt;20 resolved), `DESCRIPTIVE_ONLY` (20-49),
`READY_FOR_VALIDATION` (≥50) — and **never** a fourth state claiming
calibration at any N. Gates 4-6 are unchanged and unmovable by any of this.

**Historical backfill**: checked what's recoverable for the 10 real rows
already collected (Sept 10/11/14) via `options_shadow.backfill_recoverable_
evidence()`, tested against a downloaded copy of the real cloud DB (never the
live one). Two durable, exact sources exist: the immutable `audit` table's own
`canonical_evaluated` event for that exact `shadow_id` (an exact match, not
approximate), and the `signals` table joined on `(symbol, session_date)` —
safe *only* because `workflow.py`'s per-symbol same-day dedup guarantees at
most one match; a row is backfilled only when exactly one match exists, never
guessed. Result: **all 10 rows** recovered `underlying_price`/`stock_stop`/
`stock_target`/`direction`/`strategy`/`contract_quality_score`/
`contract_quality_grade`/`quality_eligible`/`risk_rejection_reason`.
**`p_direction`/`theta_drag`/`p_trade`/`expected_move_pct`/`move_to_be_pct`/
`break_even_within_expected_move`/`dte_used_in_model` remain permanently
`NULL`** for these 10 rows — never persisted anywhere, not recoverable from
any durable source, and correctly not fabricated. The function is built and
tested (`dry_run=True` by default) but was **not** run against the real
cloud or local ledgers in this pass — see the cloud-safety note below.

## `evaluate_contract()` — conclusion: KEEP, reclassified

Traced its only callers (its own 3 unit tests), confirmed via grep + call-graph that
no production path (`workflow.py`, `canonical_bridge.py`, `broker.py`, any
`automation/` script) calls it. It is not dead weight, though: its expected-move
calculation uses the contract's own **implied volatility** (`underlying × iv ×
√(dte/365)`), while the live path uses an **ATR-based** (historical-volatility-proxy)
expected move and only ever produces a boolean (`break_even_within_expected_move`),
never the numeric edge this function computes. That's genuinely independent
information, not a duplicate.

Its own `ev_after`/`preference` output, however, **is** a second, competing EV/
preference model and should never be read as "the" Model EV — the docstring now
says so explicitly. **Recommendation: keep the function, keep its tests, keep it
un-wired.** A clean follow-up (not done in this pass, to keep this commit focused)
would extract just the IV-based geometry (`expected_move`, `breakeven_distance`,
maybe an `expected_move_coverage` ratio) into a named contract-analysis metric the
live path could optionally read — without carrying its competing EV number along.
That's a real "avoid a second grader" tradeoff worth a deliberate follow-up, not a
same-commit rewrite.

## Remaining genuinely undecidable policy questions

Not resolved here, on purpose:

1. **What risk allocation, if any, should options eventually receive in
   Strategy-500?** A standard 100-multiplier contract's max loss ($70–125+) is
   15–25x the current $5 per-trade cap. Raising the cap, adding an option-specific
   allocation, or deciding the account size simply cannot carry standard contracts
   are all live options — none chosen here.
2. ~~Should `p_direction`/`p_trade` be persisted per shadow record~~ — **done in
   Phase 2**, going forward. Still open: should the same be attempted for the
   ~10 already-collected historical rows for which it's provably impossible
   (see Phase 2's backfill section) — the answer here is no, honesty requires
   leaving them `NULL`, but it's worth stating plainly that those 10
   observations will never contribute to a `p_direction` calibration read.
3. Should the dashboard/options_desk.py's separate, Robinhood-broker-based EV
   pipeline eventually be reconciled with `canonical_bridge.py`'s, or do they
   legitimately serve different pipelines (Pipeline 2 vs. Pipeline 3)?
4. Should `evaluate_contract()`'s IV-based geometry be extracted into canonical/ as
   described above — and if so, does it become a new small canonical sub-layer, or
   a field addition to an existing one?
5. The `expected_move`/`iv_context` columns being unconditionally `NULL` in
   `options_shadow.record()` is a genuine, separate correctness gap, flagged but
   **not fixed here** — it's tangential to the semantic-conflation focus of this
   pass and deserves its own small, reviewable fix.
6. **Who runs `backfill_recoverable_evidence(dry_run=False)` against the real
   cloud ledger, and when?** It's proven safe against a downloaded copy of the
   real data, but this repo has no write path to the live GitHub Actions
   cache — applying it for real requires a deliberate manual step (download
   the artifact, run it, decide what to do with the result), not something
   this pass performs or automates.
7. Should `resolve_outcomes()` eventually distinguish `expired ITM` from
   `expired OTM` (both are currently just `outcome='expired'`,
   `option_outcome='unavailable'`)? Deferred — it would need the underlying's
   close on the expiry date, which the resolver doesn't currently snapshot,
   and adding it half-built risked exactly the "looks more sophisticated than
   the evidence supports" trap this whole effort exists to avoid.

## Cloud-ledger safety (Phase 2)

Confirmed before finalizing: no code in this phase runs against
`~/.tradingview_mcp_data` or the GitHub Actions cache. All migration/resolver/
backfill testing used either a fresh temporary SQLite file or an explicit
**copy** of a downloaded, read-only cloud-DB artifact backup in a local
scratch directory — never the original download, never the live path. HAL's
position was not read, referenced, or touched in Phase 2 at all.
