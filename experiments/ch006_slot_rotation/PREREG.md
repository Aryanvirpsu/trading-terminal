# CH-006 — Slot rotation: replace a stalled position only when an eligible signal is waiting (pre-registration)

Registered 2026-11-03, **BEFORE any CH-006 outcome column, replay output or Challenger-arm number exists.**
Status: **research Challenger, offline replay only. Cannot promote. Never touches the live paper ledger, the Champion, or any open position (including UNH).**
Champion under test: `decision_engine/gates-v1.1`, config `cfg-a0eede144e`, runtime `54adbea27af62e6835656807684d19033a95abf7` — **frozen, never edited by this experiment.**
Integrity: this file's sha256 is recorded in `PREREG.lock.json`. Every script in this folder must refuse to run if it differs.

## Question
When the account is full, does replacing a **stalled** position with an otherwise eligible waiting signal improve the **whole account's** net result,
under the same $500 cash account and the same risk limits?

The Champion stays at **three** positions. A fourth slot is out of scope and is not registered here. It would be a separate Challenger,
considered only after this one has been read.

## What the evidence does and does not say (stated before any test)
* The audit found `max_open_positions` as the binding capacity constraint in **65 of 65** logged `TRADEABLE` canonical evaluations (2026-10-27 to 2026-11-02).
* Those 65 are **evaluations** (repeat cycles, shadow rows), not 65 distinct missed entries. The `canonical_evaluated` rows are keyed by shadow
  candidate id, not by signal id, so they could not be matched one-to-one to signals. They show a **recurring bottleneck**. They do **not** show
  how many distinct entries were missed, and they do **not** show that any replacement would have done better than the incumbent.
* The 6 signals blocked on 2026-10-26 were blocked by the **daily entry cap (2 per day)**, a different constraint. Rotation would not have helped them (see below).
* UNH (open since 2026-10-15) is used **only** to illustrate how long a slot can stay occupied. Its holding period and cost are not evidence that
  exiting earlier would have helped. The ledger's MFE/MAE columns have unverified units and are **not used anywhere in this test** (+1R touches are recomputed from bars).

## Arms (offline replay of the same candidate population)
| Arm | Rule |
|---|---|
| **CONTROL = CHAMPION** | unchanged. Exits only at stop or target. |
| **ROT** | identical to CONTROL plus the single rotation rule below. |

Held fixed and identical in both arms: the candidate population and its decisions (the logged `TRADEABLE` decision in `regular`, entry-eligible
cycles), Champion candidate order (`scanner_rank`), sizing, fills, stops, targets, slippage model, and **every** production risk check
(3 open positions, 1 per sector, sector exposure cap, correlated-position cap, 2 entries/day, $5 max loss per trade, $125 position cap,
**$100 cash reserve**, $10 daily loss limit, $50 drawdown halt, loss-streak cooldown, no duplicate symbol, no re-entry of a symbol already entered that session).
The reserve, the $125 cap and the risk limits are **not** relaxed for the Challenger. No scoring, conviction, gate or threshold change of any kind.

## The one rotation rule
A rotation is evaluated at a `regular`, entry-eligible cycle (before the 15:00 ET entry cutoff), only for a candidate **C** that is blocked by capacity.

1. **Candidate eligible.** C is `TRADEABLE`, fresh quote, canonically executable as a stock **except** for a capacity check
   (`max_open_positions`, `max_positions_per_sector`, `max_sector_exposure`), and C passes **every** other fit rule: not blocked by the daily entry cap,
   not a symbol already held or entered today, not a symbol sold by rotation within 10 sessions, daily-loss, drawdown and cooldown checks clear,
   and the sizing/cash test below passes. Candidates blocked by the daily entry cap are **never** rotation-eligible (the replacement is itself an entry and would hit the same cap).
2. **Incumbent stalled.** An open incumbent I is *stalled* only if **all three** hold at the decision cycle:
   * age: at least **K = 10** completed sessions since its entry session (entry session = session 0);
   * it has **never touched +1R** (any 5-minute bar high ≥ fill + R since entry, with R = fill − stop; the Champion's actual fill price);
   * it is still open (stop and target not reached).
   Age alone never triggers anything. K = 10 is the most conservative value of the earlier CH-003 curve (fewest rotations). It is fixed now and is **not** tuned.
3. **Removal must unblock C.** Removing I must make C pass all capacity checks (open count, sector count, sector exposure). If several incumbents are stalled and qualify,
   take the one with the **earliest entry**.
4. **Cash must work.** After the sale of I (under the settlement model below), `cash − $100 reserve` must cover C's sized order
   (`qty = min($5 / (fill − stop·(1−slip)), $125 / fill, buying_power / fill)`, buying_power = settled cash − $100).
5. **One rotation per cycle**, in Champion order (first qualifying C). The replacement counts against the 2-entries/day cap. The sale does not.
6. **No churn.** A symbol sold by rotation cannot be re-entered by either arm's logic for 10 sessions in the Challenger arm.
7. A rotation sale that realizes a loss counts exactly like a stop-out for the daily loss limit and the loss-streak cooldown.

**Execution.** I is sold at the open of the first 5-minute bar that starts at or after the decision cycle, at `open × (1 − slippage)`.
C is bought at the **first `regular` entry-eligible cycle at or after the moment the proceeds are available**, at `quote_ask × (1 + slippage)`, offered in
Champion order together with every other candidate at that cycle (C is not guaranteed; the normal Champion entry logic applies). If no entry follows the sale within the next session, the rotation is
recorded as **orphaned**. Its cost stays in the account result. Nothing is excluded.

## Costs and cash settlement (included in both arms)
* **Slippage:** base 5 bps buy/sell, gap 25 bps. Sensitivity 15 bps (gap 35) and stress 30 bps (gap 50). Fees: none (commission-free, as configured). Costs apply to the incumbent's exit **and** the replacement's fill.
* **Settlement S1 (primary, gating):** proceeds of **every** sale (stop, target or rotation, in **both** arms) are unavailable for buying until the first regular entry-eligible cycle of the **next** session
  (T+1). New buys are funded only from settled cash. The paper broker does not model settlement (it frees cash instantly), so S1 is an explicit stricter-than-ledger assumption for a real Robinhood cash account.
  **I could not verify the real account's settlement terms from the repo** (`PAPER_500_ACCOUNT.md` does not state them). If the owner's actual rule differs, the S1 definition must be amended **before any outcome is read**.
* **Settlement S0 (sensitivity, non-gating):** instant release, which is what the live paper broker does. S0 is the model used for the Stage A validity check.
* **Terminal valuation (both arms, identical):** open positions at window end are valued `last 5-minute close × (1 − slippage)` (mark-to-liquidation), so the arm that realized its costs earlier is not penalized relative to the arm that has not paid them yet.

## Data and state
* **Source:** the Ubuntu host's append-only ledger and shadow DB (`robinhood_500_baseline.db`, `shadow_candidates.db`: `shadow_cycles`, `shadow_candidates`, `shadow_bars_5m`), read from the **nightly off-host-verified backups**.
  The backup used for evaluation is recorded with its sha256 in `RESULTS.md`. Lock-time reference snapshot: backup `20261102T212501Z`, GitHub artifact `avdi-…-backup-34`, zip digest `617f6bf75346210810fa1db39557947f70ed33c03ed9571fae34f8121875f940`.
* **Starting book (both arms, identical):** reconstructed from the ledger's own fills and orders dated before the window start. At lock time that is UNH, CVX, SO and cash $136.21 (to be confirmed from the data, not asserted).
* **Bar horizon:** shadow candidates are tracked for `TRACK_DAYS = 25` calendar days. A Challenger-only position (a replacement) whose bars end before it exits is **truncated**: marked at the last available close × (1 − slippage) and flagged. The result is computed with and without truncated rotations (see criteria).
* **Champion version freeze:** the Champion arm and the live ledger are the same system. If a deploy changes decision, risk, fill or sizing logic (any change under `lab/` or runtime risk/config env), the window is **cut at that date**; later data is a different strategy version and is never pooled.
  Ops-only deploys are logged and do not cut the window.

## Windows (fixed now)
| Purpose | Period | Use |
|---|---|---|
| **Diagnostic** | sessions 2026-10-27 to 2026-11-03 (where the hypothesis came from) | Opportunity-set audit and, after the code freeze, at most one clearly labelled in-sample Challenger run. **Cannot support a pass, a promotion, or any parameter choice.** |
| **Fresh test** | sessions **2026-11-04 through 2027-01-15**, NYSE calendar | The only data that can decide the pass criteria. Evaluated **once**. |
| **Extension (at most once)** | the next 40 NYSE sessions after 2027-01-15 | Applied automatically, **only** if `n_qualified < 10` at the window end, decided from non-outcome counts before any outcome field is opened. No other extension. |

## Phase order (nothing skips ahead)
1. **Lock** this protocol (sha256 in `PREREG.lock.json`, committed). *This deliverable.*
2. **Opportunity-set audit** on the diagnostic window. Uses no outcome fields. Method below. Writes `OPPORTUNITY_AUDIT.md`.
3. **Replay engine + Stage A validity gate** (CONTROL arm only). Fidelity fixes are allowed and must be logged. Then `ch006.py` is **frozen** (`CODE.lock.json`).
4. *(Optional, in-sample, labelled)* one run of both arms on the diagnostic window. No parameter may change because of it. Any new idea becomes a new Challenger (CH-007…).
5. **Forward collection** over the fresh window. Outcome metrics are **sealed**: the script refuses to print P&L, R, drawdown or Δ for the window before the window end unless `--in-sample` is passed with a diagnostic date range.
6. **One evaluation** at the window end. `RESULTS.md`. Never re-run with changed rules on the same window.

**Allowed mid-window outputs (non-outcome only):** Stage A match status, number of qualified rotations so far, data-coverage gaps, window-cut events.
**Not allowed:** any P&L, R, drawdown, win/loss, or Δ between arms.

## Opportunity-set audit (phase 2; no outcome fields)
Input: the 65 `canonical_evaluated` rows with `setup_tradeable = true` (2026-10-27 to 2026-11-02), joined to `shadow_candidates` / `shadow_cycles` by cycle and candidate id.
1. **Deduplicate** to distinct opportunities keyed `(session_date, symbol)`. Record the first and last qualifying cycle and the number of repeat evaluations.
2. **Verify** per opportunity from the records: session type `regular`; before the 15:00 ET cutoff; fresh quote; `capacity_reason` and `failed_gates`; whether C would have passed the other fit rules (symbol not held, sector/exposure with an incumbent removed, daily caps, cash and reserve with S1 proceeds, size ≥ the $1 fractional minimum).
3. **Stratify** and never mix: **(A)** blocked by `max_open_positions` or other capacity checks, rotation-relevant; **(B)** daily entry cap (2026-10-26); **(C)** other or ineligible.
4. **Rotation-feasible?** For each A-opportunity, was any incumbent stalled (K = 10, never touched +1R from bars) at that cycle?
5. **Unresolved:** any evaluation that cannot be matched to a candidate/signal, or lacks bars, is listed as `unresolved` with the reason. It is not guessed, not dropped silently, and not counted as an opportunity.
The audit reports counts and eligibility only. It does not open `outcome`, `outcome_pnl_ps`, MFE/MAE, or any post-decision price.

## Stage A — validity gate (CONTROL arm, before any ROT output)
The CONTROL arm replayed under S0 must reproduce the live ledger over the window: the same set of `(symbol, entry session)`, entry fills within **0.1 %** of the recorded `avg_entry`, and for every position closed in the window the same exit session and exit reason, with exit price within **0.5 %**.
**Any mismatch ⇒ the experiment is INVALID until the fidelity cause is found and logged; no ROT result is interpreted.** The same gate is run daily during collection (non-outcome status only). Replay invariants also run in both arms
at every step: ≤ 3 open positions, ≤ 1 per sector, settled cash − purchase ≥ $100, no position above $125, no fill outside its bar's range, no negative cash.

## Metrics (per arm × cost case × settlement model)
Net $ P&L after costs (whole account, terminal valuation above) · net R (÷ $5) · **max dollar drawdown** (bar-level, mark-to-market) · entries · exits by type · **turnover** (round trips, notional traded) · capital utilisation · worst trade (R) · longest losing streak · open at window end.
Rotation accounting for each rotation, summing exactly to the account Δ:
**(a)** incumbent forgone path (its CONTROL outcome from the decision time) · **(b)** replacement result · **(c)** cost of the incumbent exit · **(d)** cost of the replacement fill · **(e)** settlement delay (replacement price at the actual entry vs the first cycle after the decision) · **(f)** knock-on effects on later entries (slot timing, daily caps).
Also reported: `n_qualified` (rotation triggers that met every rule), `n_executed`, `n_orphaned`, `n_truncated`, `n_blocked_by_cash`, rotations per month.

## Pass criteria (fixed now; read only at the window end)
Let Δ = ROT − CONTROL, whole account, after costs, **under S1**.

**SUPPORTED (eligible for a human-gated longer forward test, never automatic promotion) requires all of:**
1. Stage A passed and no replay invariant violated.
2. `n_executed ≥ 10`.
3. Δ net $ ≥ **+$10** (2R, about 2 % of the account) at **5 bps and at 15 bps** under S1.
4. **Leave-one-rotation-out:** Δ stays > 0 when any single rotation is removed (not carried by one event or one sector).
5. The same holds **excluding truncated rotations** (criteria 3 and 4 both hold on the non-truncated subset).
6. Max dollar drawdown of ROT is not worse than CONTROL by more than **$2.50**.
7. No correctness violation; Champion code, config and the live ledger untouched; results not pooled with any other strategy version.
Reported but **non-gating:** the 30 bps stress case, and S0.

**KILLED (archived, no retest on this window or with other K):** `n_executed ≥ 10` and Δ net $ ≤ 0 at 5 bps under S1.
**INCONCLUSIVE:** `n_executed < 10` after the single extension. No conclusion is drawn; no parameter is changed to get more rotations.
**NOT SUPPORTED:** anything else.

## Integrity rules
* The rule, K, windows and criteria above change **only** by a dated amendment committed **before** any outcome field of the fresh window is opened, with the reason. After that, the lock stands.
* No look at CONTROL-vs-ROT outcome differences before the window end. No choosing K, the stall definition or the thresholds from the diagnostic window.
* Results from this Challenger are never pooled with Champion results from a different code or config version, or with another Challenger.
* `ROBINHOOD_TRADING_ENABLED=false` and `BROKER_PROVIDER=none` unchanged. This experiment places no orders anywhere.

## Disclosure: what was seen before this lock
Seen, in this conversation (2026-11-03), and not hidden: nightly ops status; ledger positions (including realized P&L of closed positions and UNH/CVX/SO fills, stops and targets); the equity curve through 2026-11-02; counts of signals by action and executed flag; audit reasons
(`not_executed`: daily entry cap on 10-26, "stock leg not canonically executable" afterwards); `canonical_evaluated` rows (binding constraint `max_open_positions`, 65 of 65); the list of `TRADEABLE` symbols with entry/stop/target; UNH's ledger MFE/MAE (units unverified, not used).
Earlier research on the Champion population (CH-001…CH-005 results, the blocked-event counterfactual `PROFIT_MODE_FORWARD_01`) is known and is a bias risk for choosing K.
**Not opened:** `signals.outcome`, `outcome_pnl_ps`, `outcome_at`, any post-decision price or bar for the unentered candidates, and any Challenger-arm number. This lock precedes all of those.

## Known limits (in advance)
* Small sample. The test will likely produce only a handful of rotations; the likeliest honest result is INCONCLUSIVE or a weak direction, not a verdict.
* One regime (late 2026), one sector rotation. Replacement entries use decision-time quotes, ask side only. No earnings or news filter.
* The ROT arm's replacements are counterfactual fills, not real orders; bar coverage for them is limited by the 25-day tracking horizon.
* S1 assumes T+1 settlement for a real cash account; the repo's paper broker does not model it.
* Taxes, wash-sale and good-faith-violation rules are outside this test (new buys use settled cash only, so none is triggered by construction).
