# Ledger reconcile incident — 2026-10-16 (`ledger_inconsistent`)

## What was observed
From 2026-10-16 23:51 UTC the cloud runtime's health check returned `UNHEALTHY: ledger_inconsistent` and kept doing so through at
least the 2026-10-19 close (GitHub runs 38983580742 and 39348702596). The scheduler kept running (0 discovery or provider
failures). Because the backup job in `ubuntu-ops.yml` depended on a green probe, no off-host backup was taken after 2026-10-15.

## Mechanism (reproduced against the deployed commit `b236d54`)
`broker.reconcile()` rebuilds cash from **fill rows** and compares it with `risk.account_state()`, which derives cash from the
**positions table**. A fill row with no position behind it makes them disagree for ever (fill rows are append-only).

1. The tracker feeds exit logic the **current daily bar**, so `high` is a running session high.
2. `manage_open_positions` triggers a target exit on `high >= target`; a LIMIT sell fills only on strict trade-through
   (`high > target`, `PAPER_LIMIT_TRADE_THROUGH=True`).
3. While the session high sat exactly at the target, every 60 s tracker cycle queued another full-size sell order.
4. When the high cleared the target, all of them filled in one pass: the first closed the position; each of the rest inserted a
   fill row, found no position, and logged `exit_without_position`.

Replay on a copy of the verified 2026-10-15 ledger (13 cycles at the target, then one above it): 13 orders, 12 phantom fills,
delta **+$1,600.56**. On 2026-10-16 EOG's public minute bars show its running high at exactly the $157.60 target for 13 minutes
(14:48–15:00 ET), then above it at 15:01. **That the host actually did this is not confirmed**: it needs the host's 2026-10-16
`orders` / `fills` / `audit` rows (signature below).

## What changed
| Change | Where |
|---|---|
| An exit order with no open position is cancelled and audited; no fill row is written | `lab/paper/broker.py` `process_order` |
| One working exit order per symbol and intent | `lab/paper/broker.py` `manage_open_positions` |
| A fractional order can no longer be filled as a whole share | `lab/paper/fills.py` `_partial` |
| A partial exit's realized P&L is carried into the close and counted in cash | `lab/paper/broker.py`, `lab/paper/risk.py` |
| `reconcile()` leaves **audited** phantom fills out of the rebuild and lists them | `lab/paper/broker.py` `phantom_fills`, `reconcile` |
| `health()` returns the reconcile amounts; historical phantoms are `degraded: phantom_fills_excluded` | `lab/paper/runtime.py` |
| The off-host backup runs after a failed probe; `health-log` is printed after a failed health step | `.github/workflows/ubuntu-ops.yml` |

No gate, threshold, sizing rule, trigger, fill price or slippage changed on the normal path (nothing becomes more favourable).
`ROBINHOOD_TRADING_ENABLED=false` and `BROKER_PROVIDER=none` are untouched.

## The one control-adjacent change, and its limits
Fixing `process_order` stops *new* phantom fills but cannot remove existing ones, so a damaged ledger would stay red. `reconcile()`
therefore ignores a fill only when **all** hold: its order is an exit (`intent != 'entry'`), and the ledger itself audited
`exit_without_position` for that exact order. Anything else that disagrees with the positions table (an entry fill with a stray
audit event, a missing position effect with no audit event) still fails closed with its exact delta. The excluded fills are
listed by `reconcile()` and `health()` stays degraded until someone reviews them. No row is modified. If this one change is
unwanted, drop the commit "Reconcile: recognise audited phantom fills" and the rest stands on its own.

## Verification
* New tests: `test_exit_without_position_no_phantom_fill.py`, `test_partial_fill_accounting.py`,
  `test_reconcile_phantom_fills.py`, `test_ops_workflow_read_only.py`. The first two fail on the unfixed tree.
* End to end: damage a copy of the real 2026-10-15 ledger with the unfixed deployed code, then open it with this branch: the
  deployed `reconcile()` fails (+$1,600.56); the fixed code reconciles, lists the 12 phantom fills and changes no existing row; a
  replay from scratch with the fixed code records exactly 1 order and 1 fill.

## Confirming the cause on the host (read-only)
`acceptance-status 2026-10-16` (or the host backup `20261016T202501Z`) should show: two or more EOG `SELL` `LIMIT`
`exit_target` orders created 14:48–15:00 ET and all `filled` about 15:01 ET at 157.6 × 0.846323; `exit_without_position` audit
rows equal to orders minus one; and `cash_from_fills − cash_from_state = (orders − 1) × $133.38`. If the host shows something
else, `reconcile()` will keep failing closed after this change, which is the intended behaviour.

## Deploying
Not done by this change. A deploy uses the existing `deploy-ubuntu.yml` path (manual dispatch from `main`; refused near the
market window; verified backup first; automatic rollback). If the hypothesis was right, the next status read should then show
`healthy: true` with `degraded: ["phantom_fills_excluded"]`.
