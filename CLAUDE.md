# CLAUDE.md — project instructions

## Active constraint: build freeze

This project is under an active **build freeze** on the paper-trading pipeline
(`lab/paper/`, `lab/decision_engine.py`, `automation/paper_scheduler.py`). Only
correctness fixes, fill-simulation/P&L/risk-control bugs, stale-data leaks, provider
outages, and weaknesses *proven by paper results* are in scope until **50 resolved
paper trades** exist. See `PAPER_TRADING_PLAN.md` and
`PAPER_GRADUATION_CHECKLIST.md`. If asked to add a feature to that pipeline, name the
freeze first rather than silently complying or silently refusing.

The paper account simulates a **real $500 Robinhood cash account**
(`PAPER_500_ACCOUNT.md`) — no margin, no shorting, no naked options. Risk limits are
absolute dollars, not percentages.

Robinhood integration is **read-only**: `ROBINHOOD_TRADING_ENABLED=false` and
`BROKER_PROVIDER=none` must never be changed without the user explicitly asking for
live execution — which is a different, much bigger decision than anything in this repo
today.

## How to do stock research

Read `STOCK_RESEARCH_PLAYBOOK.md` first. It combines three pillars — this repo's own
decision engine/sector map/paper journal, the financial-statement/DCF skills
(`CLAUDE_SKILLS_FINANCE.md`), and the quant/TA/risk/tax skills
(`CLAUDE_TRADING_SKILLS.md`) — into the default approach for assessing a stock. None of
those skills feed the automated pipeline; they're for conversation and ad hoc research.

## Key docs, by topic

| Topic | Doc |
|---|---|
| What "done" looks like today | `PAPER_BASELINE.md` |
| Paper-trading design | `PAPER_TRADING_PLAN.md` |
| $500 account specifics | `PAPER_500_ACCOUNT.md` |
| Decision-engine gate logic | `DECISION_LOGIC_AFTER.md` |
| Data quality / provider layer | `DATA_QUALITY_AFTER.md`, `DATA_PROVIDER_MATRIX.md` |
| Sector map | `SECTOR_MAP_AFTER.md` |
| Options (shadow mode only) | `OPTIONS_SHADOW_REPORT.md` |
| Model EV vs. calibrated evidence vs. execution authorization (options) | `EVIDENCE_GRADUATION_AFTER.md` |
| Stock research method | `STOCK_RESEARCH_PLAYBOOK.md` |
| Imported finance skills | `CLAUDE_SKILLS_FINANCE.md`, `CLAUDE_TRADING_SKILLS.md` |
| Graduating past paper trading | `PAPER_GRADUATION_CHECKLIST.md` |

## Repo/safety facts worth remembering

- `origin` is the private repo; `upstream` (the public tradingview-mcp project) has
  push disabled — fetch-only, never push private work upstream.
- No secrets, `.env`, account databases, or OAuth tokens are ever tracked — verified
  at `v1-paper-baseline`.
- The paper ledger and its DB live outside the repo, in `~/.tradingview_mcp_data/`.
