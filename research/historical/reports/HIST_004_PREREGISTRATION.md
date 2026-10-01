# HIST-004 — capacity-aware slot ranking at Historical Lab scale (pre-registration)

Registered 2026-09-30, BEFORE the `hist001_full_2024_2026_corrected` replay's choice-event data has been
reviewed for this purpose. Status: **provisional / exploratory**, same status class as
`experiments/ch002_slot_ranking/PREREG.md`, which this experiment extends.

**Amendment 2 (2026-10-01, before any HIST-004 result has been computed):** two changes, both made before
looking at any ranking outcome.

1. **Baseline switch**, same rationale and same baseline as HIST-002's Amendment 1: the capacity-aware
   ranking test now runs against `exp_dd_001_no_drawdown_v2_complete` (EXP-DD-001's uncapped, complete
   2024-01-01..2026-03-10 discovery run — 108 trades, +$41.71, max drawdown $125.72), not the $50-capped
   control, which hides most of the window. **HIST-004 gets the exact same number of entry slots Champion
   actually used in this baseline — this experiment reorders WHICH candidate fills an already-used slot, it
   never adds slots.** That is HIST-003's question, not this one.
2. **Arm definitions corrected to what `decision_capture` actually stores.** Checked directly against
   `baseline.py`'s `_recording_evaluate`/`_recording_evaluate_canonical` capture code before writing any
   ranking logic: `expected_r` and a liquidity/execution-confidence score (both referenced in this file's
   original §Arms below) are **not captured anywhere** in `decision_capture` — only `price`, `stop`,
   `target`, `quality` (`confidence_quality`), `sector`, `risk_budget`, `data_quality`, and
   `stock_executable` are. The original arm list could not have been computed as written; rather than
   silently computing something different while keeping the old arm names, the arms are corrected here,
   before any ranking result exists:
   - **CHAMPION** — Champion's real historical pick (ground truth, unchanged).
   - **HIST-004A** — highest `quality` (`confidence_quality`) desc, tie → symbol. Unchanged in spirit from
     the original CH-002A/HIST-004A.
   - **HIST-004B** — highest planned reward:risk ratio, `(target − price) / (price − stop)`, computed purely
     from the three captured price levels (replaces the uncapturable `expected_r`). Tie → quality.
   - **HIST-004C** — composite = `quality × planned_RR` (replaces the original formula's
     execution-confidence term, which does not exist in the captured data; multiplicative, no fitted
     weights, same no-invented-formula discipline as the original).
   - **RANDOM** — computed as the exact expected value (the mean resolved outcome across all live candidates
     in the episode, weighted uniformly), mathematically equivalent to averaging over unlimited random
     permutations for a single-pick-from-N episode, rather than literally sampling 2000 permutations (no
     observable difference in result, avoids wasted computation).
   Every other element of this pre-registration — the choice-episode scope, the metrics, the success
   criteria, the "don't invent a clever formula after seeing winners" discipline — is unchanged.

## Why this experiment, and why now

`choice_events()` (`research/historical/hist001/analysis.py`) records full candidate detail every time two
or more TRADEABLE candidates compete in the same evaluation cycle for a scarce slot. Medium's 3-month window
found **zero** genuine choice events — `HIST_001_CHAMPION_BASELINE.md` §G calls this "itself a real, useful
finding for HIST-004 (the future ranking-policy study), not a defect," and explicitly states "HIST-004 will
need the Full 2024-2026 window's much larger trade count to gather real choice-event data." The (now-retired
for strategy purposes, but still valid for choice-event counting, since choice events are a function of
candidate competition, not of the AVGO cascade's downstream lockout) original Full run found **2** genuine
choice events (RTX-over-NEM, NEE-over-XOM), both correct in hindsight but far too few to draw a ranking
conclusion from alone. A live pilot of the same hypothesis already ran and is registered at
`experiments/ch002_slot_ranking/PREREG.md` (result: not supported / uninformative, data-starved — only ~2 of
11 days had real competition in that pilot's window). HIST-004 is that same hypothesis, same arms, run
against the corrected Full replay's own choice-event population instead.

## Hypothesis

With a $500 cash account (3 open positions, 1 per sector, capacity/sizing per `PAPER_500_ACCOUNT.md`), AVDI
sometimes has more eligible TRADEABLE candidates than open slots on a given cycle. A ranking rule using only
decision-time information (no outcome, no hindsight) may select the eventually-better-performing candidate
more often than Champion's existing `scanner_rank`-ascending order.

## Variable changed (only this)

The ORDER in which same-cycle eligible TRADEABLE candidates are offered to the fixed capacity rules, applied
as a pure counterfactual re-ranking of each genuine choice event the corrected HIST-001 Full replay actually
recorded. All else held fixed: eligibility gates, capacity caps, sizing, fills, costs, stops/targets, exits,
and — critically — the candidate POPULATION itself (this experiment never asks whether a different candidate
should have been eligible, only which of the ALREADY-eligible candidates should have been chosen).

## Arms (decision-time features only; no outcome, MFE/MAE, or hindsight field — identical to CH-002)

- **CHAMPION** — existing order: `scanner_rank` ascending (the real order the workflow iterates and the real
  order `choice_events()` already records as `selected`).
- **HIST-004A** — highest conviction (`quality` desc, from `decision_capture`).
- **HIST-004B** — highest `expected_r` (engine's own EV in R), tie → quality.
- **HIST-004C** — composite = (quality/100) × planned RR ((target−entry)/(entry−stop)) × execution score,
  identical formula to CH-002C. Multiplicative, no fitted weights.
- **RANDOM** — 2000 random orderings per choice event (yardstick: does any policy beat random ranking?).
Ties broken by symbol (deterministic).

## Data / population

Every genuine choice event `choice_events()` records for `hist001_full_2024_2026_corrected` (candidate_detail
already captures each competing candidate's decision-time quality/sector/price/stop/target — no re-decision,
no re-run of the replay). Given the small expected count (2 in the uncorrected Full run; the corrected run
may differ slightly since AVGO-cascade-era capacity dynamics changed, but is not expected to change the
COUNT of genuine choice events dramatically, since choice events depend on simultaneous candidate eligibility,
not on whether the account was locked out afterward — this expectation is stated here, before the corrected
count is known, precisely so it can be checked rather than assumed after the fact).

## Metrics

Per-choice-event: which candidate each ranking rule would have selected, and that candidate's REAL,
already-resolved realized R/$ outcome (read from the corrected replay's own positions table when the
selected candidate is the one Champion actually entered; hypothetically resolved via `resolve_hypothetical()`
— H6, the same machinery `capacity_opportunity_cost()` already uses — when it is not). Aggregate: total R
under each rule across all choice events, number of events where a rule's pick differs from Champion's,
percentile of each deterministic rule vs. the RANDOM distribution.

## Success / failure criteria

- **Directionally supported:** a deterministic rule's total R > Champion AND > RANDOM's mean, and the
  advantage does not rest on a single event (no single choice event accounts for the entire gap).
- **Not supported:** within RANDOM's spread, or the advantage rests on a single event.
- **Cannot promote from this data**, almost certainly, stated in advance: even the Full 2024-2026 window is
  expected to produce single-digit genuine choice events (2 in the uncorrected run) — nowhere near the ≥30
  independent choice-events this project's standing promotion rule requires (the same bar CH-002's own
  PREREG sets). This experiment's realistic purpose is to see whether the DIRECTION of any effect is at
  least consistent with CH-002's live pilot, not to reach a promotion decision.
- **Kill:** fewer than 2 genuine choice events in the corrected run (can't compute a ranking comparison of
  any kind), or no better than RANDOM.

## Known limits (stated in advance)

Expected to remain data-starved, exactly like CH-002 — this is disclosed now, not discovered after running,
precisely so a small-n "not supported" result is not later mistaken for a surprising negative finding. MFE
based hypothetical resolution inherits every limitation `capacity_opportunity_cost()`'s own docstring already
states (H6's reference-price fill assumption, no spread modeled for the hypothetical leg). Today's survivor-
ship-biased universe (§C of the Full report) applies here identically, since choice events are drawn from
that same universe.
