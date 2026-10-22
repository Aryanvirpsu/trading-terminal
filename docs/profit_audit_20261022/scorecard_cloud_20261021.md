# AVDI scorecard — evidence through 2026-10-21 (backup 20261021T202501Z, generated 2026-10-22T02:26Z)

**Net P&L $+0.23** (+0.05% on $500): realized $-2.54, unrealized $+2.76. Max drawdown $10.43 (2.06%). Open: [('UNH', 'decision_engine/gates-v1.1')]

**vs SPY 2026-09-10→2026-10-21:** strategy +0.05% vs SPY +2.01% (excess -1.97 pts; not exposure-matched; the account averaged 28% invested)

| engine | trades | win rate | net $ | profit factor | expectancy $/trade | expectancy R | avg win | avg loss | payoff |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| decision_engine/gates-v1 | 2 | 50% | +4.66 | 1.85 | +2.33 | 0.475 | 10.14 | -5.48 | 1.85 |
| decision_engine/gates-v1.1 | 4 | 25% | -7.20 | 0.538 | -1.80 | -0.272 | 8.38 | -5.19 | 1.61 |
| POOLED (reconcile only) | 6 | 33% | -2.54 | 0.879 | -0.42 | -0.023 | 9.26 | -5.27 | 1.76 |

Frequency: 7 entries in 29 sessions (0.241/session); capital deployed on average $142.74 (28.5%); entries disabled by the ledger check 66× ({'2026-10-19': 22, '2026-10-20': 22, '2026-10-21': 22}).
Modeled costs: entries 5.0 bps, exits 3.33 bps, fees $0; 14 phantom fills excluded.

| closed trade | strategy | sector | exit | hold d | net $ | R |
|---|---|---|---|---:|---:|---:|
| HAL (v1) | sector_relative_strength | energy | exit_stop | 6.0 | -5.48 | -1.1 |
| AMD (v1) | sector_relative_strength | technology | exit_target | 4.0 | +10.14 | 2.05 |
| DELL (v1.1) | liquid_momentum | technology | exit_stop | 19.0 | -5.28 | -1.06 |
| META (v1.1) | liquid_momentum | communication | exit_stop | 21.1 | -5.30 | -1.06 |
| TMO (v1.1) | sector_relative_strength | health_care | exit_stop | 2.0 | -5.00 | -1.13 |
| EOG (v1.1) | sector_relative_strength | energy | exit_target | 8.2 | +8.38 | 2.16 |
