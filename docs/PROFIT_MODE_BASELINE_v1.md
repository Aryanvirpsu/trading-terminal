# AVDI PROFIT MODE BASELINE REPORT v1

Date: 2026-09-24 · Evidence: Case 1 (cloud) paper ledger `paper-ledger-db-23`, 2026-09-10..09-24 ·
Provenance, hashes and reproduction steps: `experiments/profit_mode_baseline_v1/PROVENANCE.md`.
No strategy logic was changed to produce this report.

> **Read this first — what this baseline does and does not support.**
> - About **13 independent resolved events** (22 resolved signals) — not a statistically usable sample.
> - The ledger contains only **2 resolved actual entries** (HAL, AMD).
> - **META is one highly correlated repeated event, not six independent wins.** It alone is +13.12R.
> - This report is enough to choose better *experiments*. It is **not** enough to claim AVDI is profitable
>   or unprofitable, or that any gate is helping or hurting.

## 0. Bottom line

1. **Why 8 TRADEABLE signals produced 2 entries.** All 6 losses of candidates happened at ONE place: the
   broker's pre-trade risk check (`risk_blocked`). Sizing used the reference price; the simulated fill was
   ask+5 bps, so cost came to $125.50-$125.75 against a $125.00 per-stock and sector cap. No candidate was
   lost at conviction, quality, account fit, reserve, stale quote, or position cap. This was a correctness
   bug (P0 fix #2, commit `b0ec71d`), not a strategy result.
   *(The "14 TRADEABLE / 3 entries" figure pooled an Aug-19 report from an earlier ledger with a different
   sample: 6 TRADEABLE + 1 entry. This ledger is 8 TRADEABLE / 2 entries.)*
2. **Did the gates that removed the other candidates help or hurt net expectancy?** The data cannot say.
   Every apparent gate "cost" comes from one event (META, +9% overnight gap on 09-21; 6 signals, +13.12R).
   Excluding it, the blocked population is 1 target / 12 stops (−9.39R).
3. **The conviction gate is not the binding constraint.** The $500 account's capacity (3 open, 1 per
   sector, 2 entries/day) skips 12-17 candidates at *every* conviction threshold tested. Loosening
   conviction changes little.
4. **The real research question is capital allocation:** with only 3 slots, *which* candidates deserve
   the capital (CH-002, first priority). Exit management (breakeven after +1R) is a provisional secondary
   hypothesis (CH-001) — daily bars cannot order events within a day, so its apparent +2.3R may be an
   ordering artifact until re-tested on intraday bars.

## 1. Evidence inventory

| Evidence | Status | Use |
|---|---|---|
| Ledger `paper-ledger-db-23` (55 signals, 4 orders, 2 positions, 25 shadow-option rows) | Trustworthy: hash-verified, single config/engine version, reconciled P&L | Primary |
| Daily OHLC (yfinance, 17 symbols) | Public data; unadjusted | Bar-by-bar outcome replay |
| Case 1 daily reports 2026-08-19 .. 09-24 (`origin/main`) | Consistent with ledger from 09-10 | Cross-check only |
| Aug-19 report (6 TRADEABLE, 1 entry) | **Different, earlier ledger — excluded** | Not pooled |
| `~/.tradingview_mcp_data/predictions.jsonl` | **PROVENANCE_UNVERIFIED — excluded** | None (see §9) |
| Case 2 local ledger (15 signals, 0 orders; DB mtime Sep 10) | Untouched, tiny | Not used |
| Options shadow rows (25) | Shadow only; not analysed here | Future |

## 2. Champion

- **Version:** `cfg-a0eede144e` + `decision_engine/gates-v1`. **v1.0** = code up to `2473d1b` (the ledger's
  run; pre-P0). **v1.1** = same strategy + P0 execution fixes (`b0ec71d`). Decisions/signals are identical
  across v1.0/v1.1; *execution outcomes* differ, so do not pool executed trades across them.
- **Gates:** 8 HARD (symbol_resolution, valid_levels, quality_threshold ≥45 [65 speculative], positive_ev,
  liquidity ≥0.40, signal_alignment, risk_limit, critical_family) + 4 SOFT (data_quality ≥0.55, freshness,
  signal_disagreement not high, **conviction: quality ≥ 60**). HARD fail → REJECT; SOFT fail → MONITOR.
- **Strategies:** `liquid_momentum`, `sector_relative_strength`. Instrument: stock only (options = shadow).
- **Risk/sizing:** $5 max loss/trade, $125 position cap, 3 open, 1/sector, 2 entries/day, $10 daily loss,
  $50 drawdown, $100 reserve, fractional shares.
- **Execution model:** market BUY at ask +5 bps; stop exits −5 bps (−25 bps gap); target = limit; fees $0;
  stop before target when a bar touches both. Mean planned RR = 2.23 → gross breakeven win rate 31%.

## 3. Accepted-trade results (n = 2 resolved; treat as anecdote)

| Trade | Entry→Exit | P&L | R | MFE / MAE (R) |
|---|---|---|---|---|
| HAL (09-11→09-17, stop) | 35.61→33.80 | −$5.48 | −1.10 | 0.51 / 1.01 |
| AMD (09-17→09-21, target) | 548.47→612.60 | +$10.14 | +2.05 | 1.94 / 0.60 |

Net +$4.66; win rate 1/2; avg win $10.14, avg loss $5.48; expectancy +$2.33/trade (+0.48R); slippage paid
≈ $0.15 total (~3% of net); fees $0; peak drawdown 1.36%. Reconciliation: YES.

## 4. Candidate funnel (55 signals)

| Stage | n | Note |
|---|---|---|
| TRADEABLE | 8 | all 12 gates passed |
| → executed | 2 | HAL, AMD |
| → **risk_blocked** (cost $125.50-125.75 > $125 cap; also sector cap) | **6** | COP, CVX, TMO, DE, AAPL, NVDA — sizing/fill mismatch (fixed) |
| → lost at conviction/quality/account-fit/reserve/quote/position-cap | 0 | |
| MONITOR (only `conviction` failed) | 21 | q 45-60 |
| REJECT (`quality_threshold`+`conviction`; 1 also `signal_disagreement`) | 26 | q < 45 |
| Resolved (bar-by-bar replay) / open | 22 / 33 | ledger says 24 / 31 (2 same-day artifacts, §9) |

Counterfactual of the 6 blocked TRADEABLEs (5 bps costs): COP stop −1.35R, CVX stop −1.04R, TMO/DE open
(MFE 1.74R/1.69R), AAPL/NVDA open (MFE 0.15R/0.08R). With P0 sizing and the real capacity limits the
Champion replay enters 5 (COP, TMO, DE, AMD, AAPL): 2 resolved, +0.80R, +$10.54 (actual: +$4.66).

## 5. Gate attribution (replay, R net of 5 bps entry + exit slippage)

| Population | n | resolved | target/stop | ΣR | mean R | mean R per independent event |
|---|---|---|---|---|---|---|
| TRADEABLE (q≥60) | 8 | 4 | 1/3 | −1.26 | −0.32 | −0.32 |
| MONITOR (conviction) | 21 | 7 | 2/5 | −0.80 | −0.11 | −0.50 |
| REJECT (quality<45) | 26 | 11 | 5/6 | +4.53 | +0.41 | −0.25 |
| MONITOR+REJECT | 47 | 18 | 7/11 | +3.73 | +0.21 | −0.47 |
| MONITOR+REJECT **ex-META** | 41 | 12 | 1/11 | −9.39 | −0.78 | — |

At 15 bps costs (3×) every row moves by ≈ −0.03 to −0.06R; no conclusion changes. Independent events are
resolved outcomes deduplicated by (symbol, resolution day): 13 of 22.

## 6. Conviction / quality analysis

| Quality bucket | n | resolved | target/stop | ΣR | open MFE/MAE (R) |
|---|---|---|---|---|---|
| <50 | 32 | 13 | 5/8 | +2.47 | 0.57 / 0.39 |
| 50-54 | 10 | 4 | 1/3 | −0.93 | 0.55 / 0.30 |
| 55-59 | 5 | 1 | 1/0 | +2.18 | 0.79 / 0.19 |
| 60-64 | 5 | 2 | 1/1 | +1.13 | 0.64 / 0.53 |
| 65-69 | 1 | 0 | — | — | 1.74 / 0.04 |
| 70+ | 2 | 2 | 0/2 | −2.39 | — |

- Exact R is reconstructable (entry, stop, target and a cost model are stored); R above is *not* a
  target-hit rate substitute. Costs beyond 5 bps slippage (spread) are **not** available (§9).
- **No monotonic relation between quality and outcome.** The only two q≥70 signals (COP, CVX) both
  stopped out; the <50 bucket's +2.47R is META. Cells have 0-13 resolved signals; no bucket supports
  loosening OR tightening the gate.
- Same-data Challenger run (capacity-constrained portfolio replay, P0 sizing):

  | Variant | accepted | resolved | ΣR | notes |
  |---|---|---|---|---|
  | Champion (conviction 60) | 5 | 2 | +0.80 | skipped: sector 2, max_open 1 |
  | conviction 55 | 6 | 3 | +2.98 | difference = one META entry |
  | conviction 50 | 5 | 2 | +1.15 | skipped: sector 6, max_open 12 |
  | conviction 45 | 5 | 2 | +1.15 | skipped: sector 6, max_open 17 |

  Conclusion: **uninformative** — the 2-3 resolved trades differ by one META entry. Do not act on it.

## 7. The "8 of 20 blocked winners" number

- Ledger: 8 blocked targets = **META ×6**, AMD, MRK (12 blocked stops). Replay: 7/18 (39%; Wilson 95%
  20-61%), which brackets the 31-33% breakeven (payoff 2.23R).
- The 6 META signals are one underlying event (one earnings-style gap, open 680→753 on 09-21). Counting
  events, blocked targets are 3 (META, AMD, MRK-in-ledger only).
- Net R with META: +3.73R over 18. **Without META: −9.39R over 12 (1 target).** Result is a single
  outlier winner, not gate cost. The old reading ("gates too tight") is not supported; neither is the
  opposite.
- Ledger-vs-replay: 53/55 outcomes agree. The 2 that don't are same-day look-ahead artifacts of the ledger
  (§9): MRK (flatters) and META-09-21 MONITOR (penalises).

## 8. Loss attribution, execution leakage

- **Losses:** the three TRADEABLE stops were all energy (COP, CVX, HAL) in the same weeks — one sector
  bet, not three. COP's replay stop is −1.35R (adverse gap through the stop). The realized loss was HAL.
- **Largest leakage by far: 6 of 8 TRADEABLEs (75%) never traded** because of the sizing overshoot ($0.50-
  $0.75 per position, 0.4-0.6%). That is a bug, now fixed.
- Slippage (model 5 bps): ≈$0.15 on 2 trades. Fees $0. Spread cost is **unmeasured**: fills use the quote
  ask, but hypothetical (non-entered) outcomes assume the reference price, so their costs are understated.
- Exit experiment (same 22 baseline-resolved signals, daily bars, BE stop active from the *next* bar):

  | Variant | ΣR | ex-META ΣR |
  |---|---|---|
  | Baseline (stop 1R / target ~2.2R) | +2.47 | −10.65 |
  | E1 breakeven stop after +1R | **+4.79** | −8.34 |
  | E2 target 1.5R | −3.03 | −11.97 |
  | E3 1.5R + BE | −0.72 | −9.65 |

  E1 helps in both columns (+2.3R), mostly by turning −1R stops that first reached +1R into ~0R. E2/E3
  are worse on the same set. Caveat: daily bars cannot order intrabar moves.

## 9. Data limitations and measurement defects

- **Tiny n:** 2 resolved trades; 22 resolved signals; 13 independent events; 60% still open (resolved-only
  results are biased toward fast outcomes; open MFE/MAE are shown separately).
- **One regime / one news event:** two weeks, 17 names, one +9% gap dominating.
- **Outcome tracker:** signal-day tracking uses the *whole-day* bar high/low (includes pre-signal price
  action), and only the *latest* bar is tested for a hit each run. Effect on this ledger: 2 same-day
  artifacts (above). Recommended (not applied): resolve from the bar after the signal.
- **No spread/quote data** stored for hypothetical fills; no earnings/event calendar in the ledger.
- **Real-state contamination (experiment-isolation defect):** running the unit suite appends `TEST`/fixture
  rows (incl. `BAC TRADEABLE q75.5`) to the real `~/.tradingview_mcp_data/predictions.jsonl` — 571 rows on
  2026-09-24 alone in bursts matching test runs (my own and possibly another tool's). Case 2's paper DB
  was **not** modified (mtime Sep 10). `predictions.jsonl` is marked `PROVENANCE_UNVERIFIED` and is excluded.
  The earlier "17:14 DB modification" report was an artifact of my own file copy (copy mtime), not a
  mutation.

## 10. Challenger hypotheses (by expected information value)

**Registered order of work (agreed 2026-09-24):** CH-002 slot ranking first → CH-001 breakeven shadow /
intraday validation → keep collecting forward paper evidence → no further conviction-threshold work yet.
CH-001 is *provisional* (not promoted); CH-002 keeps every risk/capacity limit fixed and changes only
ranking (Champion vs highest-conviction vs highest-expected-R vs composite quality×payoff×execution),
measured as portfolio-level net R using only decision-time information.


| # | Hypothesis / exact change | Evidence | Method | Minimum evidence | Promote if | Kill if |
|---|---|---|---|---|---|---|
| 1 | **CH-001 breakeven stop after +1R** (one exit rule) | +2.3R on 22 same-set outcomes; 3 stops had first reached ≥+1R | Bar replay on every future signal (accepted and rejected), intrabar-safe with 30-min bars | ≥30 independent resolved events, incl. 2 regimes | ΣR ≥ +0.15R/event vs Champion OOS, stable at 3× costs | ≤ 0 OOS or hurt by 30-min ordering |
| 2 | **CH-002 slot-allocation ranking** (change only how the 3 slots/1-per-sector are filled: e.g. RR or distance-to-stop rank vs quality rank) | Capacity skips 12-17 at every threshold; quality has no monotonic edge | Capacity-simulator replay over a widened daily candidate log | ≥30 competing candidate-days | Higher R per slot-day OOS | No better than quality rank |
| 3 | **CH-003 quality score validity** (rank-IC of quality vs R; ablate components) | 70+ both stops; <50 outperforms | Rank correlation with event-level dedupe | ≥40 independent resolved events | Positive IC OOS | IC ≈ 0 → treat score as noise |
| 4 | **CH-004 conviction 55 (shadow only, never live)** | 21 MONITOR, ~1 event/week | Event-deduped counterfactual accumulator | ≥30 independent resolved events in 50-59 | ΣR>0 ex-outlier, stable 3× costs | ≤0 ex-outlier |
| 5 | **CH-005 gap/event-risk exclusion** (skip entries around earnings/known catalysts) | COP gap stop −1.35R; META gap decides 6 signals | Needs event calendar; replay on bars | ≥20 events touching the filter | Removes tail losses without removing ≥ equal winners | Removes winners ≥ losers |

Ranked by information: 1 is testable now on every candidate and is independent of the gate. 2-3 explain
*why* results look the way they do. 4 is deliberately low near-term (~30 weeks of forward data) and
therefore should be answered from a widened candidate log, not forward paper alone.

## 11. Recommended next steps (no strategy change yet)

1. ~~Fix test isolation~~ — done (`tests/conftest.py`, commit after `09dd999`; real-state SHA-256 verified
   unchanged across a full run; negative control fails loudly).
2. Resolve signal outcomes from the *next* bar and log spread/quote at decision time.
3. Track candidate outcomes at event level (dedupe by symbol/event) in the profit board.
4. Register CH-001..CH-003 as configs; run against the unchanged Champion on the same data.
