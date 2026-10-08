# HIST-005 — concurrent position capacity (3 vs. 4 open positions)

Prepared **2026-10-08**, before any HIST-005 replay or outcome analysis.
Status: **DRAFT PRE-REGISTRATION — DOCUMENT ONLY; NOT RUN; NOT PROMOTED**.

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
