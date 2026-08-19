# CLAUDE_TRADING_SKILLS.md

Reference material imported from
[agiprolabs/claude-trading-skills](https://github.com/agiprolabs/claude-trading-skills)
(MIT licensed, third-party — not an Anthropic repo). 67 Agent Skills covering trading,
DeFi, and quantitative finance. See `CLAUDE_SKILLS_FINANCE.md` for the earlier,
Anthropic-official finance-skills import (financial ratios + DCF modeling); this doc
covers the newer, much larger marketplace and is explicit about what from it actually
applies to a Robinhood **stocks/options** account versus what is Solana/crypto-only.

## Safety review (done before importing anything)

This is an unfamiliar third-party source, so it got the same scrutiny as any external
code before being trusted:

- Scanned every `SKILL.md` for injection patterns (instructions to ignore prior
  guidance, auto-execute trades, exfiltrate data, handle private keys/seed phrases) —
  **none found**.
- Scanned for hardcoded secrets/API keys — the only matches were public on-chain
  token contract addresses (USDT, USDC, etc.) in reference tables, which is normal,
  legitimate content for a DeFi-data skill, not a credential leak.
- Spot-checked the one skill with real execution risk (`dex-execution`, which builds
  and signs Solana swap transactions) — it has an explicit **"never skip the
  display-and-confirm steps"** rule built into its own instructions before any
  transaction is submitted.
- Repo is 5 MB of text/Python/PNG example charts; no unexpected binaries.
- MIT licensed, `LICENSE.md` present, matches the `.claude-plugin/marketplace.json`
  manifest (this is a real Claude Code plugin marketplace, addable via
  `/plugin marketplace add agiprolabs/claude-trading-skills` from an interactive
  `claude` terminal — that command opens a terminal dialog this session can't drive,
  so the skills below were added the same way as the previous cookbook import: copied
  directly into `.claude/skills/`, which is the same end state).

Conclusion: legitimate, well-built, safe to use.

## The asset-class mismatch — and why not everything is "primary"

The marketplace is **crypto/Solana/DeFi-first**: of the 67 skills, roughly two-thirds
are Birdeye/Helius/Solana-RPC/Jupiter-DEX/pump.fun/Kalshi/Polymarket integrations —
new data providers and a different asset class entirely from a Robinhood stocks/
options account. Importing all 67 as "primary" tools would mean:

- Violating the paper-trading build freeze's explicit **"no new providers"** rule —
  most of the crypto-only skills *are* new provider integrations (Birdeye, Helius,
  CoinGecko, DeFiLlama, SolanaTracker, Kalshi, Polymarket).
- Reaching for a Solana wallet-tracking or AMM-slippage skill when researching an
  equity makes no sense — the methodology doesn't transfer.
- Duplicating systems this repo already has and has already tested: a realistic
  order-book fill simulator (`lab/paper/fills.py`), a risk engine
  (`lab/paper/risk.py`), an evidence journal (`lab/paper/journal.py`), and FinBERT/
  lexical news sentiment (`lab/model_registry.py`).

So the full marketplace is mirrored **completely** (satisfying "add this") at
`reference/claude-trading-skills/`, but only the **asset-agnostic quant/TA/risk/
backtesting/tax subset — 25 of 67 skills** — is promoted to `.claude/skills/` as a
directly-usable, "reach-for-it-by-default" tool. The other 42 stay available in the
mirror for the day this platform ever touches crypto or prediction markets, but aren't
part of the everyday stock-research toolkit.

## The 25 promoted skills

| Skill | Use for |
|---|---|
| `pandas-ta` | 130+ technical indicators over any OHLCV data (works on stocks despite the crypto-flavored description — it's just pandas) |
| `ta-lib` | C-optimized TA + 61 candlestick pattern functions |
| `backtrader` | Event-driven backtesting, complex order types |
| `vectorbt` | Vectorized backtesting, parameter sweeps |
| `strategy-framework` | Standard template: entry/exit rules, sizing, risk params |
| `walk-forward-validation` | Time-series-aware overfitting checks (complements `lab/forecast_benchmark.py`) |
| `portfolio-analytics` | Return metrics, risk-adjusted ratios, rolling performance |
| `position-sizing` | Fixed-fractional, volatility-adjusted, liquidity-constrained sizing |
| `risk-management` | Drawdown controls, exposure limits, circuit breakers (methodology reference alongside `lab/paper/risk.py`) |
| `kelly-criterion` | Optimal-sizing math with fractional variants |
| `cointegration-analysis` | Engle-Granger/Johansen tests for pairs trading |
| `correlation-analysis` | Rolling correlation, hierarchical clustering, tail dependence |
| `mean-reversion` | Hurst exponent, half-life, z-score, ADF (rigor reference for the `mean_reversion` paper strategy) |
| `regime-detection` | Volatility-clustering/trend regime methods (reference alongside `market_regime`) |
| `volatility-modeling` | GARCH, EWMA, realized vol — directly useful for options work |
| `ohlcv-processing` | Resampling, gap handling, anomaly detection |
| `trading-visualization` | Candlesticks, equity curves, drawdowns, correlation heatmaps |
| `options-pricing` | Black-Scholes, binomial, Monte Carlo, IV solving (verified working — see below) |
| `exit-strategies` | Stop-loss / take-profit / trailing-stop methodology |
| `cost-basis-engine` | FIFO/LIFO/HIFO/specific-ID cost basis |
| `tax-loss-harvesting` | Harvest-opportunity scoring with wash-sale compliance |
| `wash-sale-detection` | The IRS wash-sale rule (61-day window) — originally an **equities** rule |
| `trade-accounting` | Double-entry bookkeeping, P&L statements |
| `regulatory-reporting` | **IRS Form 8949 / Schedule D generation** — directly relevant to a US equities account |
| `tax-liability-tracking` | Real-time gain classification (short/long term) |

## Deliberately NOT promoted (mirrored only, in `reference/claude-trading-skills/`)

Crypto/Solana data providers and infrastructure: `birdeye-api`, `coingecko-api`,
`dexscreener-api`, `defillama-api`, `helius-api`, `solana-rpc`, `solana-tx-building`,
`solanatracker-api`, `pumpfun-mechanics`, `jito-bundles`, `shredstream`,
`yellowstone-grpc`, `raptor-dex`. On-chain/DEX analysis: `dex-execution`,
`dex-pool-analysis`, `liquidity-analysis`, `lp-math`, `impermanent-loss`,
`mev-analysis`, `token-economics`, `token-holder-analysis`, `wallet-profiling`,
`whale-tracking`, `sybil-detection`, `copy-trading`, `yield-analysis`,
`market-microstructure`, `custom-indicators`, `rl-execution`. Prediction markets and
new asset classes: `kalshi-api`, `kalshi-crypto-index-markets`, `kalshi-weather-
markets`, `polymarket-api`, `prediction-market-strategy`. Crypto-specific tax export:
`crypto-tax-export`. Overlaps with a system this repo already has and has tested:
`sentiment-analysis` (→ FinBERT/lexical in `model_registry.py`), `slippage-modeling`
(→ `lab/paper/fills.py`), `trade-journal` (→ `lab/paper/journal.py`, which already
tracks MFE/MAE/blocked-winners — materially more sophisticated). Left as reference
only pending an explicit decision, since they touch the freeze's **"no new ML
models"** boundary: `feature-engineering`, `signal-classification` (XGBoost/LightGBM
signal classifiers — do not wire these into the live decision path without that
conversation happening first). Marked `[STUB]` upstream with a real
implementation but a stated roadmap for more: `options-pricing` is promoted anyway
(it works, see below); `fixed-income` was left mirrored-only as genuinely low-relevance
to a $500 equities/options account.

## Verified working

Spot-checked rather than assumed:

```bash
$ python .claude/skills/options-pricing/scripts/black_scholes.py --demo
# full Black-Scholes greeks (delta/gamma/theta/vega/rho) + an implied-vol solver — real output

$ python .claude/skills/kelly-criterion/scripts/kelly_calculator.py
# Kelly Fraction: 0.2500 (25.0%) ... "Full Kelly is NOT recommended for live trading"
```

**Windows note:** several scripts print Unicode symbols (σ, box-drawing characters)
that raise `UnicodeEncodeError` in a default Windows console (cp1252). Fix: run with
`PYTHONIOENCODING=utf-8` set, or from Git Bash (which is already UTF-8). This is the
same class of issue already handled elsewhere in this project.

## How to use

### A) Directly in this Claude Code session

The 25 promoted skills are registered at `.claude/skills/` alongside the two from
`CLAUDE_SKILLS_FINANCE.md`. Just ask:

> "Backtest a mean-reversion strategy on AAPL using vectorbt"
> "What's the Kelly-optimal position size for a 55% win rate, 1.5R average winner?"
> "Run a cointegration test between XOM and CVX for a pairs trade"
> "Generate Form 8949 for these paper-account closed trades"

(New session needed to pick up skills added mid-session — same caveat as before.)

### B) Standalone Python

Every promoted skill's scripts run without Claude or an API key:

```bash
PYTHONIOENCODING=utf-8 python .claude/skills/<skill-name>/scripts/<script>.py --help
```

### C) The full marketplace, if ever needed

`reference/claude-trading-skills/` has all 67 skills, the original `README.md`,
`examples.md`, and `trading-skills.md` catalogue, plus `tests/` from upstream. If this
platform ever adds crypto or prediction-market coverage, the mirror is already there —
promoting a skill from mirror to `.claude/skills/` is a one-line `cp -r`.

## Build-freeze boundary — read this before using any of these for real signals

`lab/paper/`, `lab/decision_engine.py`, and the automated scan → decision → paper-order
pipeline are under an active build freeze (`PAPER_TRADING_PLAN.md` §Build freeze):
only correctness fixes and freeze-covered exceptions until 50 resolved paper trades
exist. **These 25 skills are conversational/ad hoc research tools. None of them are
wired into that pipeline, and none should be without an explicit decision to do so** —
using `signal-classification` or `feature-engineering` to add a new signal, or
`options-pricing` to replace the existing options-shadow grading, would both count as
"additional strategies" / "new models" under the freeze. Using `kelly-criterion` or
`portfolio-analytics` to double-check the paper account's own risk math by hand, in
conversation, is fine — that's just using a calculator.

See `STOCK_RESEARCH_PLAYBOOK.md` for how these two skill packs combine with this
project's own engines for day-to-day research questions.

## Attribution

Source: <https://github.com/agiprolabs/claude-trading-skills>. MIT License,
Copyright (c) 2026 AGIPro — full text at
`reference/claude-trading-skills/LICENSE.md`. Not affiliated with Anthropic.
