# CH-002 — Capacity-aware slot ranking (pre-registration)

Registered 2026-09-24, BEFORE any CH-002 result was computed. Status: **provisional / exploratory**.
Baseline: `docs/PROFIT_MODE_BASELINE_v1.md` (Champion v1.1 = cfg-a0eede144e + P0 fixes, `b0ec71d`).

## Hypothesis
With a $500 cash account (3 open positions, 1 per sector, 2 entries/day, $125 cap) AVDI often has more
eligible candidates than slots, so *which* candidates receive capital may matter more than whether a
candidate clears the conviction gate. A ranking rule that uses only decision-time information may earn
more portfolio-level net R than the existing ordering.

## Variable changed (only this)
The ORDER in which same-day eligible candidates are offered to the fixed capacity rules.
All held fixed: eligibility gates, 3 open / 2 per day / 1 per sector, $125 sizing, $5 risk, fills,
5 bps slippage (25 bps gap), stops/targets, exits, candidate population.

## Arms (decision-time features only; no outcome, MFE/MAE or hindsight field)
- **CHAMPION** — existing order: `scanner_rank` ascending (finalist order the workflow iterates).
- **CH-002A** — highest conviction (`quality` desc).
- **CH-002B** — highest `expected_r` (engine's own EV in R), tie → quality.
- **CH-002C** — composite = (quality/100) × planned RR ((target−entry)/(entry−stop)) × execution score
  (`liquidity` gate value = execution confidence, 0–1). Multiplicative, no fitted weights.
- **RANDOM** — 2000 random orderings (yardstick: does ANY policy beat random ranking?).
Ties broken by symbol (deterministic).

## Data / period
Ledger `paper-ledger-db-23`, 2026-09-10..09-24, 55 signals, 5 finalists/day, 17 symbols; bar-by-bar
replay from the session AFTER the signal (see baseline). Costs: 5 bps (15 bps sensitivity).
Pools: **P1** = Champion-eligible (TRADEABLE) — the primary test. **P2** = TRADEABLE + MONITOR —
a *ranking-power probe only* (more competition); NOT a conviction-threshold test and never a
recommendation to relax the gate.

## Metrics
Portfolio-level net R under the capacity simulator: realized ΣR (resolved by 09-24 close) and total ΣR
(= realized + mark-to-market of still-open positions at the 09-24 close), plus $ at fixed sizing;
number of "choice days" (days where ≥2 candidates competed for a constrained slot); trades that differ
from Champion; leave-META-out and leave-one-event-out sensitivity; percentile vs RANDOM.

## Success / failure criteria
- **Directionally supported:** total ΣR > Champion AND > RANDOM mean at 5 and 15 bps, in P1 and P2,
  and the advantage survives leave-META-out.
- **Not supported:** within RANDOM's spread, or advantage rests on a single event.
- **Cannot promote from this data.** Promotion needs ≥30 independent choice-days with real competition,
  out-of-sample (forward/shadow), stable at 3× costs, no correctness violations.
- **Kill:** no better than RANDOM after ≥30 choice-days.

## Known limits (stated in advance)
Choice days in P1 are expected to be very few (~2 of 11), MFE/MAE from daily bars, hypothetical fills use
the reference price (spread not modelled), ~13 independent events, META is one correlated event.
Result will be read as *directional*, not evidence of profitability.
