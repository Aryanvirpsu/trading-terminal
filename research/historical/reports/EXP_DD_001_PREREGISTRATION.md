# EXP-DD-001 — Champion without the max_drawdown kill switch (pre-registration)

Registered 2026-09-30, BEFORE this experiment's own result has been computed. Status: **diagnostic
Challenger**, run before HIST-002/003/004 per explicit instruction, because it bears directly on whether the
corrected Full baseline's reported numbers (net +$23.10, −0.256R) represent a real, settled outcome or a
truncated sample.

## Why this experiment, and why now

`max_drawdown_lockout_check()` (`research/historical/hist001/analysis.py`) found that
`hist001_full_2024_2026_corrected`'s own account tripped its REAL `max_drawdown` gate ($50, the account's
real `PAPER_MAX_DRAWDOWN_USD`/`STRATEGY_500_POLICY.max_drawdown`) on genuine trading losses on 2025-02-03,
and never recovered — equity froze at exactly $511.02 for the last ~54% of the 2024-01-01..2026-03-10
evaluation window, with zero further trades. This is NOT a data artifact (unlike the AVGO split cascade):
it is Champion's own real risk policy doing exactly what it is configured to do. The open question this
experiment answers: **did that $50 kill switch protect the account from a strategy that had deteriorated,
or did it prevent a later recovery?** This is the single highest-value question to answer before spending
time on HIST-002/003/004, since all three would otherwise be compared against (or built on top of) a
truncated 10-month sample rather than a genuine 2-year one.

## Hypothesis

Removing the max_drawdown constraint (and ONLY that constraint) lets Champion's real decision/execution
logic continue trading through and past the 2025-02-03 drawdown, producing a 2024-01-01..2026-03-10 result
that is either (a) materially better than the $50-capped CONTROL's $511.02 ending equity — suggesting the
kill switch prevented a real recovery — or (b) materially worse — suggesting the kill switch correctly
protected the account from a strategy that had genuinely deteriorated by early 2025.

## Variable changed (only this)

The `max_drawdown` field is removed from BOTH of the two independent places it is enforced in the real
execution path (found by reading each gate's own source, not assumed from a single config value):

1. `lab/paper/risk.py`'s `check_entry()` — compares `drawdown_usd < r.max_drawdown`, `r = paper.config.risk()`.
   Patched via `NoDrawdownChallenger` to return `max_drawdown=float("inf")` (this gate requires a numeric
   value — comparing a float to `None` raises `TypeError` — so "unbounded" means a value nothing can
   exceed, not an absent value).
2. `lab/paper/canonical_bridge.py`'s `evaluate_canonical()` — calls `canonical.account_fit.stock_account_fit`
   with the hardcoded `STRATEGY_500_POLICY`. Patched to `max_drawdown=None, max_drawdown_pct=None`, which
   `canonical/risk_policy.py`'s own `effective_limit()` resolves to `LimitResult(None, UNBOUNDED)`, and
   `account_fit.py`'s own `if dd_cap.limit is not None:` guard then skips the check entirely (this gate
   DOES support `None` as "no limit" — the real, intended convention, not a workaround).

Every OTHER parameter is read from the REAL current config/policy via `dataclasses.replace()` and passed
through completely unchanged: $5/trade risk (`max_loss_per_trade`/`risk_per_trade_pct`), $125 position
notional, 3 max open positions, 1 per sector, $10 max daily loss, $100 min cash reserve, $0.30 max sector
exposure, cooldown rules, fractional shares. Stops, targets, fills, strategy selection, sizing formula,
corporate-action handling, event-identity tracking, and macro replay are completely untouched — this
experiment's implementation (`no_drawdown_challenger.py`) never touches `lab/paper/risk.py`,
`lab/paper/canonical_bridge.py`, or `canonical/account_fit.py` themselves; it patches at the same
`mock.patch.object` seams the rest of Historical Lab already proves safe to use.

## Data / method

Identical dataset, universe, warmup, and evaluation window to the corrected Full CONTROL:
`HIST001_FULL_2024_2026_5M`/`_DAILY`, all 11 sectors, `warmup_start=2024-01-01`,
`evaluation_start=2024-04-01`, `evaluation_end=2026-03-10`, `PRICE_TREND_MACRO_V1`,
`split_aware_diagnostic=True` (unchanged from the control — this experiment is additive on top of the
already-corrected baseline, not a reversion to the pre-correction behavior), plus the new
`disable_drawdown_gate=True`. Run via the fast engine (`perf/historical-fast-v2`), ONLY after that engine's
own parity check against the CONTROL's golden result (`full_result_corrected.pkl`) has passed exactly —
required before this experiment's own result can be trusted, per explicit instruction.

## Control

`hist001_full_2024_2026_corrected` (committed `h1/historical-lab` @ `7d3a6e0` and earlier) is the fixed,
immutable CONTROL. This experiment never modifies, reruns, or supersedes it — both results are reported
side by side, from this point forward and especially from 2025-02-03 onward, where the two paths diverge.

## Metrics (both runs, same methodology — `analysis.py`'s own functions, unchanged)

Ending equity, net P&L, trade count, win rate, R-multiple expectancy (where `planned_risk` is available —
see the known, separately-tracked `planned_risk=NULL` provenance gap, not blocking this experiment),
`drawdown_and_streaks()`'s max drawdown/worst trade/tail losses/streaks, `stability_breakdown()`'s
year/month/quarter P&L, and three NEW facts specific to this question: (1) the full monthly equity curve
after 2025-02-03, (2) the lowest equity point reached in the Challenger run (did it fall further before any
recovery?), (3) whether equity ever drops below $400 / $350 / $300 at any point in the Challenger run (a
"risk of ruin" proxy, since this account has no stop against drawdown by construction here).

## Success / failure criteria

- **Kill switch prevented a recovery:** Challenger's ending equity (2026-03-10) is materially higher than
  the control's $511.02, AND the Challenger's lowest post-breach equity point stayed well above zero (i.e.,
  removing the gate did not expose the account to a much worse intermediate low before recovering).
- **Kill switch correctly protected the account:** Challenger's ending equity is materially lower than
  $511.02, or the Challenger's equity falls meaningfully further (toward or past $400/$350/$300) before any
  recovery, even if it ends higher — meaning the extra time exposed to real risk was not worth the eventual
  outcome.
- **Ambiguous:** the two paths end up close to each other, or the sample after 2025-02-03 is too thin
  (few trades) to draw a confident conclusion either way.
- This experiment is diagnostic only. **No threshold, gate, or production/main setting changes as a result
  of this experiment, regardless of outcome** — that would be a separate, much bigger decision requiring its
  own explicit authorization, per standing project policy on Champion/production changes.

## Known limits (stated in advance)

Running without a drawdown gate is itself a confound: once the real gate would have stopped trading, further
real losses (if the deterioration was real and continuing) have no backstop in this run at all — this
experiment deliberately accepts that risk to answer the research question, which is why it will never be
applied to the live Ubuntu/paper runtime or to `main` regardless of its result. The `planned_risk=NULL` gap
affects R-multiple comparability between the two runs identically (same unresolved provenance issue, not
specific to this experiment) — dollar P&L and trade-count comparisons are unaffected by it.
