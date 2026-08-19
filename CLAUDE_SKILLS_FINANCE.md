# CLAUDE_SKILLS_FINANCE.md

Finance/investment-analysis material imported from
[anthropics/claude-cookbooks](https://github.com/anthropics/claude-cookbooks)
(MIT licensed). This is **reference tooling for ad hoc fundamental research** — it is
not wired into the decision engine, the sector map, or the paper-trading system, and
does not touch anything under the current build freeze (see
`PAPER_TRADING_PLAN.md` §Build freeze).

## What was actually in the cookbook

The cookbook is a general Claude API/SDK cookbook, not a trading-focused one. A full
scan of every folder (`patterns/`, `capabilities/`, `third_party/`, `claude_agent_sdk/`,
`skills/`, etc.) turned up exactly two pieces of genuine finance/investment material —
both under `skills/custom_skills/` — and nothing about trading, technical analysis,
backtesting, or live market data. That side is already covered by this repo's own
`lab/` and `dashboard/`. What the cookbook adds is the side this repo doesn't have:
**corporate-finance fundamentals** — financial-ratio analysis and DCF/valuation
modeling, packaged as [Agent Skills](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview)
(a `SKILL.md` + supporting scripts).

(There's also a `financial-analyst` subagent under
`claude_agent_sdk/chief_of_staff_agent/` — a startup burn-rate/runway advisor for a
fictional SaaS company. Not equity/investment-relevant, so it was left out.)

## What was imported and where

```
reference/claude-cookbooks-finance/     full self-contained bundle (see below)
.claude/skills/analyzing-financial-statements/    same skill, registered for direct use in Claude Code
.claude/skills/creating-financial-models/         same skill, registered for direct use in Claude Code
```

`reference/claude-cookbooks-finance/` contains everything needed to run this
independently of Claude Code:

| Path | What it is |
|---|---|
| `skills/analyzing-financial-statements/` | `SKILL.md` + `calculate_ratios.py` + `interpret_ratios.py` |
| `skills/creating-financial-models/` | `SKILL.md` + `dcf_model.py` + `sensitivity_analysis.py` |
| `sample_data/financial_statements.csv` | 8 quarters of P&L data to try the ratio calculator on |
| `sample_data/portfolio_holdings.json` | a sample equity portfolio (ticker, shares, cost basis, sector) |
| `notebooks/01_skills_introduction.ipynb` | Skills basics — beta headers, first Excel/PPTX/PDF via Claude |
| `notebooks/02_skills_financial_applications.ipynb` | the finance walkthrough: ratio dashboards, portfolio analysis, CSV→Excel→PowerPoint→PDF |
| `notebooks/03_skills_custom_development.ipynb` | how these two skills were built — start here if you want to add a third |
| `skill_utils.py`, `file_utils.py` | shared helpers the notebooks import |
| `requirements.txt`, `.env.example` | upstream's own dependency list + API-key template |
| `UPSTREAM_README.md`, `UPSTREAM_CLAUDE.md`, `UPSTREAM_LICENSE` | original docs + MIT license, for attribution |

## What each skill does

**`analyzing-financial-statements`** — calculates and interprets financial ratios from
income-statement/balance-sheet/cash-flow data: profitability (ROE, ROA, margins),
liquidity (current/quick/cash ratio), leverage (debt-to-equity, interest coverage),
efficiency (asset/inventory/receivables turnover), valuation (P/E, P/B, P/S,
EV/EBITDA, PEG), per-share metrics. Takes CSV, JSON, Excel, or a text description;
returns values + trend analysis + industry-context interpretation.

**`creating-financial-models`** — DCF valuation (WACC, terminal value via perpetuity
growth or exit multiple, enterprise/equity value), sensitivity analysis (tornado
charts, data tables), Monte Carlo simulation (probability-weighted valuation ranges),
and scenario planning (best/base/worst case). Covers corporate valuation, M&A/LBO
analysis, and project finance.

Both are documented under **Limitations** in their own `SKILL.md` as exactly what they
are: models are only as good as their inputs, not a substitute for professional advice,
and industry benchmarks are general guidelines.

## How to use them — three ways

### A) Directly in this Claude Code session (simplest)

The two skills are registered as **project-scoped skills** at `.claude/skills/`. Just
ask, in this repo:

> "Calculate financial ratios for this company from the attached statements"
> "Build a DCF model for [company] with 10% revenue growth and a 9.5% WACC"
> "Run a sensitivity analysis on WACC and terminal growth for this DCF"

Claude Code discovers and loads the matching skill automatically — no extra API key
setup beyond what already runs Claude Code itself.

> **Note:** project skills are picked up when a session starts. If you don't see
> `analyzing-financial-statements` / `creating-financial-models` in the available-skills
> list, start a new session (or restart the app) after this import.

### B) Standalone Python — no Claude, no API key at all

The calculation engines are plain Python and run on their own; verified working:

```bash
python .claude/skills/analyzing-financial-statements/calculate_ratios.py
python .claude/skills/creating-financial-models/dcf_model.py
```

Each has a worked example in its `__main__` block (a sample company's full ratio
set; a 5-year DCF for "TechCorp" — in the bundled example: enterprise value $3,071M,
$57.41/share). Import the functions directly for real data:

```python
import sys
sys.path.insert(0, ".claude/skills/analyzing-financial-statements")
from calculate_ratios import calculate_ratios_from_data

results = calculate_ratios_from_data(your_financial_data_dict)
```

```python
sys.path.insert(0, ".claude/skills/creating-financial-models")
from dcf_model import DCFModel

model = DCFModel(company_name="Acme Corp", revenue=[...], ebitda=[...], ...)
model.set_assumptions(projection_years=5, revenue_growth=[...], ...)
model.calculate_wacc(risk_free_rate=0.04, beta=1.2, market_premium=0.07, ...)
model.project_cash_flows()
model.calculate_enterprise_value()
print(model.generate_summary())
```

`interpret_ratios.py` and `sensitivity_analysis.py` are library modules meant to be
imported alongside their counterparts, not run directly.

Excel-export features additionally need `openpyxl` (already in
`reference/claude-cookbooks-finance/requirements.txt`); the core calculations above
need only `pandas`/`numpy`, which this project already has installed.

### C) The original cookbook path — via the raw Anthropic API + code execution

This is how the upstream notebooks demonstrate it: Claude using the Skills +
code-execution beta over the API, generating actual Excel/PowerPoint/PDF output. Useful
if you want this outside Claude Code (e.g. a scheduled research job), or want to see
how Skills + code execution work at the API level.

```bash
cd reference/claude-cookbooks-finance
python -m venv venv
venv\Scripts\activate            # Windows — use `source venv/bin/activate` on macOS/Linux
pip install -r requirements.txt
copy .env.example .env           # Windows — `cp .env.example .env` on macOS/Linux
# edit .env and set ANTHROPIC_API_KEY=sk-ant-...  (https://console.anthropic.com/)
jupyter notebook
```

Then open, in order: `01_skills_introduction.ipynb` (basics) →
`02_skills_financial_applications.ipynb` (the finance walkthrough) →
`03_skills_custom_development.ipynb` (how to build a third skill of your own).

## Why this is kept separate from the trading platform

The mission is in an active **build freeze**: `lab/paper/` and the decision engine only
get correctness fixes until 50 resolved paper trades exist (see
`PAPER_GRADUATION_CHECKLIST.md`). This import touches none of that — it lives in
`reference/` and `.claude/skills/` specifically so it can't get accidentally wired into
the live pipeline. If you later want DCF/fundamental-ratio output folded into the
decision engine's `fundamentals` data-quality category, that is a deliberate feature
decision to make **after** the milestone, not now.

## Attribution

Source: <https://github.com/anthropics/claude-cookbooks>, paths
`skills/custom_skills/analyzing-financial-statements`,
`skills/custom_skills/creating-financial-models`, `skills/notebooks/01-03`,
`skills/sample_data/`. MIT License, Copyright (c) 2023 Anthropic — full text at
`reference/claude-cookbooks-finance/UPSTREAM_LICENSE`.
