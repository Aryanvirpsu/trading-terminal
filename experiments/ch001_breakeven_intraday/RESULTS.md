# CH-001 breakeven-after-+1R — intraday replay results (2026-09-24)

Pre-registration `PREREG.md` (committed before this ran). Reproduce offline: `python ch001.py`
(inputs: `ohlc_5m.csv` = Yahoo 5-minute bars, `../profit_mode_baseline_v1/signals_enriched.json`).
Replay starts at the first 5-minute bar at/after each signal's `created_at`.

## Verdict: consistent with the hypothesis, NOT an ordering artifact — but the whole effect is ONE event.
Provisional. Not promotable.

| Pool | Champion ΣR | Breakeven ΣR | Δ | ambiguous |
|---|---|---|---|---|
| All 55 signals, 5 bps (23 resolved) | +4.77 | +7.08 | **+2.31** | 0 of 55 |
| All 55 signals, 15 bps | +3.73 | +6.01 | +2.28 | 0 |
| TRADEABLE (8; 4 resolved), 5 bps | −1.26 | +0.05 | +1.31 | 0 |
| Executed (2) | +1.13 | +1.13 | 0.00 | 0 |

- Max drawdown of cumulative R (all signals, resolution order): 13.94R → 11.62R.
- Leave-META-out: Champion −8.36R vs breakeven −6.04R (advantage unchanged, +2.32R).
- Event level (14 events): −5.21R vs −2.89R.
- **Winners prematurely cut: 0. Losses reduced: 2 — both COP** (signals 09-10 MONITOR and 09-11
  TRADEABLE), which are the *same* underlying trade.

## Why the ordering is trustworthy here
COP reached +1R on **09-15 15:25 UTC**, the breakeven stop would have exited on **09-16 13:40 UTC**
(−0.03R), and the original stop was hit on **09-17 13:30 UTC** (−1.35R). Trigger, breakeven exit and
stop are in different sessions, so no candle-internal ordering assumption is involved. No 5-minute bar
contained both the +1R trigger and the original stop, so **0% AMBIGUOUS** (the prior daily-bar
worry does not materialise in this sample). Residual sub-5-minute ambiguity cannot be excluded
(1-minute data covers only the last 8 days).

## What limits the conclusion
- **One reversal event.** 19 of 55 signals reached +1R. 17 kept going (targets hit, or still open above
  entry); only COP round-tripped. The +2.3R is that single event counted twice (two COP signals).
- **Open positions haven't been tested.** Still-open trades that later fall back to entry would be
  cut by the rule; that risk is not in this sample (a trending fortnight with two large upside gaps:
  META, AMD). The rule's usual cost — cutting winners in choppy markets — is unobserved, not disproved.
- ~13 independent events; only 2 resolved actual entries; hypothetical fills use the reference price.

## Decision
CH-001 stays **provisional**. Its intraday replay agrees with the daily-bar result, which removes the
ordering objection, but the evidence is one event. Next: the shadow logger (main, `69fb294`) now records
complete daily and 5-minute bars for every v1.1 finalist, so the same replay can run on genuinely new,
forward candidates. Promotion needs ≥30 independent events including choppy regimes, stable at 3× costs.
