# $500 Strategy Account — User Guide

A practical, repeatable momentum/breakout swing-trading system on a **paper**
$500 account. It screens liquid US stocks + options, sizes every trade to a
disciplined risk model, **manages exits** against predefined stops/targets, and
journals everything so you can review and improve. Nothing here uses real money.

> **Not financial advice.** This is an educational simulation. Prices come from
> Yahoo Finance / TradingView and may be delayed. Paper results do not predict
> real results.

---

## 1. The big picture

```
        ┌─────────────────────────────────────────────────────────┐
        │  strategy_daily_run   (run once per trading day)         │
        │  ├─ 1. snapshot equity      → builds the performance curve│
        │  ├─ 2. manage open trades   → HOLD / SCALE / TAKE / EXIT  │
        │  └─ 3. find new candidates  → ranked, pre-sized cards     │
        └─────────────────────────────────────────────────────────┘
                 │ you review & decide (cash is a valid choice)
                 ▼
        paper_trade / paper_option_trade   → commit a candidate
                 │
                 ▼
        strategy_log_trade                 → journal the thesis + plan
                 │  … time passes, price moves …
                 ▼
        strategy_manage_positions          → stop hit? target hit?
                 │
                 ▼
        close the position + strategy_close_trade → review & learn
```

Everything reads/writes one local file: `~/.tradingview_mcp_data/portfolio.db`.
The account's `user_id` is **`strategy-500`**.

---

## 2. The risk model (Balanced)

Chosen for a small account that still wants to use options. All limits are read
from **live equity**, so they scale as the account grows or shrinks.

| Rule | Value on $500 | Why |
|------|---------------|-----|
| Max risk per trade | ~5% (~$25) | No single loss materially dents the account. |
| Max capital per trade | ~25% (~$125) | Caps concentration; lets one option contract fit. |
| Minimum reward:risk | 2:1 | Only asymmetric setups. Enforced by construction. |
| Stop basis | 1.5 × ATR below entry | Volatility-aware, not an arbitrary %. |
| Targets | 2R and 3R above entry | Guarantees the ≥2:1. |

**The honest tension with options:** one contract is 100 shares of premium. A
$1.00 call = $100 = 20% of a $500 account. The engine only surfaces option ideas
whose full premium fits the ~$125 cap, and shows max loss = premium paid. Expect
options here to be cheap, near-dated calls — real leverage, defined risk, but you
won't be trading size until the account grows.

---

## 3. Daily workflow

**Once per trading day** (ideally mid-morning after the open settles):

1. **`strategy_daily_run`** — snapshots equity, reviews open positions, finds new candidates.
   - Add `execute_stops=true` to auto-close anything that hit its stop (capital preservation).
2. **Read the management review.** For each open position:
   - `HOLD` → do nothing.
   - `SCALE_OUT_T1` → first target hit; trim and trail your stop up to breakeven.
   - `TAKE_PROFIT_FINAL` → final target hit; close it.
   - `EXIT_STOP` → stop hit; close it now (or let `execute_stops` do it).
3. **Consider new candidates.** Only take one if the thesis genuinely excites you.
   Cash is a valid position — most days you may add nothing.
4. **To take a trade:**
   - Stock: `paper_trade(symbol, quantity, "BUY", exchange="NASDAQ", user_id="strategy-500")`
   - Option: `paper_option_trade(symbol, "CALL", strike, expiry, contracts, "BUY", user_id="strategy-500")`
   - Then **`strategy_log_trade`** with the thesis, entry, stop, targets (and
     `option_type/strike/expiry` for options) so management can track it.
5. **When you close a trade,** call **`strategy_close_trade(journal_id, outcome_pnl, notes)`**
   with what you learned. This feeds win-rate and expectancy.

**Watch it anytime** in the dashboard (below) or ask for `strategy_status`.

---

## 4. Every tool

| Tool | What it does |
|------|--------------|
| `strategy_setup_account` | Create the $500 account. `reset=true` wipes it back to a fresh $500 (destroys all its history). |
| `strategy_find_trades` | Screen liquid US stocks for long momentum/breakout setups; returns ranked, **pre-sized** trade cards (entry, stop, 2R/3R targets, share/contract size) + a call-option idea each. Executes nothing. |
| `strategy_daily_run` | The daily routine in one call: snapshot equity → manage positions → find candidates. |
| `strategy_manage_positions` | Review open positions vs stops/targets (HOLD/SCALE/TAKE/EXIT + unrealized R). `execute_stops=true` auto-closes stopped-out positions. |
| `strategy_status` | Full snapshot: cash, positions with live P&L, total equity, return %, journal stats, equity curve. |
| `strategy_log_trade` | Journal a committed trade's thesis + entry/stop/targets/R:R (+ option fields for options). |
| `strategy_close_trade` | Close a journal entry with realized P&L + review notes. |

Supporting tools you'll use alongside these:
`paper_trade`, `paper_option_trade` (commit trades), `paper_portfolio`,
`paper_option_portfolio`, `stock_options_chain` (inspect a chain before an option trade),
`coin_analysis` / `multi_agent_analysis` (deeper look at a single name).

---

## 5. The dashboard

A local web page so you can **see** the account without asking in chat.

```bash
python tradingview-mcp/dashboard/app.py     # then open http://127.0.0.1:5057
```

Shows: total equity / return / cash / P&L / win-rate KPIs, an **equity curve**
(grows as you run `strategy_daily_run` over time), a live **Position Review**
(each open trade vs its stop/targets with a HOLD/SCALE/EXIT badge), stock &
option positions with mark-to-market P&L, the full trade journal, and an
on-demand **"Find trades now"** button. Auto-refreshes every 30s. It only *reads*
and recommends — it never places trades.

---

## 6. Making it recurring ("not a one-time wonder")

The system is built to run every day and accumulate history (equity curve,
journal, win-rate). Three ways to keep it going:

1. **Manual (simplest):** run `strategy_daily_run` yourself each trading day. One call.
2. **`/loop` while you're at the terminal:** e.g. `/loop 1d strategy_daily_run` runs it
   on an interval during an active session. Good for actively-managed stretches.
3. **Scheduled (`/schedule`):** a cron-style routine. **Caveat:** scheduled *cloud*
   agents can't reach this local database — scheduling only works if it runs on
   *this* machine. Ask and I'll set up a local scheduled task if that's what you want.

Whichever you choose, the equity curve and journal build up over sessions, which is
the whole point — the strategy improves from reviewed history, not from a single scan.

---

## 7. Limitations (so you trust it appropriately)

- **Paper only.** No real orders. Going live needs a broker (see the README's broker
  section — Axis Direct/Robinhood are wired but untested against their live APIs).
- **Long-only, single-leg.** Buys stock or buys calls. No shorting, no spreads,
  no naked writing (yet).
- **No options *backtesting*.** There's no free historical option-chain data, so the
  system trades options *forward* (paper) rather than backtesting them. Stock
  strategies **can** be backtested — see `backtest_strategy` / `compare_strategies`.
- **Screener universe is a curated ~35 liquid names,** not the whole market — chosen
  for tight spreads and real option liquidity. It can be expanded in
  `strategy_service.py:UNIVERSE`.
- **Data can be delayed or rate-limited.** TradingView's scanner occasionally has
  brief outages; the engine retries and degrades gracefully.

---

## 8. Quick start

```
strategy_setup_account                     # once (already done)
strategy_daily_run                         # your daily routine
# → review candidates, then e.g.:
paper_trade  AAPL 0.4 BUY exchange=NASDAQ user_id=strategy-500
strategy_log_trade  AAPL STOCK breakout "thesis…" 308.63 295.53 [334.83,347.93] 0.4
# … later …
strategy_manage_positions                  # HOLD / SCALE / EXIT guidance
strategy_close_trade  <journal_id> <realized_pnl> "review notes"
```

Open the dashboard to watch it all live.
