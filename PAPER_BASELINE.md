# PAPER_BASELINE.md

The frozen starting point for paper trading. Everything after this tag is judged
against it, and only evidence from paper results justifies changing the engine.

## Repository safety (verified)

| Check | Result |
|---|---|
| `origin` is the private personal repo | `https://github.com/Aryanvirpsu/trading-terminal.git` |
| `upstream` push disabled | `upstream  DISABLED (push)` — fetch-only |
| `.env` tracked? | **No** — ignored at `.gitignore:250`, and **never** committed in history |
| `.env.example` committed? | **Yes** (1 tracked) |
| Tokens / API keys in tracked content | **None** — scanned for `sk-`, `ghp_`, bearer tokens, inline `api_key=` |
| Account DBs / brokerage caches tracked | **None** — the paper DB and all account state live in `~/.tradingview_mcp_data/` |
| Model weights tracked | **None** |
| Screenshots tracked | **None** — the only images are `assets/architecture.png` and `.github/assets/tradingview-mcp-demo.gif`, both upstream assets (verified present in `upstream/main`) |
| Robinhood OAuth files outside the repo | **Yes** — `robinhood_mcp_token.json`, `robinhood_mcp_client.json` in `~/.tradingview_mcp_data/` |

Total tracked files: **173**. Working tree clean at tag time.

## Baseline identity

```
tag           v1-paper-baseline
commit        b0f66790d4140241b380524d3e6a79725edc7da2
date          2026-07-30 16:02:30 +0530
branch        main
schema        paper DB v1
config        recorded per-signal as `config_version` (SHA-1 of the behaviour-changing env keys)
tests         429 passing
```

## Preserved capabilities (unchanged by the paper build)

Multi-provider data layer · Yahoo primary / TradingView optional · decision gates +
shared freshness · strategy-aware data quality · sector map · progressive stock view ·
scanner performance fixes · Robinhood MCP read-only · FinBERT (disabled by default) ·
the existing test suite.

## Safety switches (unchanged)

```
ROBINHOOD_TRADING_ENABLED=false     # MCP client refuses write tools before any network call
BROKER_PROVIDER=none                # live order routing unconfigured
```

The paper package never imports a broker client. There is no code path from a paper
order to a real one.

## What "paper trading" means here

A separate, self-contained ledger `robinhood_500_baseline` in
`~/.tradingview_mcp_data/paper/` — NOT the `strategy-500` account and NOT the MCP
`paper_trade` tools, both of which are left untouched. It simulates the **real $500
Robinhood cash account** being targeted (see `PAPER_500_ACCOUNT.md`); the earlier
$10,000 default is demo/test data only.

It exists because those two record trades at a last/mid price, and a paper record that
assumes midpoint fills teaches you nothing about whether a strategy works. It is sized
at $500 because a strategy validated at $10,000 and scaled down afterwards is a
different strategy — affordability is part of the result, not a detail.

## The milestone

**50 resolved paper trades** with complete evidence, realistic fills, and no critical
data-integrity violations. Until then, every performance number is noise and is
labelled as such in `PAPER_PERFORMANCE.md`.
