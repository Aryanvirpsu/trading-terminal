"""H5 -- known-forward reproduction of the 2026-09-25 Ubuntu acceptance session.

Everything under this package is scoped to ONE specific, already-happened forward session and its
replay/comparison. It is deliberately kept separate from the general H1-H4 Historical Lab infrastructure
(clock, provider, adapters, avdi_adapter, execution) and from the fabhaus research corpus: nothing here is
reused for later walk-forward/profitability research, and the fabhaus corpus is never imported by anything
in this package.

Source of truth: `docs/UBUNTU_LIVE_ACCEPTANCE_01.md` (commit `f12a552`, the actual Ubuntu acceptance
report for Friday 2026-09-25, produced by `automation/avdi_acceptance.py`'s read-only extraction against
the real ledger/shadow DB and an independently verified off-host backup). This session's environment has
no SSH/backup access to the Ubuntu host itself (129.159.91.34) or its raw shadow-log/ledger database, so
`reference.py`'s structured facts are transcribed from that document at the granularity it actually
reports -- session/event/window level and two fully-detailed order traces (DELL, META) -- NOT a fabricated
row-by-row 26-cycle machine trace, which would require raw data this environment cannot reach. This scope
limit is stated plainly in `research/historical/reports/H5_FORWARD_REPRODUCTION.md` rather than hidden
behind invented precision.
"""
