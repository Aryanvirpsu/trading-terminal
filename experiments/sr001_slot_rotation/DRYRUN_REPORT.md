# SR-001 outcome-blind dry run — 2026-11-03

**Verdict: the 60-session clock does NOT start yet.** Baseline and mechanics pass. The evidence capture does not: two decision-critical inputs are not recorded.
No outcome column, signal outcome, post-decision price for an unentered candidate, or arm result was opened. `sim.py` is a prototype and is not frozen.

## 1. Baseline freeze — PASS
* SR-001 protocol sha256 `48caabf0…c190838` matches its recorded checksum. (The `.sha256` file has CRLF line endings, so `sha256sum -c` fails on it; the hash itself matches.) The protocol was an **untracked file** with no commit timestamp; it is now committed verbatim on this branch with line endings pinned.
* Champion = `54adbea27af62e6835656807684d19033a95abf7`, image `avdi-paper:54adbea…`, engine `decision_engine/gates-v1.1`, config `cfg-a0eede144e`. **11 of 11** key files running in the container are byte-identical to that commit. No `PAPER_*` overrides are set.
* Settings captured from the running container: 3 slots, 1 per sector, $125 cap, $5 loss, $100 reserve, 2 entries/day, $10 daily loss, $50 drawdown, 5 bps slippage (25 gap), no fees, up to 5 finalists per cycle.
* Ordering: `scanner_rank` = score-descending, one finalist per symbol. SR-001's ticker-ascending tie-break is applied only on a true tie.
* Calendar: `market_calendar.py` (hash recorded). Entry cutoff is close − 60 min. If the clock starts at the **2026-11-04** session, the 60th session is **2027-02-01** (closed inside the window: Nov 26, Dec 25, Jan 1, Jan 18; early closes Nov 27 and Dec 24).
* Details: `BASELINE_MANIFEST.json`.

## 2. Data capture — FAIL on two required inputs
What works (a good answer to the 65-case limitation):
| Requirement | Status |
|---|---|
| Record every candidate before the cap rejects it | **Yes.** `shadow_candidates.eligible` means TRADEABLE with every gate passed except capacity. Since Oct 27: 92 TRADEABLE rows, all `eligible=1`, all `capacity_reason=max_open`, none executed. |
| Stable ID | **Yes.** `cand_id = <cycle_id>:<SYMBOL>`. 65 of 73 regular TRADEABLE rows carry a `signal_id` that resolves in the ledger. The 8 without one are **already-held symbols** (UNH, CVX) rescanned ("existing position – no duplicate entry"). |
| Decision timestamp | **Partial.** `scan_ts` is the cycle *start*. The quote source time lands ~15 s later and the row is written ~23 s later. Decision time can only be bracketed: [latest quote source time, `shadow_cycles.created_at`]. Use `created_at` as the conservative decision time. |
| Open positions, settled cash | **Yes**, per cycle in `capacity_json` (open symbols, entries today, cash). But `available_cash` is plain cash: the paper broker has no settlement model, so *settled* cash must be derived from fill timestamps. |
| Quote and receipt timestamps | **Partial.** `quote_source_ts` (provider time) is stored. There is **no local receipt time** per quote. |
| **Incumbent executable bid at each decision cycle** | **MISSING.** Held symbols appear as candidates only when they are finalists: UNH 12, CVX 31, SO 10 of 110 regular cycles. The tracker polls every 60 s but keeps only the latest quote in `runtime_state.json`. SR-001 rule 2 (bid ≤ entry cost) cannot be evaluated without it. |
| **Subsequent executable quotes** (the exit "first eligible subsequent quote", the replacement's next quote) | **MISSING.** A candidate that stops being a finalist gets no later quote. |
| Bars | **Not point-in-time.** `shadow_bars_5m` is overwritten per (symbol, ts) with a single `fetched_at` (end-of-day batch), no revision history, and tracking stops 25 days after a symbol was last seen. Replacement positions held longer would run out of bars. |
| Retention | Backup artifacts keep 90 days; the window is 89 days. The first day's artifact would expire on the last day. Archive copies off-GitHub. |

Also: the 65 `canonical_evaluated` rows are **not joinable** (0 of 65 ids match any `cand_id`). The CH-006 audit design that joins them to the shadow tables will not work. Use `shadow_candidates`.

## 3. Mechanics on synthetic cases — PASS (17 tests, `tests/unit/test_sr001_mechanics.py`)
* Waits for settled cash: the replacement is bought only at or after the T+1 open, never in the cycle of the sale, even if cash were ample.
* $100 reserve: no exit is submitted when the cash projection falls short; the reserve is intact after entry.
* Exit without replacement: when the candidate fails revalidation, the exit stays in the challenger record, the orphan is logged, the vacancy is **not** given to another ticker during the reservation, and normal rules resume after expiry.
* Unfilled exit (stale quote) is cancelled and counted; the position and slot are kept.
* Both accounts are identical (events, positions, cash, receivables) when no rotation triggers: incumbents too young (9 vs 10 completed sessions), no eligible candidate, incumbent bid above entry cost, after cutoff, and through ordinary entries and exits.
* Also covered: earliest-entry then ticker tie-break; first-candidate-only; one attempt per session; 2/day cap counts normal fills; real-calendar T+1 across Thanksgiving, the early-close Friday and the weekend.

## Interpretations of SR-001 I had to make (please confirm or correct before freezing)
* **I1** "10 completed sessions after the fill session" = session index difference ≥ 11 at the decision cycle.
* **I2** A reserved replacement must be fundable at its **full planned size from settled cash**, otherwise it waits (ordinary entries may be cash-limited, as in production). Without this, a $36 partial entry could happen on the day of the sale.
* **I3** T+1: sale proceeds settle at the open of the next regular session. The Champion's paper broker does not model settlement; SR-001's "project T+1 settlement model" does not exist in production code.
* SR-001 uses 15/30 bps per side; the Champion's own fills use 5 bps. That is a deliberate research stress, not a parity claim.

## What has to be fixed before the clock starts
1. **Persist incumbent and follow-up quotes** (symbol, bid, ask, last, source time, local receipt time, provider), at least every cycle for open positions and for any reserved candidate. This is a logging-only change to the runtime (decision-logging is inside the correctness freeze exception) but it needs a PR, tests and a deploy outside market hours. **I have not made it and have not deployed anything.**
2. Add a local receipt time to candidate quotes, and a decision time field.
3. Archive daily verified snapshots (hash + off-GitHub copy) for the full window; do not rely on 90-day artifacts.
4. Decide bar sourcing for simulated positions beyond the 25-day horizon (independent daily bar capture, stamped when fetched).
5. Confirm I1–I3, then freeze `sim.py` + manifest hashes.
Then the clock starts at the first regular session after a clean re-run of this dry run.

## Overlap to resolve
`research/ch-006-slot-rotation` (pushed earlier today) registers a different, overlapping rotation protocol (stall = never touched +1R; 49-session window; starts from the live book). SR-001 is the protocol you called locked. Recommendation: mark CH-006 superseded by SR-001 (neither has produced a result), so only one is ever interpreted.
