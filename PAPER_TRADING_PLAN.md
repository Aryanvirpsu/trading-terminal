# PAPER_TRADING_PLAN.md

How the paper account runs. Stocks only. Options are shadow-mode until they earn
their way in (`PAPER_GRADUATION_CHECKLIST.md`).

## Strategies (three, stocks only)

| Strategy | Thesis | Entry conditions |
|---|---|---|
| `liquid_momentum` | Trend persistence in names you can actually get filled in | price > 20-DMA > 50-DMA, RSI ≤ 78, ≥ $5M/day dollar volume |
| `sector_relative_strength` | Leaders inside leading sectors outperform | sector ranks in the top 2 by RS/breadth/momentum, name holds its 50-DMA |
| `mean_reversion` | Pullbacks inside intact uptrends, not falling knives | price > 50-DMA (trend intact) **and** (RSI < 40 or price < 20-DMA) |

Relative strength is deliberately **not** scored inside lagging sectors — a name
fighting its sector is not an RS setup. Weak sectors are still scanned, but only for
setups that stand on their own.

## The funnel (why this is affordable)

```
rank 11 sectors by RS vs SPY + breadth + momentum   (12 Yahoo history calls, cached)
        ↓ take strongest 2 + weakest 1
rank member stocks with the three strategy scorers   (~20-30 cached history calls)
        ↓ at most 5 finalists, one per symbol
run the FULL decision engine only on finalists       (the expensive step, ≤5 calls)
        ↓ at most 3 planned orders per day
```

Live measurement: 3 sectors → 16 candidates → 5 finalists.

## Execution gate — all must hold

A paper trade is created **only** when every one of these is true:

1. `decision == TRADEABLE`
2. every hard **and** soft decision gate passed
3. the price data is **decision-valid** (source age within its category freshness limit)
4. freshness state is `fresh` or `ageing` — never `stale`/`critically_stale`/`fallback`
5. provider provenance exists on the result
6. entry, stop, target and quantity are all present, positive and correctly ordered
7. expected value is positive **after** costs
8. every portfolio risk check passes

`MONITOR` and `REJECT` are never executed. Each refusal is journalled with its exact
reasons — a blocked trade is evidence too.

## Risk defaults — a REAL $500 Robinhood cash account

The account being simulated is **$500 cash**, not a $10,000 demo. Account size decides
which trades are reachable, so every limit is an ABSOLUTE DOLLAR amount. Full detail in
`PAPER_500_ACCOUNT.md`.

| Control | Default | Env |
|---|---|---|
| Starting cash / equity | **$500** | `PAPER_INITIAL_CASH`, `PAPER_INITIAL_EQUITY` |
| Max planned loss per trade | **$5** | `PAPER_MAX_LOSS_PER_TRADE` |
| Max capital in one position | **$125** | `PAPER_MAX_POSITION_NOTIONAL` |
| Max open positions | **3** | `PAPER_MAX_OPEN` |
| Max new entries per day | **2** | `PAPER_MAX_ENTRIES_PER_DAY` |
| Minimum cash reserve | **$100** | `PAPER_MIN_CASH_RESERVE_USD` |
| Max daily loss | **$10** | `PAPER_MAX_DAILY_LOSS_USD` |
| Max total drawdown | **$50** | `PAPER_MAX_DRAWDOWN_USD` |
| Positions per sector | **1** | `PAPER_MAX_PER_SECTOR` |
| Max option premium | **$75** | `PAPER_MAX_OPTION_PREMIUM` |

No margin, no leverage, no borrowing, no shorting (`PAPER_MARGIN_ENABLED=false`,
`PAPER_ALLOW_SHORTING=false`). **Buying power = cash − reserve = $400.**

Position size is the smallest of: risk budget ÷ stop distance · the $125 notional cap ·
buying power at the **realistic fill price** (ask + slippage) · whole-share rounding
when fractional shares are off. Fractional shares are ON, so share price does not
restrict the universe.

No trade may be created whose realistic cost exceeds buying power. Affordability is a
dedicated stage before the risk checks, and an unaffordable signal is journalled as
such — on a $500 account "could not buy" and "chose not to buy" are different facts.

## Fill simulation (never midpoint)

| Order | Fill rule |
|---|---|
| Market BUY | **ask + slippage** |
| Market SELL | **bid − slippage** |
| Limit | only when price **trades through** the limit (a touch does not fill) |
| Stop | if the bar **opens through** the stop → fills at the **open + gap slippage** (worse than the stop); otherwise triggers intrabar at stop + slippage |
| Large order | **partial fill** when size exceeds a share of bar volume |
| No quote / crossed book / non-positive qty | **rejected** |
| Unfilled DAY order | **expired** at the close |

Fees are configurable (per-share, percentage, minimum). Corporate actions (splits, cash
dividends) are applied only where a provider actually reports them.

When a bar touches **both** the stop and the target, the **stop is assumed to have
filled first**. The record is never flattered.

## Daily workflow

```bash
python automation/paper_scheduler.py premarket --dry-run   # watch the funnel, place nothing
python automation/paper_scheduler.py premarket             # scan → analyse → ≤3 orders
python automation/paper_scheduler.py hours                 # fills, stops, targets, tracking
python automation/paper_scheduler.py postmarket            # expire, reconcile, write report
python automation/paper_scheduler.py status                # account + milestone progress
python automation/paper_scheduler.py performance           # cumulative metrics
```

Pre-market aborts without placing anything if the provider health check fails.
Market hours touches only symbols we already care about — open positions, pending
orders and tracked signals. **It does not rescan the market.**

## Shadow tracking — the point of the whole exercise

Every `REJECT` and `MONITOR` signal is tracked forward exactly like a live position:
MFE, MAE and eventual outcome against its hypothetical entry/stop/target.

That produces the number that matters: **blocked winners** — setups a gate refused
that went on to reach their target. If that stays high across a real sample, the gates
are too tight. That is the *only* evidence that justifies loosening them.

## Build freeze

Once paper trading, journaling and reporting work (they do), feature development
stops. Permitted afterwards: correctness bugs, fill-simulation errors, P&L errors,
risk-control failures, stale-data leaks, provider outages, performance failures, and
weaknesses **proven by paper results**.

Deferred: new models, embeddings, forecasting, new providers, live execution,
additional strategies, major UI changes.
