# MODEL_EVALUATION.md

How the terminal uses ML models, what was evaluated, and why the current choices
were made. Guiding principle from the brief: **prefer lightweight, local,
open-source inference; do not blindly deploy heavy models; only adopt a model that
shows a real out-of-sample improvement on the smallest footprint.**

Everything here is wired through one seam — the **model registry**
(`lab/model_registry.py`) — so any slot can be upgraded by an env var without
touching the terminal.

---

## 1. Environment / footprint on this machine

`transformers` and `torch` are **not installed**, so no Hugging Face weights are
downloaded or run here. The terminal is fully functional regardless: every model
slot has a dependency-free fallback and a graceful-degradation path. Enabling a HF
model is a two-step opt-in (`pip install transformers torch` + set the env flag).

---

## 2. Financial sentiment  ✅ shipped (lexical default, FinBERT-ready)

**Slot:** `sentiment` · **Default backend:** lexical · **Optional:** `ProsusAI/finbert`.

* **Default — lexical:** a finance-tuned polarity lexicon (Loughran-McDonald-style
  positive/negative terms + the catalyst keyword engine) with negation handling.
  Deterministic, offline, ~microseconds per headline, fully explainable.
* **Optional — FinBERT (`ProsusAI/finbert`):** enabled with
  `SENTIMENT_MODEL_ENABLED=true` when `transformers`+`torch` are present. Loaded
  **lazily and once**, batched, truncated. Any load/runtime error silently falls
  back to lexical — a model outage can never blank the UI.
* **Also worth benchmarking** (documented, not wired): `clapAI/Fin-ModernBERT`,
  validated FinGPT sentiment heads. Drop-in: set `SENTIMENT_MODEL` to the id.

**Aggregation (the important part).** Per the brief, sentiment is **not** a blind
average. `aggregate_news_sentiment()` combines each headline's polarity with:
* **recency decay** — exponential, ~48 h half-life; undated → neutral 0.5;
* **source reliability** — priors for Reuters/Bloomberg/WSJ/CNBC/… (unknown = 0.5);
* **ticker relevance** — subject vs passing mention vs irrelevant (excluded);
* **duplicate detection** — near-duplicate headlines collapsed by title-Jaccard,
  keeping the most reliable source.

Output is an explainable aggregate (score, label, confidence, positive/negative/
neutral counts, dedup count, top weighted headlines, and the active backend),
surfaced in the Catalysts tab.

---

## 3. Semantic search & RAG  🔶 declared, offline

**Slot:** `embeddings` · **Default:** not wired · **Configured ids:**
`FinLang/finance-embeddings-investopedia` (primary),
`sentence-transformers/all-MiniLM-L6-v2` (light fallback), reranker
`BAAI/bge-reranker-v2-m3` / `onnx-community/bge-reranker-v2-m3-ONNX` (Node/ONNX).

Declared in the registry with env ids so the retrieval layer can be switched on
without code changes. Not enabled here because it requires the embedding stack and
a document store; the metadata contract (ticker, doc type, source, publication
time, reporting period, chunk location) is specified for when it is wired. Until
then, catalysts/news are retrieved directly per ticker (Google News + Finnhub)
and relevance-filtered by the aggregation layer above.

---

## 4. Time-series forecasting  🔬 benchmark shipped, HF models deferred

**Slot:** `forecast` · **Candidates named by the brief:**
`ibm-granite/granite-timeseries-ttm-r3`, `amazon/chronos-2`,
`google/timesfm-2.5-200m-pytorch`.

Rather than deploy any of these on faith, `lab/forecast_benchmark.py` implements
the **offline walk-forward harness** the brief asked for, with **structural
leakage prevention** (every forecast for *t+h* is computed from `closes[:t+1]`
only; a guard mutates the future and asserts predictions don't change) and the
required metrics (MAE, RMSE, MASE, directional accuracy) across multiple assets,
regimes and horizons.

**Baselines measured** (5 real assets — AAPL, SPY, NVDA, KO, TLT — 2y daily,
horizons 1 & 5, out-of-sample):

| Model | mean MASE | mean directional accuracy |
|---|---|---|
| **last_value (naive)** | **1.000** | 0.532 |
| moving_avg_5 | 1.296 | 0.513 |
| linear_trend | 1.532 | 0.527 |

**Reading:** MASE = 1.0 means "no better than naive last-value"; the two other
free baselines are **worse** out-of-sample, and directional accuracy hovers at
coin-flip (~0.52). This is the honest bar.

**Decision:** the smallest thing that isn't beaten wins → **last-value is the
current baseline.** A heavy HF model earns its footprint only if, on this same
harness, it clears roughly **MASE < 0.95 AND directional accuracy > 0.55**. The
harness is ready: set `FORECAST_MODEL_ENABLED=true`, install the model's deps, add
its `(history, horizon) -> level` predictor to `MODELS`, and the same loop scores
it apples-to-apples. Forecasts, when adopted, are reported as **quantiles /
distributions**, never a single "guaranteed" price.

Reproduce: `python lab/forecast_benchmark.py` (writes
`~/.tradingview_mcp_data/forecast_benchmark.json`); `--synthetic` for a fully
offline, deterministic run.

---

## 5. Anomaly detection  🔶 declared

**Candidate:** `ibm-granite/granite-timeseries-tspulse-r1` for unusual price/volume
moves, volatility spikes, feed anomalies and missing/corrupted candles. Kept
**separate** from directional scoring per the brief. Not wired here (deps); the
cheap statistical guards (gap-through-stop, spread-widening, volume-dry-up) already
feed the decision engine's invalidation conditions in the meantime.

---

## 6. Ensemble & confidence

The decision engine already scores **independent** families (trend, momentum,
fundamentals, valuation, earnings-revisions, catalysts, sentiment, relative
strength, forecast, risk) and separates P(direction), P(trade-profitable), data
confidence and execution confidence — models feed **components**, never a direct
buy/sell. The registry adds a versioned, env-configurable sentiment component and
records the active backend so a score is reproducible. Confidence is penalised for
stale data, source disagreement, low coverage, illiquidity and thin samples.

---

## 7. Operational rules (met)

* One persistent model service, lazy startup, batching, health check
  (`/api/models`), no repeated downloads or per-call initialisation.
* Every model path fails to a working fallback; no model can block a request or
  blank a panel.
* Model choices are env-configurable and versioned (`REGISTRY_VERSION`).
