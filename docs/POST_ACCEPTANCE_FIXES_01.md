# POST_ACCEPTANCE_FIXES_01

Follow-up to `UBUNTU_LIVE_ACCEPTANCE_01` (session 2026-09-25). Scope: correctness/evidence/reliability defects only.
**No Champion gate, signal, ranking, capacity, stop, target or strategy threshold was changed.** Today's ledger and trades
were not rewritten (see "Preserved evidence"). Code on `main`: `93c8e16` (CH-001), `322e325` (executable-risk sizing), `190e1fc` (host timers + backup hardening) and the follow-up commit that adds the reboot tooling, counterfactual and these docs.

## 1. CH-001 evidence contamination — fixed

* **Why HAL and AMD were included.** `shadow_log.evaluate_ch001` iterated every row of the ledger `positions` table with no version,
  boundary or origin filter. At the first close job (16:10 ET) it derived CH-001 rows for the two pre-v1.1 positions (HAL opened
  2026-09-11, AMD opened 2026-09-17) from 5-minute bars that the on-host smoke cycle had pulled in. The rows were therefore v1.0
  history mislabelled as forward v1.1 evidence.
* **Eligibility rule added (durable fields only, no symbol names).** A position enters CH-001 forward evidence only if (1) a v1.1
  evidence boundary exists, (2) `positions.opened_at` ≥ that boundary, (3) its originating `signals.engine_version` starts with
  `decision_engine/gates-v1.1`, and (4) it did not originate from a manual cycle (`shadow_candidates.session_type='manual'`).
  Skips are counted (`skipped_pre_boundary / _version / _manual / _no_boundary`).
* **Rows removed / rebuilt (ledger untouched).** `rebuild-ch001` copied the 3 contaminated derived rows (1 obs: AMD `plus1r_ts`
  2026-09-21; 2 results: HAL, AMD) to `shadow_ch001_quarantine` (with reason and timestamp), logged the action in the append-only
  `shadow_maintenance_log`, emptied the derived tables inside the single explicit maintenance path, re-derived deterministically from
  ledger + raw bars under the new rule, and restored the append-only triggers. Executed on the host 2026-09-26 00:04:51 UTC.
* **Valid CH-001 sample afterwards: 0 results, 0 +1R observations.** Eligibility per position: HAL ✗ (pre-boundary), AMD ✗ (pre-boundary),
  **DELL ✓, META ✓** (both v1.1, opened after the boundary, still open). **No actual +1R forward observation exists yet**
  (DELL MFE 0.17 R, META 0.43 R at the close).
* **Regression tests** (`tests/unit/test_multiscan_day.py`): a v1.0 position can never enter (two of them, even with a perfect +1R-then-stop
  bar path); no boundary → nothing; post-boundary position with a v1.0 engine label excluded; manual-cycle position excluded; rebuild
  quarantines, re-derives deterministically, keeps the ledger identical and restores the append-only triggers.

## 2. Executable-risk sizing — fixed

* **Trace (DELL, 2026-09-25).** strategy risk budget $5.00 → reference entry 563.42 → decision stop 517.79 → old risk/share used
  **reference** distance 45.63 → quantity 0.109577 → paper broker filled at ask 565.4026 + 5 bps = **565.6853** → modelled stop fill
  517.79 × (1 − 5 bps) = 517.531 → **realised risk/share 48.154 → $5.277 if the stop fills** (+5.5 % over budget; the ledger recorded
  `planned_risk = 5.00`). META: $5.296 (+5.9 %).
* **Root cause.** `stock_account_fit` and `risk.position_size` sized risk from `|reference entry − stop|` and used the executable price only
  for cash/notional. This was a correctness defect, not a reporting convention.
* **Invariant now enforced.** For a long, `quantity ≤ risk_budget / (executable_entry − stop_fill + round-trip fees)` where
  `executable_entry = ask × (1 + slippage)` and `stop_fill = stop × (1 − exit slippage)` — the same assumptions the paper broker uses —
  together with cash, reserve, position cap, sector cap and quantity rules. Quantities **round down** (6 dp) so rounding can never breach the
  budget. Stops were not moved and fills were not made more favourable. Explicitly outside the guarantee (documented): a gap through the stop
  beyond the modelled slippage.
* **Tests** (`tests/unit/test_executable_risk.py`, 21): the broker's own fill simulator prices both legs; reference == fill, fill above
  reference, large spread, high slippage, tight stop, fees, fractional and whole shares, position-cap / cash / sector-cap / risk-budget binding,
  legacy `position_size`, and the DELL/META replays. Every realised stop loss ≤ $5.00 (+ cent rounding).
* **Counterfactual (stored separately, ledger not rewritten):** `docs/counterfactuals/2026-09-25_executable_risk_sizing.json`.

| Trade | Actual qty | Actual realised stop loss | Corrected qty | Corrected realised stop loss | Quantity change |
|---|---|---|---|---|---|
| DELL | 0.109577 ($61.99) | **$5.277** | **0.103833** ($58.74) | $5.000 | −5.24 % |
| META | 0.118596 ($89.01) | **$5.296** | **0.111974** ($84.04) | $5.000 | −5.58 % |

Evidence note: DELL and META (sized before the fix) remain valid executed v1.1 evidence but carry this sizing caveat; from the first
cycle on the fixed build the sizing is corrected. The build (`code_version`) is recorded on every shadow cycle and every backup manifest.

## 3. Reliable off-host backup and health scheduling — host timers

GitHub cron proved unreliable (2 of ~16 probes fired; nightly backup did not), so **the host owns timing** (systemd timers,
`Persistent=true`, installed by `deploy/ubuntu/install-timers.sh`, owner-managed):

| Timer (time zone) | Fires | Does |
|---|---|---|
| `avdi-health.timer` | every 15 min, 24/7 + 2 min after boot | read-only status → `~/avdi-runtime/health/latest.json`, one-line history `health.log`; ALERT file + journald `err` when unhealthy/unreachable |
| `avdi-close-backup.timer` | Mon-Fri **16:25 America/New_York** (next: Mon 20:25 UTC) | skips non-trading days; waits (≤ 25 min) for the 16:10 close job; online sqlite snapshot inside the container; independent verification; best-effort off-host trigger |
| `avdi-verify.timer` | daily **22:15 America/New_York** | latest backup exists, ≤ 30 h old (≤ 80 h on non-trading days), sha256 + integrity + ledger AND shadow present |

* **Backup contents.** Paper ledger, shadow DB, `runtime_state.json`, daily reports, and `MANIFEST.json` with timestamp, reason, runtime
  version, engine and config version, the v1.1 evidence boundary, ledger summary and per-DB size / **SHA-256** / **integrity_check**.
  Finished backups are read-only (0444).
* **Retention.** Every backup for 7 days, then one per day up to 60 days, nothing older, a 2 GB cap (oldest first) and never the newest
  (12 backups ≈ 20 MB today; the 44 GB disk cannot fill from backups).
* **Failure behaviour.** Backup/verify/health run as separate oneshot units: a failure raises `ALERT_BACKUP`/`ALERT` (file + journald `err`),
  never touches the trading process. Proven on the host with a fake `docker` that fails `backup-live`: script exit 1, alert raised, runtime PID
  unchanged, health healthy, 0 restarts; a normal run cleared it. The in-runtime close-job backup is wrapped so it can never break the close.
* **Off-host mechanism.** GitHub artifact via the existing restricted `ops` SSH key. Verified loop: host-verified backup → workflow pulls it →
  re-verifies sha256 + integrity + contents off-host → uploads → **acknowledges to the host** (`offhost-ack`), which health reports
  (`offhost_backup_stale` degraded if > 30 h on a trading day). Proven (run 36203572295, backup `20260926T000605Z`). **Automatic trigger
  from the host** (`avdi-offhost-dispatch.sh`) needs a fine-grained token limited to *this repo* with the single permission "Actions: read
  and write" at `~/avdi-runtime/secrets/gh_dispatch_token`; it is **NOT CONFIGURED** yet and is reported as such, never as success. Until then
  a single GitHub nightly cron is only a backstop and manual dispatch works.

## 4. VM reboot acceptance — passed (outside market hours)

Pre-state → `sudo systemctl reboot` → automatic comparison (`deploy/ubuntu/reboot_acceptance/`): **29 / 29 checks passed.**

| | Before | After |
|---|---|---|
| Boot ID | `c5452fc7` | `88ea93eb` (new) |
| Recovery | — | **SSH back in 45 s; `avdi-runtime` healthy in 73 s** (dashboard + proxy also back) |
| Release | `190e1fc…` | `190e1fc…` (correct release, recreated by `unless-stopped`) |
| Cash / equity | $353.67 / $504.49 | identical; reconciles, delta 0.0 |
| Positions | DELL 0.109577 @565.6853, META 0.118596 @750.5 | identical |
| Ledger row hashes | signals 76, orders 6, fills 6, positions 4, equity 12 | byte-identical |
| Evidence boundary | $504.66 @ 2026-09-25T13:35:22Z | identical |
| Shadow DB | 28 cycles, 138 observations, 17 events, 5,070 bars, 3 quarantined CH-001 rows | identical |
| Slots | 27 cycle-log entries, 29 done | unchanged — **no back-fill**, no new orders |
| Timers | 3 enabled/active | 3 enabled/active (health ran 2 min after boot) |
| Live execution | disabled | disabled (`ROBINHOOD_TRADING_ENABLED=false`, `BROKER_PROVIDER=none`) |

## 5. Preserved evidence (untouched)

Starting equity **$504.66**, ending equity **$504.49**, two open paper positions (DELL, META), 133 raw observations, 12 independent events,
26 discovery cycles, zero missed slots, ledger row hashes identical before/after the reboot. The only derived data changed is the CH-001
rebuild, with the removed rows preserved in `shadow_ch001_quarantine`.

## 6. Tests

* Full unit suite (`TZ=UTC`, baseline-aware gate `deploy/ci_gate.py`): **1,394 passed, 5 failed (all 5 known baseline), 0 new failures** (was 1,356 + 5 before this work). New/extended suites: executable risk (21), backup reliability (7), CH-001 version isolation (5 new + 3 updated), ops-gate validation (+5), and the compressed market-day simulation (18 tests in `test_multiscan_day.py`, scenarios A-E, stale quote, restart, events, funnel, CH-001, acceptance extraction). Host tests: 3 timer services run for real, backup failure isolation, off-host loop, VM reboot 29/29.
* **Known baseline failures (unchanged, 5):** `test_canonical_v11_invariants::test_oi_policy_has_no_overlapping_hard_and_soft_ranges`,
  `::test_contract_quality_identical_across_pipelines`, `test_finbert_sentiment::test_transformers_available`,
  `test_finbert_service::test_available`, `::test_score_force`.
* No new failures.

## 7. Still open

* Host → GitHub off-host trigger needs the scoped token (above).
* Provider mix (TradingView ≈ 60 % of quotes, transient retries), single VM, holiday behaviour not yet seen live (next: 2026-11-26),
  planned-risk gap-through-stop remains unavoidable, ledger `not_executed` reason text is still generic — unchanged from the acceptance report.
