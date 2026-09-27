# AVDI Historical Lab

A thin research harness around the **existing** AVDI decision stack (scanner, canonical A/B/C/D/E,
Champion/Challenger sizing, risk, the paper broker's fill simulator). It supplies its own clock, market
data provider, isolated account/ledger, and walk-forward/outcome/event-identity machinery around that
stack — it does not duplicate any of AVDI's decision logic. See `CLAUDE.md` for the standing rule this
work is held to.

Install (separate from the production dependency set — never installed on the Ubuntu runtime):
```
pip install -r research/historical/requirements.txt
```
Run its tests: `python -m pytest tests/historical -q` (add `-m "not network"` to skip the one live-data test).

## Status (2026-09-27)

| Phase | What | Status |
|---|---|---|
| **H0** | Production isolation guard (`guards.py`) — refuses any path resolving into `/data/case1(2)`, `.tradingview_mcp_data`, the real ledger/shadow filenames, or a host fingerprinted as the Ubuntu runtime | **Done.** `tests/historical/test_production_isolation.py` (26 tests) |
| **H1** | Canonical bar schema + validation (`schemas/bars.py`), dataset manifest with SHA-256 provenance (`manifest.py`), source-agnostic adapter interface (`datasets/base.py`), a generic (config-driven) Hugging Face adapter, and a Parquet+DuckDB store | **Done.** `tests/historical/test_h1_dataset_foundation.py` (14 tests incl. one real 5-symbol pull) |
| **H2** | `HistoricalClock` + `HistoricalMarketProvider`, with a hard lookahead guard | **Done.** `tests/historical/test_h2_clock_and_lookahead.py` (18 tests) |
| **H3** | Wire the historical provider into the real scanner/decision-engine data-fetch points (adapters only, no duplicated logic) | Not started |
| H4-H15 | Historical account/execution, reproduce 2026-09-25, outcome engine, event identity, walk-forward, MLflow, funnel attribution, HIST-001..004, regime attribution, options, Nautilus cross-check, Optuna | Not started |

### The Hugging Face dataset gap (disclosed, not papered over)

Searched the Hugging Face datasets API directly (`ohlcv`, `us equities minute bars`, `nasdaq intraday`,
`stock market ohlc`, `yahoo finance stock`, `stooq`, `polygon.io`, `daily stock prices csv`, and more) —
**no freely-licensed dataset providing intraday US-equity OHLCV bars for arbitrary tickers exists on the
Hub today.** `datasets/huggingface_equities.py` is built and tested as a generic, config-driven adapter
(point it at a real `dataset_id` + column map once one exists, or once this project's own data is
uploaded to the Hub) but is **not wired to any specific dataset id** — doing so would have meant either
fabricating an id that might not exist or silently mis-mapping a dataset's actual columns.

The H1 acceptance sample (AAPL/MSFT/META/NVDA/DELL, 5-minute bars, a few days) instead uses
`datasets/yahoo_bootstrap.py`, clearly labelled `source="yahoo-bootstrap"` in its manifest so it can never
be mistaken for a Hugging Face import. Yahoo's public intraday history is short and rate-limited — fine
for this acceptance sample, not a basis for years of walk-forward research. **A real bulk data source
(vendor, a specific verified HF dataset, or your own uploaded parquet) is needed before H4 onward can do
anything beyond replaying the last few days.**

## Guarantees (tested)

* No historical path can resolve into the production ledger/shadow directory or filenames.
* No historical process can start on a host fingerprinted as the Ubuntu production runtime.
* Every dataset is schema-validated (OHLC ordering, non-negative volume, monotonic per-symbol timestamps,
  no duplicate symbol/timestamp rows) before it is stored, and every stored file is content-hashed and
  re-verifiable against its manifest.
* The market data provider cannot return, or be asked for, information timestamped after its bound
  clock's current instant — an explicit request for the future raises, it is never silently clamped.
