# The 11 trader skills: review and integration decisions (2026-10-22)

Installed from `Jakeschincariol/day-trader-skill` (commit `3d0f4ef`) after a code audit (standard-library Python, public data only, writes only to a `trader/` folder, the one subprocess is an optional `pdftotext`).
AVDI's strategy gates, `risk.py` ($5 / $125 / 3 positions / 1 per sector / 2 per day / $100 reserve / $10 daily loss / $50 drawdown) and ledger remain authoritative. `trader-broker` is **not** connected.

## What was actually run
| Skill | Evidence |
|---|---|
| `trader-news` | Run live on the open position UNH: returned price vs prior close, ~12 headlines with keyword tags, SEC 8-K lookups. Works. |
| `trader-analyst` | Run live on UNH: six quarters of SEC XBRL margins/EPS and the date of the last earnings 8-K (2026-10-13). Works and is the more reliable event signal. |
| the other nine | Read (SKILL.md and code) and covered by the pack's own 315 offline tests (all pass); **not** run on live data in this review. |
| CH-005 | The pack's scanner/entry/exit/time-machine ideas (pullback and opening-range setups) were tested the rigorous way instead: CH-005 pullback-in-uptrend is **NOT SUPPORTED** (see `experiments/ch005_pullback_uptrend/RESULTS.md`). |

## Decisions
| Skill | Overlap with AVDI | Decision | Measured contribution so far |
|---|---|---|---|
| `trader-scanner` | AVDI already has a sector-ranked funnel (strongest sectors -> <=5 finalists -> decision engine) over ~90-630 names; the scanner covers ~5,900 | **Standalone.** Possible later as a *wider candidate source feeding the Champion's own gates*, but any ORB/pullback rule it implements must pass a pre-registered test first; the pullback family just failed | none |
| `trader-news` | The decision engine has a catalyst family; the lab neutralises it historically | **Integrated as an advisory line** in the daily report for open positions (`scripts/avdi_daily_report.py --event-risk`). Never a gate | none yet; flags are keyword matches, so noisy (it tagged generic "earnings" articles for UNH) |
| `trader-analyst` | Same | **Integrated (advisory)**: last-earnings-8-K date -> "earnings gap unlikely within ~6 weeks" | none yet. It correctly shows UNH reported on Oct 13, two days before the Champion entered |
| `trader-entry` | Champion entry is a gate decision, not a trigger | **Standalone.** Its only rule (first close above the opening range) is the untested ORB family | none |
| `trader-exit` | Stop/target already come from AVDI's decision engine | **Standalone.** Its 1R/2R/ATR options duplicate existing exits; exit changes go through CH-001 (breakeven) | none |
| `trader-alerts` | The 60 s tracker already manages stops/targets | **Standalone.** Useful only for day trades, which AVDI does not take | none |
| `trader-timemachine` | Bootstrap of the stock's own last 60 sessions | **Standalone, sanity check only.** A resampling is not evidence (its own example ends "close to a coin flip") | none |
| `trader-risk`, `trader-budget` | AVDI's `risk.py` and sizing are authoritative and differ (configurable limits vs fixed dollars) | **Not integrated** (two risk engines could disagree and one could be loosened by mistake) | n/a |
| `trader-broker` | AVDI is paper-only; no broker configured | **Not connected, by instruction** | n/a |
| `trader-pelosi` | STOCK Act filings arrive up to 45 days after the trade | **Deprioritised**: no timing edge for a short-horizon system | none |

## Honest summary
Integrated into AVDI's reporting: two advisory lines (news and analyst) for open positions. Integrated into decisions: none. Expected contribution: small (avoiding an earnings gap on a held name), and **not yet measured**.
To measure it without touching the pipeline: append each day's flags for open positions to an append-only file and, after >= 30 flagged-versus-unflagged position-days, compare outcomes. Do not claim value before that.
