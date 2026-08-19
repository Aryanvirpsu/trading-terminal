"""Paper-trading subsystem — realistic simulated execution + full evidence journal.

The mission of this package is NOT to add features. It is to run the existing
decision engine against the market, execute only what passes every gate, simulate
fills honestly, and record enough evidence to find out whether the engine has an
edge — including the signals we deliberately did NOT trade.

Modules:
    db             SQLite store + versioned migrations (lives outside the repo)
    config         Risk + execution knobs, all env-overridable
    fills          Realistic fill simulation (never midpoint)
    risk           Configurable pre-trade risk controls
    journal        Records TRADEABLE / MONITOR / REJECT + MFE/MAE + outcome
    broker         Order lifecycle, positions, P&L reconciliation
    strategies     Liquid momentum · sector relative strength · mean reversion
    workflow       Pre-market / market-hours / post-market
    report         Deterministic daily report + performance metrics
    options_shadow Options recorded in shadow mode only — never into the ledger

Nothing here can place a real order. `ROBINHOOD_TRADING_ENABLED=false` and
`BROKER_PROVIDER=none` are untouched by this package.
"""
