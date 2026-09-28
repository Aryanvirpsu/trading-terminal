# HIST-001 — pre-registration (committed BEFORE any result is computed)

Per the directive: this file is written and committed before HIST-001's baseline is run. It is not altered
after looking at results except through an explicitly versioned amendment (a new dated section appended
below, never an edit to what is already committed here).

**HIST-001 is descriptive, not optimization.** It answers: *how does the current, unmodified Champion
decision code behave historically, under the validated `PRICE_TREND_MACRO_V1` capability fingerprint,
before anything is changed?* It is **not** "a backtest of full AVDI" — see §6.

## 1. Identity

| Field | Value |
|---|---|
| Experiment ID | `HIST-001` |
| Git commit (pre-registration) | `ed6df56a12a4de4437c19ffb091897809cdd6e5e` (branch `h1/historical-lab`) |
| Champion decision-engine version | `decision_engine/gates-v1.1` (`lab/paper/journal.py:ENGINE_VERSION`) |
| Champion sizing/execution version | post-`322e325` (the executable-risk fix; current `lab/paper/risk.py`/`canonical/account_fit.py`, unchanged since) |
| Account policy | `canonical.risk_policy.STRATEGY_500_POLICY` (via `canonical_bridge.py`) |
| Capability fingerprint | `PRICE_TREND_MACRO_V1` (`research/historical/capability.py`) — real price/TA + real historical macro; every other evidence family stubbed (§6) |
| Event-clustering version | `research/historical/event_identity.py` (H7), as committed at the pre-registration commit above |
| Walk-forward machinery | `research/historical/walkforward.py` (H8), as committed at the pre-registration commit above |
| Outcome engine | `research/historical/outcomes.py` (H6), as committed at the pre-registration commit above |

## 2. Dataset (per-batch manifests are the authoritative record; summarized here)

* **Price/volume source**: `fabhaus/equities_5m_stockprices`, pinned revision
  `f17c0b0c3cf6a455994f93d6a85e76274172ab03` (never `main`/`latest`) — the source accepted in
  `FABHAUS_AUDIT_REPORT.md` (timestamps ACCEPTED, volume `RELATIVE_ONLY`, corporate actions
  `ACCEPTED_WITH_POLICY`).
* **Macro source**: FRED (`DGS10`, `DGS2`, `VIXCLS`, `FEDFUNDS`), real ALFRED vintage-aware fetch
  (`research/historical/macro.py`), a NEW dataset per batch covering that batch's date range (never the
  `KNOWN_FORWARD_2026_09_25_MACRO` dataset, which only covers Sept 2026).
* **Daily decision bars**: fabhaus provides 5-minute resolution only — no separate daily table exists in
  the source. Daily bars for decision-making (`strategies._bars()`'s 55-bar trend/RSI floor) are
  **aggregated from fabhaus's own native 5-minute bars** (open of the session's first bar, running high/low,
  close of the last bar, summed volume) — the SAME technique already built and tested for "today's
  still-forming daily bar" synthesis in `avdi_adapter.py`. This is a **deliberate, disclosed methodology,
  not a limitation discovered after running**: it makes one real intraday source serve both decision and
  execution consistently, and volume produced this way inherits fabhaus's own `RELATIVE_ONLY` verdict
  unchanged (summing a `RELATIVE_ONLY` series does not make it `ABSOLUTE`).
* **Corporate-action policy**: `raw` bars remain immutable and are what the paper broker actually trades
  against (per `corporate_actions.py`'s own design and this project's standing rule). The canonical
  REPORTING/continuity view is `split_adjusted` (`corporate_actions.split_adjusted_view`, lookahead-safe,
  fail-closed on an ambiguous/unconfirmed split). Ticker changes, mergers, and delistings are quarantined
  (`detect_quarantine_candidates`), never silently remapped or dropped; the count of quarantined
  observations/events is reported per batch, never silently absorbed.
* **Volume trust**: `RELATIVE_ONLY` for every fabhaus-sourced dataset in HIST-001 (enforced at runtime by
  `volume_trust.py`: `score_liquid_momentum` is excluded from `strategies=` for any non-`ABSOLUTE` source).
* **Universe**: the REAL, current `dashboard/sector_map.py` membership AVDI's own forward scanner draws
  from — 90 unique symbols across all 11 sectors at the pre-registration commit above. This is **today's**
  membership, not point-in-time historical membership for 2024-2026 (fabhaus/this project has no reliable
  point-in-time constituent history) — **this is a disclosed survivorship-bias source, quantified per batch
  in each report's survivorship-bias section (§17 of the directive), never called unbiased.**
* **Timezone/session policy**: fabhaus timestamps are genuine UTC (`FABHAUS_AUDIT_REPORT.md` §2, corrected
  finding); daily-bar aggregation and the 5-minute execution feed both use this directly, ET-aware only
  where the Champion's own session-state/cutoff logic requires it (`market_regime.py`, patched for
  clock-awareness per H5).

## 3. Initial time range and staged rollout

Per the directive's own explicit staging (**mandatory**: each stage begins only once the previous is
deterministic and resource-safe):

| Stage | Universe | Period | Status as of this pre-registration |
|---|---|---|---|
| **Smoke** | 10 symbols (the `technology` sector: AAPL, ADBE, AMD, AVGO, CRM, CSCO, DELL, MSFT, NVDA, ORCL) | 2024-01 (one month) | To be run immediately after this commit |
| **Medium** | ~50-100 symbols (target: the full 90-symbol universe, or the largest fabhaus-available subset of it) | ~3 months | Not started — gated on Smoke being deterministic |
| **Full baseline** | Full research universe (§2) | 2024-01 through 2026-03 (fabhaus's practical coverage) | Not started — gated on Medium |

**This pre-registration covers the Smoke stage's exact parameters now; Medium and Full are pre-registered
at the universe/period level above, with their own exact dataset manifests/hashes recorded and committed
before THEIR results are computed, per the same rule.** A later stage's report will link back to this file,
never restate or revise it.

## 4. Walk-forward structure (H8) — intended for the FULL baseline; Smoke exercises a slice of Development-1 only

| Period | Kind | Range |
|---|---|---|
| `development_1` | DEVELOPMENT | 2024-01-01 .. 2024-07-01 |
| `validation_1` | VALIDATION | 2024-07-01 .. 2024-10-01 |
| `development_2` | DEVELOPMENT | 2024-10-01 .. 2025-04-01 |
| `validation_2` | VALIDATION | 2025-04-01 .. 2025-07-01 |
| `holdout` | HOLDOUT | 2025-07-01 .. 2026-03-01 |

No optimization occurs on any period in HIST-001 (directive §4/§18) — this split exists only to create
clean reporting boundaries and a genuinely locked final period for future Challenger work. HIST-001 may
report the unchanged Champion's behavior on every predefined section (development, validation, AND holdout)
as part of this single pre-registered baseline — reporting descriptive results on the holdout here does
**not** spend it for tuning purposes; what would spend it is using its results to CHANGE anything about the
Champion or a Challenger afterward, which HIST-001 explicitly does not do (§18). The Smoke stage
(2024-01) falls inside `development_1` and is a pipeline-validation run, not a walk-forward-scale
conclusion.

## 5. Champion logic reused (unchanged, no new seams)

Real scanner (`strategies.scan()`), the real canonical A/B/C/D/E pipeline (`canonical_bridge.py`,
`canonical/account_fit.py`), the real decision engine (`decision_engine.evaluate()`, real `data_quality.py`
coverage/gate logic, real `PRICE_TREND_MACRO_V1` family wiring), real ranking (whatever `scan()`'s own
candidate sort already does), real account fit/capacity/sector limits (`risk.check_entry`/`account_state`),
the real executable-risk sizing (`risk.position_size`/`stock_account_fit`, post-`322e325`), the real fill
simulator (`lab/paper/fills.py`), the real entry cutoff (session-schedule `allow_entries`, generalized from
`known_forward/schedule.py`'s single-day design to an arbitrary date range), and the real exit logic
(`broker.manage_open_positions`). No historical-only threshold, no manual exclusion based on observed
profitability, no lowering of the `0.55` data_quality floor.

## 6. Historical capability disclosure (mandatory on every HIST-001 report, verbatim or by direct reference)

> `PRICE_TREND_MACRO_V1` is not yet feature-identical to the full forward AVDI evidence stack.

* **Replayed** (real, live decision-engine code, real historical data): price/trend/momentum
  (`_fam_trend`), RSI/ATR, `liquid_momentum`/`sector_relative_strength`/`mean_reversion` scoring, real
  executable-risk sizing, real capacity/sector caps, real fill simulation, macro-rates (`_fam_macro`, via
  `macro.py`'s vintage-aware FRED replay).
* **Neutralized to a fixed/zero-confidence stand-in** (the same contract the live system itself falls back
  to when a source is unavailable): catalyst headlines (`_fam_catalyst`), short interest (`_fam_short`),
  SEC filings (`_fam_filings`), options flow (`_fam_options_flow`), social sentiment (`_fam_social`),
  analyst ratings (`_fam_analyst`), regime (`_safe_regime`), sector breadth/rotation (real MEMBERSHIP is
  used — H5's multi-sector extension — but real RELATIVE RANKING/rotation is not), trading halts.
* **Unavailable at any historical resolution today**: live bid/ask microstructure beyond the
  close-plus-synthetic-spread model; true point-in-time universe membership (§2's survivorship-bias
  disclosure).

HIST-001 is: **a historical baseline of the current Champion decision code under the `PRICE_TREND_MACRO_V1`
capability set** — never described as a backtest of the full forward Champion.

## 7. AMD / price-data caveat carried forward

H5.5 found AMD reaching TRADEABLE under `PRICE_TREND_MACRO_V1` where the real forward session did not,
most likely because of a real price-data difference between fabhaus/Yahoo-sourced technicals and the
forward session's own live provider mix — not confirmed, not resolved, AMD is **not** excluded or
hand-edited out of HIST-001. Per-event source-attribution fields (historical OHLC source, volume
source/trust, macro source, capability fingerprint) are recorded on every event/trade record so this
remains an open, traceable data-equivalence question rather than a silently absorbed one.

## 8. Metrics to be reported (directive §11-§14, §19; recorded here so no metric is added or dropped after
seeing results)

Portfolio: starting/ending equity, net P&L, net R, return %, max drawdown (and in R), profit factor,
Sharpe/Sortino (only if the independent-event count is large enough to be statistically meaningful — stated
explicitly per batch, not computed reflexively), exposure, capital utilization. Trades: total entries,
resolved trades, independent trade events, win rate, average win/loss, expectancy, median R, best/worst
trade, longest losing streak, average holding time. Path behavior (via `outcomes.py`): MFE, MAE, +1R
frequency, target/stop/ambiguous/unresolved counts. Execution: spread/slippage cost, execution leakage,
% rejected by account/risk constraints, sizing/risk-invariant violations (expected zero — any nonzero count
is a correctness anomaly per §19, not a "finding"). Funnel: universe → scanner detections → raw setups →
validation → A/B/C/D/E → finalists → TRADEABLE → capacity-eligible → orders → fills → exits, each stage's
observation count, independent-event count, conversion %, and major rejection reasons. Attribution: symbol,
sector, long/short, month/quarter/year, time-of-day bucket, quality bucket, conviction bucket, setup/
strategy, entry/exit reason — only already-defined or simple descriptive partitions, no novel regime
definitions invented for this purpose. Capacity opportunity cost (via `outcomes.resolve_hypothetical`) for
every TRADEABLE blocked by the daily cap/sector cap/open-position cap/capital/cutoff. Choice-event records
(candidate set, Champion selection, later real outcome, later hypothetical outcome of the blocked
candidates) for every genuine multi-candidate competition. CH-001 shadow result (Champion exit vs.
breakeven-after-+1R), computed but never allowed to affect a real position, reported as `SHADOW ANALYSIS`
only.

## 9. Success/failure interpretation (fixed now, applied after the fact, never the reverse)

* **BASELINE VALID** — mechanics/data are trustworthy enough to use for Challenger comparisons.
* **BASELINE VALID WITH LIMITATIONS** — usable, but named data/universe/capability constraints (§2/§6/§7)
  must accompany every conclusion drawn from it.
* **BASELINE INVALID** — a correctness/data problem makes the results unsuitable for strategy research.

This verdict is about research validity, never about whether the Champion made money — a losing but
trustworthy baseline is a completely acceptable outcome and must not be reported as if it were a defect.

## 10. No parameter search (directive §18) — binding for every stage of HIST-001

No Optuna, no nearby-threshold testing, no alteration of conviction/data_quality/daily capacity/stops/
targets/ranking/macro weights/signal weights, at any stage. HIST-001 is one strategy/config: the Champion as
committed at §1's git commit.

---

*Amendments, if any become necessary, are appended below as new dated sections and never edit the text
above.*
