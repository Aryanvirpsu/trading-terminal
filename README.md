# Trading Terminal

A research terminal and paper-trading simulator built on [TradingView MCP](https://github.com/atilaahmettaner/tradingview-mcp). Use the local dashboard to research stocks and ETFs, compare market evidence, and inspect simulated positions, orders, and performance. The MCP server exposes market data and analysis tools to compatible AI clients.

## Quick start

From the repository root, with Python 3.10 or newer and `uv` installed:

```sh
uv run --extra dashboard python dashboard/app.py
```

Open [the local dashboard](http://127.0.0.1:5057). To run the MCP server from this checkout:

```sh
uv run tradingview-mcp
```

See [installation and client setup](INSTALLATION.md), the [tool guide](GUIDE.md), and [example queries](EXAMPLES.md). The upstream repository documents the original MCP project; use this repository's paper-trading documents for this terminal's operating rules.

## Paper trading and research status

The Champion runs against a **simulated $500 cash account**. Its baseline allows up to three open stock positions and two new entries per day, caps each position at $125 and planned loss per trade at $5, and keeps a $100 cash reserve. Margin and shorting are disabled. See the [account rules](PAPER_500_ACCOUNT.md) for the full limits.

**Live execution is disabled.** Keep `BROKER_PROVIDER=none` and `ROBINHOOD_TRADING_ENABLED=false`. Paper fills, historical simulations, and indicator signals are different kinds of evidence; none establishes live performance.

The paper pipeline is under a build freeze until there are at least **50 resolved paper trades with complete evidence**. The [graduation checklist](PAPER_GRADUATION_CHECKLIST.md) also requires realistic fills, verified risk controls, and evidence of positive expectancy after costs. Correctness fixes can be reviewed during the freeze. Strategy experiments stay separate from the Champion.

## Where to look

| Need | Reference |
| --- | --- |
| Research a stock or ETF | [Research playbook](STOCK_RESEARCH_PLAYBOOK.md) |
| Understand paper orders, evidence, and risk | [Paper baseline](PAPER_BASELINE.md) · [Trading plan](PAPER_TRADING_PLAN.md) |
| Understand data sources and fallback behavior | [Provider matrix](DATA_PROVIDER_MATRIX.md) · [Data quality](DATA_QUALITY_AFTER.md) |
| Inspect deployment and ledger recovery | [Ubuntu runtime](docs/UBUNTU_RUNTIME.md) · [Ledger incident](docs/LEDGER_RECONCILE_INCIDENT_20261016.md) |

Keep credentials, runtime SQLite ledgers, and account backups out of Git. See [security](SECURITY.md) and [contributing](CONTRIBUTING.md).

This project is independent of TradingView and intended for research and simulation. [MIT license](LICENSE).
