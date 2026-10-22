# Ledger recovery: deployment result (2026-10-22)

Executed under the owner's authorisation (merge and controlled deploy of PR #11; permanent retention of the 14 historical phantom fills with their unresolved status).
Plan followed: `docs/LEDGER_RECOVERY_DEPLOYMENT_PLAN.md`. Nothing in the plan was skipped; nothing was bypassed (the repo has no branch protection and the `production` environment has no required reviewers, so no approval gate existed to honour).

## Timeline (UTC, 2026-10-22)
| Time | Step | Result |
|---|---|---|
| 04:19 | Pre-flight: PR head `412a2b3` == last verified head; CI (tests x2, Docker build, CodeQL x3) green; `mergeable`; diff touches no `docker/env`, config, schema, limits, flags, strategy files | pass |
| 04:19 | Window: 00:19 ET Thursday, outside the guarded window (-10 min .. +20 min around the session) | pass |
| 04:19 | Backups available: host `20261021T202501Z` (verified on the host, off-host and locally), plus the deploy's own pre-deploy backup | pass |
| 04:19 | PR #11 marked ready and merged (merge commit pinned to the verified head) -> `main` = `54adbea` | merged |
| 04:20 | `deploy-ubuntu.yml` on `main`, `simulate_failure=false` (run 39721731389) | **success** |
| 04:20 | Deploy test gate: 1,434 passed, 1 skipped, **5/5 known failures, 0 unexpected** | pass |
| 04:20 | Host: build -> market guard -> graceful stop -> pre-deploy backup -> start -> healthy in ~10 s; equity `$500.24` before and after; no rollback | pass |
| 04:22 | `ubuntu-ops.yml` from `main` (run 39721962667): status, health, evidence, host-verify, pull, off-host verify, acknowledge | **first green ops run since Oct 15** |

## Acceptance criteria (from the host's own status output)
| Criterion | Observed | Pass |
|---|---|---|
| `healthy: true` | true | yes |
| `unhealthy: []` | `[]` | yes |
| `reconciled: true` / `reconcile.delta: 0.0` | true / 0.0 | yes |
| `excluded_phantom_fills: 14` | 14 ($1,867.33) | yes |
| `history_resolved: false` | false | yes |
| `historical_phantom_fills_unresolved` visible | in `degraded` | yes |
| Deployed runtime == merged commit | `code_version` = `54adbea27af62e6835656807684d19033a95abf7` | yes |
| Verified off-host backup | `20261022T042050Z` (sha256 + SQLite integrity, host and off-host), artifact `avdi-ubuntu-backup-26` | yes |
| Append-only history | `verify_append_only.py` Oct 21 -> post-deploy snapshot: 0 violations, no rows added or changed | yes |
| No duplicate exits or unauthorized orders | orders/fills/audit unchanged at 27/27/3,210 (the snapshot predates the first session) | **not yet testable** |
| Existing limits enforced | code and configuration untouched (diff scope check); behaviour to be confirmed at the first session | **not yet testable** |

## What is still unverified, and when it will be
The pre-deploy snapshot proves the deploy changed no history, but only a **session run by the new build** can prove entries resume under the unchanged limits and that no duplicate exit orders appear.
Check after the first session close (Thursday 2026-10-22, ~16:25 ET): `python scripts/sync_cloud_data.py`, then `python scripts/avdi_daily_report.py --event-risk` and
`python docs/ledger_inconsistent_20261016/verify_append_only.py --old <20261022T042050Z> --new <newest>`; expect `entries_disabled` events = 0 for the day, `exit_without_position` events = 0,
`reconcile.delta` = 0.0, `excluded_phantom_fills` still 14, and any new entries within $125 / $5 risk / 3 positions / 1 per sector / 2 per day.
If any expectation fails, follow the rollback procedure in the deployment plan (revert the merge on `main`, redeploy); do not loosen reconciliation.

## Remaining anomalies
* `offhost_backup_stale` was degraded during the outage (backups gated on health); the first green run clears it once the host records the acknowledgement.
* The deploy workflow's final status step prints only the first 30 lines, so `reconcile` and `code_version` are not visible in its log; the ops run shows them. A small log improvement, not urgent.
* AGENTS.md (untracked, old build-freeze text) still contradicts CLAUDE.md (Profit Mode); neither was edited.
