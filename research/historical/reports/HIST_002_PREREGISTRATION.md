# HIST-002 — CH-001 full delta-R at Historical Lab scale (pre-registration)

Registered 2026-09-30, BEFORE the `hist001_full_2024_2026_corrected` replay's results have been reviewed for
this purpose (the replay itself is running for HIST-001's own baseline; this file is committed while it is
still in flight, so no HIST-002 analysis can have looked at its outcome yet). Status: **provisional /
exploratory**, same status class as `experiments/ch001_breakeven_intraday/PREREG.md`, which this experiment
extends.

**Amendment 1 (2026-10-01, before any HIST-002 result has been computed):** the baseline this experiment
runs against is changed from `hist001_full_2024_2026_corrected` (the $50-max_drawdown-capped Champion
control) to `exp_dd_001_no_drawdown_v2_complete` (EXP-DD-001's uncapped, complete 2024-01-01..2026-03-10
discovery run — 108 trades, ending equity $534.38, net P&L +$41.71, max drawdown $125.72/21.6%, lowest
equity $446.59, longest losing streak 20). Rationale, per explicit direction: the capped control's own
sustained lockout (tripped 2025-02-03, frozen through the rest of the window) hides more than half of the
strategy's real behavior from every downstream Challenger; a hard absorbing kill switch is a production
safety decision, not a discovery tool, and testing exit-management changes against a truncated sample would
under-count how often +1R is even reached. The $50 cap itself remains unchanged in production (Ubuntu) and
is not being challenged by this amendment -- this only changes which HISTORICAL sample HIST-002 is measured
against. Every other element of this pre-registration (hypothesis, variable changed, ambiguity discipline,
metrics, success criteria) is unchanged from the original text below.

## Why this experiment, and why now

`ch001_shadow()` (`research/historical/hist001/analysis.py`) has always reported, for every already-run
HIST-001 stage, which real evaluation-phase positions reached +1R per their own ledger MFE — but its own
`scope_limit` field has said since Medium: *"full CH-001 delta-R (Champion exit vs. breakeven-after-+1R
exit) requires a bar-by-bar outcomes.py resolution from the +1R timestamp forward — not computed in this
pass."* `HIST_001_CHAMPION_BASELINE.md` defers this explicitly to HIST-002 at both the Medium and Full
sections. A live pilot of the same hypothesis already ran and is registered at
`experiments/ch001_breakeven_intraday/PREREG.md` (Champion baseline +2.3R on 22 same-set outcomes, daily
bars only, ~13 independent events, 2026-09-10..09-24) — HIST-002 is that same hypothesis, same rule, same
ambiguity discipline, run instead against the Historical Lab's full 2024-2026 corrected evaluation window,
which has a far larger and more diverse trade population than a 13-day live pilot can offer.

## Hypothesis

Moving a position's stop to its entry price once price reaches +1R (R = entry − original stop) reduces
realized losses (trades that reach +1R and then reverse) by more than it cuts realized winners, net of the
same cost model Champion already trades under — measured across HIST-001's full corrected 2024-2026
evaluation-phase trade population, not the 13-day live pilot's population.

## Variable changed (only this; SHADOW ONLY, never applied to a real position)

Exit rule, evaluated as a pure counterfactual against the CORRECTED HIST-001 Full replay's own real, closed,
evaluation-phase positions: after the first 5-minute bar (fabhaus intraday resolution, the same source
HIST-001 itself replays against) whose HIGH ≥ entry + 1R, the stop becomes the entry price, effective from
the NEXT bar. Original stop/target, entry price, position sizing, fees, slippage/spread model, and every
Champion decision (which symbol, when, at what size) are all held EXACTLY as the corrected HIST-001 Full
replay produced them — this experiment only asks "what if this one already-open, already-real position's
exit had been managed differently after it reached +1R," never "what if Champion had entered differently."

## Population

Every evaluation-phase closed position from `hist001_full_2024_2026_corrected` whose own recorded MFE
reached ≥ 1R (`ch001_shadow()`'s existing `reached_plus_1r` list is the starting population — this
experiment resolves each of those positions' bar-by-bar path forward from its own +1R timestamp, which
`ch001_shadow()` explicitly does not do). Sub-populations reported separately: all reaching positions, and
the subset excluding AVGO (given AVGO's split-driven history in this dataset, reported both ways so no
single corporate-action-affected symbol can drive the headline number without that being visible).

## Ordering rule (no favourable assumptions — identical convention to CH-001)

Within one 5-minute bar the order of high and low is unknown. A trade is classified **AMBIGUOUS** (never
resolved favourably to the variant) when a single bar contains:
- the +1R trigger AND the original stop (before the stop has moved), or
- the moved stop (entry) AND the target, or
- the original stop AND the target (Champion's own stop-first convention is kept, and flagged here).
Headline comparison uses unambiguous trades only; ambiguous ones are reported with best/worst bounds and as
a percentage of the reaching-+1R population.

## Metrics

Net ΔR (variant total R − Champion's real realized total R, on the SAME resolved population only — no
partial-population comparison), expectancy (mean R) under each rule, max drawdown of cumulative R in
resolution order, count of winners prematurely cut (Champion reached target, variant exited at breakeven
first), count of losses reduced (Champion hit its real stop, variant exited at breakeven instead), %
ambiguous, leave-AVGO-out sensitivity, leave-any-single-symbol-out sensitivity (guards against one dominant
symbol driving the result, the same discipline the corrected Full report's own correctness-audit section
already applies to the headline P&L numbers).

## Success / failure criteria

- **Directionally supported:** variant net R > Champion's real net R on the unambiguous population, survives
  leave-AVGO-out AND leave-any-single-symbol-out, AND ambiguous share < 20% or the worst-case bound still ≥
  Champion.
- **Not supported:** advantage disappears under intraday ordering, depends on the ambiguous set, or does not
  survive removing any single symbol.
- **Cannot promote from this data alone**, regardless of direction: promotion of a Champion exit-rule change
  requires forward/live shadow evidence (≥30 independent events under `PAPER_500_ACCOUNT.md`'s real
  constraints), per this project's standing Champion/Challenger promotion rule — a historical-replay shadow
  result, however large the n, is evidence to justify running that forward shadow, not a substitute for it.
- **Kill:** no better than Champion (within noise) after the full corrected population is resolved.

## Known limits (stated in advance)

Fabhaus's 5-minute bars are the finest resolution available for the whole 2024-2026 window (same
intraday-ordering ambiguity CH-001 already discloses, at a different scale). MFE is read from the position's
own ledger MFE field (real, captured during the actual replay), not recomputed independently — a
double-check that the two agree is part of this experiment's own correctness pass, not assumed. This
experiment is a SHADOW analysis only; running it, or any result it produces, changes nothing about Champion
and authorizes no code change to `lab/paper/broker.py`'s real exit logic on its own.
