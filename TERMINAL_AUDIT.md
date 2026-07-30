# TERMINAL_AUDIT.md

Diagnosis and remediation log for the research terminal
(`dashboard/app.py` + `dashboard/research.py` + `dashboard/terminal.html`, backed
by the `lab/` provider modules and the `src/tradingview_mcp` services).

The terminal is a Flask app that serves a single-page institutional UI and a set
of `/api/*` JSON endpoints. It reuses every provider already wired into the repo
(TradingView screener, Yahoo Finance, Finnhub, StockTwits, Google News, FRED,
SEC EDGAR) plus the local paper-trading ledger.

---

## 1. Method

* Mapped the architecture: Flask routes → `research.py` (SWR cache, provider
  guards, per-symbol analysis) → `lab/*` provider adapters → `src` services.
* Ran the app on `:5057` and reproduced every complaint against the **live**
  endpoints (curl + browser DOM/JS inspection), then captured latency from the
  built-in `/api/diag` ring buffer.
* Confirmed root causes in code before changing anything.

---

## 2. Confirmed problems, root causes, and fixes

### P1 — Ticker search returns only a single already-valid symbol; no universe
**Reproduced:** `"Apple"`, `"Nokia"`, `"Alphabet"`, `"Google"`, `"BRK B"`,
`"NOKIA.HE"` all returned `valid:false / unsupported`. Only exact tickers worked.

**Root cause:** the search box called `/api/symbol/resolve`, whose
`_resolve_compute()` did `valid_symbol_shape()` (regex on an already-ticker-shaped
string) and then a **live price fetch**. There was no ticker table, no
company-name/alias index, no fuzzy matching — a company name is not a ticker
*shape*, so it never resolved.

**Fix:** new **security-master service** (`dashboard/security_master.py`): a local
SQLite universe of ~30k US stocks/ETFs/ADRs + curated foreign listings & aliases,
built from Finnhub's US symbol list + a curated seed (+ SEC when reachable), with
an in-memory ranked, fuzzy, alias- and typo-tolerant search. New `/api/search`
endpoint. `resolve_symbol()` now consults the master first for identity, then
attaches a price. Company names, aliases, former names, space forms and foreign
listings all resolve.

### P2 — Search is slow (4–9 s) and inconsistent
**Reproduced:** `/api/symbol/resolve` averaged **4062 ms, max 8938 ms** (from
`/api/diag`) — paid on every keystroke.

**Root cause:** each keystroke hit Finnhub → Yahoo → TradingView synchronously.

**Fix:** search is now a purely **local** in-memory scan over prebuilt bisect /
fuzzy-bucket indexes. Measured **median ~9 ms, p95 ~56 ms, max ~65 ms** over the
full 30k universe (target was < 150 ms). Frontend adds a 140 ms debounce, stale
sequence-drop, keyboard nav, and recent/popular lists.

### P3 — Foreign / exchange-suffixed symbols broken
**Reproduced:** `NOKIA.HE` → `unsupported`.

**Root cause:** `to_yahoo()` did a blanket `.replace(".", "-")`, turning
`NOKIA.HE` into the non-existent `NOKIA-HE`. It couldn't tell a US share-class dot
(`BRK.B`) from a foreign-exchange suffix (`.HE`, `.L`, `.T`, `.NS`, …).

**Fix:** `security_master.to_yahoo()` preserves known foreign suffixes verbatim
and only converts a share-class dot to a dash; `research.to_yahoo/to_finnhub`
delegate to it. `resolve_symbol` skips the (US-only) Finnhub quote for foreign
symbols and goes straight to Yahoo, which supports them.

### P4 — Space / slash share-class forms not normalized
**Reproduced:** `"BRK B"` and `"BRK/B"` → `unsupported`.

**Root cause:** `canonical()` stripped spaces, producing `BRKB` (not a symbol).

**Fix:** `canonical_query()` maps `ROOT<sep>CLASS` → `ROOT.CLASS` (`BRK B` → `BRK.B`).

### P5 — Stock comparison did not exist
**Reproduced:** no compare view, endpoint, or code anywhere (`grep -ri compar` → nil).

**Fix:** new **comparison workspace** — `/api/compare?symbols=…&range=…` returns
one normalized schema (aligned %-performance, volatility, beta vs SPY, max
drawdown, relative strength, correlation matrix, fundamentals, technicals). New
**Compare** view with a 2–8 security tray, synchronized multi-line chart,
side-by-side metric table and a correlation heatmap. Each panel degrades
independently with an explicit *data unavailable* state (never fabricated).

### P6 — Analysis lacked scenarios / “what changed” / relevance-weighted sentiment
**Finding:** the decision engine was already rich (independent signal families,
EV gating, supporting/conflicting/missing evidence, provenance). Gaps vs the
brief: no bull/base/bear payoff view, no diff-since-last-analysis, and news
sentiment was a keyword lean that treated every headline equally.

**Fix:** `_overview_compute()` now adds **bull/base/bear scenarios** (anchored on
the engine's entry/stop/target + P(direction)) and **what-changed** (persisted
snapshot diff of decision/quality/P(dir)/price). A new **model registry**
(`lab/model_registry.py`) provides per-headline financial sentiment with
**recency decay + source reliability + ticker relevance + duplicate detection**,
so unrelated or stale headlines don't pollute a ticker's reading.

### P7 — Provider failures / blank sections
**Finding:** the SWR cache, circuit breaker, per-provider `_guard` health
classification, time-boxing and structured error states were already in place and
good. Improvements: near-duplicate news collapse by title-Jaccard similarity;
foreign-symbol provider routing; and every new panel returns a typed state
(`ok|loading|empty|throttled|no_price|error|missing`) the UI renders as a
labelled placeholder, never a fake value.

### P8 — Model integrations underused
**Finding:** no configurable model layer; sentiment was keyword-only.

**Fix:** provider-independent **model registry** configured by env vars, failing
gracefully to a lexical backend when `transformers`/`torch` are absent. HF FinBERT
loads lazily and once when enabled. A real **walk-forward forecast benchmark**
(`lab/forecast_benchmark.py`) with leakage guards establishes the baseline bar
before any heavy time-series model is adopted (see `MODEL_EVALUATION.md`).

---

## 3. Performance summary (measured on this machine)

| Path | Before | After |
|---|---|---|
| Ticker suggestion (search) | 4.0 s avg / 8.9 s max | **~9 ms median / 65 ms max** |
| Security-master index | — | 30,303 rows, in-memory |
| `/api/search` HTTP round-trip | — | 4–25 ms warm, 123 ms cold |
| `/api/compare` (3 symbols, cold) | (did not exist) | ~4 s, then SWR-cached |
| Analysis / catalysts / sentiment | unchanged (already SWR-cached + concurrent) | + async cold-miss placeholders |

---

## 4. Files changed / added

**New**
* `dashboard/security_master.py` — security-master service + universal search.
* `lab/model_registry.py` — provider-independent sentiment/model registry.
* `lab/forecast_benchmark.py` — offline walk-forward forecast benchmark.
* `tests/unit/test_terminal.py` — 41 tests (normalization, search, ranking,
  foreign listings, compare math, dedup, cache, provider fallback, states).
* `TERMINAL_AUDIT.md`, `MODEL_EVALUATION.md`, `DATA_PROVIDER_MATRIX.md`.

**Changed**
* `dashboard/research.py` — master-aware `resolve_symbol`; `to_yahoo/to_finnhub`
  delegate to the master; `search/popular_symbols/master_stats`;
  `compare()` + comparison math; scenarios + what-changed; model-registry-backed
  catalyst sentiment; `registry_status()`.
* `dashboard/app.py` — `/api/search`, `/api/search/popular`, `/api/search/stats`,
  `/api/compare`, `/api/models`; models + search in `/api/health`; boot pre-warm
  of the security master.
* `dashboard/terminal.html` — multi-result search dropdown (keyboard nav,
  recent/popular, exchange·type·currency labels); Compare view + tray; bull/base/
  bear + what-changed in the Overview tab; news-sentiment aggregate + model badge.
* `.env.example` — data-provider keys, model-registry flags, dashboard port.

**Preserved:** every existing API key, the paper/cash ledger, broker
integrations, the scheduler, and all previously-working MCP tools and dashboard
panels. No secrets are logged or shipped to the frontend.

---

## 5. Verification

* `pytest tests/` → **182 passed** (141 existing + 41 new). No regressions.
* All required tickers verified end-to-end against the running app: `AAPL`,
  `MSFT`, `BRK.B`, `NOK`, `NOKIA.HE`, `SPY`, an invalid ticker, a low-liquidity
  name, and a symbol with multiple international listings (Nokia US ADR vs
  `NOKIA.HE`). See `VERIFICATION_REPORT.md`.
