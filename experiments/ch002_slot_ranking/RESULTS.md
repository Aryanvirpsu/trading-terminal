# CH-002 slot ranking — results (run 2026-09-24, same data as the baseline)

Pre-registration: `PREREG.md` (committed before this was run). Reproduce: `python ch002.py` (offline).
Raw output: `ch002_results.json`. All numbers are portfolio-level net R under the fixed capacity rules
(3 open, 2 entries/day, 1 per sector, $125 sizing, 5 bps/25 bps gap, slot frees the day AFTER exit).
Total R = realized + mark-to-market of still-open positions at the 09-24 close.

## Verdict: NOT SUPPORTED / UNINFORMATIVE. Nothing here justifies changing the ordering.

Against the pre-registered criteria: the primary pool (P1) shows no advantage over RANDOM, and the
probe pool's advantage rests on one decision. There are far too few real choice days to promote or kill.

## Results (5 bps; 15 bps changes each row by ≈ −0.1 to −0.2R and no conclusion)

**P1 — Champion-eligible (TRADEABLE) — the primary test**

| Policy | trades | total R | vs RANDOM (mean 1.24, sd 0.81) | differs from Champion |
|---|---|---|---|---|
| CHAMPION (scanner order) | COP, TMO, DE, AAPL | +0.23 | 0th pct | — |
| CH-002A conviction | identical | +0.23 | 0th pct | 0 trades |
| CH-002B expected-R | identical | +0.23 | 0th pct | 0 trades |
| CH-002C composite | **CVX** (not COP), TMO, DE, AAPL | +0.55 | 16th pct | 1 trade |

- The only real difference is CVX vs COP on 09-11. Both are energy, both stopped out (−1.04R vs
  −1.35R, the gap being COP's adverse gap through its stop). That is +0.32R from a coin flip between two
  names that are **one sector bet**.
- Every arm sits at or below the RANDOM mean → criterion "beats RANDOM" fails.

**P2 — TRADEABLE + MONITOR (ranking-power probe only; NOT a conviction test)**

| Policy | total R | vs RANDOM (mean 2.50, sd 0.85) |
|---|---|---|
| CHAMPION | +1.43 | 0th pct |
| A / B / C (identical sets) | +3.42 | 78th pct |

- Leave-META-out: Champion −1.10, policies +0.89 (advantage +2.0R persists), but ...
- The +2.0R traces to **one decision**: on 09-17 the scanner order put CRM (MONITOR, rank 2) ahead of
  AMD (TRADEABLE, rank 4). AMD hit its target (+2.15R); CRM is open (−0.32R). A one-decision difference
  in a pool the live system cannot even trade (MONITOR is not executable) is not evidence.
- The P2 "Champion" is a straw man: `scanner_rank` ignores the decision label, so it prefers a
  non-executable candidate. In live operation only TRADEABLE candidates compete, i.e. P1.

## What the data actually says about capacity

- Only **5 finalists/day** reach the engine (rank 1-5, every one of 11 sessions), so the candidate
  supply is thin: real competition for a slot among TRADEABLE candidates happened on ~2 of 11 days
  (09-11 energy ×3 → 1 slot; 09-22 tech ×2 → 1 slot) plus one max-open day (09-17).
- On both, the competing candidates were **same-sector** names (COP/CVX/HAL; AAPL/NVDA) with near-identical
  quality (72.9/72.4/61.3) and the same fate (all three energy names stopped out). Ranking *within* a
  correlated cluster is close to a coin flip; the economically meaningful allocation is *which sector*
  gets the slot, and the cap already forces one per sector.
- The earlier baseline note that capacity "binds at every conviction threshold" is consistent: 12-17 skips
  came from relaxed-gate populations holding positions for days, not from the current TRADEABLE flow.

## Decision-relevant conclusions

1. Do **not** change the ordering. No arm demonstrated an edge; the differences are 1-2 decisions.
2. CH-002 stays registered (provisional) but is **data-starved**: ~2 real choice days per 11 sessions →
   ~30 choice days needs ~8 months of forward data at today's supply.
3. To make CH-002 answerable, raise candidate supply/observability instead of tuning: log every finalist
   (not only 5/day) and each policy's pick, in shadow, and resolve outcomes later. This changes no
   Champion behaviour.
4. Treat same-sector, same-day candidates as **one** bet when counting evidence (this also applies to the
   energy trio and META).

## Caveats (unchanged from the baseline)
~13 independent resolved events; META is one correlated event; only 2 resolved actual entries; daily-bar
outcomes; hypothetical fills at reference price (spread not modelled). Directional at best.
