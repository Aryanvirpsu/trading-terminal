# VERIFICATION_REPORT.md

End-to-end verification of the research terminal after the fixes in
`TERMINAL_AUDIT.md`. Every result below was captured against the **running** app
on `http://127.0.0.1:5057` (live `/api/*` endpoints + browser DOM/JS inspection),
not from code reading.

---

## How to run locally

```bash
# from the repo root: tradingview-mcp/
cp .env.example .env            # then paste your free-tier keys (FINNHUB_API_KEY etc.)
pip install -e .                # installs the package + deps (Flask, yfinance, pandas, numpy…)

# 1) Build the ticker universe once (optional — the app also builds it on boot):
python dashboard/security_master.py refresh     # SEC + Finnhub -> security_master.db (~30k)

# 2) Start the terminal:
python dashboard/app.py                          # serves http://127.0.0.1:5057
#   (or set DASHBOARD_PORT=xxxx)

# 3) Tests + benchmark:
pytest tests/ -q                                 # 182 passed
python lab/forecast_benchmark.py                 # walk-forward baseline benchmark
python lab/model_registry.py                     # model-registry status + demo
```

Open `http://127.0.0.1:5057`, press `/` to focus search, type a company name.

---

## 1. Universal search  ✅

Local security master, 30,303 rows, **median ~9 ms / p95 ~56 ms / max ~65 ms**
(target < 150 ms). HTTP round-trip 4–25 ms warm, 123 ms cold.

| Query | Result | Note |
|---|---|---|
| `AAPL` | AAPL · Apple Inc. · Common Stock · NASDAQ · USD | symbol |
| `Apple` | AAPL | company name |
| `Microsoft` | MSFT | company name |
| `Alphabet` / `Google` | GOOGL | alias / former name |
| `BRK.B` / `BRK B` | BRK.B · Berkshire Hathaway (B) · NYSE | space-form normalised |
| `NOK` / `Nokia` | NOK · Nokia ADR · NYSE · USD | US ADR |
| `NOKIA.HE` | NOKIA.HE · Nokia Oyj · Helsinki · EUR | foreign listing |
| `SPY` | SPY · SPDR S&P 500 ETF · NYSE Arca · ETP | ETF |
| `microsft` | MSFT | typo tolerance |
| `ZZZZINVALID` | `no_match` (0 hits) | clean empty state |

Frontend: multi-result dropdown, keyboard nav (↑/↓/Enter/Esc verified),
recent + popular lists, exchange · type · currency labels.

## 2. Symbol resolve (identity + live price)  ✅

| Ticker | valid | state | price | source |
|---|---|---|---|---|
| AAPL | ✔ | ok | 333.02 | finnhub |
| MSFT | ✔ | ok | 381.70 | finnhub |
| BRK.B | ✔ | ok | 494.93 | finnhub |
| NOK | ✔ | ok | 9.10 | finnhub |
| **NOKIA.HE** | ✔ | ok | 8.222 (EUR) | **yahoo** (foreign path) |
| SPY | ✔ | ok | 738.93 | finnhub |
| GEV / IMAX (lower-liquidity) | ✔ | ok | 1014.75 / 43.38 | finnhub |
| ZZZZINVALID | �’ | unsupported | — | — |

## 3. Comparison workspace  ✅  (AAPL vs MSFT vs BRK.B, 6M)

3/3 loaded · benchmark SPY (+7.24%).

| Symbol | Perf | Vol(ann) | Beta | MaxDD | vs SPY | P/E |
|---|---|---|---|---|---|---|
| AAPL | +30.63% | 27.21 | 0.76 | -12.71% | +23.39% | 39.9 |
| MSFT | -18.48% | 34.27 | 0.78 | -26.42% | -25.72% | 22.6 |
| BRK.B | +2.37% | 15.45 | 0.00 | -8.40% | -4.87% | 13.2 |

Correlation matrix (daily returns): AAPL·MSFT 0.24, AAPL·BRK 0.22, MSFT·BRK 0.02.
BRK.B beta ≈ 0 is **real** (124 aligned return-days, corr 0.004) — a genuine
diversifier this window, exactly the kind of insight the feature exists to surface.

**Multiple international listings** (NOK vs NOKIA.HE, 3M): both load and stay
distinct — NOK (USD, NYSE, -15.43%) vs NOKIA.HE (EUR, Helsinki, -7.74%).

**Degradation** (AAPL, NOK, ZZZINVALID): invalid symbol → `state=no_price`,
price/perf `None` (rendered "–", never fabricated); correlation includes only the
loaded symbols.

## 4. Analysis engine  ✅  (AAPL overview)

`decision=TRADEABLE`, `quality=62.9/45`, `P(dir)=0.652`, `EV/sh=10.93`,
6 supporting / 1 conflicting signals, 9 independent families, `missing_data=[]`.
New: **bull/base/bear** (380.12 +14.1% / 356.57 +7.1% / 321.25 −3.5%) with
invalidation conditions, and **what-changed** (first-run recorded; subsequent runs
diff decision/quality/P(dir)/price). All rendered in the Overview tab.

## 5. News, catalysts & sentiment  ✅

* Catalysts (AAPL): 16 catalyst headlines, model-backed per-headline sentiment,
  aggregate score 0.092 over 44 relevance/recency/source-weighted headlines,
  1 near-duplicate collapsed, backend `lexical`.
* Sentiment (AAPL): stock `bullish` (0.455) from news + social + analyst —
  contributors listed; missing providers reported, never faked-neutral.

## 6. Model registry & fallback  ✅

`/api/models`: sentiment backend `lexical`, `transformers_installed=false`,
FinBERT declared and ready (`SENTIMENT_MODEL_ENABLED=true` to enable). Simulated
model absence → graceful lexical fallback (no crash, no blank panel).

## 7. Forecast benchmark (leakage-free)  ✅

Walk-forward over AAPL/SPY/NVDA/KO/TLT (2y, horizons 1 & 5): last_value MASE 1.00
(winner), moving_avg_5 1.30, linear_trend 1.53; directional accuracy ~0.52. Heavy
HF models must beat this bar before adoption. Leakage guard: **passed**.

## 8. Provider reliability  ✅

`/api/health`: finnhub `ok`, yahoo `ok`, tradingview `ok`; fred/alphavantage/
stocktwits/google_news/snaptrade/gemini `configured`. Circuit breaker, SWR,
in-flight coalescing, time-boxing and typed failure states all exercised. Unit
tests cover throttle classification, task isolation, and cache live/stale/miss.

## 9. Tests & UI  ✅

* `pytest tests/` → **182 passed** (141 existing + 41 new), 0 regressions.
* All 8 views (Home, Scan, Stock, **Compare**, Cat, News, Sched, Set) render with
  content; **zero browser console errors** across a full navigation pass.

---

## Environment note

Visual screenshots could not be captured in this headless session (the browser
pane wasn't compositing frames); verification therefore used DOM/JS inspection and
live API calls, which confirm rendered content, structure and values directly.

## Remaining limitations

See `README` §"Terminal — remaining limitations" and the summary in
`TERMINAL_AUDIT.md`. In short: HF forecasting/embeddings/anomaly models are
declared and benchmark-gated but not enabled here (no `torch`/`transformers`);
SEC CIK enrichment needs a compliant User-Agent (Finnhub covers names/types
meanwhile); foreign-listing coverage is the curated seed plus anything Yahoo
resolves, not an exhaustive global master; and some cold analysis panels return a
`loading` placeholder for a few seconds while the decision engine computes.
