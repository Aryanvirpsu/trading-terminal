# UBUNTU_LIVE_ACCEPTANCE_01 — first full real market session on the Ubuntu runtime

Session: Friday 2026-09-25 (regular, full day). Runtime: `avdi-runtime`, image `avdi-paper:9b5c30e…` (main `9b5c30e`), started
01:06 UTC on 09-25 (deploy), **RestartCount 0**. Paper execution only. **No strategy, gate, threshold, ranking, stop or capacity
setting was changed.** This was an acceptance test of the evidence machine, not a strategy experiment.
Evidence sources: `automation/avdi_acceptance.py` (read-only extraction), the ledger, the shadow log, container logs, GitHub run
history, and an independently downloaded and verified off-host backup.

## Verdict

The runtime worked as designed under real market conditions. It ran the whole day unattended, every scheduled cycle completed, two
genuine paper trades were traced end to end, repeated observations did not inflate the sample, and the evidence persisted and was
verified off-host. Not everything is clean: the forward CH-001 tables were contaminated with two v1.0 positions (a real bug, still
unfixed), and GitHub's scheduled workflows are too unreliable to be the only off-host backup path. The day is a single session with
12 independent events and 2 trades: it proves the machinery, not any edge.

## Schedule vs actual
| Item | Expected | Actual |
|---|---|---|
| 08:30 health check | 1 | done — `ok`, ledger `quick_check ok`, reconciled, disk 89.8 % free, providers healthy |
| 09:15 observation scan | 1 | done — `premarket_prep`, 34.0 s, **no trades** |
| Discovery cycles (09:35-15:50 ET, every 15 min) | 26 (22 entry-eligible to 14:50 + 4 observation-only post-cutoff) | **26 completed** (22 `regular`, 4 `post_cutoff`), **0 missed, 0 failed** |
| 16:10 close processing | 1 | done — postmarket, reconcile true, report JSON written, CH-001 evaluation, verified local backup |
| Slot table | 29 slots | 29 `done`, 0 pending, 0 missed |
| Tracker | ~1/min fast + ~1/5 min full | 313 fast + 79 full passes, no tracker errors recorded, last quote age max 0.9 s |

**Timing.** Discovery duration average **34.0 s**, max **53.3 s** (09:50); lateness vs slot median 2.6 s, p90 4.9 s, max 22.0 s (the
09:35 cycle, which also takes the evidence-boundary snapshot). No cycle overlapped another (singleton and overlap locks held).

## Funnel (per cycle; 5 finalists every cycle)
Universe considered 23 symbols (26 on two cycles); scanner detections 16-28; finalists 5 (3 in the 15:05 cycle).

| Window | TRADEABLE / MONITOR / REJECT per cycle | Capacity-eligible | Entries |
|---|---|---|---|
| 09:15-11:50 (12 cycles) | 0 / 1-3 / 2-4 | 0 | 0 |
| 12:05 | 2 / 1 / 2 | 2 | **1 (DELL)** |
| 12:20-12:50 | 1 / 2 / 2 | 1 | 0 (AAPL sector-blocked; DELL held) |
| 13:05 | 2 / 1 / 2 | 2 | **1 (META)** |
| 13:20-14:50 | 2-3 / 0-1 / 2 | 2-3 | 0 (daily cap reached; existing positions) |
| 15:05-15:50 (observation only) | 1-2 / 1 / 1-2 | 1-2 | 0 (after the entry cutoff) |

Per-cycle `is_choice_event` was 1 from 12:05 onward (real slot competition, the data CH-002 was starved of).

## Observations vs events (sample-size protection)
* **133 raw observations** (110 regular, 18 post-cutoff, 5 premarket prep) → **12 unique events**, one per symbol
  (AMD 26 obs, DELL 26, CRM 25, AAPL 23, META 20, TMO 6, NVDA 2, FCX/MSFT/NEM/TSLA/VRTX 1 each). Every symbol kept a single event_id all day.
* Only **21 of the 133** observations were journaled in the ledger (55 v1.0 + 21 v1.1 signals): unchanged observations were not re-journaled.
  Label flips near a threshold do journal (META flipped REJECT/MONITOR several times: 20 observations, 1 event, 8 journal rows).
* The manual smoke cycle `2026-09-24T2033manual` is excluded everywhere (`session_type='manual'`, `evtm_` events, never joined by real events).

## State transitions
* **DELL: MONITOR (09:15-11:50 ET) → TRADEABLE at 12:05 ET → entered**, then TRADEABLE for the rest of the day (held).
* **META: REJECT/MONITOR flips → TRADEABLE at 13:05 ET → entered.**
* AAPL, TMO, MSFT, TSLA reached TRADEABLE and were **not** entered (reasons below).
* Later scans found names the first (09:35: AMD, CRM, DELL, META, NVDA) did not: AAPL at 09:50, TMO and VRTX at 10:05, MSFT at 14:35,
  FCX/NEM/TSLA at 15:05 — 12 distinct symbols by the end of the day.

## Entries — full end-to-end traces
**DELL (12:05 ET).** Candidate quality 67.0, expected R 1.007, classification TRADEABLE → capacity: two eligible (DELL, AAPL) but one
technology slot; the Champion's scanner order picked DELL (a quality-ranked pick is also DELL) → quote: provider `yahoo`, ask 565.4026,
spread 0.1 % → executable price ask + 5 bps = **565.6853** → sizing 0.109577 sh (risk-limited: planned risk $5.00, notional $61.99) → paper
order `ord_6b76c171eb2fcd10` BUY MARKET → fill 565.6853 (slippage $0.2827, fees $0) at 12:05:23 ET → ledger position open (stop 517.79,
target 659.78) → tracker: MFE 0.17 R, MAE 0.51 R at the close.
**META (13:05 ET).** Quality 62.1, expected R 0.924, TRADEABLE, `communication` sector (no conflict) → quote provider `tradingview`, ask 750.1249 →
executable price **750.5** → 0.118596 sh, planned risk $5.00 → order `ord_811f32a092e7b76e` → fill 750.5 (slippage $0.3751) → position open
(stop 706.2, target 839.43) → tracker MFE 0.43 R, MAE 0.09 R.
Both positions are **carried overnight** (open, 0 exits). No duplicate entry occurred (`duplicate_entries` empty; DELL 11 and META 5 later
TRADEABLE observations were correctly not re-entered).

## Refusals and reasons (every TRADEABLE that did not trade)
| Symbol | Observations | Outcome | Reason |
|---|---|---|---|
| AAPL | TRADEABLE at 12:05 | not entered | technology slot held by DELL — shadow `capacity_reason=sector`; the ledger reason is the generic "stock leg not canonically executable" |
| TMO | 6 (14:20-15:50) | not entered | daily entry cap reached (2 per day, incl. persisted entries) → then post-cutoff observation-only |
| MSFT | 1 (14:35) | not entered | daily entry cap reached |
| TSLA | 1 (15:05) | not entered | post-cutoff, observation only |
Ledger `not_executed` reasons overall: 17 × "stock leg not canonically executable" (includes MONITOR/REJECT first observations) and 3 × the daily-cap reason.

## Account persistence and the v1.1 evidence boundary
* Boundary recorded **09:35:22 ET, 11 s before the first real signal**: equity **$504.66**, cash $504.66, 0 open positions, entries_today 0, ledger 55/4/2 rows.
* At the close: cash $353.67 (from fills), 2 open positions, reconciliation **true** (delta 0.0), equity **$504.49** (the ledger's end-of-day mark).
  **v1.1 P&L = 504.49 − 504.66 = −$0.17** (unrealized; nothing closed). Midday it was +$0.50. v1.0 gains are not used.
* v1.0 and v1.1 remain separately queryable: `signals.engine_version` 55 × `gates-v1`, 21 × `gates-v1.1`; `avdi_runtime.py evidence`.

## Shadow logger integrity and CH-001
* Shadow DB: `integrity_check ok` (also ledger). 27 cycles, 133 observations, 12 events, 1 boundary row, 5,070 five-minute bars (latest 15:55 ET, i.e. the
  full session), 264 daily bars. Append-only triggers intact; no shadow failures logged.
* **CH-001: no usable forward evidence yet.** Neither open position reached +1 R (max 0.43 R). **Contamination found:** `evaluate_ch001` iterates *all* ledger positions, so the
  tables now contain 2 result rows and 1 observation for **v1.0** positions (HAL, AMD; opened 09-11/09-17, before the boundary). These must be excluded from any v1.1 analysis
  (`opened_at < 2026-09-25T13:35:22`); the code should be fixed to evaluate only positions opened at/after the boundary. Not fixed today by instruction.

## Provider failures, stale quotes, errors
* `discovery_failures 0`, `provider_failures 0`, no exceptions or fatals in the container log, no audit failures. Quote timestamps were 8.8-53 s *after* each cycle started
  (0 of 80 checked at midday older than 60 s); the age metric is bounded by cycle start, not per quote. Quotes came from `tradingview` (~60 %) and `yahoo` (~40 %).
* Noise: ≥ 365 transient `tradingview_ta` `JSONDecodeError` retries in the first 5 hours (3 retries each, none counted as a provider failure) — likely datacenter-IP throttling; it
  adds seconds to some cycles.
* Service restarts: **none** (RestartCount 0, StartedAt 01:06:29 UTC). The only human actions on the host this session were read-only checks; the runtime received no manual intervention.

## Backups
* Local: `20260925T201003Z` written by the 16:10 close job — manifest ok, ledger 626,688 B and shadow 1,093,632 B, both `integrity_ok`.
* Off-host: the **scheduled** GitHub ops workflow is unreliable — only 2 probe runs (17:53 and 21:28 UTC, delayed) fired against ~16 expected, and the nightly 17:40 ET backup job did not fire.
  I dispatched the workflow manually (run 36200780711, read-only): artifact `avdi-ubuntu-backup-4`, independently re-downloaded and verified — sha256 matches, `integrity_check ok` on both
  DBs, contains the ledger, the shadow DB, `runtime_state.json` and today's report; shadow has 27 cycles / 133 observations / 1 boundary row / 5,070 five-minute bars, ledger has 76 signals, 2 open positions.

## Evidence contamination (summary)
1. `2026-09-24T2033manual` smoke cycle in the production shadow DB — excluded (documented, cannot be deleted).
2. CH-001 rows for v1.0 positions HAL/AMD — must be excluded from v1.1 analysis; bug to fix.
3. 5 `premarket_prep` observations are included in the 133 raw count; they are observation-only and not decision evidence.

## Exact remaining production risks
1. **CH-001 contamination bug** (above) — fix before relying on CH-001 output.
2. **Off-host backup depends on unreliable GitHub schedules.** Manual dispatch works; a nightly automatic copy is not guaranteed. Until changed, dispatch it manually (or after each session).
3. **TradingView quote/TA dependence from a datacenter IP** (~60 % of quotes, 365+ retries in 5 h): a wider outage would fall back to Yahoo; behaviour and data-source mix differ from v1.0 (Yahoo).
4. **Single 1-vCPU VM, no HA; VM reboot, deploy-during-window refusal and holiday/early-close handling were never exercised live.** Docker-group/deploy-key residual risk is documented.
5. **Overnight gap risk on DELL and META** relies on the tracker/stops at the next open (gap fills are modelled).
6. **Planned-risk convention:** planned risk uses the decision's reference entry, not the fill, so DELL's loss at the stop is ≈ $5.25 vs $5.00 planned.
7. **Reporting quality:** ledger `not_executed` reason is generic for sector-blocked and non-TRADEABLE signals; the quote-age metric is bounded by cycle start.
8. **The dashboard is not connected** to the live runtime (the old synthetic-state build); `avdi_runtime.py status` / the acceptance script are the current views.
9. **Statistical:** one session, 12 independent events, 2 trades. Nothing here is evidence of profitability.

## Explicit answers
1. **Did Ubuntu actually run all day without human intervention?** Yes. 29/29 slots done, 0 restarts, 0 errors; no manual action touched the runtime.
2. **Did repeated 15-minute discovery work in the real market?** Yes — 26/26 cycles, median lateness 2.6 s, average 34 s.
3. **Did later scans discover anything the first scan did not?** Yes — AAPL, TMO, VRTX, MSFT, FCX, NEM, TSLA first appeared after 09:35 (12 symbols total vs 5 at the first scan).
4. **Did candidate state change correctly intraday?** Yes — DELL MONITOR→TRADEABLE (12:05) and META →TRADEABLE (13:05) were journaled once and traded; unchanged observations were not re-journaled. Whether the label flips near the conviction threshold (e.g. META) carry information is a strategy question, not an infrastructure one.
5. **Did the paper broker receive every valid eligible TRADEABLE?** The two TRADEABLEs that had capacity (DELL, META) were entered at the first eligible cycle. Every other TRADEABLE was stopped **before** the broker by a designed limit (sector cap for AAPL; the 2-per-day cap for TMO/MSFT; the entry cutoff for TSLA) — no broker refusal occurred.
6. **Did any valid candidate disappear because of infrastructure rather than strategy?** None observed: 0 missed/failed cycles, 0 stale quotes, 0 provider failures. TradingView retry noise could in principle perturb a single cycle; no evidence it did.
7. **Did event clustering prevent repeated observations from inflating the sample?** Yes — 133 observations collapsed to 12 events (AMD 26 → 1, DELL 26 → 1, CRM 25 → 1).
8. **Did shadow evidence persist and back up correctly?** Persisted and verified (integrity ok; local backup at close; off-host copy verified). The automatic off-host path is unreliable — see risk 2.
9. **Did CH-001 collect usable forward evidence?** No. No position reached +1 R, and the tables were contaminated with v1.0 positions (bug).
10. **Is the runtime ready to collect Profit Mode evidence every market day?** Yes for discovery, tracking, shadow logging, persistence and local backup. Before treating the evidence as clean: fix the CH-001 contamination, and make off-host backup independent of GitHub's schedule (or accept manual dispatch). Reboot and holiday behaviour remain unverified.
