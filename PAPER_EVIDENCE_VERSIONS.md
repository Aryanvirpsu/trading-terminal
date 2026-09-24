# PAPER_EVIDENCE_VERSIONS.md — which paper evidence may be pooled

Executed-trade evidence from materially different versions must **never** be pooled into one
sample. Every journal row carries `signals.engine_version`; the strategy fingerprint is
`config_version` (unchanged across v1.0/v1.1: `cfg-a0eede144e`).

| Version | `engine_version` | Code | Applies to | Notes |
|---|---|---|---|---|
| v1.0 | `decision_engine/gates-v1` | up to `855bb72` | Case 1 runs 2026-09-10 .. 2026-09-24 | Pre-fix. Sizing used the reference price, so 6 of 8 TRADEABLEs were refused at the $125 cap; the reserve was netted twice on the canonical path; quotes could be relabelled fresh. Signals/decisions are valid; **executed-trade outcomes are biased**. Ledger: `paper-ledger-db-23`. |
| v1.1 | `decision_engine/gates-v1.1` | this commit and later | first Case 1 run after `v1.1-paper-p0` | Same strategy + execution-integrity fixes (sizing at ask+slippage, reserve once, no stale-quote relabelling, refusal reasons kept). New forward sample starts here. |

Analysis baseline for v1.0: `docs/PROFIT_MODE_BASELINE_v1.md` (on branch `c1/cloud-runtime`).
Any future strategy change (a promoted Challenger) starts a new version and a new sample.
