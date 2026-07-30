# MODEL_INVENTORY.md

Every model referenced by the terminal, its real footprint, current usage, and a
keep / replace / disable recommendation. **Baseline capture — nothing was
downloaded or deleted.**

## Headline finding

**No Hugging Face model is installed, downloaded, or loaded.**

* `~/.cache/huggingface` = **488 KB**, contains **no `hub/` directory** → zero model
  weights on disk.
* No `~/.cache/torch`, no `*.safetensors` / `*.onnx` / `*.bin` / `*.gguf` / `*.pt`
  anywhere on disk (repo or home).
* Not installed: `torch`, `transformers`, `sentence-transformers`, `onnxruntime`,
  `onnx`, `optimum`, `tokenizers`, `accelerate`, `safetensors`, `scikit-learn`.
  Installed: `huggingface_hub` 1.21.0 (client only — no inference stack).
* `GEMINI_API_KEY` is set but used **only as a health boolean** in
  `research.provider_health()` — **no** `google-generativeai`/`genai` import, no LLM
  call anywhere. Every scanner/engine module is explicitly "NO LLM, NO Claude".

So the only thing that actually runs today is a **pure-Python lexical sentiment
scorer** plus rules engines. Everything else in the list below is **declared** (an
id string in `lab/model_registry.py` / `.env.example` / docs) but **inert** until
someone installs the deps and flips a flag.

---

## A. Active models (actually running today)

| Model / component | ID / location | Task | Size | Memory | Load time | Inference speed | Current usage | Verdict |
|---|---|---|---|---|---|---|---|---|
| **Lexical financial sentiment** | `lab/model_registry.py` (`lexical_sentiment`) | Headline polarity + aggregation | ~30 KB code | negligible | **1.4 ms import** | **0.09 ms/call, ~160k/s** | **Default sentiment backend** (Catalysts tab, decision engine) | **KEEP** — free, instant, explainable |
| **Catalyst keyword engine** | `lab/catalyst_scan.py` (`_COMPILED`) | Rules-based catalyst/event scoring | regex table | negligible | ~ms | ~instant | Catalyst scoring, scanner | **KEEP** |
| **Decision engine** | `lab/decision_engine.py` | 9-family EV/confidence scoring (statistical, not ML) | code | negligible CPU | n/a | **37–53 s** (throttle-bound, not model-bound — see `BASELINE.md`) | Stock overview / analysis | **KEEP** (fix throttling, not the "model") |
| **Forecast baselines** | `lab/forecast_benchmark.py` | last-value / MA / linear-trend | code | negligible | n/a | µs | Benchmark harness | **KEEP** — current forecast baseline |

---

## B. Declared Hugging Face models (NOT installed — none on disk)

Sizes are **approximate, from each model card/architecture** (nothing was
downloaded to measure). Load time / memory / inference are **N/A — cannot run**
without `torch`+`transformers`. "Fits?" judges the host reality: **4 GB VRAM /
1.1 GB free RAM**.

| Model ID | Task | Slot | Approx size (fp32) | Fits? | Current usage | Verdict |
|---|---|---|---|---|---|---|
| `ProsusAI/finbert` | Financial sentiment | `sentiment` (env `SENTIMENT_MODEL`) | ~440 MB (110 M params) | Yes (GPU comfortably; CPU tight) | Declared, **disabled** (`SENTIMENT_MODEL_ENABLED=false`) | **KEEP as opt-in** — the one worth enabling; run on the idle RTX 3050. Lexical stays the fallback. |
| `FinLang/finance-embeddings-investopedia` | Finance embeddings (RAG) | `embeddings` | ~440 MB | Yes (GPU) | Declared, **not wired** (no RAG store) | **DISABLE for now** — only needed once retrieval/RAG is built |
| `sentence-transformers/all-MiniLM-L6-v2` | Lightweight embeddings fallback | `embeddings` fallback | **~90 MB** (22 M) | Yes (easily) | Declared, not wired | **KEEP declared** — the pragmatic embedding choice when RAG lands |
| `BAAI/bge-reranker-v2-m3` | Reranker | reranking | **~2.3 GB** (568 M) | **Tight/No** — dominates 4 GB VRAM | Declared, not wired | **REPLACE** with the ONNX-quantised variant below; too heavy as-is |
| `onnx-community/bge-reranker-v2-m3-ONNX` | Reranker (ONNX, Node-friendly) | reranking | ~0.6–1.1 GB (quantised) | Marginal | Declared, not wired | **DISABLE now**; if reranking is needed, prefer this over the fp32 original |
| `ibm-granite/granite-timeseries-ttm-r3` | Time-series forecast (TinyTimeMixer) | `forecast` (env `FORECAST_MODEL`) | **~5–20 MB** (few M params) | Yes (trivially) | Declared, **disabled** | **KEEP declared, DISABLE until it clears the bar** — smallest candidate; try first, but the walk-forward harness shows baselines (MASE 1.0) are not yet beaten |
| `amazon/chronos-2` | Time-series forecast | `forecast` alt | ~200–700 MB (variant-dependent) | Yes (GPU) | Declared (docs), not enabled | **DISABLE / defer** — heavier than TTM with no proven edge on this harness |
| `google/timesfm-2.5-200m-pytorch` | Time-series forecast | `forecast` alt | ~800 MB (200 M) | Yes (GPU) | Declared (docs), not enabled | **DISABLE / defer** — largest forecaster; only if TTM+Chronos both fail the bar |
| `ibm-granite/granite-timeseries-tspulse-r1` | Anomaly detection | `anomaly` | ~few–tens MB | Yes | Declared (docs), not wired | **KEEP declared, DISABLE now** — cheap statistical guards cover this today; enable later, kept separate from directional scoring |
| `Jean-Baptiste/roberta-ticker` | NER ticker extraction from free text | (brief only) | ~500 MB (RoBERTa-base) | Yes (GPU) | **Not referenced in any code** — brief suggestion only | **DROP / do not add** — the local security master (30,303 symbols, ~9 ms fuzzy/alias search) already resolves tickers deterministically and offline; a 500 MB NER model is redundant |

---

## C. Recommendation summary (for the later optimisation pass — not done yet)

* **Keep running as-is:** lexical sentiment + rules engines + statistical forecast
  baselines. They are free, instant, and adequate. The terminal's slowness is
  **provider throttling, not models** (see `BASELINE.md`).
* **Enable only one HF model, and only deliberately:** `ProsusAI/finbert` on the
  **idle RTX 3050 GPU** (keep lexical as automatic fallback) — the single upgrade
  with a clear, bounded footprint and real accuracy benefit. Requires
  `pip install transformers torch` + `SENTIMENT_MODEL_ENABLED=true`.
* **Forecast:** if pursued, try **`granite-timeseries-ttm-r3`** first (tiny) and let
  the walk-forward harness decide; it must clear ~MASE < 0.95 to justify itself.
  Defer Chronos-2 / TimesFM.
* **Disable / defer:** embeddings, rerankers, anomaly and the large forecasters —
  none are wired to a feature yet, and the reranker fp32 (2.3 GB) would crowd the
  4 GB VRAM.
* **Drop:** `roberta-ticker` — superseded by the local security master.
* **Watch RAM:** with only **1.1 GB free**, any CPU-hosted model is risky; prefer the
  GPU (0 MiB used) for anything enabled, and load lazily/once (the registry already
  does this).

*No model was downloaded, enabled, or deleted to produce this inventory.*
