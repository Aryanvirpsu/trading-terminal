# PAPER_PERFORMANCE.md

Cumulative paper performance. Regenerate with:

```bash
python automation/paper_scheduler.py performance
```

**Status: 0 resolved trades. Every number below is a schema, not a result.** The
milestone is 50 resolved trades; until then `performance()` returns
`"warning": "fewer than 50 resolved trades — treat every number here as noise"`.

## Metrics tracked (by strategy, sector and regime)

| Metric | Source |
|---|---|
| Signal count / trade count | `signal_counts`, `resolved_trades` |
| Win rate | `overall.win_rate` |
| Average winner / loser | `overall.avg_win`, `overall.avg_loss` |
| Expectancy | `overall.expectancy` (mean P&L per trade) |
| Profit factor | `overall.profit_factor` (gross win ÷ gross loss) |
| Max drawdown | `max_drawdown_pct` (from the stored equity curve) |
| MFE / MAE | `excursions` |
| Entry / stop slippage | `slippage.entry_avg`, `slippage.exit_avg`, `gap_fills`, `partial_fills` |
| Confidence calibration | `calibration.quality_buckets` |
| Quality-bucket performance | `calibration.quality_buckets` (10-point bands) |
| Freshness-bucket performance | `calibration.freshness_buckets` |
| TRADEABLE vs MONITOR vs REJECT outcomes | `gate_outcomes` |
| Comparison vs SPY | `benchmarks.excess_vs_spy_pct` |
| Comparison vs momentum baseline | `benchmarks` (naive momentum, same window) |

Breakdowns: `by_strategy` and `by_sector`, each with the full `_stats` block.

## The three questions that actually matter

**1. Is there an edge after realistic costs?**
`overall.expectancy > 0` across ≥ 50 trades, with fills that never used the midpoint.
Read `slippage` alongside it — if expectancy is smaller than average slippage, there is
no edge, only a simulation artefact.

**2. Do the gates help, or do they block winners?**
`gate_outcomes` gives win rate by action bucket; `blocked_winners.blocked_win_rate` is
the cost of the gates.

| Reading | Interpretation |
|---|---|
| blocked win rate **≪** TRADEABLE win rate | gates are filtering noise — working as intended |
| blocked win rate **≈** TRADEABLE win rate | gates are filtering at random — they add cost, not safety |
| blocked win rate **>** TRADEABLE win rate | gates are actively removing edge — the strongest possible signal to loosen them |

**3. Is quality calibrated?**
`calibration.quality_buckets` should show hit rate rising with the quality band. If the
60-69 bucket beats the 80-89 bucket over a real sample, the quality score is noise and
the threshold is arbitrary.

## Deliberately not optimised for

**Win rate alone.** A 90 % win rate with a −3R tail is worse than a 40 % win rate with
+3R winners. Expectancy and profit factor are the primary reads; win rate is context.

## Benchmarks

Strategy return is compared against **SPY buy-and-hold over the identical window**. In
a rising market a positive P&L means nothing on its own — `excess_vs_spy_pct` is the
number that carries information.

## Honest caveats

- Fills are simulated. They are *conservative* (ask/bid + slippage, gap-aware,
  partial-aware), but they are still a model of a market, not the market.
- Daily bars drive stop/target detection, so intrabar path is approximated. When a bar
  touches both the stop and the target, the **stop is assumed first**.
- 50 trades is enough to detect a *large* effect, not a small one. It is a checkpoint
  for continuing, not proof of an edge.
