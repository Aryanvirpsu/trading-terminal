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

## H3 (done)

`avdi_adapter.py` (`HistoricalAVDIContext`) patches AVDI's own existing seams — `strategies._bars`,
`strategies.rank_sectors`, `decision_engine._load_analysis`, `_safe_regime`, the 7 `_fam_*` families with
no historical replay (routed to the production `_fam_stub` — the SAME zero-confidence contract the live
system already uses when a source is down), `ss._pick_option_idea`, `halts.is_halted` — then runs
`strategies.scan()` and `decision_engine.evaluate()` completely unchanged. `_load_analysis`'s replacement
reuses `lab.fallback_ta`'s own `_rsi`/`_atr` pure helpers (only the data FETCH is substituted, since
`fallback_ta.analysis()` calls yfinance directly with no injection seam and is production code that must
not be touched).

Verified (`tests/historical/test_h3_avdi_wiring.py`, 4 tests):
* a deterministic, no-network fixture proves the real `decision_engine.evaluate()`'s own `price` field and
  the real `_fam_trend()` never reflect information later than the clock, ARE identical when queried twice
  at the same instant, and DO change correctly once the clock genuinely advances past a planted price move;
* an explicit request for the future is refused THROUGH the adapter (not just at the raw provider);
* patches restore exactly on context exit;
* the real-data acceptance smoke test (network) runs the literal 8 acceptance steps against a real 5-symbol
  Yahoo daily sample: real scanner, real decision path, captured finalists/labels, a second independent
  clock/context at an earlier instant, no exception, no lookahead.

Disclosed limitation: sector breadth/rotation and 7 of the decision engine's 9 evidence families (catalyst,
short interest, filings, options flow, social, analyst, macro) have no historical replay yet and are
neutralised to zero confidence for every historical run — only the price-derived trend/momentum family and
the risk/regime family (itself neutral when no regime is supplied) carry real signal today. A historical
decision label should be read with that in mind until more evidence families are wired.

## Dataset audit (done): `fabhaus/equities_5m_stockprices`

Full report: `audits/FABHAUS_AUDIT_REPORT.md`. **Verdict: conditionally viable, not yet accepted.** A real,
material defect was found by direct testing (not the Hub viewer, which is broken for this dataset): the
`datetime` field is documented as UTC but is actually America/New_York wall-clock time with a "Z" suffix
mistakenly appended (proven via the closing-auction volume spike landing at the labelled 16:20-16:25, which
only makes sense at the real 16:00 ET close). This is fixable at the ingestion adapter
(`HuggingFaceEquitiesAdapter(source_tz="America/New_York")`, now built and tested) — not by itself a reason
to replace the dataset. Two further items are NOT yet resolved: volume is systematically 20-46% of Yahoo's
consolidated daily volume for every symbol checked, and the dataset's own documented lack of
corporate-action adjustment is now confirmed (NVDA's 2024-06-07 10:1 split shows up exactly as expected).
A corporate-action policy is drafted in the report; **H4 remains blocked until it is implemented**, per the
project's own rule that raw history is never silently adjusted.

Secondary candidate `GGLabYale/MTBench_finance_stock` (2013-2023 coverage) is recorded, not integrated.

## H4 status: BLOCKED

Not started. Blocked on: (1) re-running the dataset audit with `source_tz` applied and a wider symbol/month
sample; (2) a volume-normalization decision; (3) implementing the corporate-action policy in code (with
tests); (4) H5 (reproduce the known 2026-09-25 forward day) as the gate before any strategy research.
