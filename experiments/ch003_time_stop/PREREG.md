# CH-003 — Dead-capital time stop (pre-registration)

Registered 2026-10-13, **BEFORE any CH-003 replay output was computed.** Status: **exploratory / in-sample only — cannot promote.**
Motivation and evidence: `../diagnosis_no_entries_20261013/DIAGNOSIS.md` (descriptive; it is the only analysis seen so far).
Champion under test: `decision_engine/gates-v1.1`, config `cfg-a0eede144e` — **frozen, never edited by this experiment.**
Integrity: this file's sha256 is recorded in `PREREG.lock.json`; `ch003.py` refuses to run if it differs.

## Hypothesis
With a $500 cash account (3 open positions, 1 per sector, 2 entries/day) a position that neither stops out nor reaches +1R can hold a slot
for weeks. In the cloud sample DELL and META held 2 of 3 slots for 12 straight sessions while 10 of 20 TRADEABLE setups were blocked by
`max_open_positions` and 9 by sector limits. Releasing such **dead capital** after N sessions may let later setups enter and raise
portfolio-level net R without raising drawdown, net of costs. It may also **hurt**: a position can be sold just before it recovers.

## Variable changed (only this)
One extra exit rule on open positions. Everything else is held fixed and identical across arms (the replay engine is the same code):
eligibility (the logged `TRADEABLE` decision in `regular`, entry-eligible cycles), fit rules (3 open · 1 per sector · sector exposure ≤ 30 %
of equity · 2 entries/day · no symbol with an open position or already entered this session), sizing, fills, stops, targets, and the
candidate population. No scoring/conviction change of any kind (that is a separate Challenger).

## Arms
| Arm | Rule |
|---|---|
| **CHAMPION** | unchanged — exits only at stop or target |
| **TS3 / TS5 / TS8 / TS10** | time stop after N = 3 / 5 / 8 / 10 sessions |

**Time-stop definition.** Entry session = session 0. R = fill − stop (price risk at fill). +1R level = fill + R. If the position has **not
touched +1R** (any 5-minute bar high ≥ level since entry) by the **close of session N**, it is sold at the **open of session N+1's first
5-minute bar × (1 − slippage)**. A position that has touched +1R is never time-stopped (it follows Champion exits). A symbol sold by the time
stop cannot be re-entered for 10 sessions (prevents sell-and-rebuy churn artefacts; affects time-stop arms only).

## Mechanics (fixed, taken from the cloud runtime's documented behaviour)
* **Cycle clock:** the cloud's own `shadow_cycles` with `session_type='regular'` (entries allowed; post-cutoff cycles never enter).
  At each cycle, in order of `scanner_rank` (Champion order), each `TRADEABLE` candidate is offered to the fit rules; it enters at that cycle.
* **Fill:** buy at `quote_ask × (1 + slippage)` (reproduces the real DELL fill $565.685 exactly). Stop/target/entry-reference are the
  candidate's own decision-time `stop`/`target`.
* **Sizing (post risk-fix formula, all arms):** `qty = min( $5 / (fill − stop·(1 − slippage)) , $125 / fill )`; cash must stay ≥ $100 reserve;
  equity base $504.66 (the v1.1 boundary), fixed.
* **Exits** are evaluated on the cloud's 5-minute bars, starting with the first bar that begins at/after entry (never the bar containing
  the decision). Same-bar stop and target ⇒ **stop first** and flagged AMBIGUOUS. Stop: if the bar *opens* ≤ stop ⇒ exit at
  `open × (1 − gap)`, else if low ≤ stop ⇒ `stop × (1 − slippage)`. Target: limit, exits at target (no slippage). No fees.
* **Slot release:** at the end of the exit bar; a freed slot can be used by the **next cycle**. (The cloud broker frees a slot immediately;
  it does not wait a day.)
* **Open at end of data:** marked at the last bar's close, reported separately from realized.
* **Cost cases:** base slippage **5 bps** (gap 25 bps); sensitivity **15 bps** (gap 35); stress **30 bps** (gap 50).

## Stage A — validity gate (before any non-Champion arm is run)
The CHAMPION arm must reproduce the cloud's **actual v1.1 entries**: exactly {DELL 2026-09-25, META 2026-09-25, TMO 2026-10-06,
EOG 2026-10-08} (symbol + session), with each fill within 0.1 % of the recorded `avg_entry`. **If it does not, the experiment is declared
INVALID and no arm is interpreted.** Fixes to replay *fidelity* (found by comparing the Champion arm to the real ledger) are allowed and must
be logged in `RESULTS.md`; then `ch003.py` is **frozen** (sha256 in `CODE.lock.json`) and all arms are run **once**. No change to the
engine after any time-stop arm output has been seen.

## Metrics (per arm × cost case)
Realized ΣR · open-position mark ΣR · **total net R** · net $ P&L at the sizing above · entries · positions time-stopped · mean/max sessions
held · capital utilisation (mean open notional ÷ equity over regular-session bars) · **max drawdown ($, bar-level mark-to-market)** ·
worst trade (R) · longest losing streak.
Plus, vs CHAMPION: **opportunity cost** (entries that exist only because a slot was released, each with its own outcome) and
**time-stop regret** (for every time-stopped position: realized R vs the R it would have reached if held under Champion exits).
Robustness: **leave-one-event-out** (recompute Δ total net R dropping each event) and **per-arm Δ vs CHAMPION**.

## Reading the in-sample result (stated in advance)
* **Directionally supported** only if, for the family: Δ total net R > 0 vs CHAMPION at 5 **and** 15 bps for ≥ 3 of the 4 arms; **and** the
  Δ is not carried by one event (leave-one-event-out min Δ > 0 for that arm); **and** max drawdown is not worse than CHAMPION by more than
  **$2.50**.
* **Not supported:** Δ ≤ 0, sign changes between adjacent arms, or the Δ disappears when one event is removed.
* The family is read as a **curve over N**, not as a search for the best N. **No threshold is chosen as "best" from this sample.**
* Honest limits, in advance: 12 sessions; ≤ 20 independent TRADEABLE setups; only DELL/META (and possibly one more) are even eligible
  for a time stop at N ≥ 5, so the in-sample result rests on 2–3 positions; fills use decision-time quotes; ask-side only; no earnings/news
  filter; open positions are marks, not outcomes. **Expected outcome: not decisive.**

## Promotion gate (never automatic)
`Champion → Shadow ledger (cloud, append-only) → Challenger replay (this script) → Evaluation (results JSON) → Promotion gate (a human decision).`
The script reports the gate as data (`promotion_gate.eligible`, with each reason) and **always** says `auto_promote: false`. A promotion
needs a written entry in `PROMOTION_LOG.md` made by a person. Required, **all** of:
1. Stage A validity gate passed.
2. **Out-of-sample only**: sessions **after 2026-10-12** (positions *entered* after that date). In-sample results can never promote.
3. ≥ **15** independent OOS positions that were eligible for the time stop (held N sessions without +1R).
4. The N to promote is **declared in writing before** the OOS window is read; adjacent N must not reverse sign (plateau, not a spike).
5. Δ total net R > 0 at **5, 15 and 30 bps**; leave-one-event-out min Δ > 0.
6. Max drawdown not worse than CHAMPION by more than $2.50; no correctness violation; Champion code and config untouched.
7. Results are never pooled with another strategy version.
**Kill:** ≥ 15 eligible OOS positions and Δ total net R ≤ 0 at 5 bps.

## Out-of-sample mechanism
`python ch003.py` can be re-run on any newer mirrored backup (`python scripts/sync_cloud_data.py`); positions entered after 2026-10-12 are
reported separately as out-of-sample. Today that set is empty, and the output says so.
