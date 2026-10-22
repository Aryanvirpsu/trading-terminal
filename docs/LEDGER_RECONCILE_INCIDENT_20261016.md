# Ledger reconcile incident — 2026-10-16 (`ledger_inconsistent`)

**Status:** cause **confirmed against the host's own ledger**; fix verified on a copy of that ledger; **not merged, not deployed**.
Report written 2026-10-22 (ET). Evidence date: host backup `20261021T202501Z`, taken 2026-10-21 20:25 UTC.

## 1. What happened (confirmed)
On 2026-10-16 the tracker's running daily high sat exactly at EOG's $157.60 target. A target exit triggers on `high >= target`; a LIMIT
sell fills only on strict trade-through (`high > target`). So for ~14 minutes **every 60 s cycle queued another full-size sell order**.
When the high cleared the target, all fifteen filled in one pass: the first closed the position, and each of the other fourteen
inserted a fill row with no position behind it (the ledger logged `exit_without_position` for each). Fill rows are append-only, so
`reconcile()` failed from then on, and the runtime (correctly, fail-closed) **disabled new entries**.

| Evidence from the host ledger | Value |
|---|---|
| EOG `LIMIT exit_target` orders, qty 0.846323 @ 157.6 | **15**, created 18:50:37.205Z → 19:04:32.551Z (14:50–15:04 ET), gap median 60.9 s |
| Fills | 15, all written **within 8.0 ms** at 19:05:35.220–.228Z |
| EOG position | closed at 19:05:35.2209Z by the **first** fill; the other 14 fills are all later |
| Fills flagged by `exit_without_position` | **14** (one audit event per fill, written at/after the fill), total **$1,867.327** |
| META | one `STOP` order, one fill, no flag (14:22 ET); the only other order after Oct 15 |
| Deployed `b236d54` `reconcile()` on this ledger | cash from fills **$2,253.53** vs state **$386.21**: **delta $1,867.32** (= 14 × $133.3805, to the cent) |
| Delta with the 14 flagged fills set aside | **$0.00**; the cash in every `equity` row matches the unflagged fills for every session |
| Shares if the phantoms were real | EOG **−11.85** (selling shares never held); with them set aside only UNH 0.273468 remains, equal to the open position |
| Realized P&L | positions table −$2.5332 vs FIFO of the unflagged fills −$2.5331; EOG +$8.3806, META −$5.2957 |
| Host `postmarket` reconciled flag | true Oct 14–15; **false Oct 16, 19, 20, 21** |
| Entries | none after Oct 15; `entries_disabled: ledger not reconciled` logged **66×** (22 per session) on Oct 19, 20, 21 |
| Scheduler | healthy throughout (0 discovery / provider failures); the failure was in the ledger only |

*Timing note:* the host saw the touch and the trade-through about 4–5 minutes later than public minute bars (14:50 vs 14:48; 15:05 vs
15:01; META's stop 14:22 vs 14:17): consistent with provider daily-bar lag. The mechanism and counts match the reproduction exactly.

## 2. Verification of the fix (your nine checks)
| # | Check | Result |
|---|---|---|
| 1 | Retrieve the actual Oct 16 host evidence, narrowest read-only method | Done via the existing ops path: `ubuntu-ops.yml` dispatched once from this branch (run 39709406747) so the backup step no longer waits for a green probe. It ran only `status`, `health`, `health-log`, `verify-backup`, `backup-pull`, `offhost-ack`. Backup `20261021T202501Z` (artifact 25) re-verified on the host, off-host and again locally (sha256 + `integrity_check`, opened `mode=ro&immutable=1`, `journal_mode=delete`, no WAL to replay). Only the ledger was read; no signal/candidate outcome table or challenger result was used. |
| 2 | Does production match the reproduced defect? | **Yes**, structurally and numerically (§1). Replay of the suspected sequence predicted 13 orders / 12 phantoms / $1,600.56; the host had 15 / 14 / $1,867.32 (it queued two more cycles before the provider's bar cleared the target). |
| 3 | Compare orders, fills, audit, cash, realized P&L, positions | §1; `host_evidence_analysis.py` → `host_evidence_results.json`. |
| 4 | Exclusion supported by exact audit evidence; cannot mask unrelated problems | Hardened to a **fill-level** definition (`db.PHANTOM_FILLS_SQL`): exit order; audit written at/after the fill; as many audits as fills on that order; no position of that symbol open at the fill's timestamp. Tests: a sale made while shares were held, an audit written before the fill, an order with one legitimate and one phantom fill, and an extra unrelated $5 discrepancy are all **not** excluded and still fail with their exact delta. The first three fail against the earlier order-level definition. Residual: the audit table is trusted (a writer who can forge audit rows can already alter anything). |
| 5 | Every consumer treats excluded fills consistently | `reconcile`, `health`, `report._slippage_summary` and the daily report (cost statistics now exclude them: exit slippage $0.158 over the six real exits, not ≈$0.047 with 14 zero-slippage phantoms), `risk._last_mark` (never a phantom price), backup manifest (raw counts + `phantom_fills_included_in_fills`), acceptance dump (raw rows + `phantom_fill_ids_excluded_by_reconcile`). `report.performance`, equity and risk use the positions table, which was always correct. Exercised end to end on a copy of the real ledger. |
| 6 | Reconcile idempotent, deterministic, immutable | Run repeatedly: identical results; fills summed in `(filled_at, fill_id)` order; table fingerprints (rows + hashes) identical before and after, for the deployed and the fixed code. `verify_append_only.py` proves a later snapshot only appended to an earlier one (tested on the real Oct 15 → Oct 21 pair: append-only, 0 violations; negative control fails). |
| 7 | Degraded status identifies the excluded history, not "resolved" | `degraded: historical_phantom_fills_unresolved`; `health().reconcile` returns `excluded_phantom_fills` (14), `excluded_phantom_notional` ($1,867.33) and `history_resolved: false`; `reconcile()`'s note says the rows are **EXCLUDED, NOT CORRECTED** and need human review. Nothing clears it automatically. |
| 8 | Regression, integration and recovery tests | Full unit suite: deployed `b236d54` **1,410 passed / 5 failed**; this branch **1,437 passed / the same 5 failed**. The repo's own deploy gate (`deploy/ci_gate.py`) passes: 5/5 known failures, 0 unexpected. PR CI (tests, Docker image build, CodeQL) green on the first five commits; re-run on the latest. **Recovery test:** a copy of the real Oct 15 ledger damaged with the unfixed code (and the real Oct 21 ledger itself) reconciles with this branch, lists the phantoms, and changes no row; a replay from scratch with the fixed code records exactly 1 order and 1 fill. |
| 9 | Do the 5 existing failures affect deployment confidence? | **No.** They are the repo's documented permanent baseline (`deploy/ci_gate.py` `KNOWN_FAILURES`), recorded as failed with identical reasons in all four committed baselines **including the production container** (`docker/baseline/tests-container-case1-utc.json`): 3 FinBERT tests (the `transformers` package is absent; the model is disabled by default and falls back to lexical scoring) and 2 options-policy tests (`grade_contract` vs `analyse_contract`; options are shadow-only, `shadow_only=True` hard-coded). Neither touches the paper ledger, fills, reconcile or the exit path. The deploy job's test gate treats exactly these five as expected and fails on anything else. |

## 3. What changed
Seven commits on `fix/ledger-reconcile-phantom-fills` (PR #11). Pipeline files touched: `broker.py`, `fills.py`, `risk.py`, `runtime.py`,
`report.py`, `db.py` (a constant and two functions, no schema change) and one line in `automation/avdi_acceptance.py`:
1. `process_order`: an exit order with no open position is cancelled and audited; **no fill row**. `manage_open_positions`: one working
   exit order per symbol and intent (a stop still fires while a target order is pending).
2. Partial-fill defects: a fractional order can no longer be filled as a whole share; a partial exit's realized P&L is carried into the
   close and counted in cash.
3. `reconcile()` sets aside **audited** phantom fills and lists them; `health()` returns the amounts.
4. `ubuntu-ops.yml`: the off-host backup runs after a failed probe (the run still ends red); `health-log` after a failed health step.
5. Docs. 6. Hardening: the fill-level definition and the consumers above. 7. This report and the deployment plan.

No gate, threshold, sizing rule, trigger, fill price, slippage or configuration changed on the normal path; no schema change
(`db.SCHEMA_VERSION` still 2); `ROBINHOOD_TRADING_ENABLED=false` and `BROKER_PROVIDER=none` untouched; CH-004 stays paused.

## 4. Limits and open items (stated plainly)
* **Commit 3 is control-adjacent.** It is separable (drop it and the rest stands), but a ledger that already holds phantom rows then stays red.
  It never clears `historical_phantom_fills_unresolved` by itself: that needs a separate, human-approved decision (for example an append-only
  correction annotation). No ledger row is rewritten by this change.
* The exclusion trusts the `audit` table. It cannot detect a malicious writer; nothing in an append-only SQLite file can.
* **Cost of waiting:** while undeployed, entries stay disabled (3 sessions so far). That is lost paper-trading evidence, not lost money.
* Roll-forward only fixes new behaviour; the 14 phantom rows stay in `fills` for ever and are visible in every report that matters.
* No signal was journaled in the ledger after Oct 16 17:05 UTC, while shadow observations continued (cycle counts kept rising). The cause
  was not investigated (it may be the entry gate); treat Oct 19–21 as a gap in the ledger's signal journal, not in the shadow log.

## 5. Reproduce (all read-only; scripts in `docs/ledger_inconsistent_20261016/`)
```bash
git archive b236d54a469475b7c6ac7c1980b1e4b30e10de0d | tar -x -C <empty_dir>        # the deployed source
python -I host_evidence_analysis.py --mirror ~/.avdi_cloud_mirror/20261021T202501Z --deployed <empty_dir> --fixed <this checkout> --out r.json
python -I verify_fix_on_damaged_ledger.py --deployed <empty_dir> --fixed <this checkout> --ledger ~/.avdi_cloud_mirror/20261015T202501Z/robinhood_500_baseline.db --out v.json
python -I verify_append_only.py --old <earlier backup dir> --new <later backup dir>
TZ=UTC python deploy/ci_gate.py tests/unit                                            # expect: 5/5 known failures, exit 0
```
The deployment plan is `docs/LEDGER_RECOVERY_DEPLOYMENT_PLAN.md`.
