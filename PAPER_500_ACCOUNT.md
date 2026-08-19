# PAPER_500_ACCOUNT.md

The paper account now simulates the **real Robinhood cash account being targeted:
$500**. The $10,000 default is retired to demo/test status.

This is not a display change. Account size determines which trades are *reachable*,
and a strategy validated at $10,000 then scaled down is a different strategy. Trades
are generated and tested at $500 from the beginning.

## Account

```
ledger                    robinhood_500_baseline
PAPER_INITIAL_CASH        500
PAPER_INITIAL_EQUITY      500
PAPER_BUYING_POWER        500
PAPER_MARGIN_ENABLED      false     no margin, no leverage, no borrowing
PAPER_ALLOW_SHORTING      false
PAPER_ALLOW_NAKED_OPTIONS false
PAPER_FRACTIONAL_SHARES   true      Robinhood supports fractional shares
```

**Buying power = cash − reserve = $500 − $100 = $400.** The reserve is never
spendable, so it is subtracted *before* any affordability check rather than being
checked afterwards.

### Ledger separation

Ledgers are named files under `~/.tradingview_mcp_data/paper/` (outside the repo).
`robinhood_500_baseline.db` is the live ledger. `db.archive_ledger(name)` moves any
prior ledger into `archive/<name>.<timestamp>.<reason>.db` so demo data can never be
read as live statistics. No $10,000 ledger with real data existed at switchover — the
earlier runs all used throwaway temp directories — so nothing was lost.

## Risk defaults — absolute dollars

| Control | Value |
|---|---|
| Max planned loss per trade | **$5** |
| Max capital in one stock position | **$125** |
| Max open positions | **3** |
| Max new entries per day | **2** |
| Minimum cash reserve | **$100** |
| Max daily loss | **$10** |
| Max total drawdown | **$50** |
| Positions per sector | **1** |
| Correlated positions | 2 per group |

Percentage caps still apply in parallel and the **tighter of the two always wins**, so
the same code is honest at any account size.

These limits are mutually consistent by design: 3 × $125 = $375 invested leaves
exactly $125 ≥ the $100 reserve. The account can be fully deployed without ever
breaching the reserve.

## Position sizing

Size is the **smallest** of four independent constraints, and the report records which
one actually bound:

1. risk budget ÷ stop distance (max loss per trade)
2. max position notional ($125)
3. buying power at the **realistic fill price** (ask + slippage), never the last print
4. whole-share rounding when fractional shares are unavailable — always rounds **down**

Measured across real price points with $400 buying power:

| Symbol | Price | Quantity | Cost | Planned risk | Bound by |
|---|---:|---:|---:|---:|---|
| F | $12.00 | 10.411461 | $125.00 | $3.75 | position_cap |
| CVX | $150.00 | 0.832917 | $125.00 | $3.75 | position_cap |
| AAPL | $340.00 | 0.367463 | $125.00 | $3.75 | position_cap |
| NVDA | $900.00 | 0.138819 | $125.00 | $3.75 | position_cap |

**Two findings worth acting on later (not now — build freeze):**

1. **Share price is irrelevant with fractional shares.** A $900 stock is as reachable
   as a $12 one. The old intuition that a small account is confined to cheap stocks is
   simply false on Robinhood, and cheap stocks carry worse spreads — so there is no
   reason to bias toward them.
2. **The $125 position cap always binds before the $5 risk cap.** With a 3 % stop,
   actual risk per trade is **$3.75**, not $5 — the $5 limit is never reached. To use
   the full risk budget you would need a ~4 % stop at $125 notional. This is a real
   constraint interaction, and it should be revisited only if paper results show the
   position cap is costing edge.

## Affordability is a first-class outcome

`submit_entry` has a dedicated `affordability` stage, evaluated *before* the risk
checks. An unaffordable signal is journalled with `event='unaffordable'` and its
binding constraint. This is deliberate: on a $500 account, "we could not buy it" is a
different and more important failure than "we chose not to buy it", and conflating the
two would hide the real constraint.

No trade may be created whose realistic cost — quantity × (ask + slippage) + fees —
exceeds buying power. Cost is never approximated from the last price.

## Options on $500

Long single-leg only. No spreads, shorts, naked or multi-leg — the real account is not
approved or funded for them, so they are not simulated.

A long option may enter paper mode only when **all** hold:

- total premium (ask × 100 + fees, i.e. crossing the spread) fits available cash
- max loss = the **full premium** (there is no stop that saves a long option)
- premium ≤ the **$75** per-trade allocation
- chain is current and liquid (spread ≤ 10 %, OI ≥ 250, volume ≥ 25)
- conservative EV after costs is positive
- buying the stock is not clearly superior

Measured:

| Premium | Total | Affordable | Reason |
|---:|---:|---|---|
| $0.35 | $35.00 | **yes** | fits cash and allocation |
| $0.80 | $80.00 | no | exceeds the $75 allocation |
| $1.50 | $150.00 | no | exceeds the $75 allocation |

At $75 that means roughly **contracts priced ≤ $0.75**. A large share of liquid
near-the-money options are simply out of reach at this account size.
**"No affordable high-quality option" is a valid, expected result** — and it never
rejects the underlying stock.

Options remain **shadow-mode only** regardless (`PAPER_OPTIONS_SHADOW_ONLY=true`).

## $500-specific metrics

`report.small_account_metrics()`, attached to every daily report and to
`performance()`:

| Metric | Meaning |
|---|---|
| `affordable_pct` | % of signals affordable with $500 — **the headline number** |
| `rejected_insufficient_buying_power` | signals we could not fund |
| `avg_capital_required` / `max_capital_required` | capital per entry |
| `capital_utilization_pct` | how much of the account is deployed |
| `cash_remaining` / `buying_power` | spendable cash after the reserve |
| `fractional_entries` / `whole_share_entries` | fractional vs whole-share eligibility |
| `options_rejected_premium` | options refused for exceeding the $75 cap |
| `return_on_starting_equity_pct` | return on the actual **$500**, not on notional |

If `affordable_pct` is low, the strategy is **mis-sized for the account** — that is a
strategy problem, not bad luck, and it is the first thing to read.

## Verified

```
62 paper tests passing (including 18 new $500-specific)
buying power $400 = $500 − $100 reserve
2-entries-per-day limit fires on the third signal
cash reserve intact after filling the account
shorting blocked · margin disabled · naked/multi-leg options not simulated
P&L reconciles exactly (delta 0.0)
```
