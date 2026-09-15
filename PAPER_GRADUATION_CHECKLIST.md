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
| 3 | Resolved outcomes | ≥ 20 shadow records reached a terminal outcome | `resolved_outcomes` — **resolvable as of Evidence & Graduation v1.2 phase 2**: `options_shadow.resolve_outcomes()`, run daily from `workflow.market_hours()`, grades the underlying stock thesis (not the option contract's own P&L — no historical option-chain pricing source exists in this repo). The 10 rows collected before phase 2 have no `stock_stop`/`stock_target` and can never resolve unless backfilled (see `EVIDENCE_GRADUATION_AFTER.md`); rows recorded after phase 2 resolve normally over time |
| 4 | Stock launch stable | stock P&L reconciles **and** ≥ 50 resolved stock trades | `stock_launch_stable` |
| 5 | Fill realism | assumed fill is the **ask**, never the midpoint | enforced in `conservative_fill()` |
| 6 | Liquidity floor | spread ≤ 12 %, OI ≥ 250, session-open volume ≥ 10 | enforced in `canonical.contract_quality.evaluate_contract_quality()`'s hard-fail checks (`ContractQualityPolicy`) — **corrected 2026-09 (Evidence & Graduation v1.2)**: this row previously cited `options_shadow.evaluate_contract()`, which the live pipeline never calls, and stated stale numbers (10%/25) that never matched the real policy |
| 7 | Risk model | option max-loss (premium) sized within an explicit, option-specific risk budget | **not built** — see §A.1 below; this is a closed policy decision, not a pending default |

`graduation_readiness()["ready"]` being `True` means checks 1–4 have real data behind
them — it does **not** mean options may execute. See §A.1: reaching every row above
still leaves execution `NOT_AUTHORIZED` by explicit, independent policy.

### A.1 — Six gates, not one checklist (Evidence & Graduation v1.2)

A flat pass/fail list invites reading "enough samples" as "cleared for trading." It
isn't. `lab/paper/options_shadow.py::execution_gate_state()` makes the six actual
questions, and what each one does and does not authorize, explicit and separately
inspectable — call it directly (`python3 -c "from paper import options_shadow;
print(options_shadow.execution_gate_state())"`) rather than reading
`graduation_readiness()["ready"]` as a verdict:

| Gate | Question | Authorizes |
|---|---|---|
| 1. Data collection | Enough observations to evaluate the model? (rows 1–2 above) | Nothing |
| 2. Outcome evidence | Enough resolved outcomes to compare model vs. reality? (row 3) | Nothing — grades the underlying stock thesis only; `option_outcome` is always `'unavailable'` |
| 3. Predictive validity | Do the model's opinions actually track outcomes? | Nothing — ever, automatically. Informs a human decision only. Three states: `INSUFFICIENT_EVIDENCE` (&lt;20 resolved), `DESCRIPTIVE_ONLY` (20-49), `READY_FOR_VALIDATION` (≥50) — never `CALIBRATED` at any N |
| 4. Economic eligibility | Can this account afford the contract under policy? | Nothing on its own — independent of model quality by construction (`canonical.account_fit.option_account_fit`) |
| 5. Risk authorization | Does an option-specific execution policy exist? | Nothing — `STRATEGY_500_POLICY`'s option fields are explicitly `None`. **`NOT_AUTHORIZED` today, by design, regardless of sample size.** |
| 6. Execution | Is the trade actually routed? | `shadow_only=True` is hard-coded in `canonical_bridge.py` — structurally blocked, not merely unauthorized |

No amount of Gate 1/2/3 evidence opens Gate 5 — that requires an explicit,
separate policy decision (see `EVIDENCE_GRADUATION_AFTER.md`'s open questions).

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

Two independent ledgers exist (Case 1 cloud / Case 2 local — see
`DUAL_LEDGER` note in project memory); each has its own counts. Check live rather
than trusting a number written here:

```
python automation/paper_scheduler.py status     # resolved trades, this ledger
python automation/paper_scheduler.py options     # shadow records + graduation_readiness()
python3 -c "from paper import options_shadow; print(options_shadow.execution_gate_state())"  # the 6 gates
```

As of the last check (2026-09-14, cloud ledger): 1 resolved-*paper-trade*-eligible
position open (HAL — not yet resolved, does not count until it closes), 10 options
shadow records, 0 resolved shadow outcomes. Local ledger: 0 and 0. Both numbers are
already stale by the time you read this — that's expected of a live system; run the
commands above instead of trusting this block.
