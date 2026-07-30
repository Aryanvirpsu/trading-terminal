# SCANNER_SPEED_AFTER.md

Results of the Prompt-2 data-pipeline optimisation, measured with the same
process used in `BASELINE.md` (running app on `:5057` + in-process component
timings). **No Hugging Face models were added** — the system is I/O-bound and the
fixes are all in the data pipeline.

---

## Before / after

| Metric | Before | After | Improvement |
|---|---:|---:|---:|
| `/api/candidates` cold | **320 s avg (640 s max)** | **23.98 s** responsive¹ · **hard-capped** by the breaker; instant stale fallback when throttled | **−92 %**, no more 320–640 s blowups |
| `/api/candidates` warm | 2–25 ms (SWR) but bg scan 320–640 s | **17 ms** (in-proc warm 5.0 s; bg scan now bounded) | bg scan no longer runs away |
| Decision engine cold | **52.6 s** | **15.6 s** | **−70 %** |
| Decision engine warm | **36.8 s** | **5.3 s** | **−86 %** |
| Market regime | **7.4 s** | **1.58 s** cold / **0.02 s** warm | **−79 % / −99.7 %** |
| Cache hit rate | **29.7 %** | **85.7 %** warm (repeated scans) | **+56 pts** (>75 % target) |
| Scanner option grading coverage | **missing** (`spread=None`, `liq=None`) | **100 %** (A–F grade + full fields) | fixed |
| Duplicate provider calls | NASDAQ+NYSE ×2 · per-panel retry storms · no coalescing | **single exchange · coalesced · negative-cached** | eliminated |

¹ **Measured** on a responsive provider: 20-name shortlist, all 20 scanned, zero
throttled — regime 1.53 s + prerank 0.81 s + concurrent deep-scan 16.48 s +
concurrent options 5.16 s = **23.98 s total** (warm re-run 5.04 s). Under an
active TradingView throttle the same call instead returns the last good scan
**instantly (24 ms, `stale=true`)** with degradation flags — it fast-fails and
degrades rather than blocking on the old 15 s cooldowns.

---

## Acceptance targets

| Target | Status | Evidence |
|---|---|---|
| Warm scanner < 10 s | ✅ **17 ms** endpoint / 5.0 s in-proc | in-process warm `find_trades`; endpoint SWR |
| Cold scanner < 30 s when provider responsive | ✅ **23.98 s measured** | responsive run, 20/20 scanned, 0 throttled |
| Decision engine < 5 s using cached/stale | ✅ | endpoint serves stale instantly (`swr_async`); raw warm 5.3 s |
| Market regime < 500 ms cached | ✅ **20 ms** | `/api/market` warm |
| Warm cache hit rate > 75 % | ✅ **85.7 %** | repeated-scan diag delta (hit 24 / stale 12 / miss 6) |
| No option result with unexplained `spread=None`/`liq=None` | ✅ | shared grader; `test_grade_never_returns_unexplained_none` |
| No 15 s cooldown in the request path | ✅ | cooldown sleep removed; `test_breaker_open_is_non_blocking` |
| No sequential per-symbol deep scan | ✅ | `ThreadPoolExecutor` staged pipeline + per-stage timings |

---

## What changed (by section of the brief)

**1 — TradingView retry amplification** (`screener_provider.py`)
* Removed the **blocking 15 s failure-cooldown** from the request path.
* Added a **non-blocking circuit breaker** (CLOSED→OPEN after 3 fails→HALF_OPEN
  after a 20 s window). Open = fast-fail (serve stale or raise `TVThrottledError`).
* Added **negative caching** (30 s) so all panels don't re-run the same failing call.
* Added **in-flight coalescing** — identical concurrent TA fetches collapse to one.
* Kept exponential backoff + jitter; structured `TVThrottledError` (a `RuntimeError`).

**Decision engine** (`decision_engine.py`)
* Removed the blind **NASDAQ→NYSE double-hit**; the caller now passes the correct
  exchange from the security master (`_tv_exchange_hint`), so the throttle is paid
  once, then a single free yfinance fallback.
* The **9 signal families + the option pick now run concurrently** (were sequential).
* Emits `data_state` (fresh / fallback-provider), `data_source`, `engine_ms`.

**2 — Parallel staged scanner** (`strategy_service.find_trades`)
* Pipeline with **per-stage timings** in the response + logs: regime → prerank
  (one batched call, the cheap pass) → **concurrent deep-analysis of the top
  shortlist only** (bounded, 2 workers, `STRATEGY_SCAN_WORKERS`) → **options only
  for the final shortlist**, concurrently. Deep-scan capped at `STRATEGY_DEEP_CAP`
  (20).

**3 — Market regime** (`strategy_service.market_regime`)
* 15 index/VIX/sector fetches now **concurrent** (7.4 s → 1.58 s) with
  **stale-while-revalidate** (serves the last value instantly, refreshes in bg);
  one shared result, never recomputed per ticker; `cache_state` reported.

**4 — Cache behaviour**
* Per-family TTLs (existing) + **negative/error caching**, SWR, in-flight
  coalescing, provider-scoped cache keys; only successful upstream responses are
  cached (malformed/throttled are negative-cached, never stored as valid).
* **Warm hit rate 29.7 % → 85.7 %.**

**5 — Shared option grading** (`options_grading.py`, new)
* One `grade_contract` used by BOTH `/api/symbol/options` and the scanner's
  `_pick_option_idea`. Every option idea now carries bid, ask, mid, spread$,
  spread%, volume, OI, IV, delta, liquidity score, A–F grade, DTE, breakeven,
  tradeable flag and a rejection reason. Rejects/penalises zero bids, one-sided or
  stale quotes, missing Greeks, wide spreads, low volume, low OI, excess slippage.

**6 — Expanded universe + presets** (`research.build_scan_universe`)
* Fixed 73-name list → **bounded pool drawn from the 30 k security master** with
  local filters (USD, supported exchange, common-stock/ETF/ADR type) plus the
  curated liquid base. Presets: `liquid` (120), `momentum`, `mean_reversion`,
  `options_eligible`, `etf` (120 ETFs), `small_cap_speculative` (labelled). New
  `/api/scan/presets`; `/api/candidates?preset=…`. The 30 k master is **never
  deep-scanned** — only the pre-ranked shortlist is.

**7 — Graceful degradation**
* The scan payload now carries `degradation` (provider_status, names_throttled,
  breaker state, skipped_families, confidence_penalty) and per-stage `timings`;
  it serves the last good set (stale-flagged) when fully throttled instead of an
  empty list.

---

## Files changed

* `src/tradingview_mcp/core/services/screener_provider.py` — non-blocking breaker,
  negative cache, in-flight coalescing, `TVThrottledError`, `breaker_status`.
* `src/tradingview_mcp/core/services/strategy_service.py` — concurrent staged
  scanner + timings + degradation; concurrent `market_regime` + SWR;
  `universe`/`preset`/`scan_workers` params.
* `src/tradingview_mcp/core/services/options_grading.py` — **new** shared grader.
* `lab/decision_engine.py` — single-exchange load, concurrent family fan-out,
  `data_state`/`engine_ms`.
* `dashboard/research.py` — `build_scan_universe` + presets; options endpoint uses
  the shared grader; overview passes the master exchange.
* `dashboard/app.py` — `/api/candidates?preset=`, `/api/scan/presets`.
* `tests/unit/test_scanner_perf.py` — **new**, 17 tests (breaker, negative cache,
  grader, exchange hint, regime cache, presets).

## Benchmark commands

```bash
# unit + integration (throttle-independent)
pytest tests/ -q                                    # 199 passed
pytest tests/unit/test_scanner_perf.py -q           # 17 passed

# component timings (in-process)
python - <<'PY'
import sys,time; sys.path.insert(0,'src')
from tradingview_mcp.core.services import strategy_service as ss
t=time.perf_counter(); ss.market_regime(); print("regime cold %.0fms"%((time.perf_counter()-t)*1000))
t=time.perf_counter(); ss.market_regime(); print("regime warm %.1fms"%((time.perf_counter()-t)*1000))
PY

# endpoints (server on :5057)
curl -s "http://127.0.0.1:5057/api/scan/presets" | python -m json.tool
curl -s "http://127.0.0.1:5057/api/candidates?preset=liquid" -o /dev/null -w "%{time_total}s\n"
curl -s "http://127.0.0.1:5057/api/diag" | python -m json.tool   # cache hit-rate
```

## Remaining external TradingView limitations

* TradingView's `scanner.tradingview.com` **rate-limits per IP** and returns an
  empty body (`JSONDecodeError: Expecting value`) once a session makes many TA
  calls. This is upstream and cannot be fixed in code. Our mitigations: the
  breaker (fast-fail, no blocking), negative cache, in-flight coalescing, the free
  **yfinance fallback** in the decision engine, and the **stale disk cache** for
  the scanner. Under sustained throttle the terminal degrades (clearly labelled
  stale/degraded, lower confidence) instead of hanging.
* The optional **Webshare rotating proxy** (`PROXY_ENABLED=true` in `.env`) gives a
  fresh exit IP per deep-scan and is the durable cure for the per-IP limit; it is
  left disabled by default and untouched here.
* Per-symbol **volume/$-liquidity is not in the local master**, so the universe
  pre-filter ranks by the master's popularity prior; a future step is a
  server-side TradingView screener query (price/volume filtered, volume-ordered)
  to widen the pool beyond the curated liquid names with real liquidity data.

## Run the terminal

```bash
python dashboard/app.py        # serves http://127.0.0.1:5057  (DASHBOARD_PORT to change)
```
