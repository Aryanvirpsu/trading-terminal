# CH-005 — Pullback in an Uptrend: results

Run once on 2026-10-22 under the locks (`PREREG.lock.json`, `CODE.lock.json`); raw output `ch005_results.json`; reproduce with `python experiments/ch005_pullback_uptrend/ch005.py --reproduce`.

## Verdict (pre-registered criteria, test period 2023-01-03 .. 2026-10-21, 15 bps per side): **NOT SUPPORTED**
All eight criteria failed. This is a clear negative result; it is not a data or engine problem (see "How we know the result is genuine").
Evidence does **not** support further paper testing of these rules. Nothing was promoted and no production setting changed.

| Criterion | Required | Observed (test, 15 bps) | Pass |
|---|---|---|---|
| C1 trades | >= 60 | **26** | no |
| C2 net P&L at 15 and 30 bps | > 0 | **-$55.62** / -$55.02 | no |
| C3 profit factor | >= 1.25 | **0.21** | no |
| C4 expectancy and bootstrap p10 | >= +0.10 R and p10 > 0 | **-0.447 R**, p10 -0.600 | no |
| C5 max drawdown | <= $50 | **$55.62** | no |
| C6 vs matched control | >= control 90th percentile | -0.447 R vs control p90 -0.045 (0th percentile) | no |
| C7 validation net P&L at 15 bps | > 0 | **-$14.33** | no |
| C8 best-trade / single-symbol robustness | net > 0 without best trade; top symbol <= 40% | net without best trade -$61.29 | no |

## What the numbers say

| Period (signal dates) | Cost | Trades | Net P&L | Win rate | Avg win / loss | Profit factor | Expectancy | Max drawdown | Net per occupied slot-day |
|---|---|---:|---:|---:|---|---:|---|---:|---:|
| Train 2015-2019 | 5 bps | 65 | -$42.54 | 43% | +$2.82 / -$3.29 | 0.65 | -0.120 R | $55.84 | -$0.094 |
| | 15 | 58 | -$41.83 | 45% | +$2.75 / -$3.54 | 0.63 | -0.138 R | $51.23 | -$0.106 |
| | 30 | 47 | -$41.62 | 38% | +$2.42 / -$2.94 | 0.51 | -0.185 R | $53.58 | -$0.124 |
| Validation 2020-2022 | 5 | 145 | +$5.58 | 57% | +$3.05 / -$3.89 | 1.02 | +0.006 R | $57.08 | +$0.006 |
| | 15 | 141 | -$14.33 | 56% | +$2.90 / -$3.93 | 0.94 | -0.023 R | $58.43 | -$0.015 |
| | 30 | 147 | -$45.19 | 50% | +$2.97 / -$3.54 | 0.83 | -0.068 R | $57.75 | -$0.046 |
| **Test 2023-2026** | 5 | 27 | -$53.42 | 19% | +$3.19 / -$3.15 | 0.23 | -0.415 R | $53.71 | -$0.312 |
| | **15** | **26** | **-$55.62** | **19%** | +$3.03 / -$3.37 | **0.21** | **-0.447 R** | **$55.62** | **-$0.333** |
| | 30 | 26 | -$55.02 | 19% | +$3.33 / -$3.41 | 0.23 | -0.421 R | $57.17 | -$0.326 |

* **Return on capital used (test, 15 bps):** -328% of the average capital deployed (about $17 over the window). Annualisation is meaningless; the point is that the capital that was used lost money.
* **Test exits (15 bps):** 9 protective stops -$42.97, 10 trend invalidations -$24.89, 6 mean recoveries **+$14.54**, 1 time exit -$2.30. Only the recovery exits made money, and they were too few and too small.
* **Cost sensitivity:** costs are not the main problem in the test (5 bps -$53.42 vs 30 bps -$55.02). In validation a profit of +$5.58 at 5 bps becomes -$45.19 at 30 bps, i.e. any apparent edge is smaller than a realistic cost.
* **Regimes (test):** all 26 trades fell in Jan-May 2023. SPY 20-day volatility: high tercile 19 trades -$56.50 (-0.63 R); mid 7 trades +$0.88 (+0.04 R). SPY above its 50-day average 24 trades -$49.24; below 2 trades -$6.38. By sector the damage was in health care (-$24.72) and financials (-$12.30); only technology (+$5.12) and industrials (+$2.94) were positive. SPY itself returned +103% over the same test window (buy-and-hold, price only).
* **Matched control (random trend-only entries, same exits/sizing/costs, 15 bps):** expectancy -0.099 R (mean), p90 -0.045 R in the test; the pullback rules ranked at the **0th percentile** (worse than every one of 200 random seeds). In train the rules ranked 9th percentile, in validation 85th. Selection adds no robust value over random trend-following entries.
* **Comparison with the live Champion (context only, not a comparable sample):** the Champion has 6 closed live paper trades (profit factor 0.88 pooled; 0.54 under the current engine, 4 trades) and +$0.23 net over 29 sessions. CH-005's rules would have done worse than that in the test window. Neither sample can establish an edge.

## An important feature of the result: AVDI's own kill switch
In every period the strategy burned through the **$50 drawdown allowance** and, as in production, entries then stopped for good (a cash account with no open positions cannot recover equity): the train period traded only in 2015 (halt after 2015-10), the validation period until 2021-09, and the test period only until 2023-05, so the test verdict rests on about five months of trading. This is a legitimate consequence of the pre-registered constraints (the system really would have halted), but it limits what the test window shows.

**Post-hoc diagnostic D1 (exploratory, not part of the verdict; `diagnostic_d1.py`, `diagnostic_d1_results.json`):** with only the drawdown halt switched off, run on train and validation only (the test period was deliberately **not** run in this variant, so it stays unspent):

| | Trades | Net P&L | Profit factor | Expectancy | Bootstrap p10 | Max drawdown |
|---|---:|---:|---:|---|---|---:|
| Train, 5 bps | 379 | +$94.90 | 1.17 | +0.054 R | -0.004 | $65 |
| Train, 15 bps | 382 | +$17.75 | 1.03 | +0.002 R | -0.054 | $76 |
| Train, 30 bps | 378 | -$124.54 | 0.80 | -0.093 R | -0.146 | $139 |
| Validation, 5 bps | 178 | -$15.56 | 0.95 | -0.024 R | -0.107 | $78 |
| Validation, 15 bps | 178 | -$50.32 | 0.84 | -0.070 R | -0.151 | $94 |
| Validation, 30 bps | 181 | -$94.13 | 0.72 | -0.111 R | -0.187 | $104 |

Even with no kill switch the rules earn about **zero at 15 bps** (train +0.002 R, validation -0.070 R), are clearly negative at 30 bps, and only look positive at the Champion's optimistic 5 bps in one period. Against the random control, selection was at the 100th percentile in train but the 11th in validation: not robust. So the halt did not create the failure; it only ended the account sooner. A halt-free account is also not deployable under AVDI's limits (drawdown up to $76-$139 against a $50 allowance).

## How we know the result is genuine
* The 225 trades across the three periods were **independently re-derived** from the raw prices with separate pandas code: every signal condition (200-day trend, Bollinger, RSI, 3% below the 60-day high, dollar volume, SPY filter), the next-open entry price and the 2xATR stop all matched (0 mismatches); sample exits (a stop fill, a mean-recovery fill) matched to the cent.
* The engine has 24 synthetic-data tests (causality/truncation invariance, each rule, entry timing, stop and gap fills, exit priority, time exit, limits, T+1 cash settlement with a control).
* Data audit: no integrity violations; the universe, prices and code are pinned by sha256 and verified by the runner before every run.

## Caveats (unchanged from the pre-registration)
Survivorship upward bias (still negative); daily bars cannot order intra-day events; modelled costs; dividends not credited; trades are not independent; the test window effectively covers Jan-May 2023 because of the kill switch.

## What this means
These rules do not produce a tradeable edge after realistic costs under a $500 cash account with a $5-risk, 3-slot, $50-drawdown envelope. **No further paper testing is justified for CH-005 as specified.** Any variation (different stop, a deeper-pullback filter, a different exit or universe, evaluating without account halts using risk-unit accounting) would be a new, separately pre-registered challenger (CH-006 ...) and must not reuse the test period for the same rules. The 2023-2026 data remain unspent for genuinely different strategy families.
