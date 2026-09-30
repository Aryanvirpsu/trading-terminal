# HIST-003 — daily entry-capacity sweep (pre-registration)

Registered 2026-09-30, BEFORE the `hist001_full_2024_2026_corrected` replay's results have been reviewed for
this purpose. Status: **provisional / exploratory**. Unlike HIST-002/HIST-004, this hypothesis has no prior
live pilot (`experiments/ch00X_*`) to extend — it is registered here directly from the user's own scope
statement (2026-09-30): "capacity experiment: 2 vs 3 vs 4 entries/day, keeping risk per trade fixed and
comparing total risk, drawdown, concentration, and incremental expectancy." HIST-003 was referenced only as a
bare placeholder in the original HIST-001 directive's STOP condition with no defined scope anywhere in this
repo until that statement; this file is that definition, committed before any result.

## Hypothesis

Champion's current daily entry cap (`max_open_positions=3` opened per day per `PAPER_500_ACCOUNT.md`/
`canonical.risk_policy.STRATEGY_500_POLICY`) may be leaving expectancy on the table if genuinely eligible,
uncorrelated TRADEABLE candidates are being capacity-blocked on days when more than the cap's worth of real
opportunity exists — or it may already be well-calibrated, with a higher cap mostly adding correlated/lower-
quality exposure rather than independent expectancy. Per-trade risk stays fixed throughout (this experiment
never asks whether to risk more per trade, only how many independent trades per day to allow).

## Variable changed (only this)

The daily entry-count cap: **2**, **3** (Champion, unchanged), and **4** entries/day. Per-trade risk budget,
sizing formula, sector cap (1/sector), stop/target logic, fees/slippage, and every entry/exit decision
Champion already made are held fixed — this is a capacity-ceiling counterfactual, not a re-ranking or a
re-sizing experiment (that is HIST-004's and a separate, not-yet-proposed sizing study's territory,
respectively).

## Method

Re-run capacity admission ONLY as a counterfactual over `hist001_full_2024_2026_corrected`'s own already-
captured decision-time record (`decision_capture`, the same full per-symbol detail
`capacity_opportunity_cost()` already reads) — never a re-decision of which candidates were eligible or how
they were ranked. For each evaluation-phase day, take the REAL eligible-and-ranked TRADEABLE queue Champion
actually produced that day (same order, same eligibility) and admit up to N (2, 3, 4) instead of Champion's
real admission count, at Champion's real per-trade sizing. A candidate newly admitted under N=4 that
Champion's real N=3 run never entered has its outcome resolved via `resolve_hypothetical()` (H6, the same
machinery `capacity_opportunity_cost()` already uses for blocked-TRADEABLE candidates) — never a re-run of
the replay, never a change to Champion's real recorded trades at N=3.

## Metrics

Per N ∈ {2, 3, 4}: total portfolio net R and net $ (Champion's real trades at N=3, hypothetically-resolved
additions/removals at N=2/N=4), total dollars of simultaneous open risk (position count × fixed per-trade
risk, the literal "total risk" the user's scope statement asks for), max drawdown (via `drawdown_and_streaks`'s
existing equity-path method, extended to the counterfactual trade set), concentration (`concentration_analysis`'s
existing top-5/top-10-share method, extended the same way), and **incremental expectancy**: the mean R of
ONLY the trades that exist at N but not at N−1 (e.g., the 4th-admitted trade's own expectancy in isolation,
separate from the first three) — this isolates whether relaxing the cap adds good, mediocre, or negative
marginal opportunities, not just whether the aggregate total goes up.

## Success / failure criteria

- **Directionally supported (raise the cap):** N=4's incremental trades (the ones N=3 didn't take) have
  expectancy_r ≥ N=3's own expectancy_r, AND N=4's max drawdown does not exceed N=3's by more than the
  literal extra risk dollars one more concurrent position adds (i.e. the extra risk is compensated by real
  edge, not just more exposure), AND N=4's top-10 concentration share does not meaningfully worsen.
- **Directionally supported (lower the cap):** the symmetric case — N=2's excluded trades (the 3rd slot
  Champion actually used) show expectancy_r below N=2's own remaining-trade expectancy, suggesting the
  3rd slot is diluting rather than adding.
- **Not supported / keep N=3:** incremental expectancy at N=4 is materially worse than N=3's own expectancy,
  or N=2 loses expectancy relative to N=3 (the marginal 3rd slot is at least as good as the rest).
- **Cannot promote from this data alone**: same standing rule as HIST-002/HIST-004 — any cap change requires
  forward/live shadow evidence before a real Champion parameter changes, regardless of this experiment's
  direction. This experiment answers "is it worth building that live shadow," not "change the cap now."
- **Kill:** incremental expectancy is not distinguishable from zero at any N (no directional signal either
  way) given the sample size available.

## Known limits (stated in advance)

N=4's hypothetically-admitted trades depend entirely on H6's reference-price fill assumption (no spread
modeled for the hypothetical leg, same disclosed limitation `capacity_opportunity_cost()` already carries) —
these are therefore weaker evidence than Champion's own real N=3 trades, and the report will say so plainly
rather than presenting hypothetical and real trades as equally reliable. Sector-cap interaction (1 slot/
sector) means a 4th admission on a day already holding one position per sector may be capacity-blocked for a
DIFFERENT reason than the daily-count cap — this experiment counts only days where the marginal slot was
blocked specifically by the daily-count cap, not by the sector cap, and reports how many candidate days were
excluded for that reason so the N=4 sample size is stated honestly, not inflated.
