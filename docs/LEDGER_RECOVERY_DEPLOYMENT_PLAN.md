# Controlled deployment plan — ledger recovery (PR #11)

**This plan has not been executed. Nothing is merged or deployed without the owner's explicit approval.** It changes code only: no
configuration, capital limit, risk limit, schema, broker flag or strategy parameter. `ROBINHOOD_TRADING_ENABLED=false` and
`BROKER_PROVIDER=none` stay as they are. CH-004 stays paused.

## 0. Why it matters (profit view)
Until a fix is live the Champion cannot open paper trades: `entries_disabled: ledger not reconciled` has been logged on every cycle since
Oct 19 (66 events, 3 sessions, 0 entries). That is lost evidence and lost opportunity, not lost money. Each further session adds to it.
The risk of deploying is bounded (code-only, automatic rollback, nothing deleted); the cost of waiting is certain.

## 1. Approvals needed (the owner's decisions)
1. Take PR #11 out of draft and **approve the merge** (the PR has no merge until then).
2. **Approve a deploy window** (below) and, if the `production` GitHub environment requires reviewers, approve that gate.
3. Accept that `historical_phantom_fills_unresolved` stays **degraded for ever** until a separate decision about the 14 historical rows.
4. Optionally drop commit 3 (the only control-adjacent change). Without it the ledger stays red even with the fix deployed.

## 2. Pre-flight (read-only; the day before or the same evening)
| Step | How | Pass condition |
|---|---|---|
| Code | PR CI green on the final commit (tests, Docker image build, CodeQL); `TZ=UTC python deploy/ci_gate.py tests/unit` | exit 0, **5/5 known failures, 0 unexpected** |
| Diff scope | `git diff origin/main..PR` | no `docker/env/*`, no `config`, no schema/migration; `db.SCHEMA_VERSION` still 2 |
| Backup verification | After the merge, dispatch `ubuntu-ops.yml` (now backs up even when unhealthy). It runs `verify-backup` on the host and re-verifies sha256 + `integrity_check` off-host, uploads `avdi-ubuntu-backup-<n>` (90-day retention) | job `backup` green; artifact id recorded |
| Baseline snapshot | `python scripts/sync_cloud_data.py`, then record the table fingerprints (`host_evidence_analysis.py`) and keep the pristine mirror folder | hashes recorded in the change log; the latest known-good is `20261021T202501Z` (27 orders, 27 fills, 7 positions, 3,210 audit rows) |
| Window | `deploy.sh` refuses from 10 min before the open to 20 min after the close and on market days; choose an evening ET after ~16:45 or a weekend | before the next 08:30 ET prep, so the next session starts reconciled |
| Rollback target | the host keeps the 3 newest release images; the running image is `avdi-paper:b236d54...` | confirm `deploy-history.log` shows `b236d54` DEPLOYED |

## 3. Rollout (one action)
GitHub → Actions → **Deploy to Ubuntu runtime** (`workflow_dispatch`, ref `main`, **not** `simulate_failure`, **not** `--force`).
It runs, in order: the CI test gate on the runner (`ci_gate.py`) → packages that exact commit → streams it to the restricted `deploy` role →
on the host: build the image (no downtime) → market-window guard → **graceful stop (up to 120 s)** → **pre-deploy backup** → start the new
runtime → wait up to 240 s for Docker `healthy` → on failure, automatic rollback → post-deploy `health` and `status`.
State lives in the named volume `avdi_runtime_ledger`; the new runtime resumes the true account (UNH open, cash $386.21 unless changed).

## 4. Post-deployment reconciliation (within minutes)
Run the ops workflow again (status + backup) and check, in this order:
1. Deploy log ends `DEPLOYED <sha>`; `deploy-history.log` records it; `code_version` = the merged SHA.
2. `status`: **`healthy: true`, `unhealthy: []`**, `degraded` contains **`historical_phantom_fills_unresolved`** (and `offhost_backup_stale` until the new backup is acknowledged).
   `reconcile`: `reconciled: true`, `delta: 0.0`, `cash_from_fills == cash_from_state`, **`excluded_phantom_fills: 14`**, `excluded_phantom_notional: 1867.33`, **`history_resolved: false`**.
   Any other number means stop and investigate (do not widen the tolerance).
3. Pull the new backup and run `verify_append_only.py --old ~/.avdi_cloud_mirror/20261021T202501Z --new <new>`: **append-only, 0 violations**
   (only the documented state changes: open positions, live orders, last equity row, tracker-owned signal columns). This proves no historical record was rewritten.
4. `host_evidence_analysis.py` on the new backup: still exactly 14 flagged fills, $1,867.327, and zero unflagged fills without a position effect.
5. The 15:50-style checks the next morning: `prep.reconciled` true at 08:30 ET and **no new `entries_disabled`** events.

## 5. Monitoring (first three sessions, then the normal daily ops run)
| Signal | Expected | Action if not |
|---|---|---|
| `exit_without_position` audit events after the deploy | **0 new** (the guard cancels instead) | any new one → stop, roll back |
| Pending exit orders per symbol and intent | ≤ 1 | more than 1 → roll back |
| `reconcile.delta` at every close; `postmarket.reconciled` | 0.0 / true | non-zero → stop; do not loosen |
| Entries | resume under the **unchanged** limits ($125 notional, $5 risk, 3 open, 1 per sector, 2/day, $100 reserve) | any breach → roll back |
| New `degraded` / `unhealthy` codes | none besides the documented one | investigate |
| Off-host backup age | < 30 h each day; ops run green | the daily run should now succeed |
| Cost statistics | exit slippage excludes phantoms ($0.158 on the current six real exits) | a sudden drop means phantoms are leaking back in |

## 6. Rollback
* **Automatic:** if the new container is not healthy within 240 s, `deploy.sh` removes it and restarts the previous image, and logs `ROLLED_BACK`.
* **Manual triggers:** any of the failures in §5. Mechanism: revert the merge commit on `main` and run the same deploy workflow (it deploys
  `main`'s HEAD). The host also keeps the three newest release images, but switching them by hand needs host access this plan does not assume.
* **What a rollback does and does not do:** it returns the code to `b236d54`, which is the current state: unhealthy (`ledger_inconsistent`),
  fail-closed, entries disabled. It loses nothing: the change is code-only, rows the fixed code appended stay valid, and the 14 phantom rows are
  there in both versions. Do **not** restore the pre-deploy backup unless data is actually corrupted: it would erase post-deploy records.

## 7. Explicitly out of scope
Repairing or annotating the 14 historical rows; changing any limit, strategy, threshold or environment variable; live execution; CH-004;
moving the Docker/cloud UI; any edit to AGENTS.md / CLAUDE.md (they still disagree on the freeze).
