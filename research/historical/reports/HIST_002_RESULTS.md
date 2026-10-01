# HIST-002 — results

**STATUS: COMPLETE — NOT PROMOTED**

Pre-registration: `HIST_002_PREREGISTRATION.md` (Amendment 1, 2026-10-01: baseline switched to EXP-DD-001's
uncapped, complete discovery run before this result was computed). Implementation:
`research/historical/hist002/breakeven_resolver.py` (10 unit tests, all passing). Driver:
`research/historical/hist002/run_breakeven_challenger.py`. Raw detail:
`research/historical/hist002/results/hist002_breakeven_result.json`.

## Hypothesis

Moving a position's stop to its entry price once price reaches +1R reduces realized losses (trades that
reach +1R and then reverse) by more than it cuts realized winners, net of costs, measured across the
complete 2024-01-01..2026-03-10 EXP-DD-001 discovery baseline (108 trades, +$41.71, max drawdown $125.72).

## Population

108 closed, evaluation-phase positions from `exp_dd_001_no_drawdown_v2_complete`. 61 reached +1R per their
own ledger MFE and were resolved bar-by-bar under the variant rule; the other 47 never reached +1R and are
unaffected by construction (the breakeven rule never engages for them). **Zero ambiguous bars** across all
61 resolved positions — a fully clean sample, no same-bar-conflict resolution needed anywhere.

## Result

| Metric | Baseline (real Champion exits) | HIST-002 Variant (breakeven after +1R) |
|---|---|---|
| Net P&L | **+$41.71** | **+$20.44** |
| Max drawdown | **$125.72 (21.6% of peak)** | **$87.38 (16.0% of peak)** |
| R expectancy (recorded-risk subset, 74 trades) | −0.047R | −0.076R |
| Win rate | 40.7% | 29.6% |
| Longest losing streak | 20 | 14 |
| Worst trade | −$17.73 | −$17.73 (unchanged — never reached +1R) |

### By year

| Year | Baseline P&L | Variant P&L | Delta |
|---|---|---|---|
| 2024 (strongest year, 57.1% win rate) | +$71.24 | +$39.47 | **−$31.77** |
| 2025 (deterioration) | −$58.79 | −$44.09 | **+$14.70** (less bad) |
| 2026 (recovery) | +$29.26 | +$25.05 | −$4.21 |

## Interpretation

Breakeven-after-+1R delivers real, genuine downside protection during the strategy's deteriorating period
(2025): it cut that year's losses by $14.70 by preventing positions from reversing all the way from +1R back
to a full stop-out. But the same rule did comparable or greater damage during the strategy's *best* period
(2024), scratching winners at breakeven that would otherwise have run to full target — cutting that year's
profit nearly in half. The net effect across the complete path is a real drawdown reduction (30% lower) but
a larger profit reduction (51% lower), plus a worse R-expectancy and win rate.

Against the pre-registered primary criteria (net P&L, expectancy, max drawdown): 2 of 3 primary metrics got
worse. This is a genuine capital-preservation-vs-upside tradeoff, not a free improvement, and does not clear
the bar for promotion.

## Decision

**Champion change: NONE.** Breakeven-after-+1R is not promoted. Per direction, this result is not pushed
through a Stage B (production-risk-controls) re-run — it already failed the discovery-stage frontier, so the
additional compute is not justified.

**What this narrows for future work:** the problem is not simply "Champion holds winners too loosely."
Reflexively tightening exits trades away more good-regime upside than it recovers in bad-regime protection,
at least under this specific rule. The more promising lever, per the updated research order, is candidate
selection quality (HIST-004) — when AVDI has multiple genuinely eligible candidates competing for a scarce
slot, is it choosing the right one — rather than managing already-open positions more defensively.
