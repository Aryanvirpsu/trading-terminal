"""Sentiment benchmark harness — scores the fixed labelled dataset with any
backend (lexical / FinBERT / hybrid) and reports the metrics the brief asks for:
accuracy, macro F1, per-class precision/recall/F1, calibration (Brier + ECE),
latency, throughput and memory.

Backend-agnostic: a `scorer(texts) -> [{label, probs{positive,neutral,negative}}]`.

Run:
    python lab/sentiment_benchmark.py                 # lexical baseline
    python lab/sentiment_benchmark.py --finbert       # + FinBERT + hybrid (needs deps)
"""
from __future__ import annotations

import os
import sys
import time
from typing import Callable, Dict, List

sys.path.insert(0, os.path.dirname(__file__))
from sentiment_dataset import DATASET, LABELS


def _rss_mb() -> float:
    """Resident set size (MB) — captures native torch allocations, unlike tracemalloc."""
    try:
        import ctypes
        class PMC(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        p = PMC(); p.cb = ctypes.sizeof(PMC)
        ctypes.windll.psapi.GetProcessMemoryInfo(
            ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(p), p.cb)
        return p.WorkingSetSize / 1024 / 1024
    except Exception:
        try:
            import resource
            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        except Exception:
            return -1.0


def _metrics(preds: List[Dict], golds: List[str]) -> Dict:
    idx = {l: i for i, l in enumerate(LABELS)}
    n = len(golds)
    conf = [[0] * 3 for _ in range(3)]     # rows=gold, cols=pred
    correct = 0
    brier = 0.0
    ece_bins = [[0, 0.0, 0.0] for _ in range(10)]   # [count, sum_conf, sum_correct]
    for p, g in zip(preds, golds):
        pl = p["label"] if p["label"] in idx else "neutral"
        conf[idx[g]][idx[pl]] += 1
        correct += (pl == g)
        probs = p.get("probs") or {}
        for k in LABELS:
            y = 1.0 if k == g else 0.0
            brier += (float(probs.get(k, 0.0)) - y) ** 2
        c = max(float(probs.get(k, 0.0)) for k in LABELS) if probs else (1.0 if pl == g else 0.0)
        b = min(9, int(c * 10))
        ece_bins[b][0] += 1; ece_bins[b][1] += c; ece_bins[b][2] += (pl == g)

    per_class = {}
    f1s = []
    for l, i in idx.items():
        tp = conf[i][i]
        fp = sum(conf[r][i] for r in range(3)) - tp
        fn = sum(conf[i][c] for c in range(3)) - tp
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        per_class[l] = {"precision": round(prec, 3), "recall": round(rec, 3), "f1": round(f1, 3),
                        "support": sum(conf[i])}
        f1s.append(f1)
    ece = sum(abs(cnt and (sc / cnt) - (sconf / cnt)) * (cnt / n) for cnt, sconf, sc in ece_bins if cnt)
    return {
        "n": n, "accuracy": round(correct / n, 3),
        "macro_f1": round(sum(f1s) / 3, 3),
        "per_class": per_class,
        "confusion": {"labels": list(LABELS), "matrix": conf},
        "brier": round(brier / n, 4),          # multiclass Brier (lower better)
        "ece": round(ece, 4),                  # expected calibration error (lower better)
    }


def category_accuracy(preds: List[Dict], items: List[Dict]) -> Dict[str, Dict]:
    out: Dict[str, List[int]] = {}
    for p, it in zip(preds, items):
        c = it["category"]
        out.setdefault(c, [0, 0])
        out[c][1] += 1
        out[c][0] += (p["label"] == it["label"])
    return {c: {"acc": round(v[0] / v[1], 3), "n": v[1]} for c, v in sorted(out.items())}


def run(scorer: Callable[[List[str]], List[Dict]], name: str, warmup: bool = True) -> Dict:
    items = DATASET
    texts = [d["text"] for d in items]
    golds = [d["label"] for d in items]

    rss0 = _rss_mb()
    if warmup:
        scorer(texts[:2])                       # warm caches / JIT / model
    rss1 = _rss_mb()

    # Throughput: one batched call over the whole set.
    t0 = time.perf_counter()
    preds = scorer(texts)
    batch_s = time.perf_counter() - t0
    throughput = round(len(texts) / batch_s, 1) if batch_s else 0.0

    # Warm single-item latency (median of repeated single calls).
    lat = []
    for t in texts[:12]:
        s = time.perf_counter(); scorer([t]); lat.append((time.perf_counter() - s) * 1000)
    lat.sort()
    warm_latency_ms = round(lat[len(lat) // 2], 2)

    m = _metrics(preds, golds)
    m.update({
        "backend": name,
        "warm_latency_ms": warm_latency_ms,
        "batch_throughput_per_s": throughput,
        "batch_total_ms": round(batch_s * 1000, 1),
        "rss_after_load_mb": round(rss1, 1),
        "rss_model_delta_mb": round(rss1 - rss0, 1),
        "category_accuracy": category_accuracy(preds, items),
    })
    return m


# ── Backends ─────────────────────────────────────────────────────────────────
def lexical_scorer(texts: List[str]) -> List[Dict]:
    import model_registry as mr
    return [mr.lexical_sentiment(t) for t in texts]


def print_report(m: Dict) -> None:
    print(f"\n=== {m['backend'].upper()} ===")
    print(f"  accuracy={m['accuracy']}  macro_F1={m['macro_f1']}  Brier={m['brier']}  ECE={m['ece']}")
    for l in LABELS:
        pc = m["per_class"][l]
        print(f"    {l:<9} P={pc['precision']:<5} R={pc['recall']:<5} F1={pc['f1']:<5} (n={pc['support']})")
    print(f"  warm_latency={m['warm_latency_ms']}ms  throughput={m['batch_throughput_per_s']}/s  "
          f"RSS_delta={m['rss_model_delta_mb']}MB")
    print(f"  by category: " + ", ".join(f"{c}={v['acc']}" for c, v in m["category_accuracy"].items()))


if __name__ == "__main__":
    import json
    results = {}
    lex = run(lexical_scorer, "lexical")
    print_report(lex)
    results["lexical"] = lex

    if "--finbert" in sys.argv:
        try:
            import finbert_service as fb
            results["finbert"] = run(lambda ts: fb.score(ts, force=True, timeout=120), "finbert")
            print_report(results["finbert"])
            results["hybrid"] = run(lambda ts: fb.score_hybrid(ts, force=True), "hybrid")
            print_report(results["hybrid"])
            results["finbert_health"] = fb.health()
        except Exception as e:  # noqa: BLE001
            print(f"\n[finbert unavailable: {e}]")

    out = os.path.expanduser("~/.tradingview_mcp_data/sentiment_benchmark.json")
    try:
        json.dump(results, open(out, "w"), indent=2)
        print(f"\nWrote {out}")
    except Exception:
        pass
