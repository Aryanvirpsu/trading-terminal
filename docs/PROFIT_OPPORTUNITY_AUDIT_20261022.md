# AVDI profit-opportunity audit and ranked strategy plan

> **UPDATE (later on 2026-10-22), superseding parts of sections 2, 5 and 6 below:**
> * PR #11 was approved, merged and deployed (`main` = `54adbea`); the host is `healthy: true`, `reconciled: true`, delta 0.0, 14 excluded phantom fills, `history_resolved: false`. See `docs/LEDGER_RECOVERY_DEPLOYMENT_RESULT_20261022.md`. The Champion can trade again; the first session on the new build is still to be verified.
> * **Rank 2 (CH-005 pullback in an uptrend) was pre-registered, hash-locked and run once: NOT SUPPORTED.** Test 2023-2026 at 15 bps: 26 trades, net -$55.62, profit factor 0.21, expectancy -0.447 R, ranked at the 0th percentile of 200 random trend-only controls; the rules breached AVDI's $50 drawdown allowance in every period; a halt-free diagnostic on train+validation shows about zero edge at 15 bps and a loss at 30 bps. Branch `research/ch-005-pullback`, `experiments/ch005_pullback_uptrend/RESULTS.md`. **Do not pursue it further as specified.**
> * Ranks 1 and 3 remain open. The skills review is `docs/TRADER_SKILLS_REVIEW_20261022.md`; the daily accountability report is `scripts/avdi_daily_report.py`.


Written 2026-10-22 (ET). Evidence through the **2026-10-21 close** (cloud ledger, host backup `20261021T202501Z`, hash-verified).
Method: read-only. Money numbers come from the ledger via `scripts/avdi_scorecard.py` (rerun it after every close). Strategy counts below are
**decision-time facts only** (how many candidates and events, which gates fired). No candidate or challenger *outcome* was read for this audit.
Constraints held: capital and risk limits unchanged, live execution off, order-specific approval untouched, nothing merged or deployed.

## 1. The money, first

| | Value |
|---|---|
| Net P&L, 29 sessions (Sep 10 → Oct 21) | **+$0.23** (+0.05% on $500): realized **−$2.54**, unrealized +$2.76 (UNH) |
| SPY, same window | **+2.01%**. Excess **−1.97 points** (the account averaged 28.5% invested, so this is not exposure-matched) |
| Max drawdown (daily snapshots) | **$10.43 / 2.06%**, trough Oct 16 |
| Closed trades | 6: **2 wins, 4 losses**; 7 entries (0.24 per session) |
| Profit factor / expectancy | pooled 0.88 / −$0.42 per trade (−0.02 R). **v1.1 only (the current engine): 4 trades, 1 win, PF 0.54, −$1.80 per trade, −0.27 R**; v1.0: 2 trades, +$4.66 |
| Winners vs losers | wins +$10.14 (+2.05 R) and +$8.38 (+2.16 R); the four stops lost 1.06–1.13 R each (the modeled stop fill gaps through the stop) |
| Modeled costs | entries 5.0 bps, exits 3.3 bps, fees $0 → **$0.53 total**. Costs are not what is losing money |
| Capital efficiency | 28.5% of equity invested on average; **0.0054 $ of net P&L per $100 deployed per session** |

**Reading it honestly:** with 6 closed trades and 4 under the current engine, nothing here shows an edge, positive or negative. The data only
says the Champion has not yet earned its keep, and that it has traded **0 times since Oct 15**.

## 2. Where the money is blocked or leaking (ranked by what is fixable and worth fixing)

1. **The Champion cannot trade right now.** The ledger check has disabled entries on every cycle since Oct 19 (66 events, 3 sessions, 0 entries).
   Fix is ready (PR #11, draft, CI green, deployment plan written). **This is the highest-value item and it is waiting only on your approval.**
2. **Slots, not signals, are the scarce resource.** The funnel produced about **43 TRADEABLE events in 19 sessions (2.3 per session)** but only 5 entries in that
   same window (12%; 7 in all, including the two v1.0 trades). Three slots were occupied for **54% of available slot-days** (66.5 of 123.4), mostly by DELL (19 days) and META (21 days); the average
   closed trade was held **10 days**. Profit per slot-day is the number to move (replay: −$0.057 per slot-day, Oct 12 review).
3. **The risk limits cap what a win can earn.** At $5 planned loss per trade, a +2 R winner is about **+$10**. At the observed 1.2 entries per week,
   even if *every* trade won, the ceiling is about **$12 a week**; at the daily cap of 2 entries (10 a week) all-winners is $100. See §3.
4. **The engine cannot select pullbacks.** `mean_reversion` is an active strategy, yet in 19 sessions it produced 14 events and **0 TRADEABLE**:
   251 observations, every one failed `conviction`, and 70% (176) also failed the hard `quality_threshold`. The quality score rewards momentum alignment, so an oversold
   pullback is structurally scored low. Whether that is a bug or the right call is unknown, but it means the system is a pure momentum / relative-strength
   book and cannot diversify into a second, different source of edge.
5. **Execution realism is unproven.** Bid/ask are a modeled 10 bps spread around last price, not observed books; a cash account's T+1 settlement is not
   modeled (the 4th-slot "gain" disappeared when sale proceeds were withheld); a quote-provenance bug fix is prepared but not deployed.
6. **Duplicate-exit defect** (resolved in PR #11): cost the ledger its health, not money.

## 3. The stated target, as arithmetic

$2,000 by January 1 on $500 is a **400% return in ~10 weeks** (about $200 a week from now; the project's own note quotes $173 a week from Oct 12). At today's
limits, a winner earns about $10, so that needs roughly **20 winners per week with no losers**, while the account is allowed 10 entries a week in total.
It cannot be reached by trading more or by a better entry rule. It could only be approached by raising position size or risk (which needs your explicit
authorization and is how accounts blow up) or by an edge far larger than anything measured. AVDI's job is to report that plainly, not to chase it.
**Recommendation: replace the dollar target with measured targets: positive expectancy after costs over ≥50 resolved trades, drawdown inside the limit, and
SPY-relative results.** Never open a weaker trade to repair a weekly shortfall. "No trade" is a legitimate output (the engine already returned it on most cycles).

## 4. The eleven skills, by what they can add

| Group | Skill | Use for | Verdict |
|---|---|---|---|
| Find the money | `trader-scanner` | Pre-market / opening-range candidates across ~5,900 stocks | **Keep.** Broadest candidate source; its output must be run through the Champion's gates and risk limits, not around them |
| | `trader-news` | Catalyst and 8-K flags on held names and finalists | **Keep as a filter** (avoid / explain), not a signal source |
| | `trader-analyst` | Earnings dates and margins | **Use only to avoid holding through earnings** |
| | `trader-pelosi` | Congressional disclosures | **Deprioritize.** Filings arrive up to 45 days after the trade; no timing edge for a short-horizon system |
| Capture it | `trader-entry`, `trader-exit` | Rule-based entry trigger, stop and target | **Keep** (they are the S3 rules below) |
| | `trader-budget`, `trader-risk` | Sizing and limit checks | **Keep, but subordinate**: AVDI's own risk gates stay authoritative |
| | `trader-broker` | Approval-gated order tickets | **Leave unconnected.** Paper only; no broker is configured and I did not set one up |
| Learn what pays | `trader-timemachine` | 10,000 bootstrap replays of 60 days | **Sanity check, not evidence.** It resamples the stock's own recent bars; the pack's own example ends "close to a coin flip" |
| | `trader-alerts` | Stop / target / flat-by-close alarms | **Keep** for day trades only |

Also deprioritized as not blocking profit: the React / cloud UI work, options (shadow only, not authorized), and **CH-004** (needs ~64 sessions for power; the
pause stays).

## 5. Ranked plan: the three strategies worth testing

None has demonstrated positive expectancy. They are ranked by *how fast real, trustworthy evidence can be obtained* and by profit potential.

### Rank 1. Champion v1.1 as the control, with the breakeven-after-+1R exit as the first challenger (CH-001)
* **Rules:** current gates; stop and target as planned; the challenger moves the stop to entry once price reaches +1 R.
* **Conditions / capital / risk:** unchanged ($5 per trade, $125 cap, 3 open, 1 per sector, 2 per day, $100 reserve).
* **Costs:** 5 / 15 / 30 bps cases, fees $0.
* **Out-of-sample evidence:** none yet. In-sample replay on 20 events: +0.34 R at 5 bps, but all of it from one event, and the three Champion-resolved paths are all stops.
* **Frequency / expected net:** same as the Champion (~1 to 2 entries a week); the effect is unknown and probably small (+0.34 R summed over 20 events in replay, from one event).
* **Failure scenarios:** cuts winners that retest entry (0 observed so far); the effect vanishes with more events.
* **Speed:** the shadow logger already records every event; with ~2.3 events a session, 30 independent events take about 13 sessions. Events are logged whether or not the Champion can enter.
* **Invalidated by:** any prematurely stopped winner that would have hit target, or Δ ≤ 0 on ≥30 events.

### Rank 2. Pullback-in-uptrend (mean reversion), daily bars, time exit
* **Rules (from the QuantConnect #659 components, to be locked before any run):** daily close above the 200-day average; close below the lower Bollinger band (15 days, 1.75 sd);
  RSI(14) ≤ 45; ≥3% below the 60-day high; $5M average dollar volume. Exit on recovery to the band midpoint, loss of trend, or 15 sessions. **Add a hard stop** (the source has none).
* **Conditions:** established uptrends in liquid large caps; stand aside when the index is below its 200-day average.
* **Capital / risk:** identical to the Champion's limits (the original ten-position full-investment sizing cannot be used).
* **Costs:** 5 / 15 / 30 bps plus a modeled overnight-gap rule on the stop.
* **Out-of-sample evidence:** **none.** The only numbers are platform claims (26% five-year drawdown on $1M) that this project has not reproduced.
* **Frequency / net:** unknown; the engine currently rejects every such candidate, so it adds a genuinely different source of trades.
* **Failure scenarios:** knife-catching in a falling market; long holds tying up slots (the same slot problem as §2); correlation with the momentum book in a sell-off.
* **Speed:** **fastest route to real evidence.** Years of free daily bars allow a locked-protocol train / validation / lock-out test in days, versus 10 weeks of paper sessions.
* **Invalidated by:** net expectancy ≤ 0 after 15 bps on the untouched lock-out period, or a result that depends on one year or one stock.

### Rank 3. Opening-range breakout day trade (`trader-scanner` → `entry` → `exit`)
* **Rules:** first 15-minute range; enter on the first 5-minute close above the high; stop at the range midpoint; 1 R target; flat by 15:55.
* **Capital / risk:** same limits. **Cash-account catch:** sale proceeds settle T+1, so capital cannot be recycled the same day, and the 3-position limit already binds.
* **Costs:** the most cost-sensitive of the three (many small moves against a 10 bps modeled spread).
* **Out-of-sample evidence:** none beyond the pack's own 60-day replay of one stock ("close to a coin flip").
* **Speed:** testable on the shadow store's 5-minute bars and the pack's 60-day data, but the history is short and in-sample risk is high.
* **Invalidated by:** negative expectancy after realistic costs, or any dependence on same-day capital recycling.
* **Why ranked last:** highest cost drag, shortest evidence, and the structural settlement limit.

**A fourth route exists and was missed by the CH-004 pre-lock audit: the Historical Lab.** Branch `h1/historical-lab` (worktree `wt_h4`, not merged to `main`, so a search of `main` finds nothing) replays the **real** scanner and decision engine unchanged on historical bars (`HistoricalAVDIContext`). It is the fastest way to get out-of-sample evidence for the Champion itself. My notes on its data blockers date from 2026-09-28 (the dataset's UTC field is really New York wall-clock; its volumes are 20-46% of Yahoo's; no corporate-action policy yet). Its later commits (a corporate-actions module and a pre-registered HIST-005 study of 3 vs 4 concurrent positions) suggest it has moved on, so **check its current audit status before trusting any number from it**; I did not run it.

**Not pursued now:** sector-rotation and asset-class trend ETFs (the original 3 to 5 equal positions exceed the $125 cap), QC #592 momentum (its cash-account claim and weight clipping are
defective per the watchlist review), options, Pelosi copying, conviction recalibration (CH-004).

## 6. What I would do next (and what needs you)

| Step | Who | Money relevance |
|---|---|---|
| 1. Approve and deploy PR #11 so the Champion can trade again | **You** (merge, deploy window) | Restores the only live evidence stream |
| 2. Run `scripts/avdi_scorecard.py` after every close; attribute each trade | Me, daily | Makes "which decisions made or lost money" a daily fact |
| 3. Pre-register **CH-005** (Rank 2) on daily bars with train / validation / **untouched lock-out**, 5 / 15 / 30 bps, our $500 constraints; hash-lock it; run once | Me, **after you confirm the protocol choices** (universe, history length, lock-out dates) | Only way to get real out-of-sample evidence this month |
| 4. Let the CH-001 breakeven challenger accumulate events in the shadow log | Automatic once step 1 is done | Cheapest exit-side evidence |
| 5. Decide what to do with the unresolved history marker and the dollar target (§3) | **You** | Keeps the scorecard honest |

**One decision I need from you now:** confirm that CH-005 (Rank 2) should be pre-registered and run on multi-year free daily data. It touches no production code,
no limits and no configuration, and the lock-out period is only opened once.

Files: `scripts/avdi_scorecard.py`, `docs/profit_audit_20261022/scorecard_cloud_20261021.{json,md}`.
