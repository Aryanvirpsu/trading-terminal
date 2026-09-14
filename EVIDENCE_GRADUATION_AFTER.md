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
2. Should `p_direction`/`p_trade` themselves ever be persisted per shadow record
   (needed for a real "p_direction bucket" calibration table, per the original
   request) — this needs an additive DB migration not performed in this pass.
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
