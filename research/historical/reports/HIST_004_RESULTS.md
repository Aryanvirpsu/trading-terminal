# HIST-004 — results

**STATUS: COMPLETE — NOT PROMOTED**

Pre-registration: `HIST_004_PREREGISTRATION.md` (Amendment 2, 2026-10-01: baseline switched to EXP-DD-001's
uncapped, complete discovery run; arms corrected to what `decision_capture` actually stores). Episode
characterization: `research/historical/hist004/episodes.py` (10 unit tests). Ranking rules:
`research/historical/hist004/ranking_challenger.py` (7 unit tests). Driver:
`run_ranking_challenger.py`. Raw detail: `results/hist004_ranking_result.json`.

## Step 1 — genuine choice episode characterization

Per explicit instruction, done *before* any ranking test. The naive per-cycle count
(`choice_events()` in `hist001/analysis.py`) found **11,325** raw cycle-level overlaps against the
complete EXP-DD-001 baseline — almost entirely inflated by the same candidate pair persisting
TRADEABLE-but-blocked across many consecutive 15-minute discovery cycles.

Collapsing consecutive cycles that share an identical set of genuinely-live candidates (excluding
already-held, already-entered-this-session, and unchanged-observation repeats) into single episodes gives:

| | Count |
|---|---|
| Genuine independent choice episodes | **720** |
| ...with real scarcity (fewer entered than live) | 718 |
| ...with an **observable decision** (Champion actually used ≥1 slot among the competing set) | **93** |
| ...of those, cleanly resolvable (every candidate's decision-time price/stop/target captured) | **89** |

The other 627 episodes are capacity-starved situations where Champion entered *none* of the competing
candidates — real context, but not a ranking signal (nobody was "chosen over" anybody), and correctly
excluded from this selection-quality test (that population is closer to HIST-003's territory).

## Step 2 — ranking rule comparison

**Method:** for each of the 89 resolvable episodes, every candidate — including whichever Champion actually
selected — is resolved via the same bar-by-bar H6 method (`resolve_hypothetical`), using only decision-time
features (no hindsight). This sidesteps the `planned_risk` provenance gap entirely (per instruction, not
blocking discovery on it) and keeps every rule, including "Champion," on an identical evaluation footing.
For an episode where Champion used K slots, each rule's picks are its own top-K candidates by score.

### Result

| Rule | Total R (89 episodes) | R per episode |
|---|---|---|
| **Champion (real scanner_rank order)** | **+18.99** | **+0.213** |
| Random (exact expected value) | +14.89 | +0.167 |
| Planned reward:risk | +7.03 | +0.079 |
| Composite (quality × planned RR) | +7.02 | +0.079 |
| Quality alone | +4.00 | +0.045 |

**None of the three pre-registered alternative arms beat even Random, let alone Champion.** Champion's real
ranking outperforms the next-best alternative (planned RR) by more than 2.5x in total R.

### How often each rule changes the pick, and with what effect

| Rule | Changed pick | Improved | Hurt | Neutral |
|---|---|---|---|---|
| Quality | 63/89 | 15 | 19 | 29 |
| Planned RR | 68/89 | 19 | 16 | 33 |
| Composite | 64/89 | 15 | 16 | 33 |

Every alternative rule changes Champion's pick on the large majority of episodes, and in every case the
hurt-to-improved ratio is unfavorable or roughly neutral at best — none shows a clear directional edge.

### By year (total R)

| Year | Champion | Quality | Planned RR | Composite |
|---|---|---|---|---|
| 2024 | +9.00 | +6.00 | +3.01 | +9.01 |
| **2025 (deteriorating year)** | **+1.99** | **−10.00** | **−6.99** | **−13.00** |
| 2026 (recovery) | +8.00 | +8.00 | +11.01 | +11.00 |

The 2025 breakdown is the most striking part of this result: Champion's real ranking stayed slightly
*positive* through the strategy's worst year, while every alternative rule went substantially negative.
This suggests Champion's existing `scanner_rank` ordering carries real, non-obvious selective skill during
deteriorating conditions that quality-alone, reward:risk-alone, or their product does not capture.

## Interpretation

This is a clean, decisive negative result for all three tested arms, not a data-starved non-finding like
CH-002's live pilot or the earlier uncorrected Full run. The sample (89 resolvable, genuine decision
episodes with zero same-footing ambiguity) is large enough to show a consistent pattern: naive
decision-time proxies (quality, reward:risk, their product) are worse candidate-selection rules than
Champion's own real ranking, and worse than picking randomly among the genuinely-eligible competitors.

## Decision

**Champion change: NONE.** None of the three ranking arms are promoted. Per the same discipline as
HIST-002, no clever ranking formula was fitted after looking at outcomes — the tested arms are exactly
those pre-registered (Amendment 2, itself written only to correct uncapturable fields, before any result
existed).

**What this narrows for future work:** the research sequence hypothesized "selection quality" (HIST-004) as
the more promising lever than "exit quality" (HIST-002, already not-promoted). Both have now failed to beat
Champion's existing heuristics using straightforward alternative formulations. Scaling throughput
(HIST-003) on top of either an unchanged or a worse ranking policy is very unlikely to help, and is
deprioritized pending a genuinely different selection or exit hypothesis — not simply a different weighting
of the same decision-time features already tried.
