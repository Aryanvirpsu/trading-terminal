# CH-001 — Breakeven stop after +1R, intraday replay (pre-registration)

Registered 2026-09-24 BEFORE the intraday result was computed. Status: **provisional** (baseline daily-bar
result: +2.3R on 22 same-set outcomes; daily bars cannot order events inside a day).

## Hypothesis
Moving the stop to entry once price reaches +1R reduces losses (trades that reach +1R and then reverse)
by more than it cuts winners, net of costs.

## Variable changed (only this)
Exit rule: after a bar whose HIGH ≥ entry + 1R (R = entry − original stop), the stop becomes the entry
price, effective from the NEXT bar. Everything else identical: population, entry (reference price at the
signal), original stop/target, costs (entry +5 bps; stop/BE exits −5 bps, −25 bps on an adverse open gap;
target = limit; fees $0), 15 bps sensitivity.

## Data
Yahoo 5-minute bars (finest available for the whole window; 1-minute only covers the last 8 days),
17 symbols, 2026-09-10..09-24. Replay starts at the first 5-minute bar that begins AT OR AFTER the signal's
`created_at` (never from the day's open, never the bar containing the signal). Population: all 55 ledger
signals (the exit rule applies to any position); sub-populations: TRADEABLE (8) and actually executed (2).

## Ordering rule (no favourable assumptions)
Within one 5-minute bar the order of high and low is unknown. Classified **AMBIGUOUS** (never resolved
favourably) when a single bar contains:
- the +1R trigger AND the original stop (before the stop has moved), or
- the moved stop (entry) AND the target, or
- the original stop AND the target (Champion convention stays stop-first, flagged).
Headline comparison uses unambiguous trades only; ambiguous ones are reported with best/worst bounds
and as a percentage of trades.

## Metrics
Net ΣR, expectancy (mean R), max drawdown of cumulative R (in resolution order), winners prematurely cut
(Champion target-hit but variant exited at breakeven), losses reduced (Champion stop, variant exited at
breakeven), % ambiguous, event-level (symbol, day) dedupe, leave-META-out.

## Criteria
- **Supported (still provisional):** variant net R > Champion on unambiguous trades at 5 and 15 bps,
  survives leave-META-out, AND ambiguous share < 20% or the worst-case bound still ≥ Champion.
- **Not supported:** advantage disappears with intraday ordering or only exists in the ambiguous set.
- Cannot promote from ~13 independent events; promotion needs forward v1.1 shadow evidence (≥30 events).
