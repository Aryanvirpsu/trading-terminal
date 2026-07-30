# DECISION_LOGIC_AFTER.md

Decision-logic overhaul (Prompt 6): make **TRADEABLE / MONITOR / REJECT** logically
consistent, auditable, and impossible to game. The action label is now **derived
only from an explicit list of decision gates** — no ad-hoc `if reasons else …`
branch, no path where fallback data or ~52 % data quality can slip through as a
tradeable call.

## The gate model

Every evaluation returns `decision_gates: [{name, passed, value, requirement,
reason, blocking}]`. The label is a **pure function** of that list:

```
any HARD gate fails      -> REJECT   (no trade)
else any SOFT gate fails  -> MONITOR  (blocks TRADEABLE, not an active trade)
else                      -> TRADEABLE
```

| Gate | Tier | Requirement |
|---|---|---|
| `symbol_resolution` | HARD | a live price resolved from a provider |
| `valid_levels` | HARD | entry/stop/target valid, correctly ordered, non-zero risk |
| `quality_threshold` | HARD | quality score ≥ the name's threshold (45, or 65 speculative) |
| `positive_ev` | HARD | EV/share > 0 **after** spread + slippage |
| `liquidity` | HARD | execution confidence ≥ 0.40 |
| `signal_alignment` | HARD | net signals back the trade direction (> 0) |
| `risk_limit` | HARD | portfolio daily/weekly/drawdown/sector caps not breached |
| `critical_family` | HARD | core trend/momentum family has data |
| `data_quality` | soft | mean family confidence ≥ `DECISION_MIN_DATA_QUALITY` (0.55) |
| `freshness` | soft | data is fresh/ageing (not stale/critically-stale/fallback) |
| `signal_disagreement` | soft | cross-family disagreement not `high` |
| `conviction` | soft | quality ≥ `DECISION_CONVICTION_MIN` (60) |

Two **explicit, env-gated overrides** are the *only* way a soft gate can be waived
(off by default): `DECISION_ALLOW_FALLBACK_TRADEABLE` and
`DECISION_ALLOW_HIGH_DISAGREEMENT`. Nothing else can turn a degraded read into a
TRADEABLE call.

## Case → Before → After → Gate responsible

| Case | Before | After | Gate responsible |
|---|---|---|---|
| **Low data quality** (~0.52 mean family confidence) | Could read **TRADEABLE** — `data_conf` was only a soft multiplier on the score, so a high score still passed | **MONITOR** — surfaced as a pending confirmation | `data_quality` (soft) fails `≥ 0.55` |
| **Fallback provider** (TradingView throttled → yfinance), data labelled *fresh* | **TRADEABLE / “fresh”** — engine treated fallback as fresh; header separately said “stale 18h” | **MONITOR**, badge **“fallback provider”**, −20 % confidence | `freshness` (soft) fails; state is `fallback`, never `fresh` |
| **Strong trend but bearish options flow** (high disagreement only) | **TRADEABLE** — disagreement was a cosmetic ⚠ badge with no effect on the label | **MONITOR**, disagreement **HIGH**, −18 % confidence | `signal_disagreement` (soft) fails `not high` |
| **Quality below threshold** | **REJECT**, but shown as “Quality 31/45 bar” and still displaying an entry/stop/target like a real trade | **REJECT / “NO TRADE”**, exact failed gates listed, setup moved under a collapsed *Hypothetical setup* | `quality_threshold` (HARD) fails `≥ 45` |

The first three rows are the important behavioural change: cases that previously
could surface as actionable trades are now correctly held at **MONITOR** until the
degradation clears — and the UI explains exactly what must change to upgrade.

## What changed, by requirement

1. **Quality display** — score and threshold are now separate everywhere:
   `confidence_quality` (out of `quality_max` = 100) and `quality_threshold`.
   UI reads **“Quality: 62.7/100”** + **“Required threshold: 45”** (was
   “Quality 62.7/45 bar”).
2. **Explicit decision gates** — `decision_gates` list + the label derived solely
   from it (`_gate()` builder in `decision_engine.evaluate`). `evaluate_summary`
   uses the same vocabulary and can never emit TRADEABLE in the provisional pass.
3. **Unified freshness** — one classifier `lab/freshness.py` (5 states + penalties)
   used by the engine (`_engine_freshness`), the global header (`app._data_state`,
   scan-appropriate thresholds, **explicitly separate from scheduler-idle age**),
   and the panels. Fixes “header stale 18h while engine fresh”: the header now
   classifies the real data-artifact age, not the scheduler task’s own idle time.
4. **REJECT / MONITOR** — REJECT shows **NO TRADE**, the failed gates, *what must
   change*, and hides the setup under a collapsed *Hypothetical setup*. MONITOR
   shows *pending confirmation* conditions and explicit *upgrade → TRADEABLE* /
   *downgrade → NO-TRADE* triggers, and is labelled “NOT an active trade”.
5. **Scenario probabilities** — `scenario_probabilities {bull, base, bear, method,
   confidence}` from a **double-barrier ATR model (drifted gambler’s ruin)**,
   mutually exclusive and normalised to **exactly 100 %** (residue forced onto the
   largest bucket). Drift comes from the trend family, **not** P(dir).
6. **EV & risk explained** — `ev_breakdown` (EV/share, expected return %, expected
   R, EV before/after slippage, inputs, formula) and `max_loss_breakdown`
   (quantity, entry, stop, stop-distance, slippage/gap allowance, risk budget,
   total planned loss). **Max loss is never shown without its quantity.**
7. **Disagreement quantified** — `disagreement {severity, bullish/bearish/neutral
   contribution, conflicting_family_count, strongest_conflict, confidence_penalty}`.
   `high` blocks TRADEABLE unless the override rule passes; the penalty visibly
   lowers `overall_confidence`.

## Files changed

* **New** — `lab/freshness.py` (shared classifier),
  `tests/unit/test_decision_logic.py` (the full validation suite), this doc.
* **`lab/decision_engine.py`** — added `_env_true`, `_min_data_quality`,
  `_conviction_min`, `_engine_freshness`, `_disagreement`,
  `_scenario_probabilities`; rewrote the decision section of `evaluate()` to build
  `decision_gates` and derive the action solely from them, plus `ev_breakdown`,
  `max_loss_breakdown`, `scenario_probabilities`, `disagreement`, `freshness`,
  `overall_confidence`, `confidence_penalties`, `failed_gates`,
  `upgrade_conditions`; aligned `evaluate_summary` to the same vocabulary.
* **`dashboard/research.py`** — `_scenarios()` now consumes the engine’s
  authoritative `scenario_probabilities` (falls back to a normalised-to-100 split
  only if absent; never the old overlapping P(dir) numbers).
* **`dashboard/app.py`** — `_data_state()` + `_SCAN_THRESHOLDS`; `/api/scheduler`
  and `/api/health` expose authoritative `data_state` distinct from scheduler idle.
* **`dashboard/terminal.html`** — freshness CSS states + `freshBadge`; header
  “Data” badge driven by `data_state` (scheduler-idle shown separately); rewrote
  `scenariosHtml`, `dataQualityRow`, `renderOverviewSummary`, `renderOverviewDeep`;
  added `gatesHtml`, `evBreakdownHtml`, `maxLossHtml`, `disagreementHtml`,
  `qualityLine`, `setupGrid`; decision badge maps updated to
  TRADEABLE/MONITOR/REJECT (dropped “PAPER/MONITOR”).

## Tests

`tests/unit/test_decision_logic.py` — every required check, deterministic/offline
(all nine families mocked):

| # | Requirement | Test |
|---|---|---|
| 1 | fallback ≠ fresh | `test_fallback_is_not_fresh` |
| 2 | low data quality blocks TRADEABLE | `test_low_data_quality_blocks_tradeable` |
| 3 | stale global/panel consistency | `test_freshness_is_one_shared_system`, `test_header_uses_shared_classifier_not_scheduler_age`, `test_stale_global_and_panel_agree` |
| 4 | quality vs threshold separate | `test_quality_and_threshold_are_separate` |
| 5 | REJECT hides setup | `test_reject_hides_setup_and_lists_failed_gates` |
| 6 | MONITOR shows confirmations | `test_monitor_shows_pending_confirmations` |
| 7 | scenarios total 100 % | `test_scenarios_sum_to_100` (+ 59-seed property test) |
| 8 | max-loss matches quantity | `test_max_loss_matches_quantity` |
| 9 | EV includes slippage | `test_ev_includes_slippage` |
| 10 | high disagreement penalty/block | `test_high_disagreement_blocks_tradeable` (+ override tests) |
| 11 | action exactly matches gates | `test_action_is_derived_only_from_gates` |

**Result:** `77 passed` for the new file; full suite `tests/unit` kept green (see
run log). No models or execution paths were added; `ROBINHOOD_TRADING_ENABLED`
stays `false`.

## Before / after (live example — AAPL, TradingView throttled → yfinance fallback)

**Before:** decision `TRADEABLE`, “fresh”, header separately “stale 18h”, quality
shown as `62.7/45 bar`, max-loss `$11` with no quantity, EV `10.8 EV/sh`
unexplained, bull/base/bear derived from P(dir) and not summing to 100.

**After:**
```
decision: MONITOR
quality: 62.7 / 100   required threshold: 45
data_state: fallback-provider | freshness: fallback provider
failed_gates: ['data_quality', 'freshness']       # both SOFT -> MONITOR, not TRADEABLE
scenarios: bull 26.8 + base 22.2 + bear 51.0 = 100.0
ev_breakdown: EV/sh 10.85 · 0.92R · 3.19% · after-slippage 10.85 (cost/sh 0.34)
max_loss_breakdown: 0.49 sh × (11.85 + 0.51)/sh = $6.07 total planned loss
gate check: expected-from-gates == actual == MONITOR  ✓
```

The fallback + 0.54 data-quality read that previously could have printed TRADEABLE
now correctly holds at **MONITOR**, and the gate-derived label matches the gates
exactly.
