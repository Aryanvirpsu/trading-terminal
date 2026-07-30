# SENTIMENT_BASELINE.md

Baseline for the terminal's existing **lexical** financial-sentiment scorer
(`lab/model_registry.py :: lexical_sentiment`), measured on a fixed, hand-labelled
dataset before evaluating FinBERT. No new dependencies were installed for this.

## Dataset (`lab/sentiment_dataset.py`)

60 labelled financial headlines, balanced across the categories the brief calls
out. Financial-sentiment convention: `positive` = bullish, `negative` = bearish,
`neutral` = no clear directional signal.

* Labels: **positive 21 · neutral 17 · negative 22**
* Categories: earnings 10, procedural 8, guidance 7, regulatory 7, analyst 6,
  mixed_company 6, ambiguous 5, bad_news 4, market 3, legal 2, good_news 2.
* Mixed-company items appear once **per target ticker** (same text, different
  gold label) to test entity relevance.

## Lexical results

| Metric | Value |
|---|---|
| Accuracy | **0.733** |
| Macro F1 | **0.732** |
| Brier (multiclass, ↓) | 0.5221 |
| ECE (calibration error, ↓) | 0.2138 |
| Warm latency | **0.01 ms** |
| Throughput | ~124,000 headlines/s |
| Memory (model) | **0 MB** (pure Python) |

Per-class:

| Class | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| positive | 0.762 | 0.762 | 0.762 | 21 |
| neutral | 0.625 | 0.882 | 0.732 | 17 |
| negative | 0.867 | **0.591** | 0.703 | 22 |

Accuracy by category:

| Category | Acc | | Category | Acc |
|---|---|---|---|---|
| analyst | 1.00 | | guidance | 0.71 |
| good_news | 1.00 | | ambiguous | 0.60 |
| market | 1.00 | | **mixed_company** | **0.50** |
| procedural | 0.88 | | **legal** | **0.50** |
| earnings | 0.80 | | **regulatory** | **0.43** |
| bad_news | 0.75 | | | |

## Where lexical is weak (the bar FinBERT must clear)

1. **Regulatory (0.43)** — misses non-keyword negatives: "SEC opens an
   investigation", "FTC sues to block", "regulators fine the bank" don't trip the
   lexicon strongly.
2. **Mixed-company (0.50)** — a bag-of-words scorer can't tell *which* company a
   headline is bullish/bearish about ("Nvidia soars while Intel struggles").
3. **Legal (0.50)** — "judge dismisses lawsuit" (positive) vs "hit with a lawsuit"
   (negative) hinge on context.
4. **Low negative recall (0.59)** — many real negatives are worded without obvious
   negative keywords; lexical over-predicts neutral.
5. **Weak calibration** (ECE 0.21) — the lexical probabilities are coarse.

FinBERT is context-aware and trained on financial text, so these are exactly the
cases where it *could* help. Whether it does — enough to justify its latency and
memory — is measured in `FINBERT_BENCHMARK.md`.

## Reproduce
```bash
python lab/sentiment_dataset.py            # dataset stats
python lab/sentiment_benchmark.py          # lexical metrics (this file)
```
Writes `~/.tradingview_mcp_data/sentiment_benchmark.json`.
