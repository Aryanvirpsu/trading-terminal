# CH-005 — Pullback in an Uptrend: pre-registration

Registered 2026-10-22 (ET). **No outcome of this strategy has been computed on any real data before this document is hash-locked.** The only code run on real data so
far is the data fetch and the data audit (prices only). The engine was written and tested on synthetic series only (`tests/unit/test_ch005_engine.py`, 24 tests).
The rules below are the owner's specification (components of a public QuantConnect pullback strategy and the owner's CTO directive of 2026-10-22); the remaining
ambiguities are resolved here. **Nothing in this protocol is tuned.** The three periods exist for consistency and reporting, not for parameter selection.

## 1. Question
Does buying liquid US large caps that pull back inside an intact uptrend, with a hard protective stop, earn positive **net** expectancy after realistic costs, under
AVDI's $500 cash-account limits, and does it beat a matched control that uses the same trend filter, exits, sizing and costs but no pullback condition?
Hypothesis H1: net expectancy per trade is above zero at 15 bps per side **and** above what random trend-only entries earn.
A negative or null result is a valid result and will be reported as such.

## 2. Data (frozen by hash)
* Source: Yahoo Finance chart API v8, interval 1d, requested 2014-01-01 .. 2026-10-22 (last completed session 2026-10-21). Fetched 2026-10-22 by `fetch_data.py`.
* **Universe:** the 90 stocks of the AVDI sector map (`dashboard/sector_map.py`, 11 sectors) with their AVDI sector, plus SPY as the benchmark/market filter. The list and sector
  assignment are `data/universe.json` (sha256 `c196da609fd942abf22db2048e8f19963ebd5264789a9059379a5e760cdd70fc`). A ticker listed under two sectors keeps its first sector.
* **Prices:** `data/prices_daily.json.gz` (sha256 `8438e2f6ec6def85c570f947e2c9628607186c3e344dc58c97c4188ae32fffc1`). Open/high/low/close are **split-adjusted but not dividend-adjusted**
  (tradable prices); volume is split-adjusted, so close x volume is true dollar volume. Dividends are not credited (a small, conservative omission over holds of at most 16 sessions).
* **Session calendar:** the SPY session dates. Session date = the UTC date of Yahoo's bar timestamp (09:30 ET open = 13:30 or 14:30 UTC, the same calendar date); every series was checked
  to be strictly increasing, on weekdays, and contained in the SPY calendar. A symbol missing a session has NaN for it; indicators needing it are NaN; it cannot be traded that day.
* **Audit (`data/data_audit.json`)**: no OHLC violations, no non-positive prices, no negative volume, no dates outside the SPY calendar, last bar is a completed session, timezone
  America/New_York for every symbol. Findings and decisions: **AVB** has 1 bar (a corporate-action artifact in Aug 2026) and can never meet the 200-session requirement, so it never trades;
  CEG (from 2022-01), UBER (2019-05), DELL (2016-08) start later and trade only once they have 200 sessions; **OXY -53% on 2020-03-09 is the genuine oil crash** and is kept;
  AMD and CCI each have one zero-volume day (kept; it only lowers a 21-day average once).
* **Why not the Historical Lab (branch `h1/historical-lab`)**: audited 2026-10-22. It is a careful, well-tested harness (isolation guard, lookahead guard, hashed datasets, corporate-action
  detection, real scanner/decision path), but (a) its dataset is 5-minute fabhaus bars with `RELATIVE_ONLY` volume, which cannot evaluate an absolute $5M dollar-volume rule;
  (b) it neutralises 7 of the 9 decision-engine evidence families, so its Champion replay is a price-trend-only proxy; (c) its Champion baseline (HIST-001) completed only a one-month
  smoke stage with zero decisions (the scanner needs 55 daily bars); (d) it lives on an unmerged branch with a separate dependency set. CH-005 therefore uses its own small daily-bar engine
  and adopts the lab's principles (no look-ahead, hashed inputs, fail-closed data rules, single-shot evaluation).

## 3. Signal (evaluated at the CLOSE of session t, using bars up to and including t only)
A symbol is a **pullback candidate** at t iff ALL hold:
1. `close_t > SMA200_t` (simple mean of the last 200 closes, including t).
2. `close_t < SMA15_t - 1.75 x SD15_t`, where SMA15/SD15 are the mean and **population** standard deviation (ddof = 0) of the last 15 closes including t, and SD15 > 0.
3. `RSI14_t <= 45`: Wilder RSI, seeded with the simple mean of the first 14 close-to-close changes of the symbol's own history, then `avg = (avg x 13 + new)/14`.
4. `close_t <= 0.97 x max(close over the last 60 sessions including t)`.
5. `mean(close x volume over the last 21 sessions including t) >= $5,000,000`.
6. **Market filter:** `SPY close_t > SPY SMA200_t`.
7. `ATR14_t` is defined and positive (Wilder ATR of true range, same seeding, used only for the stop).
Rank among candidates: larger `(SMA15 - close) / SD15` first; ties by lower RSI, then symbol name. A signal is valid **only for the next session's open** (no carry-over, no queue).

## 4. Execution and exits
* **Entry:** at the OPEN of session t+1: `fill = open x (1 + c)`, where c is the per-side cost (section 6).
* **Protective stop (frozen):** `stop = fill - 2.0 x ATR14_t` (ATR of the signal day). Active from the entry session onward, **including the entry session itself** (stop-first on any bar whose low
  is at or below the stop). Fill: `stop x (1 - c)`; if a later session OPENS at or below the stop, fill `open x (1 - c - 0.0020)` (gap rule: slippage + 20 bps). A planned exit whose open is at
  or below the stop also uses the gap cost.
* **Mean-recovery exit:** if at a close `close >= SMA15`, exit at the next open (`open x (1 - c)`).
* **Trend-invalidation exit:** if at a close `close < SMA200`, exit at the next open. Priority at the same close: trend invalidation, then recovery, then time.
* **Time exit:** the entry session is session 1; if still open at the close of session 15, exit at the open of session 16.
* No profit target, no trailing stop, no re-entry restriction (a symbol may be bought again after it is closed). A position open at the end of the data is liquidated at the last close with the exit cost and labelled `open_at_end`.

## 5. Account and portfolio rules (AVDI Champion limits, unchanged)
* Start: $500 cash, no margin, no shorting, fractional shares allowed, long only.
* Planned risk per trade **$5**: `risk/share = fill - stop x (1 - c)`; `shares = min(5 / risk-per-share, 125 / fill, (settled cash - 100) / fill)`; skip if notional < $10.
* Max position notional **$125**; max **3** open positions; max **2** entries per session; **one open position per AVDI sector**; cash reserve **$100**.
* **Cash settlement (T+1):** sale proceeds are withheld on the sale date and become spendable at the next session; position slots free immediately (the convention of the existing profit review).
* No new entries on a session where that session's open-time exits realised a loss of $10 or more (daily loss limit), nor while equity is $50 or more below its peak close (drawdown limit).
* Not modelled (documented omissions): the post-losing-streak cooldown and correlation groups; their omission makes results slightly optimistic.
* Orders are paper only. Nothing here touches production, `ROBINHOOD_TRADING_ENABLED` or `BROKER_PROVIDER`.

## 6. Costs (the convention)
`c` = the **per-side** adverse cost in basis points applied to every fill: buys at `price x (1 + c)`, sells at `price x (1 - c)`; round trip is about `2c`. Cases **5, 15 and 30 bps**.
**15 bps is primary** (more conservative than the Champion's 5 bps paper model, because fills are at the open and the book is modelled, not observed); all pass criteria are evaluated at 15 bps,
with 30 bps required to stay profitable. Gap extra is +20 bps. Fees $0 (Robinhood), SEC/TAF fees not modelled.

## 7. Periods (chronological; entries are decided by signal date; trades are followed to their exit, even after the period ends)
| Period | Signal dates | Role |
|---|---|---|
| Train | 2015-01-02 .. 2019-12-31 | Reported for consistency; **nothing is fitted** |
| Validation | 2020-01-02 .. 2022-12-30 | Must not reverse the sign of the result (C7); includes the 2020 crash and the 2022 bear market |
| **Test (untouched)** | 2023-01-03 .. 2026-10-21 | Evaluated exactly once. Pass/fail is decided here |
Each period is an independent account starting at $500 and flat. The runner executes train, validation, then test in one run and refuses to run twice.

## 8. Matched control (survivorship and drift guard)
Same universe, SPY>SMA200 filter, `close > SMA200`, dollar-volume rule, stop, exits, sizing, limits, settlement and costs, but the entry set is **every trend-only eligible stock** (no Bollinger, RSI or
pullback condition), chosen **uniformly at random** each day subject to the same limits. 200 seeds (test) / 100 seeds (train, validation), 15 bps. The control absorbs the upward drift of a
survivor universe: an edge exists only if the pullback strategy does better than random trend-only entries.

## 9. Reported (all three periods, 5/15/30 bps)
Trades; net P&L; expectancy per trade in $ and R; profit factor; win rate; average win/loss; max drawdown ($ and %); return on capital used (net P&L / average capital deployed); net P&L per occupied slot-day;
exit-reason breakdown; test-period breakdown by year, by SPY 20-day realised-volatility tercile (cut points frozen from the TRAIN period) and by SPY above/below its 50-day average; sector breakdown;
SPY buy-and-hold for the same window; control distribution and the strategy's percentile in it. Comparison with the live Champion is **context only** (its 6 closed trades are not a comparable sample).

## 10. Pass criteria (all evaluated on the TEST period at 15 bps unless stated)
* C1 at least **60** trades.  C2 net P&L > 0 at 15 bps **and** at 30 bps.  C3 profit factor >= **1.25**.
* C4 expectancy >= **+0.10 R** per trade and the 10th percentile of a 10,000-draw trade bootstrap (seed 20261022) of expectancy is > 0.
* C5 max drawdown <= **$50** (the account's own limit).
* C6 expectancy (R) >= the **90th percentile** of the matched-control distribution.
* C7 validation net P&L > 0 at 15 bps (no sign reversal).
* C8 removing the single best trade leaves net P&L > 0, and no single symbol contributes more than **40%** of net profit.
**Verdict:** all eight pass -> "SUPPORTS FURTHER PAPER TESTING" (a forward paper test of the same frozen rules; never a promotion, never live). Any failure -> "NOT SUPPORTED". There is no partial credit and no
re-run with changed parameters; any variant would be a new, separately registered challenger (CH-006 ...).

## 11. Known biases and limitations (disclosed before the run)
1. **Survivorship (upward bias):** the universe is today's AVDI sector map; companies that were delisted or fell out of the large-cap set are absent. Mitigation: criterion C6 and the control.
2. **Hindsight in the period choice:** the owner and the author know roughly how markets behaved in 2023-2026; the test window is simply "everything after the validation window up to the data end".
   The only price data seen before this lock are daily SPY closes for September-October 2026 (used for the Oct 2026 benchmark) and the live Champion's own trades; no CH-005 outcome.
3. Daily bars cannot order events within a day (stop-first is assumed on any bar touching the stop); next-open fills ignore intraday impact; costs are modelled, not observed.
4. Dividends excluded; no cooldown or correlation-group rules.
5. Trades are not independent (overlapping holds, shared market moves); the bootstrap resamples trades and likely understates uncertainty.
6. Only 90 names, 11 sectors, three slots: expect few trades per year; C1 may fail for lack of opportunities, which is itself a finding.
7. Past performance of a rule on this history is not evidence of future profit; the Champion's 6-trade live record is not comparable.

## 12. Commitments
* Single shot: `ch005.py` verifies the lock on this file, the data hashes and the code hashes, and refuses to overwrite results.
* Any change to this protocol after the lock requires a dated `AMENDMENT_n.md` written **before** any further run and cannot alter section 10 after results exist.
* `auto_promote` is false. CH-004 stays paused. Production code, limits, strategy parameters and broker flags are untouched.
