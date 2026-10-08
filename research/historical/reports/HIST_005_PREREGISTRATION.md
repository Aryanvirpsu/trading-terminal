# HIST-005 — concurrent position capacity (3 vs. 4 open positions)

Prepared **2026-10-08**, before any HIST-005 replay or outcome analysis.
Status: **FINAL AS AMENDED BELOW — DOCUMENT ONLY; NOT RUN; NOT PROMOTED**.

**Effective version: Amendment 1 (2026-10-08).** The original registration was
committed as `7dde0ca`. Its text remains below for the audit trail. Amendment 1
supersedes the original decision rules and stopping rule, and clarifies measurement
and the research queue. Unchanged arms, data, controls, and isolation requirements
remain binding. Finalizing this document does not launch an experiment.

This document reserves HIST-005 for the concurrent-position-cap experiment. It
does not implement a Challenger, launch a replay, change the Champion, or authorize
deployment. Commit this registration and any explicitly dated amendments before
the first experiment run. Record the registration commit in every result artifact.

## Motivation and hypothesis

`HIST_003_RESULTS.md` reports that relaxing daily entries from 2 to 3 reduced ending
equity by $0.34, while 3/day and 4/day produced identical paths. The concurrent cap
remained 3 in every arm. This identifies a constraint worth testing, not evidence
that relaxing it is profitable. Several other risk gates share a generic rejection
message, so individual capacity blocks must be attributed rather than assumed.

Hypothesis: allowing a fourth concurrent stock position, while retaining two new
entries per day and all other account controls, admits enough independently useful
opportunities to improve net account results without unacceptable extra drawdown
or concentration. The null is that the extra slot is unused, merely retimes trades,
or adds exposure without improving economic quality.

## Arms and fixed account controls

| Setting | Control | Challenger |
|---|---|---|
| Run ID | `hist005_open_3_capped` | `hist005_open_4_capped` |
| Concurrent open-position cap | **3** | **4** |
| New entries per day | **2** | **2** |
| Starting cash / equity | **$500** | **$500** |
| Absolute planned-loss cap per trade | **$5** | **$5** |
| Position notional cap | **$125** | **$125** |
| Minimum cash reserve | **$100** | **$100** |
| Daily loss gate | **$10** | **$10** |
| Drawdown gate | **$50, enabled** | **$50, enabled** |
| Positions per sector | **1** | **1** |
| Correlated-position limit | **2** | **2** |
| Existing additional percentage limits | **1% per trade; 30% sector exposure** | Same |
| Cooldown | **3 losses / 1 day** | Same |
| Fractional shares | **Enabled** | **Enabled** |
| Margin / borrowing / shorting | **Disabled** | **Disabled** |
| Options | **Shadow only** | **Shadow only** |

Only `max_open_positions` differs. The existing sizing formula uses the tighter of
the $5 absolute cap and the percentage limit; neither is relaxed. Initial usable
cash is $400. Four $125 positions are not automatically affordable: cash reserve,
realistic fill cost, and every other gate still apply. No deposits, equity resets,
forced liquidation to make room, or preferential sizing of slot four are allowed.

At the absolute per-trade ceiling, four concurrent positions can carry $20 of
initial planned risk versus $15 for three. This is an explicit increase in possible
aggregate exposure. Planned risk is not a guaranteed realized-loss bound; gap and
slippage losses must remain visible.

**Baseline distinction:** HIST-002/003/004 used the EXP-DD-001 continuation with
`disable_drawdown_gate=True`. That uncapped research path is not this control.
Both HIST-005 arms use `disable_drawdown_gate=False`. A lockout is an outcome of
the registered policy, not permission to disable the gate. An uncapped follow-up
would require a separate, prospectively recorded amendment or experiment.

## Frozen implementation and data

Research source: `h1/historical-lab` at
`1de0893a4db58b906269fd7783ac24005305a7d1`. This registration adds no runtime fix
from PR #9 or #10 to that source. Before execution, record the exact implementation
commit and show that changes from the pinned source are limited to the isolated
capacity adapter and observational recording. Any necessary correctness change
must be documented before either arm runs, applied equally, and tested separately.

Reuse the real scanner, decision engine, canonical stack, sizing, paper risk checks,
broker, fills, and Historical Lab clock/provider. Preserve existing ranking, entry
eligibility rules, stops, targets, exits, realistic cost model, confirmed-split
handling, freshness rules, event identity, and volume-trust restrictions.

| Input | Frozen value |
|---|---|
| Daily dataset | `HIST001_FULL_2024_2026_DAILY` |
| Intraday dataset | `HIST001_FULL_2024_2026_5M` |
| Equity source revision | `f17c0b0c3cf6a455994f93d6a85e76274172ab03` |
| Daily Parquet SHA-256 | `076c62a9db45e7eeb5c8749b1fb2601c67d3c192844a03230117037350ec0361` |
| Intraday Parquet SHA-256 | `30f98cbad7b1ee2eafa905089f6cabfcbd91e2b8018775f88cdf79a6e7b550c2` |
| Macro dataset | `HIST001_FULL_2024_2026_MACRO` |
| Macro Parquet SHA-256 | `04fab584010dbb01e4f7bca008b7d91a9e86da588187c5bb86610d721b49b519` |
| Capability | `PRICE_TREND_MACRO_V1` |
| Equity volume trust | `RELATIVE_ONLY` |
| Universe | Same full-dataset symbol list and all 11 HIST-003 sectors |
| Warm-up | `2024-01-01` through `2024-03-31` |
| Evaluation | `2024-04-01` through `2026-03-10` |
| Split-aware replay | `split_aware_diagnostic=True`, identical in both arms |

Hashes above are recorded from the existing manifests, not newly verified by this
document-writing task. Verify actual input files against them before execution.
Use the existing multi-day schedule and vintage-aware macro history. No network
data refresh or replacement dataset is part of this registration.

The window has already informed earlier research. Label this experiment
**exploratory on previously examined historical data**, never a fresh holdout or
out-of-sample validation. Any later forward or untouched-data test needs its own
pre-registration; it cannot retroactively make this window out of sample.

## Causal replay and implementation prerequisites

Run two complete, independent sequential account paths with separate historical
ledgers and identical initial conditions. Do not append hypothetical winners to
the control's trade list. A fourth position changes cash, marks, later admissions,
cooldowns, drawdown, and possible exits; all downstream decisions must see their
own arm's actual state. Decision rules are fixed, but account-dependent eligibility
and trade quantities can legitimately diverge.

The count is enforced in two places:

1. `lab/paper/config.py::risk()` reads `PAPER_MAX_OPEN`, consumed by
   `lab/paper/risk.py::check_entry()`.
2. `canonical/account_fit.py::stock_account_fit()` checks the policy count;
   `lab/paper/canonical_bridge.py` imports `STRATEGY_500_POLICY` directly.

A future research-only adapter must set the effective count to the arm's value in
both paths, including the bridge's imported policy reference, and restore every
override on exit. Setting only `PAPER_MAX_OPEN=4` is insufficient. Do not edit
production modules or persistent account configuration to achieve this.

Before the full replay, require targeted evidence that: the fourth entry passes
when all other gates pass; a fifth fails; daily entry three still fails; cash,
sector, correlation, daily-loss and drawdown gates remain active; and all patches
restore after normal exit and exceptions. Preserve production-isolation guards.
Check control-path parity and deterministic accounting before treating results as
usable. These are future execution prerequisites, not tests claimed completed here.

Warm-up trades and state accumulate under each arm, as the existing harness does;
neither account resets on April 1. Report evaluation-start equity and open positions.
Use evaluation-phase closed trades for trade statistics. Primary performance is
evaluation equity change (`final equity - evaluation-start equity`), so pre-window
gains are not silently counted. Also report ending equity relative to the original
$500, warm-up results, and outcomes of positions crossing the evaluation boundary.

## Attribution and required metrics

Record every decision-time cap block with the effective limits, account state,
other gate results, and event identity. Distinguish a count-only rejection from
one also blocked by cash, sector, correlation, daily loss, drawdown, or cooldown.
Count independent events, not repeated scans of one persistent signal.

Classify divergent trades as: present in both arms with unchanged timing; retimed
or resized; newly admitted; displaced/lost later; or unresolved at the boundary.
For literal slot-four entries, record that three positions were already open
immediately before admission. This cohort can include retimed events: do not call
every fourth-slot fill an entirely new opportunity. Show both populations.

Report, per arm and as paired differences:

- Evaluation equity change and ending equity; realized P&L and unrealized P&L
  separately, with fill-cost and cash reconciliation.
- Entries, resolved trades, independent events, open positions at the boundary,
  slot-four fills, count-only blocks, and rejections by every other gate.
- Win rate, total R, mean R expectancy, worst trade, longest loss streak, and
  missing/invalid planned-risk counts. Use recorded decision-time planned risk;
  never substitute $5 to manufacture a missing R denominator.
- Maximum marked-equity drawdown over every recorded replay cycle, including
  warm-up and open losses, plus evaluation-only drawdown. Show legacy closed-trade
  drawdown separately if reported; do not confuse the two series.
- Peak and typical simultaneous planned-risk dollars, notional exposure, cash
  reserve, correlated-position count, and sector exposure. State how risk on an
  open position is measured; do not equate a moved stop with guaranteed protection.
- Marginal slot-four and newly admitted event outcomes separately; retiming and
  downstream displacement contributions; unresolved cohort counts.
- Results by evaluation year (2024 partial, 2025 full, 2026 partial), quarter,
  sector, and strategy; top-five share of gross positive realized profits. If no
  positive profits exist, concentration is undefined, not zero.

Retain raw arm artifacts, effective configuration and policy snapshots, code and
registration commits, dataset hashes, run commands, and the complete paired report.
Missing gate attribution or marked-equity data makes the relevant conclusion
unavailable; it is not permission to infer favorable values.

## Decision rules fixed before results

The thresholds below are new experiment design choices, not previously established
statistical significance levels. Apply all of them; do not select the favorable
metric or subset after inspecting outcomes.

**Invalid evidence:** any unexplained reconciliation failure, isolation violation,
lookahead, favorable fill-model change, uncontrolled configuration difference, or
unverified input mismatch. Diagnose separately; do not use that run to rank arms.

**Inert:** no evaluation-period fourth-slot fills. Keep three; report which other
constraint binds. Do not expand the sweep to five or relax a second gate.

**Not supported:** valid Challenger evaluation equity improvement is less than
$5, whole-run marked drawdown worsens by more than $5, or a gradeable marginal
slot-four cohort has nonpositive R expectancy. Report the magnitude and sample
uncertainty even when rejecting the arm.

**Directionally supported for a separately registered validation study** requires
all of the following:

1. At least $5 more evaluation equity gain than control, with positive Challenger
   evaluation equity gain after the unchanged fill costs.
2. Whole-run marked drawdown no more than $5 above control, with the original $50
   admission gate continuously enforced. Any gap-driven gate overshoot is disclosed
   and audited; $50 is not represented as a guaranteed maximum realized drawdown.
3. At least 30 distinct, resolved evaluation-period events contributing literal
   fourth-slot entries with valid planned risk. Their event-weighted mean R is
   positive and at least control's event-weighted mean R on the same gradeable basis.
   Aggregate repeated legs within an event using total net P&L / total planned risk.
4. Positive paired evaluation equity contribution in at least two evaluation years.
   Attribute all realized trade deltas by event, including displaced trades. Removing
   the single largest positive event contribution must leave the overall evaluation
   equity advantage positive. Report residual differences from unrealized marks.
5. Top-five gross-profit concentration worsens by no more than five percentage
   points, and every unchanged account control remains effective.

**Insufficient evidence:** some economic criteria pass but the independent-event
sample, risk provenance, attribution, concentration denominator, or resolution
coverage is inadequate. This is not a pass and does not authorize expansion.

**Promotion:** no result from HIST-005 alone changes production or the Champion.
Directional support only proposes a separately pre-registered prospective
validation with adequate independent outcomes and the same account controls.
Actual promotion remains subject to the supplied AGENTS.md build freeze,
`PAPER_GRADUATION_CHECKLIST.md`, and a separate explicit deployment decision.

## Scope and stopping rule

Two arms only: three versus four concurrent positions. Complete and report both
paths without tuning ranking, exits, sizing, regimes, daily pacing, or other risk
limits. Record failures and incomplete runs. Amend prospectively with the reason
and whether any outcomes were already seen; never overwrite this registration to
fit a result. A regime-throttle study remains separate and unstarted.

This task ends with the document. No driver, replay, ledger mutation, merge, push,
workflow dispatch, or production verification is performed as part of it.
`ROBINHOOD_TRADING_ENABLED=false` and `BROKER_PROVIDER=none` remain required.

## Amendment 1 — final accuracy and research-speed rules (2026-10-08)

Written after reviewing the registration and implementation, before any HIST-005
driver, replay, or outcome analysis. This amendment responds to the user's request
to finalize an accurate plan that finds a profitable strategy as quickly as the
evidence permits. It changes the research decision process, not the two arms or
their fixed account controls. Record this amended registration's commit before
running either arm.

### A1.1 What the existing evidence actually establishes

- HIST-002 exits, HIST-004 ranking, and HIST-003 daily pacing did not beat their
  respective controls under the tested definitions. This does not prove the
  Champion is profitable prospectively, or that every alternative exit/ranking
  policy would fail. Do not reopen these same arms with different weights.
- HIST-003 establishes that a daily cap above three was inert while the concurrent
  cap remained three. Generic canonical rejections do not prove every remaining
  miss was caused solely by the concurrent cap. HIST-005 must identify the real
  blocking gates and exercise the fourth slot before making a capacity claim.
- HIST-003's uncapped control reports +$41.71 of evaluation-position realized P&L,
  $534.38 ending equity for the full account path, and -0.047R mean expectancy on
  74 gradeable trades out of 108 resolved evaluation trades. These are different
  measurements and populations; do not conflate $41.71 with the $34.38 whole-path
  equity increase, or the partial R sample with all trades.
- The reported $125.72 drawdown uses
  `hist001/analysis.py::drawdown_and_streaks()`: a closed-trade realized-P&L curve,
  not a complete marked-equity curve. It can understate open-position losses.
  Neither that number nor a positive dollar result establishes a usable edge for
  the fixed $500 account. A losing control may still be useful for comparing arms.

Capacity is a bounded research question, not an assumption that more trading
creates profit. The objective is positive, repeatable net dollar expectancy within
the unchanged cash-account controls, supported by adequate risk-normalized and
prospective evidence. No plan can guarantee a profitable result or a completion
date for that discovery.

### A1.2 Minimal execution sequence when a run is separately requested

1. **Inspect existing artifacts first.** Check availability, code/config identity,
   hashes, count-gate attribution, and replay timing. Use existing capped-control
   artifacts only if their provenance and required measurements match exactly.
   The uncapped EXP-DD-001 result cannot substitute. Missing artifacts or generic
   rejection messages are unknowns, not evidence of zero opportunity.
2. **Prove mechanics on targeted fixtures and a short schedule.** Verify both cap
   gates, unchanged risk controls, cleanup, accounting, and recording. Smoke
   results establish mechanics only. Do not select a favorable time slice to
   screen profitability or build a new general-purpose research framework.
3. **Run at most one full control and one full Challenger.** Reuse a compatible
   control if verified; otherwise regenerate it. Prefer an already verified fast
   engine when parity covers this configuration. Do not spend a full reference
   replay merely to repeat an established equivalence; check the affected seams.
   A same-hash dataset and a faster implementation alone do not prove parity.
4. **Write one paired report and decide.** No 3/4/5/6 sweep, fitted ranking formula,
   new provider, or second risk lever in this experiment. Do not rerun unchanged
   full paths after checks pass. Implementation failures may be repaired and
   rerun with provenance; unfavorable strategy outcomes may not be tuned away.

There is no economic early-stop based on interim P&L. Finish the registered paths
unless integrity fails or a deterministic no-effect proof is available. Such a
proof requires complete chronological coverage: before any first arm divergence,
the control states and decisions are identical and the count cap never uniquely
blocks an otherwise executable entry, including warm-up. If so, changing that cap
cannot create a different path. If the necessary gate evidence is absent, do not
claim this proof. A partial negative result is not a completed experiment.

### A1.3 Exact measurement corrections

The frozen runner computes `account_snapshot` after premarket and market-hours
work, then stores its equity in decision records named `account_equity_before`.
Despite the name, that is not a reliable pre-admission snapshot. It also does not
persist a complete account mark in every cycle. A minimal research-only recorder
must capture actual pre-admission state for gate attribution, an opening evaluation
mark before the first evaluation cycle acts, and an end-of-cycle mark on every
cycle, including cycles without candidate evaluations. Preserve execution order
and all real decision functions; recording must never change eligibility or fills.

Use the same frozen historical prices and marking convention in both arms. Define
evaluation gain as final marked equity less the opening evaluation mark. Calculate
sampled marked drawdown as the largest running-peak-minus-equity decline, including
the $500 initial equity in the full-path series. Label sampling frequency: marks
at scheduled cycles still do not measure every intrabar adverse excursion. Yearly
equity contributions are changes between successive year-boundary marks, with the
partial first/last periods explicitly identified. Reconcile boundary marks with
realized and unrealized contributions; do not silently use the last warm-up mark
as an April opening mark when overnight prices changed.

For trade metrics, evaluation trades mean positions **opened** during the
evaluation phase, with closed and unresolved positions reported separately.
Report inherited warm-up positions separately. Whole-account evaluation gain
includes their subsequent changes in value; trade-level realized P&L need not
equal that gain. Preserve the registered warm-up behavior in both arms.

For R, retain the recorded entry-time planned risk, initial stop, quantity, and
modeled costs. If the existing provenance gap persists, capture those values
observationally when the order is created; do not backfill them from a later moved
stop. Missing R values remain missing and the coverage is reported for both arms
and the marginal cohort. Whole-population dollar results do not cure incomplete
normalized-risk evidence.

Event IDs are local to each arm and can diverge after a retimed close/re-entry.
Match decisions by `(cycle_id, symbol, direction)` and document deterministic
episode matching before looking at outcomes. Unmatched episodes stay explicit;
never join trades on symbol alone or choose matches by similar final P&L.
Distinct episode counts reduce repeated-scan inflation but do not guarantee
statistical independence from common market conditions.

Removing the largest event's attributed profit is an arithmetic sensitivity
diagnostic, not a causal replay of a portfolio that never entered that trade.
Include displaced-trade losses and disclose unmatched/unrealized residuals rather
than assigning the entire advantage to literal fourth-slot winners.

### A1.4 Effective decision rules — replaces the original decision-rule section

Apply in this order. Thresholds of $5 and five percentage points are prospectively
chosen economic screening tolerances, not statistical significance or power
calculations. Use unrounded values for comparisons and round only for display.

| Result | Required evidence and next action |
|---|---|
| **INVALID** | Integrity, provenance, effective controls, accounting, or required measurement is unreliable. Diagnose the specific defect; do not rank the arms. |
| **INERT** | Full replay or the complete no-effect proof shows the changed cap never affects admission. Keep three and close this lever. Zero evaluation fourth-slot fills alone is insufficient if warm-up paths diverged. |
| **NOT SUPPORTED** | Valid paired evaluation gain improves by less than $5; or Challenger evaluation gain is nonpositive; or sampled full-path marked drawdown worsens by more than $5; or a fully gradeable resolved fourth-slot cohort has nonpositive mean R. Keep three and close this lever under this definition. |
| **TENTATIVE / DATA-POOR** | Dollar and drawdown screens pass, but resolution, normalized-risk coverage, episode matching, or concentration evidence cannot support a reliable comparison. Record the uncertainty and the specific additional evidence needed; no production change. |
| **WORTH PROSPECTIVE VALIDATION** | All dollar/drawdown screens pass; the resolved fourth-slot cohort has complete planned-risk provenance and positive event-weighted mean R at least as high as control's gradeable event-weighted mean R; top-five gross-profit share worsens by no more than five percentage points; the positive paired gain survives removal of the largest positive attributed event contribution; and no unchanged risk control is bypassed. This is a research signal, never a profitability or promotion verdict. |

Report event counts, resolution rates, risk-data coverage, yearly/quarterly and
sector breakdowns for every result. Sparse or concentrated results remain
explicitly exploratory. Positive contribution in two years is useful diagnostic
evidence, not a requirement to wait for two new calendar years or a second
definition of success.

**Remove the original 30-event gate from historical research triage.** It was an
operational number, not a statistical justification, and could prevent a cheap
experiment from yielding a useful research decision. Do not wait to accumulate
30 live fourth-slot trades just to decide what to investigate next. Equally, two
winning events are not proof of an edge. The prospective study must pre-register
its sample/precision requirements and stopping rule, count all attempted variants,
and retain unspent validation data. No automatic promotion occurs at any count.

The supplied AGENTS.md freeze and graduation requirements still govern production
changes; the conflicting PROFIT MODE text in the branch's CLAUDE.md does not
silently override the user's supplied instructions. Historical-only research and
document work do not modify the frozen pipeline. Live brokerage execution remains
a separate, unauthorized decision.

### A1.5 Queue ordered by the next useful decision

After the bounded HIST-005 screen, prioritize a separately pre-registered
**regime-conditioned risk-throttle** study if capacity is inert, unsupported, or
too sparse to justify further work. The motivation is the existing loss evidence:
HIST-003's uncapped control reports -$58.79 in 2025 and a 20-trade loss streak.
This makes loss attribution worth investigating; it does not establish that a
decision-time regime feature could have predicted those losses.

First inspect existing records for decision-time features and loss concentration.
Use year/sector attribution to locate a question, never a retrospective rule such
as "skip 2025." Then register one simple, fixed rule using information available
before each decision, one unchanged control, and explicit net-profit/drawdown
criteria. A throttle may reduce risk; it must not raise the account's limits.
Compare full causal account paths with the $50 drawdown gate retained, and reject
a zero-trade policy as a profitable strategy. This amendment does not choose or
run the throttle rule.

If HIST-005 yields a credible validation candidate, choose that validation versus
the throttle using the observed economic effect, unresolved uncertainty, and
cost of the next informative test. Do not run a long capacity evidence-collection
campaign merely because a cap was mechanically binding. Avoid new models, UI work,
and infrastructure unless a concrete blocker prevents the next valid comparison.

Finish each study with one recorded decision: close it, resolve a named data gap,
or register a prospective validation. Keep one strategy hypothesis active at a
time. An untouched-data result and prospective paper evidence must support any
claim of repeatable profitability; neither a positive reused backtest nor a fixed
trade count proves it. Continue the existing Champion measurement without silently
changing its logic, and report fixed-policy failures honestly.

Repeated selection on the same history is a known source of backtest overfitting;
ordinary holdout methods alone do not automatically correct strategy-selection
bias. This supports the limited search and explicit validation boundaries above:
Bailey, Borwein, Lopez de Prado and Zhu,
[The Probability of Backtest Overfitting (2017)](https://scholarworks.wmich.edu/math_pubs/42/).
No new cross-validation infrastructure is required by this document.

### A1.6 Final scope

Finalization consists only of this amendment and document checks. No driver,
replay, new strategy, ledger update, deployment, or fresh production verification
is authorized or performed by this finalization. The next experiment remains
unstarted. Record the amended registration commit before a separately requested
run; do not report that commit as existing until it has actually been created.
