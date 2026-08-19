# OPTIONS_SHADOW_REPORT.md

Options are **recorded, never executed**. Nothing options-related has ever been written
to the paper ledger, and stock paper trading was not delayed for any of this.

Regenerate with:

```bash
python automation/paper_scheduler.py options
```

**Status: 0 shadow records.** The structure below is live; the data is not yet.

## What is recorded per contract

| Field | Note |
|---|---|
| underlying setup | the `signal_id` of the stock decision that triggered the look |
| contract, expiry, strike, type | as reported by the chain |
| bid, ask | raw quotes |
| **assumed fill** | the **ask** — we assume we pay up, never the midpoint |
| spread %, volume, open interest | liquidity evidence |
| Greeks (delta, gamma, theta, vega), IV | only scored when present |
| IV context, expected move | expected move = `price × IV × √(DTE/365)` |
| break-even | `strike ± assumed_fill` |
| max loss | premium × 100 (long option) |
| EV after costs | edge minus the cost of crossing the spread |
| preference | `prefer-stock` · `prefer-option` · `avoid-both` |
| later outcome | tracked forward like any other signal |

## Affordability comes first ($500 account)

Before any edge calculation: total premium = ask x 100 + fees must fit **both** the
$75 per-trade allocation and available buying power. Max loss on a long option is the
**full premium**. At $75 that means contracts priced around **<= $0.75** — a large
share of liquid near-the-money options are simply unreachable at this account size.

| Premium | Total | Affordable |
|---:|---:|---|
| $0.35 | $35.00 | yes |
| $0.80 | $80.00 | no - exceeds the $75 allocation |
| $1.50 | $150.00 | no - exceeds the $75 allocation |

Long single-leg only. Spreads, shorts, naked and multi-leg are **not simulated** - the
real account is not approved or funded for them.

## Scoring rules

A contract is scored **only** when bid, ask, open interest, volume, DTE and delta are
all present. Otherwise it is returned as `gradeable: false` with the exact missing
fields, and the preference falls back to `prefer-stock` (or `avoid-both` when the stock
itself does not qualify).

**"No valid option" is an acceptable result** and is expected to be the common one.
A weak or missing option never rejects the underlying stock.

Liquidity floor for a `prefer-option` verdict: spread ≤ 10 %, OI ≥ 250, volume ≥ 25.

## Why shadow mode

An option's simulated P&L is far more sensitive to fill assumptions than a stock's — a
2-cent optimism on a $1.05 contract is a ~2 % edge that does not exist. Rather than
contaminate the paper ledger while that is unproven, options accrue evidence
separately and enter the ledger only after `PAPER_GRADUATION_CHECKLIST.md` §A passes.

## Current readiness

```
sample_size          0 / 50 shadow records
gradeable_rate       n/a
resolved_outcomes    0 / 20
stock_launch_stable  false (0 / 50 resolved stock trades)
ready                FALSE
```

`options_shadow.summary()["in_ledger"]` is `False` and is asserted by
`test_options_never_enter_the_ledger`.
