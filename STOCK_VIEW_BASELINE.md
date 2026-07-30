# STOCK_VIEW_BASELINE.md

Per-panel profile of the stock-detail page **before** the progressive-loading
work (Prompt 3). Measured against the running app on `:5057` (symbol `NFLX`,
cold = first hit with empty caches, warm = repeat). No ML added.

---

## Endpoint timings

| Panel | Endpoint | Cold | Ready (async) | Warm | State on failure |
|---|---|---:|---:|---:|---|
| Quote / header | `/api/symbol/resolve` | **1,331 ms** | — | 102 ms | `unsupported` |
| Chart (3M) | `/api/symbol/chart` | **3,277 ms** | — | 15 ms | `empty` |
| Position / account | `/api/state` | 17 ms | — | 17 ms | `error` |
| Technicals | `/api/symbol/technicals` | 84 ms → | **2,065 ms** | 16 ms | `throttled` |
| Fundamentals | `/api/symbol/fundamentals` | 1,087 ms | — | 15 ms | `error` |
| Options | `/api/symbol/options` | 1,136 ms | — | 15 ms | `unsupported`/`empty` |
| Catalysts | `/api/symbol/catalysts` | 1,054 ms | — | 15 ms | `empty` |
| **Sentiment** | `/api/symbol/sentiment` | 27 ms → | **15,903 ms** ⚠ (ends errored) | 15 ms | `error`/`missing` |
| **Decision engine** | `/api/symbol/overview` | 15 ms → | **4,111 ms** | 16 ms | `unsupported` |

(`→` = returns a `loading` placeholder immediately, then the real value lands on a
later poll; "Ready" is the wall-clock to usable data.)

---

## Findings

1. **First useful content is gated on the header's network quote.** `resolve`
   costs 1,331 ms cold because it fetches a live Finnhub quote — yet the security
   master already holds the name/exchange/type/currency **locally (< 10 ms)**. The
   header could paint instantly and fill the price in.

2. **The overview tab waits for the WHOLE decision engine (4.1 s ready).** There is
   no fast "summary" — the two cheap families (trend from the base TA call, regime
   from cache) are ready in ~1.6 s cold / < 100 ms warm, but the tab blocks on the
   7 slow I/O families (catalyst, options-flow, social, analyst, macro, short,
   filings) before showing anything.

3. **Sentiment is the worst panel — 15.9 s and it still errors.** It fans out six
   providers including a sector-ETF technicals call that routes through the
   throttle-prone TradingView screener; under throttle the internal gather drags
   to ~16 s and returns `None`.

4. **Chart is slow cold (3.3 s)** — a full yfinance history fetch.

5. **Warm is already excellent (15–102 ms)** thanks to the SWR/DL layer — the cold
   *first paint* is the whole problem.

6. **Duplicate requests are already mitigated** by the client `DL` layer
   (in-flight de-dup + cache) and server `swr` coalescing: `overview` is requested
   by the Overview tab, the Risk tab and the Catalysts view, but repeats within
   TTL collapse to one call. The header (`resolve`) and the panels do not re-fetch
   the same key from separate components.

---

## Targets for this phase

| Target | Baseline | Goal |
|---|---:|---:|
| Cached header visible | 1,331 ms | **< 500 ms** (master identity instant) |
| Cached chart + summary | 3,277 ms / 4,111 ms | **< 1 s** |
| Warm decision summary | (none) | **< 2 s** |
| Cold decision summary | 4,111 ms (full) | **< 5 s** (fast partial) |
| Deep analysis | blocks the tab | **loads progressively, non-blocking** |
| Sentiment panel | 15.9 s, errors | strict timeout + graceful partial |
| One failed panel blocks page | no (independent) | keep independent |

The plan: add a **fast decision summary** (trend + regime + scanner score +
scenarios, returned with a completion status and the list of families still
computing), render it immediately, and stream the deep analysis in behind it —
while painting the header from the local master and tightening the sentiment
timeout.
