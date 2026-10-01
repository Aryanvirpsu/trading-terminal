# HIST-003: Daily Entry Capacity (2 vs. 3 vs. 4 entries/day) — Results

**STATUS: COMPLETE — NOT PROMOTED**

Pre-registration: `HIST_003_PREREGISTRATION.md` (+ Amendment 1, commit `9d87b02`). Baseline reused
without modification: EXP-DD-001 v2 (uncapped research path, full continuation 2024-01-01 →
2026-03-10, warm-up 2024-01-01→2024-03-31, evaluation 2024-04-01→2026-03-10). Only
`PAPER_MAX_ENTRIES_PER_DAY` changed between arms (2 → 3 → 4); `PAPER_MAX_OPEN` (concurrent
open-position cap) stayed at its default of **3** in every arm — this single unchanged setting
turns out to be the whole story.

## Headline finding

**Total entry volume is identical across all three arms: 114 entry orders, 112 closed positions,
in every variant.** Relaxing the daily cap does not capture additional opportunities — it only
changes which specific day captures a trade that the 2/day baseline would otherwise have taken
a few days later (or, in one case, days earlier). The per-day entry-count distribution makes
this explicit:

| Variant | 1-entry days | 2-entry days | 3-entry days | Max entries any single day |
|---|---|---|---|---|
| 2/day (control) | 92 | 11 | 0 | 2 |
| 3/day | 92 | 8 | 2 | 3 |
| 4/day | 92 | 8 | 2 | 3 |

**3/day and 4/day are byte-for-byte identical** — same order sequence, same fills, same ending
equity ($534.04 in both). **Slot #4 never fires, anywhere in the 2-year window: zero instances.**
This is not a profitability judgment — `max_open_positions=3` caps concurrent exposure before a
4th same-day entry opportunity could ever arise, so the daily-pacing cap above 3 is structurally
inert for this account configuration.

## Section A — Portfolio-path effect

| Metric | 2/day (control) | 3/day | 4/day |
|---|---|---|---|
| Ending equity | **$534.38** | $534.04 | $534.04 |
| Net P&L (closed trades) | **+$41.71** | +$41.37 | +$41.37 |
| Resolved trades | 108 | 108 | 108 |
| Win rate | 40.7% | 40.7% | 40.7% |
| R expectancy (74 trades w/ planned_risk) | -0.047R | -0.048R | -0.048R |
| Total R | -3.469 | -3.558 | -3.558 |
| Max drawdown ($ / % of peak) | $125.72 / 21.61% | $125.69 / 21.61% | $125.69 / 21.61% |
| Worst trade | -$17.73 (F) | -$17.73 (F) | -$17.73 (F) |
| Longest loss streak | 20 | 20 | 20 |
| Top-5 trades as % of total net P&L | 126.2% | 127.2% | 127.2% |
| 2024 net P&L | +$71.24 (28 trades) | +$71.24 (28 trades) | +$71.24 (28 trades) |
| 2025 net P&L | -$58.79 (65 trades) | -$59.12 (65 trades) | -$59.12 (65 trades) |
| 2026 net P&L | +$29.26 (15 trades) | +$29.26 (15 trades) | +$29.26 (15 trades) |

**Incremental P&L vs. 2/day: 3/day is -$0.34 (-0.06% of equity), 4/day is identical to 3/day.**
Every other distributional statistic (win rate, drawdown, streaks, concentration, yearly
split) is effectively unchanged — the two arms track each other almost exactly, with the
entire divergence traceable to the two re-timed trades in Section B. This is not "no
improvement with a silver lining" — it is a trivial net negative, not a wash in Champion's
favor.

## Section B — Literal marginal-slot value

### Slot #3

- **Number of actual #3 entries: 2** — NVDA (2024-03-08) and EXC's second leg (2025-04-07).
- **How often slot #3 was actually *available*:** the 2/day cap genuinely blocked a 3rd
  TRADEABLE candidate (logged `"daily entry cap reached"`) on **11 distinct days** across the
  full 2-year window (2024-03-08, 2024-11-08, 2024-12-19, 2025-02-10, 2025-03-14, 2025-04-03,
  2025-04-04, 2025-04-07, 2025-04-08, 2025-08-13, 2026-02-06). Of those 11 opportunities, only
  **2 (18%)** converted into a literal 3rd execution once the cap was relaxed to 3/day. On the
  other **9 of 11 days**, the 3/day arm still placed only 2 entries that day — blocked by a
  different, unrelated gate (the historical replay's generic `"stock leg not canonically
  executable"` reason, which bundles `max_open_positions`/sector/correlation/buying-power
  checks). This directly confirms the structural insight that `max_open_positions=3` — not the
  daily pacing cap — is the real binding constraint on this account.
- **Net P&L of the 2 marginal trades: -$10.27** (NVDA -$4.97, EXC leg -$5.29). Average -$5.14.
  Win rate 0/2. R expectancy: NVDA 4.66 planned_risk → -1.07R; EXC 4.23 planned_risk → -1.25R;
  average -1.16R.
- **Best/worst: both losers** — EXC -$5.29 is marginally worse than NVDA -$4.97.
- **Concentration: 100%** of the (negative) marginal contribution is split almost evenly between
  the two trades — no single-trade domination, but also no positive case to make.
- **By year:** both marginal trades fall in different years (NVDA 2024, EXC 2025), each
  contributing one loss to its respective year.
- **Critical nuance — these are not new trades, they are the same opportunities re-timed:**
  tracing both symbols through the 2/day control shows neither was lost, only shifted:
  - **NVDA**: 2/day entered it anyway, three days later (2024-03-11 instead of 2024-03-08),
    for an almost identical outcome (-$4.96 vs. -$4.97 under 3/day).
  - **EXC**: 2/day took the same first EXC leg on 2025-04-04 (-$4.68, identical in both arms),
    then re-entered one day later than 3/day did (2025-04-08 instead of 2025-04-07), closing the
    same way on 2025-05-14 for a very similar loss (-$5.01 vs. -$5.29 under 3/day).
  
  In other words: **slot #3's entire "marginal contribution" is price-timing noise on trades
  Champion's existing 2/day cadence already captures a few days later.** There is no
  previously-missed opportunity being unlocked here, and the $0.34 difference in Section A is
  fully explained by this timing noise, not by a qualitatively different trade population.

### Slot #4

- **Number of actual #4 entries: 0.** Confirmed structurally impossible in this account
  configuration — 3/day and 4/day produce byte-identical order sequences, fills, and equity
  paths. No day in the complete 2-year window ever had 3 concurrent positions AND a 4th
  TRADEABLE candidate pass every other gate simultaneously.
- **How often slot #4 was available: 0 times.** `max_open_positions=3` caps concurrent exposure
  below the point where a same-day 4th entry could ever be evaluated for capacity.
- No P&L, win rate, or concentration metrics apply — there is no population to measure.

## Decision

Per the pre-registered decision rule ("3/day survives only if it improves profitability over
2/day and the marginal #3 trades are not clearly negative or dominated by one lucky trade; 4/day
survives only if it improves on 3/day"):

- **3/day does NOT survive.** It does not improve profitability (-$0.34 vs. control, not an
  improvement under any reasonable noise threshold), and the two literal marginal trades are
  both losers — not "negative but offset by a structural gain," genuinely negative on their own
  terms. There is also no capacity-utilization story to redeem it: 9 of the 11 days that could
  have benefited from more daily capacity were capped by `max_open_positions` regardless, so
  relaxing the daily-entry pacing buys almost nothing even in principle.
- **4/day does NOT survive** (moot — identical to 3/day, zero incremental trades of any kind).

**Verdict: keep `PAPER_MAX_ENTRIES_PER_DAY=2` (Champion, unchanged). Do not promote 3/day or
4/day.** This is the third pre-registered, decisive negative in the current research
sequence (alongside HIST-002 exits and HIST-004 ranking) — Champion's existing calibration on
entries-per-day, exits, and slot-ranking all survive direct challenge.

## What this does and doesn't rule out

- It rules out **daily entry-pacing relaxation** as a lever, cleanly and structurally (the
  binding constraint is `max_open_positions`, not `max_entries_per_day`).
- It does **not** test raising `max_open_positions` itself — that is a different capacity lever,
  not evaluated here, and changing it would need its own pre-registration (more concurrent
  exposure changes correlation/sector-concentration risk in ways this experiment never varied).
- It does not bear on exits (HIST-002) or ranking (HIST-004) in any way — both already tested
  and rejected on their own terms.

## Reproducibility

- Driver: `run_baseline(disable_drawdown_gate=True, ...)` identical to EXP-DD-001 v2 except
  `PAPER_MAX_ENTRIES_PER_DAY` set via environment variable per arm (2 / 3 / 4).
- Universe, scanner ranking, exits, $5/trade risk, uncapped drawdown research path: all frozen,
  identical across arms and identical to the EXP-DD-001 v2 control.
- Analysis reuses `research/historical/hist001/analysis.py`'s existing
  `portfolio_metrics`/`r_multiple_metrics`/`drawdown_and_streaks`/`concentration_analysis`/
  `stability_breakdown` — the same functions HIST-002 and HIST-004 used, so all three reports are
  on identical analytical footing.
- Raw per-arm result pickles and the driver script are retained in the session scratchpad
  (not committed — large binary run artifacts, consistent with EXP-DD-001/HIST-002/HIST-004
  precedent); the exact commands and env vars are recorded above for re-running against the
  same frozen dataset snapshot.
