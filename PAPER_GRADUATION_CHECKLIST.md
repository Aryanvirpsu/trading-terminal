# PAPER_GRADUATION_CHECKLIST.md

Two separate graduations. Neither is a matter of opinion — each is a list of checks
with evidence attached.

---

## A. Options: shadow mode → paper ledger

Options are currently **recorded only**. Nothing options-related has ever been written
to the paper ledger, and `options_shadow.summary()["in_ledger"]` returns `False`.

| # | Check | How it is measured | Status |
|---|---|---|---|
| 1 | Sample size | ≥ 50 shadow records | `graduation_readiness()` |
| 2 | Chain validity | ≥ 50 % of records had full microstructure (bid, ask, spread, OI, volume, DTE, delta) | `gradeable_rate` |
| 3 | Resolved outcomes | ≥ 20 shadow records reached a terminal outcome | `resolved_outcomes` |
| 4 | Stock launch stable | stock P&L reconciles **and** ≥ 50 resolved stock trades | `stock_launch_stable` |
| 5 | Fill realism | assumed fill is the **ask**, never the midpoint | enforced in `conservative_fill()` |
| 6 | Liquidity floor | spread ≤ 10 %, OI ≥ 250, volume ≥ 25 | enforced in `evaluate_contract()` |
| 7 | Risk model | option max-loss (premium) sized within the same per-trade risk budget | to be added at graduation |

`graduation_readiness()["ready"]` is `False` today, and will stay `False` until every
box is ticked with real data. **"No valid option" is an acceptable result** and is
expected to be the common one.

---

## B. Paper → anything further

This checklist deliberately does **not** end in "go live". It ends in "the evidence
justifies the next conversation".

### Hard prerequisites

| # | Requirement | Threshold |
|---|---|---|
| 1 | Resolved paper trades | **≥ 50** with complete evidence |
| 2 | Data-integrity violations | **zero** critical (reconciliation must pass every session) |
| 3 | Fill realism | no midpoint fills; gap fills observed and recorded |
| 4 | Risk controls | every limit demonstrably fired at least once in testing |
| 5 | Stale-data leaks | **zero** — no order ever created from display-only data |
| 6 | Report determinism | the same DB state regenerates a byte-identical report |

### Evidence that the engine has an edge

| # | Question | Where to read it | Passing looks like |
|---|---|---|---|
| 7 | Is expectancy positive after realistic costs? | `performance().overall.expectancy` | > 0 across ≥ 50 trades |
| 8 | Does it beat SPY over the same window? | `performance().benchmarks.excess_vs_spy_pct` | > 0 |
| 9 | Do the gates help or block winners? | `blocked_winners.blocked_win_rate` vs TRADEABLE win rate | blocked win rate **below** TRADEABLE win rate |
| 10 | Is quality calibrated? | `calibration.quality_buckets` | hit rate rises with quality bucket |
| 11 | Does freshness matter? | `calibration.freshness_buckets` | `fresh` ≥ `ageing` |
| 12 | Is it one lucky strategy or three? | `performance().by_strategy` | no single strategy carrying everything |
| 13 | Is it one lucky sector? | `performance().by_sector` | not concentrated in one sector |
| 14 | Drawdown tolerable? | `performance().max_drawdown_pct` | within the configured limit |

### Explicit failure conditions

Any of these means **stop and reconsider**, not "tune until it passes":

- Expectancy negative across ≥ 50 trades.
- `blocked_win_rate` ≥ TRADEABLE win rate — the gates are filtering edge, not noise.
- Calibration inverted (low-quality signals outperform high-quality ones).
- Reconciliation ever fails and the cause is not found.
- Returns explained entirely by one sector's beta.

### What graduation does *not* mean

Passing this list does not authorise live trading. `ROBINHOOD_TRADING_ENABLED=false`
and `BROKER_PROVIDER=none` stay as they are. Live execution is a separate decision
with separate safety work, deliberately out of scope.

---

## Current status

```
resolved paper trades        0 / 50
options shadow records       0 / 50
reconciliation               passing
stale-data leaks             0 (enforced + unit-tested)
critical integrity issues    0
```

Nothing on either list can be assessed yet. **Run the platform.**
