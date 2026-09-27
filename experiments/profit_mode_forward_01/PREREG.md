# PROFIT_MODE_FORWARD_01 — pre-registration

Registered 2026-09-27, using only data available through the 2026-09-25 close (the first, and so far only, real v1.1
market session). No strategy change is made by this analysis. Boundaries respected throughout:

* v1.0 evidence stays separate (never pooled with v1.1).
* The 2026-09-25 session is preserved exactly as executed (ledger untouched).
* DELL and META (2026-09-25) are valid v1.1 observations but are flagged `pre-risk-fix sizing` (realised stop loss
  $5.28/$5.30 vs the $5.00 budget) — see `PAPER_EVIDENCE_VERSIONS.md`. Trades from `main >= 322e325` are the
  corrected-sizing forward cohort.
* Evidence is counted by independent `event_id`, never by raw observation.

## Method
* Funnel and strategy/portfolio attribution: computed from `shadow_candidates`/`shadow_cycles` (the shadow logger),
  which records decision-time facts only.
* Blocked-event counterfactuals: replayed on Yahoo 5-minute bars from each event's own `scan_ts` forward (never from
  the day's open), same convention as CH-001/CH-002: executable entry = ask×(1+5 bps), stop fill = stop×(1−5 bps),
  stop checked before target on a bar that touches both, and marked AMBIGUOUS in that case.
* This is ONE session, 12 independent events, 2 trades, both still open at the data cutoff (2026-09-25 15:55 ET,
  the market closed for the weekend). Every counterfactual below is a snapshot, not a resolved outcome — none of it
  is evidence that a gate or a portfolio cap should change.
