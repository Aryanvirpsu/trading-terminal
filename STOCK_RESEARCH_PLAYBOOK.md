# STOCK_RESEARCH_PLAYBOOK.md

How stock research and assessment should actually be done in this project, combining
three pillars: this repo's own engines, the financial-statement/valuation skills, and
the quant/TA/risk skills. This is the "primary way to measure and assess stocks" —
for **conversation and ad hoc research**, not the automated paper-trading pipeline,
which stays frozen and untouched by any of this (see the boundary section at the end).

## The three pillars

### 1. This project's own engines — the evidence layer

Always the starting point for anything about a specific stock. Nothing else
substitutes for it, because it's the only pillar with live data, gates, and
freshness/provenance guarantees:

- `dashboard/research.py` — `overview()` / `summary()`: price, technicals,
  fundamentals, catalysts, sentiment (FinBERT/lexical), options chain.
- `lab/decision_engine.py` — nine-family evidence scoring, coverage-based data
  quality, gate-derived TRADEABLE/MONITOR/REJECT, scenario probabilities, EV
  breakdown, disagreement quantification. Read `DECISION_LOGIC_AFTER.md` and
  `DATA_QUALITY_AFTER.md` for exactly how to interpret its output.
- `dashboard/sector_map.py` — sector/industry relative strength, breadth, RS vs SPY.
  Use this to decide *which* stocks are even worth a deep look before spending a full
  decision-engine call on them (see `PAPER_TRADING_PLAN.md`'s funnel).
- `lab/paper/` — the $500-account-realistic paper ledger, journal (including
  REJECT/MONITOR outcomes), and options-shadow tracking. Read from it (account state,
  signal history, blocked-winners) freely; don't write to it outside the frozen
  pipeline's own entry gate.

### 2. `CLAUDE_SKILLS_FINANCE.md` — fundamentals & valuation

Use `analyzing-financial-statements` when the question is about a company's actual
financial health (ratios, margins, leverage, trend vs prior periods) and
`creating-financial-models` when it's about what the company is *worth* (DCF,
sensitivity, Monte Carlo, scenario planning). These are the "is this a good business"
questions the decision engine doesn't answer — it scores trade setups, not intrinsic
value.

### 3. `CLAUDE_TRADING_SKILLS.md` — quant, TA, risk, backtesting, tax

Use the 25 promoted skills (full list in that doc) for:
- **Idea generation / hypothesis testing**: `pandas-ta`/`ta-lib` for indicators beyond
  what the engine's trend family computes; `backtrader`/`vectorbt` to backtest an idea
  *before* it becomes a strategy; `walk-forward-validation` to sanity-check that a
  backtest isn't overfit (same discipline as `lab/forecast_benchmark.py`).
- **Cross-checking the engine's own numbers by hand**: `kelly-criterion` and
  `position-sizing` against what `lab/paper/risk.py` computed; `portfolio-analytics`
  against the paper account's own equity curve; `correlation-analysis` /
  `cointegration-analysis` before treating two positions as diversified.
- **Options math**: `options-pricing` (Black-Scholes/Greeks/IV) alongside
  `dashboard/research.py`'s options chain data and `lab/paper/options_shadow.py`'s
  affordability/liquidity checks.
- **Tax questions on the real account**: `cost-basis-engine`, `wash-sale-detection`,
  `tax-loss-harvesting`, `regulatory-reporting` (Form 8949), `tax-liability-tracking`,
  `trade-accounting` — genuinely equities-relevant, not crypto-flavored despite the
  marketplace's overall framing.

## A worked example — "should I look at NVDA right now?"

1. **Sector Map** — is Technology/Semis leading or lagging? (`sector_relative_strength`
   only means something in a leading sector — see `PAPER_TRADING_PLAN.md`.)
2. **Decision engine** — `evaluate("NVDA", ...)`: what's the gate-derived action, the
   data quality, the disagreement across the 9 families, the EV after costs?
3. **Fundamentals** — `analyzing-financial-statements` on the latest 10-Q: margins
   trending up or down? Leverage reasonable?
4. **Valuation** — `creating-financial-models` DCF: does the current price imply
   growth assumptions that seem achievable?
5. **Technical cross-check** — `pandas-ta`/`ta-lib` for a second opinion on momentum
   beyond the engine's own trend family; `volatility-modeling` if options are in play.
6. **Options** — chain data from `research.py` + `options-pricing` for Greeks/IV +
   `options_shadow.py` for affordability at the real $500 account size.
7. **Size it** — `kelly-criterion`/`position-sizing` cross-checked against
   `lab/paper/risk.py`'s actual $5/trade, $125/position caps.
8. **Tax context**, if it's a real Robinhood decision (not paper) — wash-sale window,
   cost basis method, whether harvesting a loss elsewhere makes sense first.

That's "absolutely powerful": every angle — evidence, fundamentals, valuation,
technicals, options math, sizing, tax — pulled together instead of picking one lens.

## What this playbook does NOT change

- The automated pre-market → decision engine → paper-order pipeline in
  `lab/paper/workflow.py` is **unchanged**. It still runs on the engine's own 9-family
  scoring and the 3 approved strategies (`liquid_momentum`, `sector_relative_strength`,
  `mean_reversion`). None of the 27 promoted skills feed it automatically.
- `ROBINHOOD_TRADING_ENABLED=false` and `BROKER_PROVIDER=none` are untouched. Nothing
  here executes a real trade, crypto or equity.
- The build freeze stands: turning any of pillar 2 or 3 into an automatic input to the
  live decision engine (e.g., wiring `signal-classification` in as a tenth evidence
  family) is a feature decision for **after** the 50-trade milestone, made explicitly,
  not something either skill pack does on its own by being installed.
