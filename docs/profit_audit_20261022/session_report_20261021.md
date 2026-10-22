# AVDI session report — 2026-10-21  (evidence: backup 20261022T042050Z taken 2026-10-22T04:20Z; generated 2026-10-22 04:40Z)

## 1. Money
* Account: equity **$500.23** on $500 = **$+0.23** since inception (+0.05%). Realized **$-2.54**, unrealized **$+2.76** (marked at the backup's last bar, not live).
* This session: equity change **$-0.65**; exits today: none.
* Max drawdown $10.43 (2.06%); capital deployed on average $142.74 (28.5%); modeled costs: entries 5.0 bps, exits 3.33 bps, fees $0.
* SPY same window: +2.01% (strategy +0.05%; not exposure-matched).

## 2. What caused it
* decision_engine/gates-v1: 2 closed, 1 wins, net **$+4.66**, profit factor 1.85, expectancy $+2.33/trade (0.475 R)
* decision_engine/gates-v1.1: 4 closed, 1 wins, net **$-7.20**, profit factor 0.538, expectancy $-1.80/trade (-0.272 R)
* By strategy: liquid_momentum 2 trades $-10.58; sector_relative_strength 4 trades $+8.04
* By exit: exit_stop 4 trades $-21.06; exit_target 2 trades $+18.52. Stops: 4 of 6 closed trades.
* Orders filled this session: 0

## 3. Identified but not executed (counts only; no hypothetical gain is assumed)
* TRADEABLE events not executed this session: **1**
  * 1 event(s), 2 observations: capacity `-`; entries disabled (post_cutoff) - observation only
  * 1 event(s), 14 observations: capacity `-`; entries disabled (regular) - observation only
* Last 7 days: 10 TRADEABLE events not executed. Their later prices were not examined.

## 4. What is blocking validated opportunities
* Entries disabled by the ledger check: 66 events ({'2026-10-19': 22, '2026-10-20': 22, '2026-10-21': 22}). **Entries were disabled by the ledger check on this session.** (runtime that produced this backup: `unknown`)
* Open slots in use: 1 of 3 (UNH health_care); free cash after the $100 reserve is limited by the $125 position cap.
* Trade frequency 0.241 entries/session; at $5 planned risk a +2R winner earns about $10, so the account cannot reach large dollar targets by volume.

## 5. Highest-value next change
* Entries were disabled this session and the backup does not record its build (pre-deploy snapshots are taken by a fresh container). If the reconcile fix was deployed after this session, **confirm the next session's report shows no `entries_disabled` events**; if it was not, deploy it.

## Advisory: event risk on open positions (trader-news; never a gate)
* UNH: last earnings 8-K filed 2026-10-13 (9 days ago). Reports are roughly quarterly, so a new earnings gap is **unlikely within the next ~6 weeks**.
* Headline flags below are KEYWORD MATCHES on news titles, not event dates; treat them as prompts to read, not as signals: 3 item(s).
  * 2026-10-21 20:20 ET UNH [earnings] Yahoo Finance UnitedHealth's Earnings Jumped 60%. Is the Comeback Stock a Buy, Sell, or Hold?
  * 2026-10-21 12:04 ET UNH [earnings, people] Yahoo Finance What Elevance Health (ELV) Said on Its Q3 Earnings Call
  * 2026-10-21 10:38 ET UNH [earnings, macro] Yahoo Finance UnitedHealth (UNH): Does Optum Truly Diversify Its Earnings Risk?

