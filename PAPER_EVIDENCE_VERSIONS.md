# PAPER_EVIDENCE_VERSIONS.md — which paper evidence may be pooled

Executed-trade evidence from materially different versions must **never** be pooled into one
sample. Every journal row carries `signals.engine_version`; the strategy fingerprint is
`config_version` (unchanged across v1.0/v1.1: `cfg-a0eede144e`).

| Version | `engine_version` | Code | Applies to | Notes |
|---|---|---|---|---|
| v1.0 | `decision_engine/gates-v1` | up to `855bb72` | Case 1 runs 2026-09-10 .. 2026-09-24 | Pre-fix. Sizing used the reference price, so 6 of 8 TRADEABLEs were refused at the $125 cap; the reserve was netted twice on the canonical path; quotes could be relabelled fresh. Signals/decisions are valid; **executed-trade outcomes are biased**. Ledger: `paper-ledger-db-23`. |
| v1.1 | `decision_engine/gates-v1.1` | this commit and later | first Case 1 run after `v1.1-paper-p0` | Same strategy + execution-integrity fixes (sizing at ask+slippage, reserve once, no stale-quote relabelling, refusal reasons kept). New forward sample starts here. |

**Runtime change at the v1.1 boundary (2026-09-25):** v1.0 ran ONE scan per day at ~09:30 ET on GitHub Actions
(one look at each finalist). v1.1 runs on the Ubuntu runtime (`docs/UBUNTU_RUNTIME.md`) with a discovery scan every
~15 min (09:35-15:50 ET, entries until the 15:00 ET cutoff) and a 60 s / 300 s tracker. Same gates, same strategy,
same `config_version`, but entry TIMING opportunities differ, so v1.0 and v1.1 executed trades must never be pooled.
Within v1.1 the ledger journals a signal on the first observation or a decision-label change; independent evidence is
counted by shadow-log `event_id`, never by observation. The Ubuntu ledger was seeded from Case 1 artifact
`paper-ledger-db-23` (sha256 19506161d73965e8692e473fefdb421489061accb0f4ab1006e25f1b2c0ae128, cash $504.66, 0 open
positions); rows before 2026-09-25 are v1.0. On-host smoke cycles are identifiable as `session_type='manual'`
(cycle `2026-09-24T2033manual`) and must be excluded from evidence.

Analysis baseline for v1.0: `docs/PROFIT_MODE_BASELINE_v1.md` (on branch `c1/cloud-runtime`).
Any future strategy change (a promoted Challenger) starts a new version and a new sample.

**Sizing caveat within v1.1 (2026-09-26).** DELL and META (2026-09-25) were sized on the *reference-entry* risk distance and so carry a
realised stop loss of $5.277 / $5.296 against the $5.00 budget. Builds from `main` at/after commit `322e325` size on the *executable* entry and
modelled stop fill (`docs/POST_ACCEPTANCE_FIXES_01.md`). Both remain valid executed v1.1 evidence; the build is identifiable per shadow cycle
(`shadow_cycles.code_version`) and per backup manifest (`runtime_version`). Forward CH-001 evidence includes only positions opened at/after the
v1.1 boundary with a v1.1 engine label (HAL/AMD rows were quarantined in `shadow_ch001_quarantine`).
