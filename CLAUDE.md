# CLAUDE.md — project instructions

## Active policy: PROFIT MODE (supersedes the earlier build freeze)

The strategy/build freeze on the paper pipeline (`lab/paper/`, `lab/decision_engine.py`,
`automation/paper_scheduler.py`) is **lifted** (2026-09-24). The 50-resolved-trade
requirement no longer blocks experimentation. The old freeze text is in git history.

**Objective:** find, validate and deploy decision logic that improves *net* profitability
as fast as possible without corrupting the evidence.

**Champion / Challenger**
- The current strategy is the **Champion** and is immutable: never silently change its
  logic, thresholds, weights, gates, exits, sizing or execution model.
- All strategy experiments run as isolated, reproducible **Challengers** (CHALLENGER-001,
  ...). A Challenger may change strategy, gates, thresholds, weights, entries, exits and
  instrument/contract selection, within the fixed risk envelope.
- Results from materially different strategy versions must **never be pooled** into one
  sample. Promotion, killing and archiving are recorded; prior results are never rewritten.
- Validate with historical replay, walk-forward/out-of-sample tests, shadow decisions,
  rejected-trade counterfactuals and paper trading — not only forward paper trades. Report
  metrics net of spread/slippage/fees, in R terms, and label in-sample vs out-of-sample.

**Correctness/evidence freeze (still in force).** Only confirmed correctness bugs may change:
accounting semantics, market-data truthfulness, timestamp/freshness semantics, fill
assumptions and slippage/spread/cost accounting, execution-simulation integrity, decision
logging/auditability, experiment isolation. Never make fills, prices, slippage, spreads,
costs or timestamps more favorable to improve apparent performance.

**Live execution stays OFF.** `ROBINHOOD_TRADING_ENABLED=false` and `BROKER_PROVIDER=none`
must not change unless the user explicitly authorizes live execution as a separate decision.
Do not increase leverage/risk to inflate P&L; judge quality in normalized terms (R/expectancy)
and report drawdown, worst trade, streaks and tail losses.

**Priorities:** P0 correctness defects corrupting evidence · P1 causes of realized losses ·
P2 gates rejecting profitable candidate populations · P3 execution leakage · P4 position /
instrument / contract selection · P5 exits and trade management · P6 new signals with a clear
hypothesis · P7 infrastructure · P8 UI/polish. Don't work lower while a higher blocker exists
unless it has direct P&L value.

**Deferred:** infrastructure, cloud, Nautilus and UI work is paused unless it directly improves
P&L research, execution integrity, data collection or experiment throughput. Use the whole
candidate population (accepted + rejected); no cherry-picked misses.

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
| Primary market-hours runtime (Ubuntu, paper only), deploy, health, shadow evidence | `docs/UBUNTU_RUNTIME.md` |
| Which paper evidence may be pooled (v1.0 vs v1.1) | `PAPER_EVIDENCE_VERSIONS.md` |

## Repo/safety facts worth remembering

- `origin` is the private repo; `upstream` (the public tradingview-mcp project) has
  push disabled — fetch-only, never push private work upstream.
- No secrets, `.env`, account databases, or OAuth tokens are ever tracked — verified
  at `v1-paper-baseline`.
- The paper ledger and its DB live outside the repo, in `~/.tradingview_mcp_data/`.
