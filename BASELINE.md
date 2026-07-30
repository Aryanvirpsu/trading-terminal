# BASELINE.md

Real, measured performance baseline of the trading terminal **before any
optimisation**. Captured against the running app (`dashboard/app.py` on
`http://127.0.0.1:5057`) plus in-process component timings. Nothing was changed,
downloaded, or deleted to produce these numbers.

> **Measurement conditions.** Captured 2026-07-25 on the host below. Several
> numbers were taken while TradingView's screener was **actively rate-limiting**
> (`JSONDecodeError: Expecting value` = empty-body throttle). That is a genuine,
> reproducible operating condition for this app, not a test artefact — it is
> called out inline where it dominates a number, with the un-throttled component
> cost given alongside.

---

## 1. Environment

| Resource | Value |
|---|---|
| CPU | Intel Core **i7-12650H**, 10 cores / 16 threads, 2.3 GHz base |
| RAM | **15.7 GB total**, **1.1 GB free** at capture (14.6 GB in use — under pressure) |
| GPU 0 | **NVIDIA RTX 3050 Laptop, 4 GB VRAM** — 0 % util, **0 MiB used** |
| GPU 1 | Intel UHD Graphics (integrated, 1 GB) |
| OS / Python | Windows 11, Python 3.13.3 |
| Dashboard process | **152 MB working set / 789 MB private**, 34 threads |

**GPU is completely idle** — no CUDA compute apps, 0 MiB VRAM. Nothing in the app
uses the GPU today.

---

## 2. Model loading (startup cost)

**No ML models load** — there is no `torch` / `transformers` / `onnxruntime` /
`sentence-transformers` installed (see `MODEL_INVENTORY.md`). The only "model" that
runs is the pure-Python lexical sentiment scorer. So "model-loading time" is really
**heavy Python-library import time at boot**:

| Import | Cold time |
|---|---|
| yfinance | **1,755 ms** |
| pandas | **1,092 ms** |
| tradingview_ta | 291 ms |
| flask | 289 ms |
| tradingview_screener | 285 ms |
| numpy | 218 ms |
| feedparser | 97 ms |
| **Total heavy imports** | **≈ 4.0 s** |
| model_registry (lexical) | 1.4 ms |
| security master (30,303 rows) | **~1.0 s load, 52 MB heap** |

Lexical sentiment inference: **0.09 ms first call, ~160,000 headlines/sec**,
negligible memory. It is effectively free.

---

## 3. Stock scanner (`find_trades` → `/api/candidates`)

**Structure:** `market_regime()` → `_prerank_universe()` (73-name liquid US
universe) → **sequential loop** over the top 22 (stocks) / 30 (options-only) names
calling `_analyze_cached()` (TradingView TA) one at a time, then a per-qualifier
Yahoo options fetch.

### Speed (measured component costs, cold)

| Stage | Time | Notes |
|---|---|---|
| `market_regime()` | **7,442 ms** | Yahoo indices + VIX + 11 sector SPDRs; cached 60 s |
| `_prerank_universe()` | **928 ms** | one bulk screener query → 70 names |
| `_analyze_cached()` per name | **1,732 ms** cold / **0 ms** warm | 1 TA call; 600 s in-proc cache |
| `_pick_option_idea()` per name | **2,056 ms** | Yahoo options chain |
| **Full cold scan (un-throttled est.)** | **≈ 70–75 s** | 7.4 + 0.9 + 30×1.7 + ~6×2.0, **sequential** |
| **Full cold scan (observed, throttled)** | **avg 320 s / max 640 s** | from `/api/diag`, n=2 real runs |
| Warm endpoint (SWR stale) | **2–25 ms** | serves last good set + background refresh |

The scan loop is **single-threaded/sequential** — it does not even use the 2
concurrent TA slots the throttle layer allows. Under throttling, each failing name
costs 3 retries (~5 s backoff) **+ a 15 s failure-cooldown sleep**, which is what
turns a ~70 s scan into 300–640 s.

### Ranking quality

* Pre-rank is momentum-based over a **fixed 73-name universe** (`UNIVERSE`) — it
  will never surface a mover outside that list.
* Each card carries: stock score, trend state, entry, stop, targets, R:R — good,
  structured output.
* **Gap:** at capture the endpoint was serving a **stale** set
  (`names_deep_scanned=4, candidates_found=1, stale=True`) because live scans were
  rate-limited — i.e. users are often looking at a minutes-to-hours-old scan.

---

## 4. Options scanner

There are **two** options paths of very different quality:

### 4a. In-scanner option idea (`_pick_option_idea`, inside `find_trades`)
* **~2,056 ms per name.**
* Picks one affordable, risk-capped contract (strike, expiry, premium, DTE, cost).
* **Quality gap:** the returned idea has **`spread_pct = None`, `liquidity_score =
  None`** — no spread/liquidity grading is attached in the scanner path.

### 4b. Dedicated options endpoint (`/api/symbol/options`) — good
* **~400 ms warm**, ranked contracts with full quality:

| strike | grade | liq | spread | OI | vol | tradeable |
|---|---|---|---|---|---|---|
| 335.0 | A | 100 | 3.5 % | 2,040 | 47,022 | ✔ |
| 332.5 | A | 88 | 4.6 % | 946 | 32,131 | ✔ |
| 325.0 | A | 85 | 12.3 % | 1,806 | 7,465 | ✔ |

* AAPL: 11 tradeable contracts, A–D grades, spread%, OI, volume, pass/fail reasons.

The dedicated endpoint's ranking is strong; the **scanner's inline option idea is
the weaker of the two** and doesn't reuse it.

---

## 5. Stock-detail + decision-engine load times

Cold panel load (first request → useful data), symbol `ORCL`:

| Panel | Cold load | Final state |
|---|---|---|
| resolve (identity + price) | 1,399 ms | ok |
| technicals | 532 ms | ok |
| chart (3M) | 403 ms | ok |
| catalysts | 405 ms | ok |
| fundamentals | 1,147 ms | ok |
| options | 398 ms | ok |
| **overview (decision engine)** | **> 10,000 ms** | still `loading` after 10 s |
| **sentiment** | **> 10,000 ms** | still `loading` after 10 s |

### Decision engine (`decision_engine.evaluate`) — measured in isolation

| Run | Time |
|---|---|
| **COLD** | **52,570 ms** |
| **WARM** | **36,795 ms** |

**Why it is this slow (from the logs):** the TA call hits the rate-limit cliff
(`JSONDecodeError: Expecting value`), retries **3×** (0 / 1.0 / 4.4 s), then sleeps
a **15 s failure cooldown**, then repeats — and `evaluate()` has an **exchange
fallback** (tries NASDAQ *then* NYSE), doubling the storm. Successful TA results are
cached (600 s) but **errors are not**, so the **warm run re-pays the whole throttle
storm** (36.8 s). The 9 signal families are also evaluated **sequentially**
(`_fam_trend`, `_fam_catalyst`, `_fam_regime`, `_fam_short`, `_fam_filings`,
`_fam_options_flow`, `_fam_social`, `_fam_analyst`, `_fam_macro`).

Un-throttled, the same engine is on the order of a few seconds; the 37–53 s figures
are the throttled reality and are the single biggest UX problem in the terminal.

---

## 6. API calls, duplicates, sequential loops, cache misses

From `/api/diag` (accumulated over the running session):

| Metric | Value |
|---|---|
| Cache **hit rate** | **29.7 %** (hit 94 / stale 106 / miss 117) |
| Background refreshes | 101 succeeded, **10 failed** (`refresh_err` = throttled) |
| Cache entries live | 110 |

**Sequential loops (fixable):**
* `find_trades` — per-ticker analysis loop, one name at a time (no concurrency).
* `decision_engine.evaluate` — 9 families evaluated one after another.

**Duplicate / redundant work:**
* TA **errors are not cached**, so `overview`, `technicals`, and the sentiment
  sector-ETF proxy each **independently re-trigger the same failing TA retry+cooldown
  storm** for the same symbol — the same rate-limited call is paid several times.
* The decision engine's NASDAQ→NYSE exchange fallback pays the throttle twice per
  symbol.

**Already good (not a problem):**
* Client data layer (`DL`) does in-flight de-dup + SWR + abort-on-switch.
* Server `swr()` coalesces N concurrent identical requests into one call.
* Frontend does **not** aggressive-poll (only a 1.5 s dev perf panel); panels fetch
  on navigation, so there is no client-side request storm.

**Cache-miss hotspots:** the low 29.7 % hit rate is driven by (a) errors never
being cached (every failed TA re-computes), and (b) short TTLs on the throttle-prone
paths, so a rate-limited provider is retried instead of short-circuited.

---

## 7. CPU / RAM / GPU summary

| | Baseline |
|---|---|
| Dashboard process RAM | 152 MB working set / **789 MB private** (no models; pandas+numpy+yfinance + 52 MB security master) |
| Host RAM headroom | **1.1 GB free** — tight; a 440 MB+ model would strain it on CPU |
| GPU | **Unused** (0 MiB / 4 GB). Available for a small model if moved off CPU |
| CPU | Bursts during scans (sequential TA); mostly **I/O-wait on providers**, not CPU-bound |

The terminal is **I/O-bound on external providers (TradingView TA above all)**, not
CPU/GPU/RAM-bound. No computation here needs the GPU today.

---

## 8. Bottleneck ranking (diagnosis only — no changes made)

1. **TradingView TA rate-limit cliff + retry(3)+15 s-cooldown amplifier** — the
   dominant cost in the decision engine (37–53 s) and the scanner (320–640 s).
2. **Sequential scanning / family evaluation** — no concurrency where the throttle
   budget (2 concurrent) would allow it.
3. **Errors not cached** — the same failing call is re-paid across panels and across
   warm re-runs; 29.7 % cache hit rate.
4. **`market_regime()` at 7.4 s cold** — a heavy synchronous input to every scan.
5. **Scanner option idea lacks liquidity/spread grading** (quality gap vs the
   dedicated options endpoint).
6. **Fixed 73-name scan universe** — limits ranking breadth.
7. **~4 s cold library-import startup** and 789 MB process footprint on a
   1.1 GB-free host.

None of the above involves ML models — see `MODEL_INVENTORY.md`. All figures here
are the **pre-optimisation baseline** to compare future changes against.
