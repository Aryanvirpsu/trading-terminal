"""HIST-001 Full stage: stream the 21 monthly fabhaus shards NOT already fetched for Medium
(2024-07 through 2026-03 -- the full remaining range at the pinned revision, confirmed via the HF tree API
to be exactly 27 monthly shards total, 2024-01..2026-03), filtering to the same 90-symbol universe Medium
used. Medium's already-fetched 6 months (2024-01..2024-06) are reused via their existing fetch manifests --
never re-downloaded.

Same resource-safe design as fetch_medium_shards.py: each month streamed independently (8MB chunks),
filtered on the fly, never held in full in memory or on disk. This is 21 of fabhaus's ~478GB corpus's total
shards (not the whole corpus, and not even the whole 27-shard range in one call -- Medium's 6 are reused).
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

from fetch_medium_shards import DATASET_ID, REVISION, SELECTED_COLUMNS, SYMBOLS

FULL_SHARDS = [f"{y}-{m:02d}.jsonl" for y in (2024,) for m in range(1, 13)] + \
             [f"{y}-{m:02d}.jsonl" for y in (2025,) for m in range(1, 13)] + \
             [f"2026-{m:02d}.jsonl" for m in range(1, 4)]
assert len(FULL_SHARDS) == 27, len(FULL_SHARDS)

ALREADY_FETCHED_FOR_MEDIUM = {"2024-01.jsonl", "2024-02.jsonl", "2024-03.jsonl", "2024-04.jsonl",
                              "2024-05.jsonl", "2024-06.jsonl"}
NEW_SHARDS = [s for s in FULL_SHARDS if s not in ALREADY_FETCHED_FOR_MEDIUM]
assert len(NEW_SHARDS) == 21, len(NEW_SHARDS)

HERE = Path(__file__).resolve().parent
CHUNK_SIZE = 8 * 1024 * 1024


def _out_paths(shard: str):
    month = shard.replace(".jsonl", "")
    return (HERE / f"full_{month}_filtered.jsonl", HERE / f"full_{month}_fetch_manifest.json")


def _fetch_one_shard(shard: str) -> dict:
    out_path, manifest_path = _out_paths(shard)
    url = f"https://huggingface.co/datasets/{DATASET_ID}/resolve/{REVISION}/{shard}"
    started = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "avdi-historical-lab-hist001"})
    sha = hashlib.sha256()
    total_bytes = 0
    kept_rows = 0
    seen_rows = 0
    buffer = b""
    symbol_counts = {s: 0 for s in SYMBOLS}

    with urllib.request.urlopen(req, timeout=120) as resp, open(out_path, "w", encoding="utf-8") as out:
        content_length = resp.headers.get("Content-Length")
        etag = resp.headers.get("ETag")
        while True:
            chunk = resp.read(CHUNK_SIZE)
            if not chunk:
                break
            sha.update(chunk)
            total_bytes += len(chunk)
            buffer += chunk
            *complete_lines, buffer = buffer.split(b"\n")
            for raw_line in complete_lines:
                if not raw_line.strip():
                    continue
                seen_rows += 1
                try:
                    row = json.loads(raw_line)
                except Exception:
                    continue
                sym = row.get("symbol")
                if sym in SYMBOLS:
                    filtered = {k: row.get(k) for k in SELECTED_COLUMNS}
                    out.write(json.dumps(filtered, sort_keys=True) + "\n")
                    kept_rows += 1
                    symbol_counts[sym] += 1
            if seen_rows % 4_000_000 < len(complete_lines):
                elapsed = time.time() - started
                print(f"  [{shard}] progress: {total_bytes/1e9:.2f} GB read, {seen_rows:,} rows seen, "
                     f"{kept_rows:,} kept, {elapsed:.0f}s elapsed", file=sys.stderr, flush=True)
        if buffer.strip():
            try:
                row = json.loads(buffer)
                if row.get("symbol") in SYMBOLS:
                    filtered = {k: row.get(k) for k in SELECTED_COLUMNS}
                    out.write(json.dumps(filtered, sort_keys=True) + "\n")
                    kept_rows += 1
                    symbol_counts[row["symbol"]] += 1
            except Exception:
                pass

    elapsed = time.time() - started
    manifest = {
        "dataset_id": DATASET_ID, "revision": REVISION, "shard": shard, "shard_url": url,
        "upstream_content_length": content_length, "upstream_etag": etag,
        "downloaded_bytes": total_bytes, "downloaded_sha256": sha.hexdigest(),
        "rows_seen_total": seen_rows, "rows_kept": kept_rows, "symbol_counts": symbol_counts,
        "selected_columns": SELECTED_COLUMNS, "symbols_requested": sorted(SYMBOLS),
        "fetch_seconds": round(elapsed, 1), "fetch_method": "streamed HTTP GET, filtered on the fly, "
        "never held in full in memory or written unfiltered to disk",
    }
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
    print(f"[{shard}] done: {json.dumps({k: v for k, v in manifest.items() if k != 'symbol_counts'})}",
         file=sys.stderr, flush=True)
    return manifest


def run():
    results = []
    for shard in NEW_SHARDS:
        out_path, manifest_path = _out_paths(shard)
        if out_path.exists() and manifest_path.exists():
            print(f"[{shard}] already fetched, skipping", file=sys.stderr, flush=True)
            with open(manifest_path, encoding="utf-8") as fh:
                results.append(json.load(fh))
            continue
        results.append(_fetch_one_shard(shard))
    zero_rows = [m["shard"] for m in results if m["rows_kept"] == 0]
    if zero_rows:
        print(f"WARNING: shards with zero kept rows: {zero_rows}", file=sys.stderr)
    print(json.dumps({"shards_fetched": len(results),
                      "total_kept_rows": sum(m["rows_kept"] for m in results)}, indent=1))


if __name__ == "__main__":
    run()
