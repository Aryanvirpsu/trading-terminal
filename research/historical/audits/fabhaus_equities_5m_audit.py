"""Strict audit of `fabhaus/equities_5m_stockprices` (the H4 primary bulk-data candidate).

Does NOT use `datasets.load_dataset` / the Hub viewer (both are noted as currently unreliable for this
dataset's schema/generation — see FABHAUS_AUDIT_REPORT.md). Instead it reads the exact raw monthly shard
files directly, at a pinned revision, via HTTP Range requests, and stream-filters them for only the target
symbols WITHOUT ever downloading or persisting a full shard (each shard is 7-23 GB; only a bounded byte
range is fetched, and only rows matching TARGET_SYMBOLS are kept).

Run manually (network + several hundred MB transient download, nothing persisted beyond the tiny filtered
sample and this script's own outputs):
    python research/historical/audits/fabhaus_equities_5m_audit.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from research.historical.guards import guard_all  # noqa: E402

DATASET_ID = "fabhaus/equities_5m_stockprices"
REVISION = "f17c0b0c3cf6a455994f93d6a85e76274172ab03"       # pinned — never "main"/"latest"
TARGET_SYMBOLS = ("AAPL", "MSFT", "META", "NVDA", "DELL", "AMD", "SPY", "QQQ")

# (shard, byte_range, label) — bounded, non-consecutive months + two days within the first shard
SAMPLES = [
    ("2024-01.jsonl", (0, 260_000_000), "2024-01, from file start (day 1 intraday sequence)"),
    ("2024-01.jsonl", (15_633_792_596 - 20_000_000, 15_633_792_596 - 1), "2024-01, from file end (month-end day)"),
    ("2025-01.jsonl", (0, 20_000_000), "2025-01, from file start (one non-consecutive month)"),
    ("2026-02.jsonl", (0, 20_000_000), "2026-02, from file start (a later non-consecutive month)"),
]

RAW_ROOT = f"https://huggingface.co/datasets/{DATASET_ID}/resolve/{REVISION}"
OUT_DIR = Path(__file__).resolve().parent / "fabhaus_sample"


def _fetch_range(shard: str, byte_range) -> tuple[bytes, str]:
    url = f"{RAW_ROOT}/{shard}"
    req = urllib.request.Request(url, headers={"Range": f"bytes={byte_range[0]}-{byte_range[1]}",
                                               "User-Agent": "avdi-historical-lab-audit"})
    with urllib.request.urlopen(req, timeout=120) as r:
        etag = r.headers.get("ETag", "").strip('"')
        return r.read(), etag


def _extract_rows(raw: bytes) -> List[Dict[str, Any]]:
    """Complete JSON lines only — a byte-range read almost certainly starts/ends mid-line; a truncated
    first/last line is DROPPED rather than guessed at."""
    text = raw.decode("utf-8", errors="strict")
    lines = text.split("\n")
    rows = []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue                                         # a boundary-truncated line — expected, skip it
        if row.get("symbol") in TARGET_SYMBOLS:
            rows.append(row)
    return rows


def run() -> Dict[str, Any]:
    guard_all(OUT_DIR)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_rows: List[Dict[str, Any]] = []
    shard_etags: Dict[str, str] = {}
    per_sample_meta = []
    for shard, byte_range, label in SAMPLES:
        raw, etag = _fetch_range(shard, byte_range)
        rows = _extract_rows(raw)
        all_rows.extend(rows)
        shard_etags.setdefault(shard, etag)
        per_sample_meta.append({"shard": shard, "label": label, "byte_range": list(byte_range),
                                "bytes_fetched": len(raw), "rows_matched": len(rows),
                                "distinct_symbols_matched": sorted({r["symbol"] for r in rows}),
                                "distinct_timestamps": len({r["datetime"] for r in rows})})
        print(f"[{label}] fetched {len(raw):,} bytes, matched {len(rows)} target-symbol rows, "
              f"symbols seen: {sorted({r['symbol'] for r in rows})}")

    sample_path = OUT_DIR / "sample_rows.jsonl"
    with open(sample_path, "w", encoding="utf-8") as fh:
        for r in all_rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    sample_sha256 = hashlib.sha256(open(sample_path, "rb").read()).hexdigest()

    meta = {"dataset_id": DATASET_ID, "revision": REVISION, "shard_etags_sha256": shard_etags,
            "samples": per_sample_meta, "total_rows_extracted": len(all_rows),
            "extracted_sample_sha256": sample_sha256, "extracted_sample_path": str(sample_path)}
    json.dump(meta, open(OUT_DIR / "fetch_manifest.json", "w"), indent=1, default=str)
    return meta


if __name__ == "__main__":
    print(json.dumps(run(), indent=1, default=str))
