"""HIST-004 -- capacity-aware slot ranking Challenger: when two or more genuinely newly-eligible candidates
compete for one genuinely scarce slot, does a decision-time ranking rule select the better-performing
candidate more often than Champion's real scanner_rank order? Pre-registration:
`research/historical/reports/HIST_004_PREREGISTRATION.md`.

Step 1 (required before any ranking test, per explicit direction): characterize genuine independent choice
EPISODES, not the naive raw per-cycle TRADEABLE-overlap count, which massively inflates the apparent sample
by counting the SAME underlying decision moment once per discovery cycle it happens to persist across (a
candidate that stays TRADEABLE-but-blocked for 10 consecutive 15-minute cycles is ONE choice, not 10). See
`episodes.py`.
"""
