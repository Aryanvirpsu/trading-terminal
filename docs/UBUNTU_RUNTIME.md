# AVDI Ubuntu paper runtime (primary market-hours runtime)

Status: **paper execution only.** `ROBINHOOD_TRADING_ENABLED=false`, `BROKER_PROVIDER=none`; the runtime
refuses to start (exit 4) if either is changed, and never imports a broker client (tested).
Source of truth: `main`. GitHub Actions = CI, deploy, backup pull, fallback — **not** the intraday scheduler.

## What runs

One container `avdi-runtime` (image `avdi-paper:<git-sha>`, built from `docker/Dockerfile.terminal --target paper`,
Python 3.11.16 + the 60 frozen Case 1 pins) running `python automation/avdi_runtime.py run` under
`docker compose -p avdi-runtime` with `restart: unless-stopped`. Two logical loops, one process, one lock:

| Loop | Cadence (ET) | Does |
|---|---|---|
| Prep health | 08:30 | providers, ledger integrity/reconcile, disk, secret *names* present (never values) |
| Prep observation | 09:15 | discovery in observation-only mode (`session_type=premarket_prep`); never trades |
| **Discovery** | 09:35, then every 15 min through 15:50 | full scan → A/B/C/D/E → fresh quote + sizing + risk → paper entries (until cutoff) → Champion journal → shadow log |
| Entry cutoff | last entry-eligible cycle 14:50 (close − 60 min) | later cycles (15:05 … 15:50) are observation-only (`post_cutoff`) |
| **Tracker (fast)** | every 60 s, 09:30–16:05 | open positions / pending orders: fresh quotes, fills, stops/targets |
| **Tracker (full)** | every 300 s | + every tracked signal (MFE/MAE), option-shadow, raw 5-minute/daily bar collection (throttled) |
| Close processing | 16:10 | expire orders, reconcile, daily report, CH-001 forward evaluation, final bars, **verified backup** |
| Idle | nights/weekends/holidays | heartbeat only (every ~5 s tick, state written) |

Early closes (13:00 ET) shift the tail: last observation 12:50, entry cutoff 11:50 slot, close processing 13:10.
Holidays/early closes come from `lab/paper/market_calendar.py` (computed; extra ad-hoc closures via
`AVDI_EXTRA_CLOSED_DATES`). Cadence/cutoff are operating choices (env, `docker/env/ubuntu-runtime.env`), **not
Champion gates**; changing the entry window is a Challenger decision.

Note: the scanner works from daily bars plus live quotes, so repeated intraday scans mostly re-evaluate the same
finalists with fresh prices; new names appear when the partial daily bar/quote moves them into the funnel.

## Multi-scan semantics (Champion behaviour)

* Every cycle re-fetches a quote **per finalist** (source timestamp validated), recomputes the executable price
  (ask + slippage), sizing, account/risk/capacity checks. Nothing is carried over from an earlier scan.
* The ledger journals a signal only on the **first observation** or a **decision-label change** (MONITOR→TRADEABLE).
  An unchanged observation is recorded in the shadow log only. An unexecuted TRADEABLE is re-attempted, reusing its
  journal row.
* A symbol with an open position/order, or already entered this session, is never entered again.
* Daily entry cap counts entries **persisted in the ledger** (restart-safe); a capped candidate is journaled with
  the reason `daily entry cap reached (N per day, incl. persisted entries)`.
* Observation-only cycles (prep, post-cutoff, or ledger not reconciled) never journal or trade.

## State and restart

Trading state is **derived from the persisted paper ledger every cycle** (cash, reserve, open positions, entries today,
sector exposure, orders). On start the runtime reconstructs it and verifies `reconcile()` **before any entry**;
a failed restore disables entries and makes health UNHEALTHY. `runtime_state.json` holds only scheduling bookkeeping.

* Volume `avdi_runtime_ledger` → `/data/case1/paper`: ledger DB, `shadow/shadow_candidates.db`, `runtime/`,
  `reports/`, `backups/`. Survives recreation, deploys, reboots.
* Slots are **at-most-once** and **never back-filled**: a slot later than 7 min (`AVDI_SLOT_GRACE_S`) is recorded as
  `missed`; a slot interrupted by a crash is marked `interrupted`, not re-run.

## Singleton / overlap

* `runtime/runtime.lock` (OS file lock, auto-released on process death) — a second `run`, or any manual
  ledger-touching command, exits 3 and is counted in `duplicate_attempts`.
* `runtime/discovery.lock` — a scan still running blocks the next slot (`overlap_skipped`); no concurrent trading.
* Deploys stop the old runtime (SIGTERM, 120 s grace) **before** starting the new one.

## Shadow evidence

`shadow/shadow_candidates.db` (separate, append-only, triggers block UPDATE/DELETE): per cycle the complete finalist
set with decision-time features, Champion action, hypothetical Challenger picks, capacity-loss reasons and the
opportunity funnel (universe → detections → finalists → TRADEABLE/MONITOR/REJECT → capacity-eligible → attempted →
refused → entries + top reasons); per observation an `observation_id` and an `event_id`; raw complete daily and
5-minute bars; forward **CH-001** tables (`shadow_ch001_obs`, `shadow_ch001_results`: +1R timestamp, hypothetical
breakeven exit, original exit, delta R, AMBIGUOUS flags). Failure of any of this never affects the Champion.

**Event rule (decided at observation time, no hindsight):** an observation joins the latest event for the same
(symbol, direction) iff the last observation was ≤ 5 calendar days ago, the current price is still strictly inside the
event's (stop, target), and no Champion position from that event has closed; otherwise a new event starts.
Count independent evidence by `event_id`, never by observation.

## Health (`python automation/avdi_runtime.py health|status`, Docker HEALTHCHECK every 60 s)

Unhealthy: heartbeat > 120 s, runtime not holding the lock, restore failed, ledger DB inaccessible/inconsistent,
disk < 10 % free, ≥ 3 consecutive provider failures, and (in session, after 09:50) no discovery within 2 intervals or
tracker stale. Degraded (reported, not failing): shadow logger failure, stale market data (> 15 min), stale backup,
duplicate-runtime attempt. `status` also reports last scan/tracker, quote age, finalists/TRADEABLE counts, entries
used, open positions, cash, last backup, last exception. Logs are structured JSON lines (`docker logs avdi-runtime`).

## Deploy / rollback

```
deploy/ubuntu/deploy.sh <git-sha> <source.tgz> [--force]   # on the host
```
Builds the image (no downtime) → refuses during the market window (−10 min … +20 min) unless `--force` → graceful
stop → verified backup → start → wait healthy (240 s) → **automatic rollback to the previous image** on failure.
First-time ledger seeding: `deploy/ubuntu/seed_ledger.sh <ledger.db>` (refuses to overwrite).
CI/deploy workflows: `.github/workflows/ci.yml` (every push/PR), `deploy-ubuntu.yml` (manual, `main` only, disabled until
`AVDI_DEPLOY_ENABLED` and SSH secrets exist), `ubuntu-ops.yml` (read-only health + backup pull, disabled until
`AVDI_OPS_ENABLED`).

## Restricted remote access (GitHub ops/deploy)

GitHub never gets a shell. Two dedicated ed25519 keys are installed in `~ubuntu/.ssh/authorized_keys` as
`restrict,command="/home/ubuntu/avdi-runtime/ops-gate.sh <role>"`, so every connection runs the gate (`deploy/ubuntu/ops-gate.sh`)
which allows only: role **ops** — `status | health | evidence | backup-now | backup-list | backup-pull` (read-only + a
consistent sqlite snapshot); role **deploy** — `status | health | deploy <40-hex-sha> [--simulate-failure]` (tarball on stdin,
max 100 MB). Every call is logged to `~/avdi-runtime/ops-gate.log`. The gate and `deploy.sh` are host-resident and are
updated only by the owner. Residual risk: a deploy-key holder can ship code that runs in the runtime container, and the
`ubuntu` account is in the `docker` group (root-equivalent) — the restriction removes interactive access, not the
ability to deploy. Deployment during the market window is refused by the host (there is no force option in the workflow).

## v1.1 evidence boundary (account continuity without mixing evidence)

The paper account continues from the Case 1 ledger (cash/equity $504.66, 0 open positions). Immediately BEFORE the first real
in-session (`regular`, entries-allowed) discovery cycle the runtime records, once and append-only, a starting snapshot in
`shadow_evidence_boundary` (`version='v1.1'`: equity, cash, open positions, realized P&L, ledger row counts).
**v1.1 P&L = current equity − boundary equity**; v1.0 gains/losses are never used. v1.0 and v1.1 rows stay separately
queryable: `signals.engine_version` (`decision_engine/gates-v1` vs `…gates-v1.1`) in the ledger, and
`python automation/avdi_runtime.py evidence` prints the boundary, v1.1 P&L and per-version counts.

## Preventing a competing scheduler

`paper-trading-schedule.yml` had its cron triggers removed and now begins with a `primary` guard reading
`deploy/PRIMARY_RUNTIME` (`ubuntu`): any `workflow_dispatch` (including the external cron-job.org ping) is a no-op unless
`fallback=true` is passed explicitly. Fallback must only be used with the Ubuntu runtime stopped — the ledgers are
separate, so a simultaneous run would fork the account.

## Known limits

* Ad-hoc market closures are not predicted. Off-host backup relies on the ops workflow (needs owner-supplied secrets);
  until then backups are on the same VM (`backups/`, 14 kept).
* One vCPU: scans run serially; a slow scan delays the tracker (no overlap by design).
* No provider API keys on the host by default (Yahoo-only market data); keys may be supplied via a root-owned env
  file with `AVDI_ALLOW_PROVIDER_KEYS=true`.
