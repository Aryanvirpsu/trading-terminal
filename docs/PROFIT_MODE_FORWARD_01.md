# PROFIT_MODE_FORWARD_01

Living Profit Mode analysis, updated from real v1.1 sessions as they accumulate. This edition covers the single
session available: **2026-09-25** (first real market day on the Ubuntu runtime). Pre-registration and reproduction
scripts: `experiments/profit_mode_forward_01/`. No strategy, gate, ranking, capacity, stop, target or threshold was
changed to produce this report.

> **Read this first.** One session, **12 independent events**, **2 executed trades**, **both still open** at the data
> cutoff (Friday's close — the market was shut for the weekend). Nothing below is evidence that any gate or cap
> should change. It is the first real population to measure, not a conclusion.

## Evidence boundaries in force
* v1.0 (55 signals, `engine_version=decision_engine/gates-v1`) is excluded entirely.
* v1.1 boundary: 2026-09-25T13:35:22Z, starting equity $504.66.
* **DELL and META used the pre-fix reference-entry sizing** (realised stop loss $5.28 / $5.30 vs the $5.00 budget —
  `docs/POST_ACCEPTANCE_FIXES_01.md`). They remain valid v1.1 observations, flagged accordingly; they are not
  re-sized here. The corrected-sizing cohort begins with the first entry on `main ≥ 322e325` (build `90c0baa`,
  deployed 2026-09-26; tracked separately once it exists — see `docs/POST_FIX_FIRST_ENTRY.md`).

## 1. Opportunity funnel

| Stage | Count (raw observations, 27 cycles) | Count (independent events, n=12) |
|---|---|---|
| Universe considered | 632 | — |
| Scanner detections | 508 | — |
| Finalists | 133 | 12 |
| TRADEABLE (ever, that day) | 29 | 6 |
| MONITOR (best reached) | — | 2 |
| REJECT (best reached) | — | 4 |
| Entered | 2 | 2 |

**Conversion rates** (raw observations): scanner → finalist 26.2%; finalist → TRADEABLE 21.8%; **TRADEABLE → entry
6.9% — the largest drop in the funnel.** By event: 12 → 6 reached TRADEABLE (50%) → 2 entered (33% of the
TRADEABLE events, 17% of all events).

## 2. Strategy gates vs portfolio/execution constraints

Two attribution families, kept separate as requested.

**Strategy filtering** (why 133 observations became only 29 TRADEABLE): soft/hard gate hits across MONITOR+REJECT
observations — `conviction` 104, `quality_threshold` 61, `freshness` 5, `signal_disagreement` 4, `signal_alignment` 1
(an observation can hit more than one gate). Conviction alone touches essentially every non-TRADEABLE observation.

**Portfolio/execution filtering** (why 29 TRADEABLE observations became only 2 entries): `sector_cap` 4 observations
(1 event: AAPL), `daily_entry_cap` 23 observations (3 events: TMO, MSFT, TSLA). **Zero** observations were lost to
open-position cap, available capital, quote staleness or broker refusal — every TRADEABLE candidate that had a free
slot was actually sized and sent to the broker.

**By independent event**, the two families are comparable in count (strategy stops 6 of 12 events before TRADEABLE;
portfolio stops 4 of the 6 that arrive), but they are mechanically different: once **any** 2 TRADEABLE events exist
in a session, the 2-per-day cap makes every subsequent TRADEABLE event structurally unenterable **regardless of its
quality** — DELL and META happened to be the first two, not necessarily the best two (see §4, capacity choice
events). On 2026-09-25 the daily-entry cap, not conviction, was the proximate reason 3 of the 4 blocked TRADEABLE
events never traded. This is the quantified version of the observation from the first acceptance report; it is a
single day's measurement, not a recommendation to loosen the cap.

## 3. Candidate supply

Every cycle saw exactly 5 finalists (the scanner's fixed count) drawn from an 8-28 detection pool across a fairly
stable universe (~23 symbols/cycle). Symbols entered the funnel gradually through the day (5 at 09:35, 12 distinct
by the close), so supply was adequate but not abundant: on a typical cycle 0-2 finalists were TRADEABLE, and real
capacity competition (≥ 2 eligible TRADEABLE at once) appeared only from 12:05 onward, in 12 of the day's 27 cycles.

## 4. Capacity choice events (chosen vs blocked, same cycle)

| Cycle (ET) | Eligible | Picked | Blocked (reason) |
|---|---|---|---|
| 12:05 | AAPL, DELL | **DELL** | AAPL (sector — both technology) |
| 13:05 | DELL, META | **META** | DELL (daily cap already open+queued) |
| 13:20-14:50 | DELL, META, (MSFT from 14:35) | — | all (daily cap: 2/2 used) |
| 14:20, 14:50, 15:20-15:50 | DELL, TMO | — | all (daily cap) |

The Champion's tie-break is **scanner rank** (finalist order), not quality: at 12:05 DELL was picked over AAPL by
rank, not because DELL scored higher (DELL quality 67.0 vs AAPL 62.9 that cycle — DELL was higher, but the ordering
rule itself does not look at quality). This is exactly the CH-002 question; per instruction, no ranking experiment
is run here — this session is recorded as the second real choice-event data point (the first, from the 2026-09-25
baseline replay of the *pre-fix* ledger, was CVX vs COP, also a coin flip within one sector).

## 5. Blocked-event counterfactuals (decision-time executable assumptions; ALL STILL OPEN)

| Symbol | Blocked by | Entry | Stop | Target | Outcome @ Fri close | R (open) | MFE (R) | MAE (R) |
|---|---|---|---|---|---|---|---|---|
| AAPL | sector cap | 338.93 | 329.88 | 360.08 | open | **+0.21** | 0.30 | 0.00 |
| TMO | daily cap | 670.39 | 649.26 | 718.71 | open | **+0.18** | 0.23 | 0.05 |
| MSFT | daily cap | 516.68 | 501.82 | 551.06 | open | **−0.06** | 0.10 | 0.09 |
| TSLA | daily cap (+cutoff) | 370.75 | 355.06 | 405.50 | open | **+0.07** | 0.14 | ~0.00 |

**Actual chosen trades, same method:** DELL open **−0.02R**, META open **+0.08R**.

Reading this correctly: at this single snapshot, the two blocked names AAPL and TMO show a marginally better
mark-to-market R than DELL, and MSFT shows worse. **None of the six positions has reached ±1R or hit a level.** This
is far too little information to say the caps cost or saved anything — it is one clock-tick of six correlated
intraday paths on the same day. No stop/target ordering ambiguity occurred (no bar touched both levels for any of
the six). Time-to-±1R: none of the six had reached +1R as of the data cutoff.

## 6. CH-001 (forward, shadow-only, corrected version filtering)

Clean slate as of the CH-001 contamination fix (`docs/POST_ACCEPTANCE_FIXES_01.md`): **0 valid results, 0 +1R
observations.** DELL (MFE 0.17R) and META (MFE 0.43R) are the only eligible positions and neither has reached +1R.
No sample yet by symbol/event clustering. No promotion possible; none sought.

## 7. Performance (v1.1, pre-fix-sizing cohort, single session)

* Realized R: 0 (nothing closed). Unrealized: DELL −0.02R, META +0.08R (open, mark-to-market).
* Expectancy: not meaningful at n=2, both still open.
* Execution leakage measured so far: DELL slippage $0.28, META slippage $0.38 (both within the 5 bps model); the
  larger, now-fixed leakage was the **sizing** defect (§ above), not fill slippage.
* MFE/MAE: DELL 0.17R/0.51R, META 0.43R/0.09R.

## 8. Experiments status

| Experiment | Status |
|---|---|
| Champion | v1.1, `cfg-a0eede144e`; sizing fixed at `main 322e325`/`90c0baa` (2026-09-26) |
| CH-001 (breakeven after +1R) | Shadow-only, clean forward evidence now, 0 events yet |
| CH-002 (slot ranking) | Not re-run (per instruction). One new real choice-event data point recorded (§4): DELL-over-AAPL was a scanner-rank tie-break within one sector, same pattern as the earlier pre-fix baseline replay |
| New hypotheses | None raised — no recurring pattern exists yet in a 1-session, 12-event sample |

## 9. What this session actually tells us (and what it doesn't)

* **Largest measured drop:** TRADEABLE → entry (6.9% of raw observations, 33% of independent TRADEABLE events),
  and on this day the daily-entry cap — not conviction — was the proximate cause for 3 of 4 blocked events.
* **Strategy-side attrition is real and large** (133 → 29 TRADEABLE observations, dominated by `conviction`) but is
  unchanged from the earlier baseline finding and not newly quantified as wrong or right here.
* **Nothing here supports loosening the daily cap, the sector cap, or conviction**, and nothing supports leaving
  them alone either — one day, two open trades, no resolved outcomes. The next several forward sessions are needed
  before the blocked-event counterfactual table has any resolved rows to read.

## Next update
Re-run this report after each new v1.1 session (and once `docs/POST_FIX_FIRST_ENTRY.md` confirms the corrected
sizing forward-cohort has begun) — append sessions, keep the pre-fix/post-fix sizing distinction, and keep counting
by independent event.
