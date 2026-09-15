# OPTIONS_SHADOW_REPORT.md

Options are **recorded, never executed**. Nothing options-related has ever been written
to the paper ledger, and stock paper trading was not delayed for any of this.

Regenerate with:

```bash
python automation/paper_scheduler.py options
```

**Status: check live** — `python automation/paper_scheduler.py options` (this doc no
longer freezes a sample-count number here; two independent ledgers exist, see
`DUAL_LEDGER` note in project memory, and any number written here goes stale
immediately).

> **Corrected 2026-09 (Evidence & Graduation v1.2):** several field descriptions
> below previously described `options_shadow.evaluate_contract()` — a standalone,
> unit-tested function that the live pipeline never actually calls. See
> `EVIDENCE_GRADUATION_AFTER.md` for the full audit. Corrections are marked inline.

## What is recorded per contract

| Field | Note |
|---|---|
| underlying setup | `signal_id` is currently always `NULL` — `record()` is called before the matching `signals` row is journaled, so this FK is structurally never populated (a known, pre-existing gap, not fixed in this pass). The stock setup itself IS captured directly: `underlying_price`/`stock_stop`/`stock_target`/`direction`/`strategy` (added Evidence & Graduation v1.2 phase 2, single-sourced from the exact values used to compute Model EV — never recomputed) |
| model inputs | `p_direction`, `theta_drag`, `p_trade`, `dte_used_in_model` (added phase 2) — the exact numbers that produced Model EV, persisted so a later "why this number" question doesn't require re-deriving anything. Only present on rows recorded after phase 2 shipped; earlier rows have these `NULL`, honestly, forever (nothing recoverable) |
| contract, expiry, strike, type | as reported by the chain |
| bid, ask | raw quotes |
| **assumed fill** | the **ask** — we assume we pay up, never the midpoint |
| spread %, volume, open interest | liquidity evidence |
| Greeks (delta, gamma, theta, vega), IV | only scored when present |
| IV context, expected move | **not currently populated** — `record()` writes these two columns as `NULL` unconditionally; a real value is computed but never persisted (a genuine gap, not yet fixed — see open questions) |
| break-even | `strike ± assumed_fill` |
| max loss | premium × 100 (long option) |
| **Model EV** (column: `ev_after_costs`) | **corrected**: NOT "edge minus spread cost" — the live value is `canonical_bridge.py`'s `p_direction`-based `model_ev_per_contract` (same `_expected_value(p_win, profit, loss, cost)` formula used everywhere in `decision_engine.py`, applied to the option premium). **This is a model opinion, not a measured expectation** — its probability input (`p_direction`) has no calibration evidence behind it. Treat a positive number as "the model currently favors this," never as "this contract has demonstrated a $X edge." `calibration_status` is `UNCALIBRATED` and `direction_model` is `HEURISTIC` for every record today; call `options_shadow.format_shadow_disclosure()` to render both alongside the number |
| preference | `prefer-stock` · `prefer-option` · `avoid-both` |
| **outcome** | as of phase 2, actually resolved daily by `options_shadow.resolve_outcomes()` (wired into `workflow.market_hours()`) — grades the **underlying stock thesis**: did the persisted `stock_stop`/`stock_target` get hit? `open` → `target_hit` / `stop_hit` / `expired`. Same-bar stop+target collisions resolve stop-first, reusing `journal.update_excursions()`'s exact policy, not an invented "ambiguous" state |
| **option_outcome** | always `'unavailable'` once `outcome` resolves — this repo has no historical option-chain pricing source, so the option contract's own P&L is never estimated from the underlying's move. Deliberately explicit rather than a silent `NULL` |

## Affordability comes first ($500 account)

**Live path**: `canonical.account_fit.option_account_fit()` checks the contract against
`STRATEGY_500_POLICY` — the same `max_loss_per_trade = $5.00` cap used for stock (no
option-specific policy fields exist; see `PAPER_GRADUATION_CHECKLIST.md` §A.1 gate 5).
A standard 100-multiplier long option's max loss ($70–125+ for a typical liquid
contract) is routinely 15–25x that cap. In the shadow data collected so far, this is
the single most common reason an otherwise-gradeable, even positive-Model-EV contract
never becomes eligible — `option_binding_constraint: "per_trade_risk"`, not a data or
quality problem. **This is not a bug and not something to loosen without an explicit
policy decision** — see the open questions in `EVIDENCE_GRADUATION_AFTER.md`.

*(The standalone `evaluate_contract()` function — not called by the live pipeline —
has its own, different affordability rule: total premium = ask × 100 + fees against a
configured `max_premium_per_trade` allocation. That number is real for that function's
own unit tests, but it is not what determines a live shadow record's eligibility.)*

Long single-leg only. Spreads, shorts, naked and multi-leg are **not simulated** - the
real account is not approved or funded for them.

## Scoring rules

A contract is scored **only** when bid, ask, open interest, volume, DTE and delta are
all present. Otherwise it is returned as `gradeable: false` with the exact missing
fields, and the preference falls back to `prefer-stock` (or `avoid-both` when the stock
itself does not qualify).

**"No valid option" is an acceptable result** and is expected to be the common one.
A weak or missing option never rejects the underlying stock.

Liquidity floor (live path — `canonical.contract_quality.ContractQualityPolicy`):
spread ≤ 12 %, OI ≥ 250 for "adequate+" (≥ 50 is the illiquid hard-fail floor, 50–250
is graded "thin"), session-open volume ≥ 10. *(Corrected: previously stated as ≤10 %
/ ≥250 / ≥25, which described the standalone `evaluate_contract()` function's own
`OptionsConfig` thresholds, not the live `ContractQualityPolicy` the canonical
pipeline actually enforces.)*

## Why shadow mode

An option's simulated P&L is far more sensitive to fill assumptions than a stock's — a
2-cent optimism on a $1.05 contract is a ~2 % edge that does not exist. Rather than
contaminate the paper ledger while that is unproven, options accrue evidence
separately and enter the ledger only after `PAPER_GRADUATION_CHECKLIST.md` §A passes.

## Current readiness

`graduation_readiness()["ready"]` becoming `True` means checks 1–4 have real data —
it does **not** mean options may execute. Run `python automation/paper_scheduler.py
options` for live numbers and the full six-gate state
(`options_shadow.execution_gate_state()`, see `PAPER_GRADUATION_CHECKLIST.md` §A.1).
Gates 5 (risk authorization) and 6 (execution) stay `NOT_AUTHORIZED` /
`SHADOW_ONLY` regardless of how Gates 1–4 read — that's a closed policy decision,
not a pending default.

`options_shadow.summary()["in_ledger"]` is `False` and is asserted by
`test_options_never_enter_the_ledger`.

## Outcome resolution (Evidence & Graduation v1.2 phase 2)

Before phase 2, `outcome` was written once as `'open'` and never touched again —
confirmed zero `UPDATE options_shadow` statements anywhere in the repo, no
equivalent of `journal.update_excursions()` for this table. That's fixed:
`options_shadow.resolve_outcomes()` runs daily from the same lifecycle stage that
already resolves stock excursions, grading the underlying stock thesis (never the
option contract's own P&L — see `option_outcome` above). The 10 rows collected
before this shipped have no `stock_stop`/`stock_target`/`underlying_price` and can
never resolve unless backfilled (`options_shadow.backfill_recoverable_evidence()` —
built, tested against a real cloud-DB copy, not applied to any live ledger in this
pass; see `EVIDENCE_GRADUATION_AFTER.md`).
