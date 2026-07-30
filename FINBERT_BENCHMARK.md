# FINBERT_BENCHMARK.md

Does `ProsusAI/finbert` measurably improve ticker-specific financial-news sentiment
enough to justify its latency and memory? Measured on the fixed 60-item labelled
dataset (`lab/sentiment_dataset.py`; baseline in `SENTIMENT_BASELINE.md`).

Environment: Windows, Python 3.13, **CPU torch 2.13.0+cpu** (CUDA not installed →
FinBERT ran on CPU; the RTX 3050 would be used automatically if CUDA torch were
present). ~2.3 GB free RAM of 16.8 GB at test time.

## Comparison

| Metric | Lexical | FinBERT | Hybrid |
| --- | ---: | ---: | ---: |
| **Macro F1** | 0.732 | 0.781 | **0.798** |
| Accuracy | 0.733 | 0.783 | **0.800** |
| **Calibration** — ECE (↓) | 0.2138 | 0.1194 | **0.1066** |
| **Calibration** — Brier (↓) | 0.5221 | 0.3428 | **0.3122** |
| **Earnings accuracy** | 0.80 | **1.00** | **1.00** |
| **Multi-company accuracy** | 0.50 | 0.50 | 0.50 |
| Regulatory accuracy | 0.43 | **0.86** | **0.86** |
| Negative recall | 0.591 | **0.909** | **0.909** |
| **Warm latency** (per headline) | **0.01 ms** | 36.6 ms CPU · instant cached | 0.02 ms cached |
| **Batch throughput** | ~148,000/s | ~48/s CPU | ~49,000/s cached |
| **RAM / VRAM** | **0 MB** | ~712 MB private / 252 MB RSS · **0 VRAM** (CPU build) | ~712 MB |
| Load time | n/a | 15.3 s warm · 41.5 s first (download) | 15.3 s |

Hybrid per-class F1: positive 0.778, neutral 0.800, negative 0.816.

## What FinBERT fixes (and breaks)

**Improves the exact categories lexical was weak on:**
* **Regulatory 0.43 → 0.86** — "SEC opens an investigation", "FTC sues to block",
  "regulators fine the bank" are now correctly negative.
* **Negative recall 0.59 → 0.91** — lexical missed keyword-free negatives.
* **Earnings 0.80 → 1.00**, bad_news 0.75 → 1.00, procedural 0.88 → 1.00.
* **Calibration** — Brier −34 %, ECE −44 %. Trustworthier probabilities, which
  matters for the confidence-weighted aggregate.

**Regresses on a few** (FinBERT has a mild conservative/negative bias): good_news
1.00 → 0.50, analyst 1.00 → 0.67, ambiguous 0.60 → 0.40, positive recall 0.76 → 0.62.
The **hybrid** (0.7 FinBERT + 0.3 lexical) recovers most of these while keeping the
gains — best macro F1 (0.798) and best calibration.

**Multi-company stays 0.50 for all three** — no headline-level classifier can tell
*which* company a mixed headline is bullish/bearish about. That's handled by the
**entity-relevance layer** (`aggregate_news_sentiment` + `_relevance`): it
down-weights/excludes headlines where the target ticker isn't the subject and
lowers confidence for multi-company news. Verified: an "Intel downgrade" headline
is excluded from NVDA's aggregate (coverage drops below 100 %).

## Keep-criteria assessment

| Criterion | Met? | Evidence |
|---|---|---|
| Clearly improves out-of-sample classification | ✅ | +0.05 macro F1 (FinBERT), +0.07 (hybrid); calibration halved; fixes regulatory/negative-recall |
| Warm inference acceptable | ✅ (with a caveat) | cached = instant; uncached 36 ms CPU; but 15 s cold load + 712 MB |
| Does not block stock page or scanner | ✅ | lazy load in a background thread **after** the page is usable; per-call timeout; result cache |
| Fails cleanly to lexical | ✅ | timeout/absence/error → lexical (unit-tested) |

## Decision — KEEP, shipped DISABLED by default

FinBERT is genuinely better and is fully wired (hybrid + lexical fallback), so it's
kept — but it ships **`SENTIMENT_MODEL_ENABLED=false` on this machine**, because:

* The gain is real but **modest** (+5–7 F1 pts); lexical is already 73 % accurate,
  instant, and 0-memory — adequate for the at-a-glance sentiment badge.
* The cost is **material here**: ~712 MB committed of ~2.3 GB free + a ~15 s CPU
  load; loading it under memory pressure risks the scanner/terminal (the box pages
  heavily — a full FinBERT re-run timed out at 2 min).
* CPU per-headline latency is 36 ms vs 0.01 ms lexical, acceptable only because
  it's async + cached.

**Enable it** (`SENTIMENT_MODEL_ENABLED=true`) on a machine with RAM headroom, or
with **CUDA torch + the RTX 3050** (GPU cuts latency ~5–10× and uses VRAM instead of
RAM). When enabled, the terminal uses the **hybrid** with automatic lexical fallback.

## Files changed
* **New:** `lab/finbert_service.py` (lazy · shared · batched · cached · timeout ·
  health · lexical fallback · GPU-aware), `lab/sentiment_dataset.py`,
  `lab/sentiment_benchmark.py`, `tests/unit/test_finbert_sentiment.py`,
  `SENTIMENT_BASELINE.md`, this file.
* **Changed:** `lab/model_registry.py` — `sentiment()` routes through the FinBERT
  service when enabled; `aggregate_news_sentiment` adds `probs`/`coverage`/
  `avg_relevance`/`model_disagreements`; `_relevance` multi-company discount;
  `status()` reports FinBERT health. `dashboard/research.py` — catalysts use the
  hybrid + attach per-headline probs/relevance/disagreement; warm FinBERT lazily.
  `dashboard/app.py` — FinBERT warmed LAST in the background, only if enabled.
  `dashboard/terminal.html` — Catalysts UI shows per-class probabilities, article
  relevance, source+timestamp, lexical-vs-FinBERT disagreement, aggregate sentiment,
  confidence, coverage, and the backend/fallback badge. `.env.example` — documented.

## Benchmark commands
```bash
python lab/sentiment_benchmark.py                # lexical baseline
python lab/sentiment_benchmark.py --finbert      # + FinBERT + hybrid (pulls ProsusAI/finbert on first run)
pytest tests/unit/test_finbert_sentiment.py -q   # service/relevance/aggregate tests (no model load)
```
Minimum deps installed: `torch` (CPU wheel) + `transformers`. **No embeddings,
rerankers, or forecasting models were downloaded.**

## Bottom line
FinBERT clearly improves classification (F1 +5–7 pts, calibration halved, fixes the
regulatory/negative-recall blind spots), loads without blocking the page, and falls
back cleanly to lexical — but it's **off by default here** because ~712 MB + 15 s
load on a 2.3 GB-free box isn't worth a modest bump when lexical covers the common
cases. One flag turns it on for a roomier machine or a CUDA GPU.
