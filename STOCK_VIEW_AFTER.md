# STOCK_VIEW_AFTER.md

Result of the Prompt-3 progressive-loading work. The stock page now paints
useful content almost immediately and streams the deep analysis in behind it.
Measured against the running app on `:5057`. **No ML added.**

---

## Before / after

| Metric | Before | After |
|---|---:|---:|
| First useful content | unknown | **93 ms** (header identity from local master) |
| Quote / header | 1,331 ms | **~450 ms** (identity 93 ms + live price 356 ms) |
| Chart visible | 3,277 ms cold | 3.3 s cold / **15 ms warm** (lazy, non-blocking) |
| Core summary | 4,111 ms (full engine) | **1.7–2.2 s cold / 15 ms warm** (fast summary) |
| Decision engine cold | 15.6 s | 15.6 s full — but **summary in ~2 s**; deep streams behind |
| Decision engine warm | 5.3 s | 5.3 s full / **15 ms summary** |
| Full deep analysis | blocks the tab | **streams in behind the summary, non-blocking** |
| Duplicate requests | (deduped) | resolve 1× · summary 1× · overview 2× (async poll, not a dup) |
| Failed panel behaviour | independent | independent + typed states; sentiment 16 s → **~6 s** cap |

---

## Acceptance targets

| Target | Status | Evidence |
|---|---|---|
| Cached header visible < 500 ms | ✅ **93 ms** | `/api/search` identity (local master) |
| Cached chart + summary < 1 s | ✅ | chart warm 15 ms, summary warm 15 ms |
| Warm decision summary < 2 s | ✅ **15 ms** | `/api/symbol/summary` warm |
| Cold decision summary < 5 s | ✅ **1.7–2.2 s** | `/api/symbol/summary` cold (fast families only) |
| Deep analysis loads without blocking | ✅ | `loadOverviewDeep()` streams behind the summary |
| No duplicate frontend requests | ✅ | resolve/summary 1× each; overview 2× = async poll |
| One failed provider doesn't block panels | ✅ | each panel independent (`swr`/`swr_async` + typed state) |
| No blank page during refresh | ✅ | skeletons + instant master header |
| All existing 199 tests pass | ✅ | **208 passed, 1 skipped** (199 + 9 new) |

---

## What changed

### Backend — two-phase decision engine (`lab/decision_engine.py`, `dashboard/research.py`)
* New **`evaluate_summary()`** computes ONLY the two cheap families — trend/momentum
  (from the base TA call, shared with the scanner + full engine, no duplicate calc)
  and regime (cached) — plus ATR entry/stop/target. Returns
  `analysis_status="summary"`, `provisional=true`, and `pending_families` (the 7
  slow I/O families still to run). ~1.7–2.2 s cold / 15 ms warm.
* The full `evaluate()` now tags its result `analysis_status="complete"`,
  `pending_families=[]`.
* New **`research.summary()`** wraps it (adds bull/base/bear scenarios, state,
  provenance) behind a 60 s `swr` cache. New endpoint **`/api/symbol/summary`**.
* Sentiment gather timeouts tightened (10 s→6 s main, 6 s→3 s sector) so a throttled
  provider is dropped for a partial reading instead of a 16 s hang.

### Frontend — progressive load (`dashboard/terminal.html`)
* **Header** paints name + exchange + type from the local master (`/api/search`,
  ~93 ms) instantly, then fills the live price when `resolve` returns — never blank.
* **Overview tab** is now two-phase: `tabOverview()` renders the **fast summary
  first** (provisional decision, quality, P(dir), entry/stop/target, bull/base/bear,
  the 2 fast family bars, a "⏳ computing deep analysis · 7 families" badge and a
  "2 of 9" family counter), then `loadOverviewDeep()` streams the full multi-family
  analysis in behind it and flips the badge to "✓ analysis complete / 9 of 9".
* Stale sequence-guard (`_ovSeq`) drops responses when the ticker switches; the
  deep phase polls the `swr_async` overview without blocking.

### Decision UI (progressive-friendly)
Shows: bull/base/bear cases · component (family) scores as signed bars · supporting
vs conflicting evidence · risk flags (reject reasons + invalidation) · confidence
(quality, P(dir), P(profit)) · **data quality** (data-confidence %, execution %) ·
**provider status** (`fresh` / `⚠ fallback provider`) · **signal disagreement**
warning (families split, or fallback provider) · what-changed-since-last-analysis ·
and the **completion status** badge. No generic conclusions — every number is
sourced and the provisional summary is clearly labelled.

---

## Browser trace findings

Driving the page for `WMT` (cold), via the in-app browser:

| t (ms) | State |
|---:|---|
| 3 | skeleton up, quote shows "loading quote…" (no blank) |
| 827 | header = **"Walmart Inc. · NASDAQ", price $109.47 +0.99%** |
| ~2 000 | fast summary: **MONITOR (provisional)**, scenarios, "⏳ computing deep · 7 families", "2 of 9" |
| deep done | badge → **"✓ analysis complete"**, "9 of 9" families |

* **Console errors: none.**
* **Network for one symbol:** `resolve` ×1, `summary` ×1, `overview` ×2 (the second
  is the async-ready poll ~1.6 s later, not a duplicate) — no repeated header/summary
  fetches, base TA shared across summary + overview + technicals.
* **Failed/slow panel:** sentiment (the slowest) loads independently via `swr_async`
  and shows a loading→state placeholder; it never blocks the header, chart, summary
  or the rest of the page.

(Under an active TradingView per-IP throttle the *summary* cold can stretch to ~7 s
because the base-TA call retries then falls to yfinance — but it still never blocks
the header or the other panels.)

---

## Test results

* `pytest tests/ -q` → **208 passed, 1 skipped** (Playwright E2E skips until installed).
* New: `tests/unit/test_stock_view.py` — 9 tests (summary status/pending/geometry,
  no-data degradation, full-evaluate marks complete, `research.summary` wrapper +
  unsupported + no-price states).
* New: `tests/e2e/test_stock_view_playwright.py` — Playwright E2E (header fast,
  summary-before-deep, completion badge, no-blank-on-tab-switch); skips unless
  `STOCK_VIEW_E2E=1` and a server is up.

## Files changed
* `lab/decision_engine.py` — `evaluate_summary()`, `_DEEP_FAMILIES`,
  `analysis_status`/`pending_families` on `evaluate()`.
* `dashboard/research.py` — `summary()` + `_summary_compute()`; tightened sentiment
  timeouts.
* `dashboard/app.py` — `/api/symbol/summary`.
* `dashboard/terminal.html` — instant master header; two-phase progressive overview
  (`renderOverviewSummary` / `loadOverviewDeep` / `renderOverviewDeep`) + the decision
  UI (completion status, data quality, provider status, disagreement, component scores).
* `tests/unit/test_stock_view.py`, `tests/e2e/test_stock_view_playwright.py` — new.
* `STOCK_VIEW_BASELINE.md`, `STOCK_VIEW_AFTER.md`.

## Remaining provider limitations
* TradingView per-IP throttling still makes the **cold** base-TA (hence a
  first-ever summary / technicals) slow when the limit is hit; the summary then
  degrades to the free yfinance fallback (clearly labelled). This is upstream — see
  `SCANNER_SPEED_AFTER.md`.
* **Chart cold** is a ~3.3 s yfinance history fetch (warm 15 ms); it loads lazily
  and never blocks the header or summary.
* **Sentiment** can still take ~6 s cold (down from ~16 s) when a sub-provider is
  throttled — it loads independently and never blocks the page.
* Playwright browsers aren't installed here, so the E2E suite is provided but skipped
  by default (`pip install playwright pytest-playwright && playwright install chromium`).

## Run the terminal
```bash
python dashboard/app.py            # http://127.0.0.1:5057  (DASHBOARD_PORT to change)
```
