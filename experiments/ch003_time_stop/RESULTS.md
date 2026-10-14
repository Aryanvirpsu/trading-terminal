# CH-003 — time stop for dead capital: results (run 2026-10-13, in-sample)

Pre-registration: `PREREG.md` (sha256 locked in `PREREG.lock.json` **before** any replay output existed). Code frozen in `CODE.lock.json`
after Stage A. Reproduce: `python ch003.py` (reads the mirrored backup `20261012T202501Z`; raw output `ch003_results.json`).
Sample: Champion `gates-v1.1`, 12 sessions (2026-09-25 .. 2026-10-12). **All in-sample; the hypothesis came from this same sample.**

## Verdict: NOT SUPPORTED. Nothing here justifies a time stop, and no threshold can be chosen from it.
Against the pre-registered criteria, **0 of 4 arms** meet them (≥ 3 of 4 required). The effect also **changes sign between adjacent arms**
(N = 3 negative, 5 positive, 8 negative, 10 positive), which the pre-registration names as "not supported".

## Stage A — the replay is faithful (this is what makes the comparison meaningful)
The CHAMPION arm reproduces the cloud's **actual** v1.1 entries exactly — DELL & META (2026-09-25), TMO (10-06), EOG (10-08) — with fills
matching the recorded `avg_entry` to four decimals (DELL $565.6853, META $750.50, TMO $666.6862, EOG $147.6976). No fidelity tuning was needed.
Independent check: the replayed Champion P&L is **−$2.32** vs the cloud's own reported **−$2.30** since the v1.1 boundary.

## Portfolio comparison (5 bps base; open positions marked at the last bar, not realised)
| Arm | Entries | Time-stopped | Realized R | Open-mark R | **Total net R** | Net P&L | Max drawdown | Δ total R vs Champion (5 / 15 / 30 bps) |
|---|---|---|---|---|---|---|---|---|
| **CHAMPION** | 4 | 0 | −1.012 | +0.605 | **−0.407** | −$2.32 | $9.48 | — |
| TS3 | 7 | 3 | −1.753 | +0.355 | −1.398 | −$6.81 | $11.71 | −0.99 / −1.11 / −1.28 |
| TS5 | 6 | 2 | −1.734 | +1.546 | −0.188 | −$2.13 | $8.84 | +0.22 / +0.12 / −0.03 |
| TS8 | 6 | 2 | −1.544 | +0.688 | −0.856 | −$5.60 | $11.47 | −0.45 / −0.54 / −0.67 |
| TS10 | 6 | 2 | −1.256 | +1.383 | +0.126 | −$1.29 | $9.48 | +0.53 / +0.41 / +0.23 |

## Why — the mechanism, from the positions themselves
* **The time stop turns marks into realized losses.** DELL and META were sitting at +0.23R and −0.17R (open marks). Every arm sold them
  at a worse price (e.g. TS3: both realized −0.49R). Every arm has **higher realized losses** than Champion (−1.26 to −1.75R vs −1.01R).
* **Time-stop regret** (R if held under Champion exits minus R realized): 8 of 9 time-stopped positions were worth *more* if simply held
  (+0.06 to +0.71R); the one exception is TS10's DELL (−0.03R). Note "if held" is the mark at the end of the data, not a final outcome.
* **What the freed slots bought:** 2–3 extra entries per arm — TS5: AAPL +0.71R, GOOGL +0.29R; TS10: AMGN +0.15R, WMT +0.69R; TS3: GOOGL
  +0.24R (itself time-stopped), NVDA −0.34R, AMGN +0.15R; TS8: CSCO −0.48R, PG +0.62R. **Nearly all of those are still open marks a few
  days old** — not outcomes. The positive arms (TS5, TS10) are positive only because of two such marks.
* So the premise is **partly true** (a released slot does admit later TRADEABLE setups: +2 to +3 entries) but this sample cannot show that
  the trade-off is favourable.

## Robustness (pre-registered checks)
| Arm | Δ R @5 bps | Δ R @15 bps | Leave-one-event-out min Δ @5 (event dropped) | Drawdown Δ | Meets all criteria? |
|---|---|---|---|---|---|
| TS3 | −0.99 | −1.11 | −1.23 (GOOGL 10-06) | +$2.23 | no |
| TS5 | +0.22 | +0.12 | **−0.49** (AAPL 09-25) | −$0.64 | no — not robust to one event |
| TS8 | −0.45 | −0.54 | −1.07 (PG 10-08) | +$1.99 | no |
| TS10 | +0.53 | +0.41 | **−0.16** (WMT 10-08) | $0.00 | no — not robust to one event |

TS5's and TS10's advantages are carried by a single newly entered position each (AAPL, WMT). At the 30 bps stress, TS5 is already
negative (−0.03 R). (Leave-one-event-out here is an accounting check — one event's positions removed from both tallies — not a re-simulation.)

## What this does and does not mean
* **Does not show** a time stop is harmful in general: 12 sessions, ≤ 3 positions old enough to be time-stopped at N ≥ 5, marks not outcomes.
* **Does show**, for this sample: no pre-registered criterion is met; the sign flips with N (a spike pattern, not a plateau); the realized
  cost of selling DELL/META is certain while the benefit of the freed slot is an unrealized mark.
* **Out-of-sample:** none yet (needs sessions after 2026-10-12). Promotion gate: **not eligible** — 0 OOS positions; `auto_promote` is false;
  there is no `PROMOTION_LOG.md`, and none should be written from this result.

## Next (each separate; the Champion stays frozen)
1. **Keep accumulating**: `python scripts/sync_cloud_data.py` then `python ch003.py` re-evaluates; positions entered after 2026-10-12 are
   reported as out-of-sample. Do not read OOS arm-by-arm before declaring in writing which N (if any) is the candidate.
2. **CH-004 — conviction/score structure**, pre-registered separately (never mixed into CH-003): does the quality ≥ 60 bar select better setups
   than the lower tiers (this sample's REJECT tier averaged +0.40R vs TRADEABLE +0.14R), and does the family-averaging dilute strong signals?
3. A better-posed dead-capital question may be *which* positions are dead (e.g. never reached +0.5R and range-bound) rather than a flat age.
